"""Always-on, fail-closed orchestration for broker-held profit protection."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol


class SupervisorError(RuntimeError):
    """The supervisor cannot safely manage the account."""


@dataclass(frozen=True)
class ProtectionTransition:
    symbol: str
    transition_id: str
    action: str
    quantity: Decimal
    stop_price: Decimal | None = None
    trailing_gap: Decimal | None = None


@dataclass(frozen=True)
class SupervisorStatus:
    running: bool
    paused: bool
    reason: str | None = None


class SupervisorStore(Protocol):
    def acquire_lease(self, scope: str, owner: str) -> bool: ...

    def heartbeat_lease(self, scope: str, owner: str) -> bool: ...

    def release_lease(self, scope: str, owner: str) -> None: ...

    def load_protection_states(self, account_scope: str) -> Mapping[str, Any]: ...

    def append_protection_event(self, symbol: str, kind: str, payload: Any) -> None: ...

    def save_protection_state(self, symbol: str, state: Any) -> None: ...

    def raise_alert(self, severity: str, message: str, context: Mapping[str, Any]) -> None: ...


class SupervisorBroker(Protocol):
    def reconcile_account(self) -> Any: ...

    def get_active_stops(self) -> Iterable[Any]: ...

    def get_quote(self, symbol: str) -> Any: ...

    def set_static_stop(
        self, symbol: str, quantity: Decimal, stop_price: Decimal, transition_id: str
    ) -> Any: ...

    def set_trailing_stop(
        self,
        symbol: str,
        quantity: Decimal,
        trailing_gap: Decimal,
        transition_id: str,
    ) -> Any: ...

    def reconcile_stop(self, symbol: str, transition_id: str | None = None) -> Any: ...


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _payload(value: Any) -> Any:
    if is_dataclass(value):
        return _payload(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _payload(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_payload(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return _payload(value.model_dump(mode="json"))
    if hasattr(value, "__dict__"):
        return _payload(vars(value))
    return repr(value)


def _enum_text(value: Any) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def _position_map(snapshot: Any) -> dict[str, Any]:
    result = {}
    for position in _value(snapshot, "positions", ()):
        quantity = Decimal(str(_value(position, "quantity", 0)))
        if quantity:
            result[str(_value(position, "symbol")).upper()] = position
    return result


def _stop_map(stops: Iterable[Any]) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = {}
    for stop in stops:
        result.setdefault(str(_value(stop, "symbol")).upper(), []).append(stop)
    return result


def _stop_price(stop: Any) -> Decimal | None:
    value = _value(stop, "stop_price", _value(stop, "price"))
    return None if value is None else Decimal(str(value))


def _stop_quantity(stop: Any) -> Decimal:
    value = _value(stop, "quantity", _value(stop, "signed_position_quantity", 0))
    return Decimal(str(value))


def _safe_or_better(side: int, candidate: Any, prior: Any) -> bool:
    candidate_price = _stop_price(candidate)
    prior_price = _stop_price(prior)
    if candidate_price is None or prior_price is None:
        return False
    return candidate_price >= prior_price if side > 0 else candidate_price <= prior_price


class ProtectionSupervisor:
    """Reconcile first, then process protection transitions one at a time."""

    def __init__(
        self,
        *,
        account_scope: str,
        broker: SupervisorBroker,
        store: SupervisorStore,
        protection: Any,
        reconcile_interval: float,
        quote_poll_interval: float,
        quote_is_stale: Callable[[Any], bool],
        owner_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.account_scope = account_scope
        self.broker = broker
        self.store = store
        self.protection = protection
        self.reconcile_interval = reconcile_interval
        self.quote_poll_interval = quote_poll_interval
        self.quote_is_stale = quote_is_stale
        self.owner_id = owner_id or str(uuid.uuid4())
        self.clock = clock
        self.running = False
        self.paused = True
        self.states: dict[str, Any] = {}
        self.positions: dict[str, Any] = {}
        self.stops: dict[str, Any] = {}
        self.quotes: dict[str, Any] = {}
        self.last_reconciliation = 0.0
        self.last_quote_poll = 0.0
        self._last_stop_response: Any = None

    @classmethod
    def from_config(cls, config: Any, **dependencies: Any) -> ProtectionSupervisor:
        """Build a supervisor from validated account and reconciliation settings."""
        reconciliation = _value(config, "reconciliation", {})
        return cls(
            account_scope=str(_value(_value(config, "account_scope", {}), "account_id")),
            reconcile_interval=float(_value(reconciliation, "interval_seconds")),
            quote_poll_interval=float(_value(reconciliation, "quote_poll_seconds")),
            **dependencies,
        )

    @property
    def lease_scope(self) -> str:
        return f"protection:{self.account_scope}"

    def start(self) -> SupervisorStatus:
        if not self.store.acquire_lease(self.lease_scope, self.owner_id):
            raise SupervisorError(f"protection lease is already owned for {self.account_scope}")
        self.running = True
        self.paused = True
        self.states = dict(self.store.load_protection_states(self.account_scope))
        try:
            self.reconcile(startup=True)
        except Exception:
            self.running = False
            self.store.release_lease(self.lease_scope, self.owner_id)
            raise
        self.paused = False
        return SupervisorStatus(True, False)

    def stop(self) -> None:
        if self.running:
            self.store.release_lease(self.lease_scope, self.owner_id)
        self.running = False
        self.paused = True

    def reconcile(self, *, startup: bool = False) -> None:
        reconcile = getattr(self.broker, "reconcile_account", None)
        snapshot = reconcile() if callable(reconcile) else self.broker.get_portfolio()  # type: ignore[attr-defined]
        positions = _position_map(snapshot)
        stop_reader = getattr(self.broker, "get_active_stops", None)
        if callable(stop_reader):
            active_stops = stop_reader()
        else:
            active_stops = tuple(
                order
                for order in self.broker.get_active_orders()  # type: ignore[attr-defined]
                if _enum_text(_value(order, "order_type", "")) in {"stop", "trailing-stop"}
            )
        stops_by_symbol = _stop_map(active_stops)
        confirmed: dict[str, Any] = {}
        for symbol, position in positions.items():
            candidates = stops_by_symbol.get(symbol, [])
            if len(candidates) != 1:
                self._fail_closed(
                    symbol,
                    "ambiguous or missing broker-held protection",
                    candidates,
                    self.stops.get(symbol),
                )
                continue
            stop = candidates[0]
            if not self._ownership_matches(position, stop):
                self._fail_closed(symbol, "ambiguous symbol/account ownership", stop, None)
                continue
            prior = self.stops.get(symbol)
            if prior is not None and not _safe_or_better(self._side(position), stop, prior):
                self._fail_closed(symbol, "broker stop weakened or reset", stop, prior)
                continue
            confirmed[symbol] = stop
            local = self.states.get(symbol)
            if local is not None and not self._local_matches(position, stop, local):
                self._fail_closed(symbol, "local and broker protection disagree", stop, prior)
        self.positions = positions
        self.stops.update(confirmed)
        self.last_reconciliation = self.clock()
        kind = "startup_reconciliation" if startup else "rest_reconciliation"
        for symbol in positions:
            self.store.append_protection_event(
                symbol,
                kind,
                {"position": _payload(positions[symbol]), "stop": _payload(self.stops.get(symbol))},
            )

    def handle_event(self, event: Any) -> None:
        event_type = str(_value(event, "type", "")).lower().replace("_", "-")
        if event_type in {"disconnect", "disconnected", "reconnecting"}:
            self.paused = True
            return
        if event_type in {"reconnect", "reconnected", "connected"}:
            self.paused = True
            if hasattr(self.broker, "restore_subscriptions"):
                self.broker.restore_subscriptions(("quotes", "portfolio", "orders", "market"))
            self.reconcile()
            self.paused = False
            return
        if event_type in {"portfolio", "order", "fill", "market"}:
            self.reconcile()
            return
        if event_type == "quote":
            symbol = str(_value(event, "symbol")).upper()
            quote = _value(event, "quote", event)
            self.quotes[symbol] = quote
            self.process_symbol(symbol)

    def tick(self) -> None:
        if not self.running:
            raise SupervisorError("supervisor has not started")
        if not self.store.heartbeat_lease(self.lease_scope, self.owner_id):
            self.running = False
            self.paused = True
            raise SupervisorError("protection lease was lost")
        now = self.clock()
        if now - self.last_reconciliation >= self.reconcile_interval:
            self.reconcile()
        if now - self.last_quote_poll >= self.quote_poll_interval:
            for symbol in self.positions:
                self.quotes[symbol] = self.broker.get_quote(symbol)
                self.process_symbol(symbol)
            self.last_quote_poll = now

    def process_symbol(self, symbol: str) -> None:
        if self.paused or symbol not in self.positions or symbol not in self.stops:
            return
        quote = self.quotes.get(symbol)
        if quote is None or self.quote_is_stale(quote):
            self.store.append_protection_event(symbol, "stale_quote_block", _payload(quote))
            return
        state = self.states.get(symbol)
        transition = self.protection.evaluate(
            self.positions[symbol], quote, state, self.stops[symbol]
        )
        if isinstance(transition, tuple) and len(transition) == 2:
            state, transition = transition
            self.states[symbol] = state
            self.store.save_protection_state(symbol, state)
        if transition is None:
            return
        self._apply_transition(transition)

    def _apply_transition(self, transition: ProtectionTransition) -> None:
        symbol = transition.symbol.upper()
        prior = self.stops[symbol]
        if transition.action == "break_even":
            if transition.stop_price is None:
                self._fail_closed(symbol, "break-even transition has no stop price", transition, prior)
                return
            self._set_static(transition)
        elif transition.action in {"activate_trailing", "tighten_trailing"}:
            if transition.trailing_gap is None:
                self._fail_closed(symbol, "trailing transition has no gap", transition, prior)
                return
            if transition.action == "activate_trailing" and not self._break_even_confirmed(symbol):
                self._fail_closed(symbol, "trailing requested before confirmed break-even", transition, prior)
                return
            self._set_trailing(transition)
        else:
            self._fail_closed(symbol, f"unsupported protection action {transition.action}", transition, prior)
            return

        reconcile = getattr(self.broker, "reconcile_stop", None)
        confirmed = (
            reconcile(symbol, transition.transition_id)
            if callable(reconcile)
            else self._last_stop_response
        )
        position = self.positions[symbol]
        if not self._confirmed_transition(position, transition, confirmed, prior):
            self._fail_closed(symbol, "broker rejected, reset, or weakened stop update", confirmed, prior)
            return
        self.stops[symbol] = confirmed
        new_state = self.protection.confirm(self.states.get(symbol), transition, confirmed)
        self.states[symbol] = new_state
        self.store.save_protection_state(symbol, new_state)
        self.store.append_protection_event(symbol, "transition_confirmed", _payload(transition))

    def _fail_closed(self, symbol: str, reason: str, evidence: Any, safest: Any) -> None:
        if safest is not None and hasattr(self.broker, "restore_stop"):
            self.broker.restore_stop(symbol, safest)
            restored = self.broker.reconcile_stop(symbol)
            if _stop_price(restored) is not None:
                self.stops[symbol] = restored
        state = self.states.get(symbol)
        if state is None and symbol in self.positions and hasattr(self.protection, "initialize"):
            state = self.protection.initialize(self.positions[symbol])
        if hasattr(self.protection, "error"):
            state = self.protection.error(state, reason)
            self.states[symbol] = state
            self.store.save_protection_state(symbol, state)
        self.store.append_protection_event(
            symbol, "error", {"reason": reason, "evidence": _payload(evidence)}
        )
        self.store.raise_alert("ERROR", reason, {"symbol": symbol, "evidence": _payload(evidence)})

    def _set_static(self, transition: ProtectionTransition) -> None:
        assert transition.stop_price is not None
        try:
            self._last_stop_response = self.broker.set_static_stop(
                symbol=transition.symbol,
                signed_position_quantity=transition.quantity,
                stop_price=transition.stop_price,
                transition_id=transition.transition_id,
            )
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            self._last_stop_response = self.broker.set_static_stop(
                transition.symbol,
                transition.quantity,
                transition.stop_price,
                transition.transition_id,
            )

    def _set_trailing(self, transition: ProtectionTransition) -> None:
        assert transition.trailing_gap is not None
        stop_price = transition.stop_price or _stop_price(self.stops[transition.symbol])
        if stop_price is None:
            raise SupervisorError("trailing transition requires a confirmed anchor")
        try:
            self._last_stop_response = self.broker.set_trailing_stop(
                symbol=transition.symbol,
                signed_position_quantity=transition.quantity,
                stop_price=stop_price,
                trailing_percent=transition.trailing_gap,
                transition_id=transition.transition_id,
            )
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            self._last_stop_response = self.broker.set_trailing_stop(
                transition.symbol,
                transition.quantity,
                transition.trailing_gap,
                transition.transition_id,
            )

    def _break_even_confirmed(self, symbol: str) -> bool:
        state = self.states.get(symbol)
        name = _enum_text(
            _value(state, "status", _value(state, "state", _value(state, "phase", "")))
        ).lower()
        return name in {"break_even_protected", "break-even-protected", "trailing"}

    def _confirmed_transition(
        self,
        position: Any,
        transition: ProtectionTransition,
        confirmed: Any,
        prior: Any,
    ) -> bool:
        status = _enum_text(_value(confirmed, "status", "")).lower()
        quantity = abs(_stop_quantity(confirmed))
        if status not in {"active", "working", "accepted", "confirmed"}:
            return False
        if quantity < abs(transition.quantity) or not self._ownership_matches(position, confirmed):
            return False
        if not _safe_or_better(self._side(position), confirmed, prior):
            return False
        if transition.action == "break_even":
            requested = transition.stop_price
            actual = _stop_price(confirmed)
            return actual is not None and requested is not None and (
                actual >= requested if self._side(position) > 0 else actual <= requested
            )
        gap = _value(
            confirmed,
            "trailing_gap",
            _value(confirmed, "trailing_percentage", _value(confirmed, "trailing_percent")),
        )
        return gap is not None and Decimal(str(gap)) <= transition.trailing_gap

    def _ownership_matches(self, position: Any, stop: Any) -> bool:
        if str(_value(position, "symbol")).upper() != str(_value(stop, "symbol")).upper():
            return False
        position_account = _value(position, "account_scope", self.account_scope)
        stop_account = _value(stop, "account_scope", self.account_scope)
        return position_account == stop_account == self.account_scope

    @staticmethod
    def _side(position: Any) -> int:
        return 1 if Decimal(str(_value(position, "quantity"))) > 0 else -1

    def _local_matches(self, position: Any, stop: Any, state: Any) -> bool:
        local_quantity = _value(state, "quantity", _value(state, "signed_quantity"))
        if local_quantity is not None and abs(Decimal(str(local_quantity))) != abs(
            Decimal(str(_value(position, "quantity")))
        ):
            return False
        local_stop = _value(state, "confirmed_stop", _value(state, "stop_price"))
        if local_stop is None:
            return True
        return Decimal(str(local_stop)) == _stop_price(stop)


ProfitProtectionSupervisor = ProtectionSupervisor
