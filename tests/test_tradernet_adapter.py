from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tradingagents.portfolio.config import TradernetCredentials
from tradingagents.portfolio.models import (
    Fill,
    MarketSession,
    MarketState,
    OrderStatus,
    PortfolioSnapshot,
    Position,
    Quote,
    StopState,
)
from tradingagents.portfolio.tradernet import (
    TradernetAdapter,
    TradernetAdapterError,
    TradernetDependencyError,
    redact_diagnostics,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tradernet"
NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClient:
    def __init__(self, **responses):
        self.responses = {name: deque(values) for name, values in responses.items()}
        self.calls = []

    def __getattr__(self, name):
        if name not in self.responses:
            raise AttributeError(name)

        def call(**kwargs):
            self.calls.append((name, kwargs))
            result = self.responses[name].popleft()
            if isinstance(result, Exception):
                raise result
            return result

        return call


def adapter(client, **kwargs):
    return TradernetAdapter(
        TradernetCredentials(public_key="public-test-key", private_key="private-test-key"),
        client=client,
        clock=lambda: NOW,
        sleep=lambda _delay: None,
        **kwargs,
    )


def test_credentials_factory_and_recursive_redaction_do_not_leak_secrets():
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return object()

    instance = TradernetAdapter(
        TradernetCredentials(public_key="public-test-key", private_key="private-test-key"),
        sdk_factory=factory,
        sensitive_identifiers=("account-sensitive",),
    )
    assert captured == {"api_key": "public-test-key", "secret_key": "private-test-key"}
    diagnostic = instance.sanitize(
        {
            "signature": "signed-value",
            "nested": [
                {"privateKey": "private-test-key"},
                "public-test-key account-sensitive",
            ],
        }
    )
    serialized = json.dumps(diagnostic)
    assert "private-test-key" not in serialized
    assert "public-test-key" not in serialized
    assert "account-sensitive" not in serialized
    assert "signed-value" not in serialized
    assert repr(instance.credentials).count("**********") == 2


def test_generic_redaction_handles_replayable_fields_and_values():
    value = {"Authorization": "Bearer replay", "items": ({"api-key": "key"}, "acct-1")}
    result = redact_diagnostics(value, ("acct-1",))
    assert result["Authorization"] == "<redacted>"
    assert result["items"] == ({"api-key": "<redacted>"}, "<redacted>")


def test_missing_optional_sdk_has_actionable_extra(monkeypatch):
    import tradingagents.portfolio.tradernet as module

    def missing(_name):
        raise ImportError

    monkeypatch.setattr(module.importlib, "import_module", missing)
    with pytest.raises(TradernetDependencyError, match=r"tradingagents\[tradernet\]"):
        TradernetAdapter(
            TradernetCredentials(public_key="public", private_key="private")
        )


def test_portfolio_parses_long_and_short_positions_exactly():
    result = adapter(FakeClient(get_portfolio=[fixture("portfolio.json")])).get_portfolio()
    assert isinstance(result, PortfolioSnapshot)
    assert isinstance(result.positions[0], Position)
    assert result.positions[0].quantity == Decimal("10")
    assert result.positions[1].quantity == Decimal("-4")
    assert result.positions[1].position_id == "position-short-sanitized"
    assert result.positions[1].average_price == Decimal("50.50")
    assert result.positions[1].market_value == Decimal("-196.00")
    assert result.positions[1].unrealized_pnl == Decimal("6.00")
    assert result.balances[0].equity == Decimal("4200.75")


def test_quote_market_and_fills_are_normalized():
    client = FakeClient(
        get_quote=[{
            "symbol": "AAA.EX", "bid": "9.9", "ask": "10.1", "last": "10",
            "as_of": "2026-08-12T12:00:00Z",
        }],
        get_market_status=[{
            "venue": "EX", "state": "open", "is_open": True,
            "trading_date": "2026-08-12", "as_of": "2026-08-12T12:00:00Z",
        }],
        get_fills=[fixture("fills.json")],
    )
    instance = adapter(client)
    quote = instance.get_quote("AAA.EX")
    assert isinstance(quote, Quote)
    assert quote.bid == Decimal("9.9") and quote.as_of == NOW
    market = instance.get_market_status()
    assert isinstance(market, MarketState)
    assert market.status is MarketSession.OPEN and market.is_open and market.as_of == NOW
    fill = instance.get_fills(order_id="o-partial")[0]
    assert isinstance(fill, Fill)
    assert fill.fill_id == "fill-1" and fill.order_id == "o-partial"
    assert fill.side.value == "sell"
    assert fill.quantity == Decimal("-3")
    assert fill.commission == Decimal("0.07")
    assert fill.executed_at == NOW.replace(hour=10, minute=1)


def test_instrument_metadata_requires_actual_sizing_and_liquidity_values():
    metadata = {
        "symbol": "AAA.EX",
        "lot_size": "5",
        "tick_size": "0.01",
        "average_daily_notional": "100000",
        "instrument_type": "stock",
        "tradable": True,
    }
    instance = adapter(FakeClient(get_instrument_metadata=[metadata]))

    assert instance.get_instrument_metadata("AAA.EX")["lot_size"] == Decimal("5")

    incomplete = adapter(
        FakeClient(get_instrument_metadata=[metadata | {"tick_size": None}])
    )
    with pytest.raises(TradernetAdapterError, match="tick_size"):
        incomplete.get_instrument_metadata("AAA.EX")


def test_all_order_statuses_and_partial_fill_are_parsed():
    result = adapter(
        FakeClient(get_order_history=[fixture("orders.json")])
    ).get_order_history()
    assert {order.status for order in result} == {
        OrderStatus.PENDING,
        OrderStatus.ACTIVE,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
    partial = next(order for order in result if order.status is OrderStatus.PARTIALLY_FILLED)
    assert partial.side.value == "sell"
    assert partial.quantity == Decimal("-10")
    assert partial.filled_quantity == Decimal("-3")


@pytest.mark.parametrize(
    "response, message",
    [
        ({"positions": [], "as_of": "2026-08-12T12:00:00Z"}, "balances"),
        ({"symbol": "AAA.EX", "bid": "11", "ask": "10", "as_of": "2026-08-12T12:00:00Z"}, "contradictory"),
    ],
)
def test_malformed_or_contradictory_reads_are_rejected(response, message):
    client = FakeClient(get_portfolio=[response], get_quote=[response])
    instance = adapter(client)
    operation = instance.get_portfolio if "balances" in message else lambda: instance.get_quote("AAA.EX")
    with pytest.raises(TradernetAdapterError, match=message):
        operation()


def test_contradictory_partial_fill_is_rejected():
    raw = fixture("orders.json")["orders"][2] | {"filled_quantity": "10"}
    with pytest.raises(TradernetAdapterError, match="partial status contradicts"):
        adapter(FakeClient(get_order_history=[{"orders": [raw]}])).get_order_history()


def test_candle_ranges_are_evaluated_for_each_invocation():
    times = iter((NOW, NOW + timedelta(days=2)))
    client = FakeClient(get_candles=[{"candles": []}, {"candles": []}])
    instance = TradernetAdapter(
        TradernetCredentials(public_key="public", private_key="private"),
        client=client,
        clock=lambda: next(times),
    )
    instance.get_candles("AAA.EX", interval="day", lookback=timedelta(days=5))
    instance.get_candles("AAA.EX", interval="day", lookback=timedelta(days=5))
    first, second = (call[1] for call in client.calls)
    assert first["end_date"] == "2026-08-12T12:00:00+00:00"
    assert second["end_date"] == "2026-08-14T12:00:00+00:00"
    assert first["start_date"] == "2026-08-07T12:00:00+00:00"


@pytest.mark.parametrize("key", ["error", "errorMsg", "errMsg"])
def test_application_error_shapes_are_never_success(key):
    instance = adapter(FakeClient(get_quote=[{key: "invalid private-test-key", "code": 422}]))
    with pytest.raises(TradernetAdapterError) as caught:
        instance.get_quote("AAA.EX")
    assert caught.value.category == "validation"
    assert "private-test-key" not in str(caught.value)


def test_sdk_http_response_is_unwrapped_and_checked():
    class Response:
        def __init__(self):
            self.checked = False

        def raise_for_status(self):
            self.checked = True

        def json(self):
            assert self.checked
            return {
                "symbol": "AAA.EX",
                "bid": "9",
                "ask": "10",
                "as_of": "2026-08-12T12:00:00Z",
            }

    response = Response()
    quote = adapter(FakeClient(get_quote=[response])).get_quote("AAA.EX")

    assert response.checked
    assert quote.ask == Decimal("10")


def test_installed_sdk_method_names_are_adapted_without_leaking_into_callers():
    calls = []

    class InstalledSdkShape:
        def get_quotes(self, symbols):
            calls.append(("quotes", symbols))
            return {"result": {"ok": True}}

        def security_info(self, symbol, sup=True):
            calls.append(("security", symbol, sup))
            return {"result": {"ok": True}}

        def get_placed(self, active=True):
            calls.append(("orders", active))
            return {"result": {"ok": True}}

        def place_order(
            self, symbol, quantity=1, price=0.0, duration="day", use_margin=True,
            custom_order_id=None,
        ):
            calls.append(
                ("place", symbol, quantity, price, duration, use_margin, custom_order_id)
            )
            return {"result": {"order_id": "1"}}

    instance = adapter(InstalledSdkShape())
    assert instance._read(("get_quotes",), ticker="AAA.EX") == {"ok": True}
    assert instance._read(("security_info",), ticker="AAA.EX") == {"ok": True}
    assert instance._read(("get_placed",), active_only=True) == {"ok": True}
    instance._mutate(
        ("place_order",),
        ticker="AAA.EX",
        side="sell",
        count=Decimal("2"),
        order_exp="day",
        margin=False,
        client_order_id="intent-1",
        market_order=True,
    )

    assert calls[:3] == [
        ("quotes", ["AAA.EX"]),
        ("security", "AAA.EX", True),
        ("orders", True),
    ]
    assert calls[3][1:6] == ("AAA.EX", Decimal("-2"), 0, "day", False)
    assert isinstance(calls[3][6], int)


def test_reads_retry_with_timeout_but_mutations_are_never_retried():
    quote = {
        "symbol": "AAA.EX", "bid": "9", "ask": "10",
        "as_of": "2026-08-12T12:00:00Z",
    }
    read_client = FakeClient(get_quote=[TimeoutError("temporary"), quote])
    assert adapter(read_client, read_attempts=2).get_quote("AAA.EX").ask == Decimal("10")
    assert len(read_client.calls) == 2
    assert all(call[1]["timeout"] == 10.0 for call in read_client.calls)

    mutation_client = FakeClient(send_order=[TimeoutError("lost response")])
    with pytest.raises(TradernetAdapterError) as caught:
        adapter(mutation_client).place_order(
            symbol="AAA.EX", signed_quantity=Decimal("1"), order_type="market",
            duration="day", margin=False, client_order_id="intent-1",
        )
    assert caught.value.unknown_outcome
    assert caught.value.category == "unknown-outcome"
    assert len(mutation_client.calls) == 1


def test_reduce_guard_uses_fresh_signed_position_and_prevents_crossing_zero():
    client = FakeClient(send_order=[{"order_id": "should-not-send"}])
    with pytest.raises(TradernetAdapterError, match="reduce-only"):
        adapter(client).place_order(
            symbol="AAA.EX", signed_quantity=Decimal("-6"), order_type="market",
            duration="day", margin=False, client_order_id="intent-2", reduce_only=True,
            position_reader=lambda _symbol: Decimal("5"),
        )
    assert client.calls == []


def test_market_order_is_explicit_and_reconciled():
    raw = fixture("orders.json")["orders"][0] | {
        "order_id": "broker-1", "client_order_id": "intent-1", "symbol": "AAA.EX",
        "quantity": "2",
    }
    client = FakeClient(
        send_order=[{"order_id": "broker-1"}],
        get_active_orders=[{"orders": [raw]}],
    )
    order = adapter(client).place_order(
        symbol="AAA.EX", signed_quantity=Decimal("2"), order_type="market",
        duration="day", margin=False, client_order_id="intent-1",
    )
    sent = client.calls[0][1]
    assert sent | {"timeout": 10.0} == sent
    assert sent["side"] == "buy" and sent["count"] == Decimal("2")
    assert sent["market_order"] is True and sent["margin"] is False
    assert order.order_id == "broker-1"


@pytest.mark.parametrize(
    "trailing, order_type",
    [(None, "stop"), (Decimal("1.5"), "trailing_stop")],
)
def test_stop_mutation_is_followed_by_strict_reconciliation(trailing, order_type):
    raw = {
        "order_id": "stop-1", "client_order_id": "transition-1", "symbol": "AAA.EX",
        "side": "sell", "quantity": "5", "filled_quantity": "0",
        "order_type": order_type, "status": "active", "stop_price": "95",
        "duration": "day", "created_at": "2026-08-12T10:00:00Z",
        "updated_at": "2026-08-12T10:01:00Z",
    }
    if trailing is not None:
        raw["trailing_percent"] = str(trailing)
    method = "set_trailing_stop" if trailing is not None else "set_static_stop"
    client = FakeClient(**{method: [{"ok": True}], "get_active_orders": [{"orders": [raw]}]})
    instance = adapter(client)
    if trailing is None:
        state = instance.set_static_stop(
            symbol="AAA.EX", signed_position_quantity=Decimal("5"),
            stop_price=Decimal("95"), transition_id="transition-1",
        )
    else:
        state = instance.set_trailing_stop(
            symbol="AAA.EX", signed_position_quantity=Decimal("5"),
            stop_price=Decimal("95"), trailing_percent=trailing,
            transition_id="transition-1",
        )
    assert isinstance(state, StopState)
    assert state.order_id == "stop-1"
    assert state.quantity == Decimal("-5")
    assert state.stop_price == Decimal("95")
    assert state.trailing_percent == trailing


def test_cycle_and_supervisor_reconciliation_adapters():
    active = fixture("orders.json")["orders"][1]
    stop = {
        "order_id": "stop-1", "client_order_id": "transition-1", "symbol": "AAA.EX",
        "side": "sell", "quantity": "5", "filled_quantity": "0", "order_type": "stop",
        "status": "active", "stop_price": "95", "duration": "day",
        "created_at": "2026-08-12T10:00:00Z", "updated_at": "2026-08-12T10:01:00Z",
    }
    client = FakeClient(
        get_portfolio=[fixture("portfolio.json")],
        get_active_orders=[{"orders": [active]}, {"orders": [stop]}, {"orders": [stop]}],
    )
    instance = adapter(client)
    assert isinstance(instance.reconcile_account(), PortfolioSnapshot)
    assert instance.reconcile_order("c-active").order_id == "o-active"
    assert instance.get_active_stops()[0].order_id == "stop-1"
    assert instance.reconcile_stop("AAA.EX", "transition-1").order_id == "stop-1"


def test_subscription_reconnects_boundedly_and_reconciles_before_events():
    events = []
    reconciliations = []

    class StreamClient:
        attempts = 0

        def subscribe(self, *, topics, callback):
            self.attempts += 1
            assert set(topics) == {"quote", "portfolio", "order", "market_status"}
            if self.attempts == 1:
                raise ConnectionError("disconnect")
            callback({"type": "quote", "data": {"symbol": "AAA.EX"}})

    callbacks = {topic: (lambda payload, topic=topic: events.append((topic, payload))) for topic in (
        "quote", "portfolio", "order", "market_status"
    )}
    instance = adapter(StreamClient())
    instance.subscribe(callbacks, reconcile=lambda: reconciliations.append("rest"), max_reconnects=1)
    assert reconciliations == ["rest", "rest"]
    assert events == [("quote", {"symbol": "AAA.EX"})]
