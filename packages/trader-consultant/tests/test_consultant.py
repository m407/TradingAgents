from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from trader_consultant import (
    AllocationProposal,
    ConsultationRequest,
    ExcludedAsset,
    TraderConsultant,
)

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


def request(actions=None):
    def position(ticker, qty, price="10"):
        return {
            "ticker": ticker,
            "kind": "stock",
            "currency": "USD",
            "quantity": qty,
            "available_quantity": qty,
            "price": price,
            "price_at": NOW,
            "quantity_step": "1",
        }

    return {
        "snapshot": {
            "snapshot_id": "test-snapshot",
            "as_of": NOW,
            "currency": "USD",
            "free_cash": "1000",
            "positions": [
                position("A", "10"),
                position("B", "0"),
                position("S", "20"),
                position("K", "10"),
            ],
            "complete": True,
            "has_active_orders": False,
            "excluded_assets": [{"identifier": "BOND", "reason": "unsupported bond"}],
        },
        "recommendations": [
            {"ticker": t, "action": a}
            for t, a in (actions or [("A", "BUY"), ("B", "BUY"), ("S", "SELL"), ("K", "KEEP")])
        ],
        "policy": {
            "buy_budget": "500",
            "cash_reserve": "100",
            "max_weights": {"A": ".5", "B": ".5", "S": ".5", "K": ".5"},
            "commission_rate": ".01",
            "slippage_rate": ".01",
            "minimum_trade": "10",
            "max_snapshot_age_seconds": 300,
            "max_price_age_seconds": 60,
            "order_ttl_seconds": 120,
            "max_attempts": 10,
        },
    }


def proposal(amount="200", sells=None):
    return {
        "purchase_amount": amount,
        "sell_target_weights": {"S": ".1"} if sells is None else sells,
        "explanation": "A small, capped rebalance.",
        "reasons": dict.fromkeys(["A", "B", "S", "K"], "Evidence supplied."),
    }


class Model:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.bound = 0

    def with_structured_output(self, schema):
        assert schema is AllocationProposal
        self.bound += 1
        return self

    def invoke(self, messages):
        self.calls.append(list(messages))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def consult(req=None, responses=None, clock=lambda: NOW):
    model = Model(responses or [proposal()])
    result = TraderConsultant(model, clock=clock).consult(req or request())
    return result, model


def test_draft_is_unconfirmed_equal_buys_and_partial_sell_with_costs():
    result, model = consult()
    assert result.status == "awaiting_confirmation"
    draft = result.draft
    assert draft.status == "awaiting_confirmation" and result.attempts == 1
    assert [(t.ticker, t.side, t.quantity) for t in draft.trades] == [
        ("A", "BUY", D(10)),
        ("B", "BUY", D(10)),
        ("S", "SELL", D(6)),
    ]
    assert draft.weight_basis_nav == 1400
    assert draft.cash_reserved_for_buys_and_fees == D("204.02")
    assert draft.estimated_cash_after == D("854.786")
    assert draft.expires_at == NOW + timedelta(seconds=60)
    assert next(a for a in draft.allocations if a.ticker == "K").resulting_quantity == 10
    assert draft.excluded_assets == (ExcludedAsset(identifier="BOND", reason="unsupported bond"),)
    assert len(draft.input_fingerprint) == 64 and model.bound == 1


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("has_active_orders", True, "active_orders"),
        ("complete", False, "incomplete_snapshot"),
        ("as_of", NOW - timedelta(seconds=301), "snapshot_age"),
        ("as_of", NOW + timedelta(seconds=1), "snapshot_age"),
    ],
)
def test_invalid_snapshot_blocks_before_model(field, value, code):
    req = request()
    req["snapshot"][field] = value
    result, model = consult(req)
    assert result.status == "blocked" and result.draft is None and model.bound == 0
    assert code in [e.code for e in result.issues]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["policy"]["max_weights"].pop("A"),
        lambda r: r["snapshot"]["positions"][0].update(price_at=NOW - timedelta(seconds=61)),
        lambda r: r["snapshot"]["positions"][0].update(price="NaN"),
        lambda r: r["snapshot"]["positions"][0].update(quantity="-1"),
        lambda r: r["snapshot"]["positions"][0].update(currency="EUR"),
        lambda r: r["snapshot"]["positions"][0].update(available_quantity="11"),
        lambda r: r["recommendations"].append({"ticker": "A", "action": "SELL"}),
        lambda r: r["recommendations"].append({"ticker": "UNKNOWN", "action": "BUY"}),
        lambda r: r["policy"].update(buy_budget="1"),
        lambda r: r["snapshot"]["positions"][2].update(available_quantity="0"),
        lambda r: r["policy"]["max_weights"].update(A=".01"),
    ],
)
def test_bad_input_and_impossible_minimum_never_call_llm(mutation):
    req = request()
    mutation(req)
    result, model = consult(req)
    assert result.status == "blocked" and result.attempts == 0 and model.bound == 0


def test_keep_only_has_no_order_and_does_not_bind_model():
    result, model = consult(request([("K", "KEEP")]))
    assert result.status == "no_changes" and result.draft is None and model.bound == 0


def test_repair_invalid_plan_and_report_exact_errors():
    bad = proposal(amount="900")
    result, model = consult(responses=[bad, proposal()])
    assert result.status == "awaiting_confirmation" and result.attempts == 2
    assert "purchase_budget" in model.calls[1][-1]["content"]


def test_ten_attempts_are_total_and_never_return_a_partial_draft():
    result, model = consult(responses=[proposal(amount="1")] * 10)
    assert result.status == "blocked" and result.attempts == len(model.calls) == 10
    assert result.draft is None
    assert "mandatory_trade" in [e.code for e in result.issues]


def test_auth_or_network_error_is_not_retried_or_leaked():
    result, model = consult(responses=[RuntimeError("SECRET credential")])
    assert result.status == "blocked" and len(model.calls) == 1
    assert "SECRET" not in result.model_dump_json()


def test_invalid_structured_response_can_be_repaired():
    result, _ = consult(responses=[{"wrong": "response"}, proposal()])
    assert result.status == "awaiting_confirmation" and result.attempts == 2


def test_target_cannot_reverse_sell_or_omit_sell():
    for target in [{"S": ".3"}, {}]:
        result, _ = consult(responses=[proposal(sells=target)] * 10)
        assert result.status == "blocked" and result.draft is None


def test_cap_redistributes_budget_between_buy_names():
    req = request()
    req["policy"]["max_weights"]["A"] = ".1"
    result, _ = consult(req)
    buys = {t.ticker: t.reference_notional for t in result.draft.trades if t.side == "BUY"}
    assert buys == {"A": D(40), "B": D(160)}


def test_existing_overweight_keep_stays_and_sell_may_reduce():
    req = request()
    req["policy"]["max_weights"].update(K=".01", S=".01")
    result, _ = consult(req)
    assert result.status == "awaiting_confirmation"
    assert {w.ticker for w in result.warnings} == {"K", "S"}


def test_sales_cannot_finance_buys():
    req = request()
    req["snapshot"]["free_cash"] = "150"
    result, _ = consult(req, [proposal()] * 10)
    assert result.status == "blocked"


def test_shares_cannot_exceed_available_or_minimum_trade():
    req = request()
    req["snapshot"]["positions"][2]["available_quantity"] = "1"
    result, _ = consult(req, [proposal()] * 10)
    assert result.status == "blocked"
    assert "available_quantity" in [i.code for i in result.issues]


def test_expiry_during_model_call_blocks_entire_plan():
    times = iter([NOW, NOW, NOW + timedelta(seconds=61), NOW + timedelta(seconds=61)])
    result, model = consult(clock=lambda: next(times))
    assert result.status == "blocked" and len(model.calls) == 1


def test_fingerprint_and_order_identity_are_bound_to_inputs():
    first, _ = consult()
    second, _ = consult()
    assert first.draft.order_id != second.draft.order_id
    assert first.draft.input_fingerprint == second.draft.input_fingerprint
    req = request()
    req["snapshot"]["snapshot_id"] = "new"
    third, _ = consult(req)
    assert third.draft.input_fingerprint != first.draft.input_fingerprint


def test_real_langchain_runnable_contract_without_network():
    from langchain_core.runnables import RunnableLambda

    class ConfiguredChatModel:
        def with_structured_output(self, schema):
            return RunnableLambda(lambda messages: schema.model_validate(proposal()))

    result = TraderConsultant(ConfiguredChatModel(), clock=lambda: NOW).consult(request())
    assert result.status == "awaiting_confirmation"


def test_invalid_input_does_not_mutate_callers_snapshot():
    req = ConsultationRequest.model_validate(request())
    original = req.model_dump_json()
    result = TraderConsultant(Model([proposal()]), clock=lambda: NOW).consult(req)
    assert result.status == "awaiting_confirmation" and req.model_dump_json() == original


def test_fractional_steps_round_down_and_leave_cash_unspent():
    req = request()
    req["snapshot"]["positions"][0]["quantity_step"] = ".25"
    req["snapshot"]["positions"][1]["quantity_step"] = ".25"
    result, _ = consult(req, [proposal(amount="203")])
    draft = result.draft
    assert [t.quantity for t in draft.trades if t.side == "BUY"] == [D(10), D(10)]
    assert draft.unused_purchase_amount == 3
    assert all(t.quantity % D(".25") == 0 for t in draft.trades if t.side == "BUY")


def test_model_can_choose_to_spend_less_than_budget():
    result, _ = consult(responses=[proposal(amount="40")])
    assert result.draft.selected_purchase_amount == 40
    assert sum(t.reference_notional for t in result.draft.trades if t.side == "BUY") == 40


def test_explicit_full_exit_and_zero_risk_cap_are_allowed():
    req = request()
    req["policy"]["max_weights"]["S"] = "0"
    result, _ = consult(req, [proposal(sells={"S": "0"})])
    assert result.status == "awaiting_confirmation"
    assert next(a for a in result.draft.allocations if a.ticker == "S").resulting_quantity == 0


def test_unknown_or_missing_llm_reasons_block():
    bad = proposal()
    del bad["reasons"]["K"]
    result, _ = consult(responses=[bad] * 10)
    assert result.status == "blocked"
    assert "reasons" in [e.code for e in result.issues]


def test_no_recommendation_position_is_not_sold():
    req = request()
    req["recommendations"] = [r for r in req["recommendations"] if r["ticker"] != "S"]
    answer = proposal(sells={})
    del answer["reasons"]["S"]
    result, _ = consult(req, [answer])
    assert result.status == "awaiting_confirmation"
    assert all(t.ticker != "S" for t in result.draft.trades)
    assert next(a for a in result.draft.allocations if a.ticker == "S").resulting_quantity == 20


def test_empty_recommendations_returns_no_changes():
    req = request()
    req["recommendations"] = []
    result, model = consult(req)
    assert result.status == "no_changes" and model.bound == 0


def test_sale_pays_its_own_fee_without_free_cash():
    req = request([("S", "SELL")])
    req["snapshot"]["free_cash"] = "0"
    req["policy"]["cash_reserve"] = "0"
    answer = proposal(amount="0", sells={"S": "0"})
    answer["reasons"] = {"S": "Reduce exposure."}
    result, _ = consult(req, [answer])
    assert result.status == "awaiting_confirmation"
    assert result.draft.cash_reserved_for_buys_and_fees == 0
    assert result.draft.estimated_cash_after == D("196.02")


@pytest.mark.parametrize("price", ["0.07", "0.3", "1.333", "19.99", "70"])
def test_nonintegral_prices_preserve_funding_caps_and_direction(price):
    req = request()
    req["snapshot"]["positions"][0]["price"] = price
    req["snapshot"]["positions"][1]["price"] = price
    result, _ = consult(req, [proposal(sells={"S": ".05"})])
    assert result.status == "awaiting_confirmation"
    draft = result.draft
    assert draft.cash_reserved_for_buys_and_fees <= D("900")
    assert sum(t.reference_notional for t in draft.trades if t.side == "BUY") <= 200
    for allocation in draft.allocations:
        if allocation.ticker in {"A", "B"}:
            assert allocation.achieved_weight <= allocation.maximum_weight
            assert allocation.resulting_quantity > allocation.current_quantity
        if allocation.ticker == "S":
            assert allocation.resulting_quantity < allocation.current_quantity
