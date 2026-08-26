from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from tradingagents.portfolio.cycle import (
    CyclePlan,
    CycleResult,
    LiveReadinessError,
    OrderPlan,
    PortfolioCycle,
    stable_cycle_identity,
)
from tradingagents.portfolio.policy import plan_target_intents
from tradingagents.portfolio.store import PortfolioStore


@dataclass
class Position:
    symbol: str
    quantity: Decimal


@dataclass
class Snapshot:
    positions: tuple[Position, ...]


class Store:
    def __init__(self):
        self.cycles = {}
        self.inputs = {}
        self.events = []
        self.alerts = []
        self.owner = None

    def acquire_lease(self, scope, owner):
        if self.owner not in (None, owner):
            return False
        self.owner = owner
        return True

    def release_lease(self, scope, owner):
        if self.owner == owner:
            self.owner = None

    def get_cycle(self, cycle_id):
        return self.cycles.get(cycle_id)

    def start_cycle(self, cycle_id, immutable_inputs):
        assert cycle_id not in self.inputs
        self.inputs[cycle_id] = copy.deepcopy(immutable_inputs)

    def append_cycle_event(self, cycle_id, kind, payload):
        self.events.append((cycle_id, kind, copy.deepcopy(payload)))

    def finish_cycle(self, cycle_id, result):
        self.cycles[cycle_id] = result

    def raise_alert(self, severity, message, context):
        self.alerts.append((severity, message, copy.deepcopy(context)))


class Graph:
    def __init__(self, decisions=None):
        self.decisions = decisions or {}
        self.calls = []

    def propagate(self, symbol, trading_date):
        self.calls.append((symbol, trading_date))
        return {"symbol": symbol, "report": "evidence"}, self.decisions.get(
            symbol, {"rating": "Buy"}
        )


class RealGraphContract:
    def propagate(self, symbol, trading_date):
        return {
            "symbol": symbol,
            "final_trade_decision": "**Rating**: Overweight\n\nEvidence.",
        }, "Overweight"


class Broker:
    def __init__(self, snapshot, fills=None, stops_confirm=True, unknown=False):
        self.snapshot = snapshot
        self.fills = list(fills or [])
        self.stops_confirm = stops_confirm
        self.unknown = unknown
        self.mutations = []
        self.reconciliations = 0

    def reconcile_account(self):
        self.reconciliations += 1
        return copy.deepcopy(self.snapshot)

    def get_quote(self, symbol):
        return {"symbol": symbol, "bid": "100", "ask": "101", "fresh": True}

    def place_order(self, order):
        self.mutations.append(("order", order.intent_id))
        return {"accepted": True}

    def reconcile_order(self, intent_id):
        if self.unknown:
            return {"intent_id": intent_id, "status": "unknown"}
        fill = self.fills.pop(0) if self.fills else Decimal("0")
        return {"intent_id": intent_id, "status": "filled", "filled_quantity": fill}

    def set_static_stop(self, symbol, quantity, stop_price, transition_id):
        self.mutations.append(("stop", symbol, quantity, stop_price, transition_id))
        return {"accepted": True}

    def reconcile_stop(self, symbol, transition_id):
        status = "active" if self.stops_confirm else "rejected"
        mutation = next(item for item in reversed(self.mutations) if item[0] == "stop")
        return {
            "symbol": symbol,
            "status": status,
            "quantity": mutation[2],
            "stop_price": mutation[3],
        }


class Policy:
    def __init__(self, orders=()):
        self.orders = tuple(orders)
        self.recalculated = []

    def plan(self, snapshot, research, quotes, cycle_id):
        return CyclePlan(
            self.orders,
            risk_decisions=({"allowed": True},),
            stop_actions=({"action": "attach-after-fill"},),
            expected_transitions=("reconciled", "executed", "protected"),
        )

    def recalculate(self, order, snapshot):
        self.recalculated.append((order.intent_id, snapshot))
        return order

    def recalculate_remaining(self, remaining, snapshot):
        self.recalculated.append(("remaining", snapshot))
        return remaining


def order(intent_id, *, increasing=True, quantity="10", kind="open-long"):
    return OrderPlan(
        "AAPL",
        Decimal(quantity),
        intent_id,
        kind,
        increasing,
        initial_stop=Decimal("90"),
        expected_transition=kind,
    )


def cycle(broker, store, graph, policy, **kwargs):
    return PortfolioCycle(
        account_scope="account-1",
        configuration_fingerprint="config-v1",
        watchlist=("aapl", "MSFT", "AAPL"),
        graph=graph,
        broker=broker,
        store=store,
        policy=policy,
        report_writer=lambda state, symbol: [f"reports/{symbol}.md"],
        **kwargs,
    )


def readiness():
    return {
        "credentials": True,
        "optional_dependency": True,
        "configuration": True,
        "database_ownership": True,
        "broker_connectivity": True,
        "reconciliation": True,
        "market_data": True,
        "hard_risk": True,
        "stop_compatibility": True,
    }


def test_stable_identity_duplicate_universe_and_immutable_audit():
    snapshot = Snapshot((Position("AAPL", Decimal("4")), Position("TSLA", Decimal("-2"))))
    broker, store, graph = Broker(snapshot), Store(), Graph({"MSFT": {"rating": "nonsense"}})
    runner = cycle(broker, store, graph, Policy())

    first = runner.run(date(2026, 8, 11))
    saved_inputs = copy.deepcopy(store.inputs[first.cycle_id])
    broker.snapshot.positions = ()
    second = runner.run(date(2026, 8, 11))

    assert first.cycle_id == stable_cycle_identity("account-1", "config-v1", "2026-08-11")
    assert [symbol for symbol, _ in graph.calls] == ["AAPL", "MSFT", "TSLA"]
    assert second.duplicate is True
    assert len(graph.calls) == 3
    assert store.inputs[first.cycle_id] == saved_inputs
    malformed = next(item for item in first.research if item.symbol == "MSFT")
    assert (malformed.rating, malformed.malformed) == ("Hold", True)
    assert "five-tier" in malformed.evidence
    assert malformed.raw_rating == {"rating": "nonsense"}
    assert malformed.report_references == ("reports/MSFT.md",)


def test_real_graph_contract_preserves_raw_decision_and_explicit_rating():
    broker, store = Broker(Snapshot(())), Store()

    result = cycle(broker, store, RealGraphContract(), Policy()).run("2026-08-11")

    research = next(item for item in result.research if item.symbol == "AAPL")
    assert research.rating == "Overweight"
    assert research.malformed is False
    assert research.raw_rating == "**Rating**: Overweight\n\nEvidence."


def test_dry_run_persists_complete_plan_and_performs_no_mutations():
    broker, store = Broker(Snapshot(())), Store()
    planned = order("entry")
    result = cycle(broker, store, Graph(), Policy((planned,))).run("2026-08-11")

    assert result.dry_run is True
    assert result.plan.orders == (planned,)
    assert result.plan.stop_actions
    assert result.plan.risk_decisions
    assert result.plan.expected_transitions
    assert broker.mutations == []


@pytest.mark.parametrize(
    ("persistent", "invocation"),
    [(False, True), (True, False)],
)
def test_live_requires_both_gates_before_broker_mutation(persistent, invocation):
    broker, store = Broker(Snapshot(())), Store()
    runner = cycle(broker, store, Graph(), Policy((order("entry"),)), live_enabled=persistent)

    with pytest.raises(LiveReadinessError):
        runner.run("2026-08-11", confirm_live=invocation, readiness_evidence=readiness())

    assert broker.mutations == []


def test_reversal_is_serialized_reconciled_and_partial_fill_is_protected():
    broker, store = Broker(
        Snapshot((Position("AAPL", Decimal("5")),)),
        fills=[Decimal("5"), Decimal("3")],
    ), Store()
    close = order("close-long", increasing=False, quantity="-5", kind="close-long")
    enter = order("open-short", quantity="-8", kind="open-short")
    policy = Policy((close, enter))
    runner = cycle(broker, store, Graph(), policy, live_enabled=True)

    result = runner.run("2026-08-11", confirm_live=True, readiness_evidence=readiness())

    assert result.status == "completed"
    assert [item[:2] for item in broker.mutations] == [
        ("order", "close-long"),
        ("order", "open-short"),
        ("stop", "AAPL"),
    ]
    assert broker.mutations[-1][2] == Decimal("3")
    assert broker.reconciliations >= 5
    assert [item[0] for item in policy.recalculated].count("remaining") == 2


def test_failed_initial_protection_halts_later_exposure_and_alerts():
    broker = Broker(Snapshot(()), fills=[Decimal("2"), Decimal("2")], stops_confirm=False)
    store = Store()
    runner = cycle(
        broker,
        store,
        Graph(),
        Policy((order("first"), order("second"))),
        live_enabled=True,
    )

    result = runner.run("2026-08-11", confirm_live=True, readiness_evidence=readiness())

    assert result.status == "halted"
    assert [item for item in broker.mutations if item[0] == "order"] == [("order", "first")]
    assert store.alerts[0][0] == "ERROR"
    assert "unprotected" in store.alerts[0][1]


def test_unknown_outcome_is_recorded_and_not_retried_on_duplicate_invocation():
    broker, store = Broker(Snapshot(()), unknown=True), Store()
    runner = cycle(
        broker, store, Graph(), Policy((order("uncertain"),)), live_enabled=True
    )

    first = runner.run("2026-08-11", confirm_live=True, readiness_evidence=readiness())
    second = runner.run("2026-08-11", confirm_live=True, readiness_evidence=readiness())

    assert first.status == "unknown"
    assert second.duplicate is True
    assert broker.mutations == [("order", "uncertain")]
    assert any(kind == "unknown_order_outcome" for _, kind, _ in store.events)


def test_interrupted_cycle_reconciles_before_resuming_research():
    store = Store()
    cycle_id = stable_cycle_identity("account-1", "config-v1", "2026-08-11")
    store.inputs[cycle_id] = {"original": True}
    store.cycles[cycle_id] = CycleResult(cycle_id, "running", True)
    broker = Broker(Snapshot(()))
    runner = cycle(broker, store, Graph(), Policy())

    runner.run("2026-08-11")

    assert store.inputs[cycle_id] == {"original": True}
    assert store.events[0][1] == "resume_reconciliation"


def test_adapts_target_policy_and_keyword_only_tradernet_shape():
    class TargetPolicy:
        def plan(self, snapshot, research, quotes, cycle_id):
            return (plan_target_intents("AAPL", 0, 2),)

    class TradernetShape:
        def __init__(self):
            self.calls = []

        def get_portfolio(self):
            return Snapshot(())

        def get_quote(self, symbol):
            return {"symbol": symbol, "bid": "100", "ask": "101"}

        def place_order(self, **kwargs):
            self.calls.append(("order", kwargs))
            return {
                "status": "filled",
                "filled_quantity": Decimal("2"),
                "client_order_id": kwargs["client_order_id"],
            }

        def set_static_stop(self, **kwargs):
            self.calls.append(("stop", kwargs))
            return {
                "symbol": kwargs["symbol"],
                "status": "active",
                "signed_position_quantity": kwargs["signed_position_quantity"],
                "stop_price": kwargs["stop_price"],
            }

    broker, store = TradernetShape(), Store()
    runner = cycle(
        broker,
        store,
        Graph(),
        TargetPolicy(),
        live_enabled=True,
        initial_stop=lambda order, quantity, fill: Decimal("90"),
    )

    result = runner.run("2026-08-11", confirm_live=True, readiness_evidence=readiness())

    assert result.status == "completed"
    assert broker.calls[0][1]["signed_quantity"] == Decimal("2")
    assert broker.calls[1][1]["signed_position_quantity"] == Decimal("2")


def test_sqlite_store_restart_returns_completed_cycle_without_reanalysis(tmp_path):
    store = PortfolioStore(tmp_path / "operations.sqlite", account_scope="account-1")
    graph, broker = Graph(), Broker(Snapshot(()))
    first = cycle(broker, store, graph, Policy()).run("2026-08-11")

    restarted = cycle(broker, store, graph, Policy()).run("2026-08-11")

    assert first.status == "completed"
    assert restarted.duplicate is True
    assert len(graph.calls) == 2
    assert store.get_cycle_row(first.cycle_id)["input_json"] is not None
