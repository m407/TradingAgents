from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from tradingagents.portfolio.supervisor import (
    ProtectionSupervisor,
    ProtectionTransition,
    SupervisorError,
)


@dataclass
class Position:
    symbol: str
    quantity: Decimal
    account_scope: str = "account-1"


@dataclass
class Stop:
    symbol: str
    quantity: Decimal
    stop_price: Decimal
    status: str = "active"
    account_scope: str = "account-1"
    trailing_gap: Decimal | None = None
    order_type: str = "stop"


@dataclass
class State:
    status: str
    quantity: Decimal
    confirmed_stop: Decimal
    error_reason: str | None = None


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Store:
    def __init__(self, states=None):
        self.states = states or {}
        self.owner = None
        self.events = []
        self.alerts = []

    def acquire_lease(self, scope, owner):
        if self.owner not in (None, owner):
            return False
        self.owner = owner
        return True

    def heartbeat_lease(self, scope, owner):
        return self.owner == owner

    def release_lease(self, scope, owner):
        if self.owner == owner:
            self.owner = None

    def load_protection_states(self, account_scope):
        return dict(self.states)

    def append_protection_event(self, symbol, kind, payload):
        self.events.append((symbol, kind, payload))

    def save_protection_state(self, symbol, state):
        self.states[symbol] = state

    def raise_alert(self, severity, message, context):
        self.alerts.append((severity, message, context))


class Broker:
    def __init__(self, position=None, stop=None, quote=None):
        self.position = position or Position("AAPL", Decimal("5"))
        self.stop = stop or Stop("AAPL", Decimal("5"), Decimal("90"))
        self.quote = quote or {"symbol": "AAPL", "bid": Decimal("120"), "stale": False}
        self.calls = []
        self.subscriptions = []
        self.confirm_override = None

    def reconcile_account(self):
        self.calls.append("reconcile_account")
        return {"positions": (self.position,)}

    def get_active_stops(self):
        self.calls.append("get_active_stops")
        return (self.stop,) if self.stop is not None else ()

    def get_quote(self, symbol):
        self.calls.append(("get_quote", symbol))
        return self.quote

    def set_static_stop(self, symbol, quantity, stop_price, transition_id):
        self.calls.append(("static", transition_id))
        self.stop = Stop(symbol, quantity, stop_price)

    def set_trailing_stop(self, symbol, quantity, trailing_gap, transition_id):
        self.calls.append(("trailing", transition_id))
        self.stop = Stop(symbol, quantity, self.stop.stop_price, trailing_gap=trailing_gap)

    def reconcile_stop(self, symbol, transition_id=None):
        self.calls.append(("reconcile_stop", transition_id))
        return self.confirm_override or self.stop

    def restore_subscriptions(self, names):
        self.subscriptions.append(tuple(names))

    def restore_stop(self, symbol, stop):
        self.calls.append(("restore", symbol))
        self.stop = stop


class Protection:
    def __init__(self):
        self.transitions = []

    def evaluate(self, position, quote, state, stop):
        return self.transitions.pop(0) if self.transitions else None

    def confirm(self, state, transition, confirmed):
        status = "break_even_protected" if transition.action == "break_even" else "trailing"
        return State(status, transition.quantity, confirmed.stop_price)

    def error(self, state, reason):
        if state is None:
            return State("error", Decimal("0"), Decimal("0"), reason)
        return replace(state, status="error", error_reason=reason)


def supervisor(broker, store, protection, clock=None):
    return ProtectionSupervisor(
        account_scope="account-1",
        broker=broker,
        store=store,
        protection=protection,
        reconcile_interval=10,
        quote_poll_interval=5,
        quote_is_stale=lambda quote: quote["stale"],
        owner_id="owner-1",
        clock=clock or Clock(),
    )


def test_startup_and_process_restart_reconcile_all_positions_and_stops():
    state = State("initial_protection", Decimal("5"), Decimal("90"))
    store, broker = Store({"AAPL": state}), Broker()
    first = supervisor(broker, store, Protection())
    first.start()
    first.stop()

    second = supervisor(broker, store, Protection())
    second.start()

    assert second.positions["AAPL"].quantity == Decimal("5")
    assert second.stops["AAPL"].stop_price == Decimal("90")
    assert [kind for _, kind, _ in store.events].count("startup_reconciliation") == 2
    assert broker.stop.status == "active"


def test_lease_exclusion_and_takeover_after_owner_releases():
    store, broker = Store(), Broker()
    first = supervisor(broker, store, Protection())
    first.start()
    second = ProtectionSupervisor(
        account_scope="account-1",
        broker=broker,
        store=store,
        protection=Protection(),
        reconcile_interval=10,
        quote_poll_interval=5,
        quote_is_stale=lambda quote: False,
        owner_id="owner-2",
    )

    with pytest.raises(SupervisorError):
        second.start()
    first.stop()
    assert second.start().running is True


def test_disconnect_pauses_transitions_and_reconnect_restores_then_reconciles():
    store, broker, policy = Store(), Broker(), Protection()
    policy.transitions.append(
        ProtectionTransition(
            "AAPL", "be-1", "break_even", Decimal("5"), stop_price=Decimal("100")
        )
    )
    runner = supervisor(broker, store, policy)
    runner.start()

    runner.handle_event({"type": "disconnect"})
    runner.handle_event({"type": "quote", "symbol": "AAPL", "quote": broker.quote})
    assert not any(isinstance(call, tuple) and call[0] == "static" for call in broker.calls)

    runner.handle_event({"type": "reconnected"})
    runner.handle_event({"type": "quote", "symbol": "AAPL", "quote": broker.quote})
    assert broker.subscriptions == [("quotes", "portfolio", "orders", "market")]
    assert ("static", "be-1") in broker.calls


def test_periodic_rest_and_poll_fallback_catches_missed_quote_event():
    clock = Clock()
    store, broker, policy = Store(), Broker(), Protection()
    policy.transitions.append(
        ProtectionTransition(
            "AAPL", "be-poll", "break_even", Decimal("5"), stop_price=Decimal("100")
        )
    )
    runner = supervisor(broker, store, policy, clock)
    runner.start()
    clock.now = 11

    runner.tick()

    assert ("get_quote", "AAPL") in broker.calls
    assert ("static", "be-poll") in broker.calls
    assert [kind for _, kind, _ in store.events].count("rest_reconciliation") == 1


def test_stale_quote_blocks_transition_and_retains_existing_broker_stop():
    broker = Broker(quote={"symbol": "AAPL", "bid": Decimal("120"), "stale": True})
    store, policy = Store(), Protection()
    policy.transitions.append(
        ProtectionTransition(
            "AAPL", "be-stale", "break_even", Decimal("5"), stop_price=Decimal("100")
        )
    )
    runner = supervisor(broker, store, policy)
    runner.start()
    runner.handle_event({"type": "quote", "symbol": "AAPL", "quote": broker.quote})

    assert broker.stop.stop_price == Decimal("90")
    assert ("AAPL", "stale_quote_block", {"symbol": "AAPL", "bid": "120", "stale": True}) in store.events
    assert not any(isinstance(call, tuple) and call[0] == "static" for call in broker.calls)


def test_break_even_must_confirm_before_native_trailing_then_tightening():
    state = State("initial_protection", Decimal("5"), Decimal("90"))
    store, broker, policy = Store({"AAPL": state}), Broker(), Protection()
    policy.transitions.extend(
        [
            ProtectionTransition(
                "AAPL", "be", "break_even", Decimal("5"), stop_price=Decimal("100")
            ),
            ProtectionTransition(
                "AAPL", "trail", "activate_trailing", Decimal("5"), trailing_gap=Decimal("5")
            ),
            ProtectionTransition(
                "AAPL", "tighten", "tighten_trailing", Decimal("5"), trailing_gap=Decimal("3")
            ),
        ]
    )
    runner = supervisor(broker, store, policy)
    runner.start()

    for _ in range(3):
        runner.handle_event({"type": "quote", "symbol": "AAPL", "quote": broker.quote})

    mutations = [call for call in broker.calls if isinstance(call, tuple) and call[0] in {"static", "trailing"}]
    assert mutations == [("static", "be"), ("trailing", "trail"), ("trailing", "tighten")]
    assert runner.states["AAPL"].status == "trailing"
    assert broker.stop.trailing_gap == Decimal("3")


@pytest.mark.parametrize(
    "bad_stop",
    [
        Stop("AAPL", Decimal("5"), Decimal("80"), trailing_gap=Decimal("10")),
        Stop("OTHER", Decimal("5"), Decimal("100")),
        Stop("AAPL", Decimal("5"), Decimal("100"), status="rejected"),
    ],
)
def test_weaken_reset_reject_or_ownership_mismatch_restores_safest_and_alerts(bad_stop):
    state = State("break_even_protected", Decimal("5"), Decimal("90"))
    store, broker, policy = Store({"AAPL": state}), Broker(), Protection()
    policy.transitions.append(
        ProtectionTransition(
            "AAPL", "trail-bad", "activate_trailing", Decimal("5"), trailing_gap=Decimal("5")
        )
    )
    runner = supervisor(broker, store, policy)
    runner.start()
    safest = broker.stop
    broker.confirm_override = bad_stop

    runner.handle_event({"type": "quote", "symbol": "AAPL", "quote": broker.quote})

    assert store.alerts[-1][0] == "ERROR"
    assert store.states["AAPL"].status == "error"
    assert ("restore", "AAPL") in broker.calls
    assert broker.stop == safest


def test_monitor_outage_does_not_cancel_or_remove_broker_stop():
    store, broker = Store(), Broker()
    runner = supervisor(broker, store, Protection())
    runner.start()
    calls_before = list(broker.calls)

    runner.stop()

    assert broker.stop.status == "active"
    assert broker.stop.stop_price == Decimal("90")
    assert broker.calls == calls_before


def test_adapts_keyword_only_broker_stop_methods_and_enum_style_state():
    class TradernetShape(Broker):
        def reconcile_account(self):
            raise AssertionError("concrete adapter uses get_portfolio")

        def get_portfolio(self):
            return {"positions": (self.position,)}

        def get_active_stops(self):
            raise AssertionError("concrete adapter exposes active orders")

        def get_active_orders(self):
            return (self.stop,)

        def set_static_stop(self, **kwargs):
            self.calls.append(("static-keywords", kwargs))
            self.stop = Stop(
                kwargs["symbol"],
                kwargs["signed_position_quantity"],
                kwargs["stop_price"],
            )
            return self.stop

        def __getattribute__(self, name):
            if name in {"reconcile_account", "get_active_stops", "reconcile_stop"}:
                raise AttributeError(name)
            return super().__getattribute__(name)

    state = State("initial_protection", Decimal("5"), Decimal("90"))
    store, broker, policy = Store({"AAPL": state}), TradernetShape(), Protection()
    policy.transitions.append(
        ProtectionTransition(
            "AAPL", "be-keywords", "break_even", Decimal("5"), stop_price=Decimal("100")
        )
    )
    runner = supervisor(broker, store, policy)
    runner.start()
    runner.handle_event({"type": "quote", "symbol": "AAPL", "quote": broker.quote})

    assert runner.states["AAPL"].status == "break_even_protected"
    assert broker.calls[-1][0] == "static-keywords"
