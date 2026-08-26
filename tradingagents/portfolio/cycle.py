"""Idempotent orchestration for one daily portfolio cycle.

The module deliberately owns orchestration, not broker, persistence, or sizing
details. Those boundaries are injected so a cycle can be tested without an SDK
and so no LangGraph retry can replay a financial side effect.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from tradingagents.portfolio.store import cycle_identity, order_intent_identity

RATINGS_5_TIER = ("Buy", "Overweight", "Hold", "Underweight", "Sell")


class CycleError(RuntimeError):
    """Base error for a cycle that cannot safely continue."""


class LiveReadinessError(CycleError):
    """Raised before mutation when either live gate or a readiness check fails."""

    def __init__(self, failures: Sequence[str]):
        self.failures = tuple(failures)
        super().__init__("live readiness failed: " + ", ".join(self.failures))


class UnknownOrderOutcome(CycleError):
    """An order may have reached the broker and must not be submitted again."""


class UnprotectedPosition(CycleError):
    """A fill was not followed by confirmed broker-held protection."""


@dataclass(frozen=True)
class OrderPlan:
    """A deterministic broker-neutral order produced by the policy layer."""

    symbol: str
    signed_quantity: Decimal
    intent_id: str
    kind: str
    exposure_increasing: bool
    order_type: str = "market"
    limit_price: Decimal | None = None
    duration: str = "day"
    margin: bool = False
    initial_stop: Decimal | None = None
    expected_transition: str = ""
    risk_decisions: tuple[Any, ...] = ()


@dataclass(frozen=True)
class SymbolResearch:
    symbol: str
    rating: str
    raw_rating: Any
    report_references: tuple[str, ...]
    malformed: bool = False
    evidence: str | None = None


@dataclass(frozen=True)
class CyclePlan:
    orders: tuple[OrderPlan, ...]
    risk_decisions: tuple[Any, ...] = ()
    stop_actions: tuple[Any, ...] = ()
    expected_transitions: tuple[Any, ...] = ()

    @classmethod
    def from_policy_result(cls, value: Any, cycle_id: str) -> CyclePlan:
        """Adapt policy ``TargetPlan``/``PlannedIntent`` results to execution orders."""
        if isinstance(value, cls):
            return value
        target_plans = tuple(value) if isinstance(value, (list, tuple)) else (value,)
        orders: list[OrderPlan] = []
        for target_plan in target_plans:
            intents = _value(target_plan, "intents", (target_plan,))
            for index, intent in enumerate(intents):
                symbol = _normalize_symbol(_value(intent, "symbol"))
                quantity = Decimal(
                    str(_value(intent, "signed_quantity", _value(intent, "order_quantity")))
                )
                kind = _enum_text(_value(intent, "kind"))
                identity = order_intent_identity(cycle_id, symbol, kind, index)
                increasing = bool(
                    _value(intent, "exposure_increasing", _value(intent, "increases_exposure", False))
                )
                orders.append(
                    OrderPlan(
                        symbol=symbol,
                        signed_quantity=quantity,
                        intent_id=identity,
                        kind=kind,
                        exposure_increasing=increasing,
                        order_type=_enum_text(_value(intent, "order_type", "market")),
                        limit_price=_value(intent, "limit_price"),
                        duration=str(_value(intent, "duration", "day")),
                        margin=bool(_value(intent, "margin", False)),
                        expected_transition=kind,
                        risk_decisions=tuple(_value(intent, "risk_decisions", ())),
                    )
                )
        return cls(tuple(orders))


@dataclass(frozen=True)
class CycleResult:
    cycle_id: str
    status: str
    dry_run: bool
    research: tuple[SymbolResearch, ...] = ()
    plan: CyclePlan | None = None
    duplicate: bool = False
    halted_reason: str | None = None


class CycleStore(Protocol):
    """Minimum append-only persistence contract used by the orchestrator."""

    def acquire_lease(self, scope: str, owner: str) -> bool: ...

    def release_lease(self, scope: str, owner: str) -> None: ...

    def get_cycle(self, cycle_id: str) -> CycleResult | None: ...

    def start_cycle(self, cycle_id: str, immutable_inputs: Mapping[str, Any]) -> None: ...

    def append_cycle_event(self, cycle_id: str, kind: str, payload: Any) -> None: ...

    def finish_cycle(self, cycle_id: str, result: CycleResult) -> None: ...

    def raise_alert(self, severity: str, message: str, context: Mapping[str, Any]) -> None: ...


class CycleBroker(Protocol):
    def reconcile_account(self) -> Any: ...

    def get_quote(self, symbol: str) -> Any: ...

    def place_order(self, order: OrderPlan) -> Any: ...

    def reconcile_order(self, intent_id: str) -> Any: ...

    def set_static_stop(
        self, symbol: str, quantity: Decimal, stop_price: Decimal, transition_id: str
    ) -> Any: ...

    def reconcile_stop(self, symbol: str, transition_id: str) -> Any: ...


class CyclePolicy(Protocol):
    def plan(
        self,
        snapshot: Any,
        research: Mapping[str, SymbolResearch],
        quotes: Mapping[str, Any],
        cycle_id: str,
    ) -> CyclePlan: ...


def stable_cycle_identity(
    account_scope: str, configuration_fingerprint: str, trading_date: date | str
) -> str:
    """Return a stable, non-secret identity for idempotent restart behavior."""
    return cycle_identity(account_scope, configuration_fingerprint, trading_date)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (Decimal, date, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return repr(value)


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_text(value: Any) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def _positions(snapshot: Any) -> tuple[Any, ...]:
    positions = _value(snapshot, "positions", ())
    return tuple(position for position in positions if Decimal(str(_value(position, "quantity", 0))))


def _normalize_symbol(symbol: Any) -> str:
    normalized = str(symbol).strip().upper()
    if not normalized:
        raise ValueError("portfolio symbol cannot be empty")
    return normalized


def _terminal_order(order: Any) -> bool:
    status = _enum_text(_value(order, "status", "")).lower().replace("_", "-")
    return status in {"filled", "partially-filled", "cancelled", "canceled", "rejected", "expired"}


def _unknown_order(order: Any) -> bool:
    status = _enum_text(_value(order, "status", "")).lower().replace("_", "-")
    return status in {"unknown", "pending-unknown", "outcome-unknown"}


def _filled_quantity(order: Any) -> Decimal:
    value = _value(order, "filled_quantity", _value(order, "filled_qty", 0))
    return abs(Decimal(str(value or 0)))


def _confirmed_stop(stop: Any, quantity: Decimal, requested_price: Decimal) -> bool:
    status = _enum_text(_value(stop, "status", "")).lower()
    actual_quantity = abs(
        Decimal(str(_value(stop, "quantity", _value(stop, "signed_position_quantity", 0))))
    )
    actual_price = _value(stop, "stop_price", _value(stop, "price"))
    return (
        status in {"active", "working", "accepted", "confirmed"}
        and actual_quantity >= quantity
        and actual_price is not None
        and Decimal(str(actual_price)) == requested_price
    )


class PortfolioCycle:
    """Run one auditable daily analysis and optional serialized execution."""

    def __init__(
        self,
        *,
        account_scope: str,
        configuration_fingerprint: str,
        watchlist: Iterable[str],
        graph: Any,
        broker: CycleBroker,
        store: CycleStore,
        policy: CyclePolicy,
        reconciler: Any | None = None,
        live_enabled: bool = False,
        readiness_check: Callable[[Any, Any, Any], Mapping[str, bool | str]] | None = None,
        report_writer: Callable[[Any, str], Iterable[str] | str | None] | None = None,
        initial_stop: Callable[[Any, Decimal, Any], Decimal] | None = None,
        order_reconciliation_attempts: int = 3,
        stop_reconciliation_attempts: int = 3,
        sleep: Callable[[float], None] | None = None,
        owner_id: str | None = None,
    ) -> None:
        if order_reconciliation_attempts < 1 or stop_reconciliation_attempts < 1:
            raise ValueError("reconciliation attempts must be positive")
        self.account_scope = account_scope
        self.configuration_fingerprint = configuration_fingerprint
        self.watchlist = tuple(watchlist)
        self.graph = graph
        self.broker = broker
        self.store = store
        self.policy = policy
        self.reconciler = reconciler
        self.live_enabled = live_enabled
        self.readiness_check = readiness_check
        self.report_writer = report_writer
        self.initial_stop = initial_stop
        self.order_reconciliation_attempts = order_reconciliation_attempts
        self.stop_reconciliation_attempts = stop_reconciliation_attempts
        self.sleep = sleep or (lambda _: None)
        self.owner_id = owner_id or str(uuid.uuid4())

    @classmethod
    def from_config(cls, config: Any, **dependencies: Any) -> PortfolioCycle:
        """Build a cycle from the validated secret-free ``PortfolioConfig`` shape."""
        mode = _enum_text(_value(config, "execution_mode", "dry-run"))
        attempts = int(_value(_value(config, "timeouts", {}), "read_attempts", 3))
        return cls(
            account_scope=str(_value(_value(config, "account_scope", {}), "account_id")),
            configuration_fingerprint=config.fingerprint(),
            watchlist=_value(config, "watchlist", ()),
            live_enabled=mode == "live",
            order_reconciliation_attempts=attempts,
            stop_reconciliation_attempts=attempts,
            **dependencies,
        )

    def run(
        self,
        trading_date: date | str,
        *,
        confirm_live: bool = False,
        readiness_evidence: Mapping[str, bool | str] | None = None,
    ) -> CycleResult:
        cycle_id = stable_cycle_identity(
            self.account_scope, self.configuration_fingerprint, trading_date
        )
        existing = self.store.get_cycle(cycle_id)
        if existing is not None and existing.status in {"completed", "halted", "unknown"}:
            return replace(existing, duplicate=True)

        lease_scope = f"cycle:{self.account_scope}"
        if not self.store.acquire_lease(lease_scope, self.owner_id):
            raise CycleError(f"cycle lease is already owned for {self.account_scope}")

        try:
            snapshot = self._reconcile_account()
            symbols = self._universe(snapshot)
            quotes = {symbol: self.broker.get_quote(symbol) for symbol in symbols}
            immutable_inputs = {
                "account_scope": self.account_scope,
                "configuration_fingerprint": self.configuration_fingerprint,
                "trading_date": str(trading_date),
                "snapshot": _jsonable(snapshot),
                "quotes": _jsonable(quotes),
                "universe": symbols,
            }
            if existing is None:
                self.store.start_cycle(cycle_id, immutable_inputs)
            else:
                self.store.append_cycle_event(
                    cycle_id, "resume_reconciliation", _jsonable(snapshot)
                )

            research = self._research(cycle_id, symbols, trading_date)
            research_by_symbol = {item.symbol: item for item in research}
            planner = getattr(self.policy, "plan", self.policy)
            raw_plan = planner(snapshot, research_by_symbol, quotes, cycle_id)
            plan = CyclePlan.from_policy_result(raw_plan, cycle_id)
            self.store.append_cycle_event(cycle_id, "plan", _jsonable(plan))
            self._persist_plan(cycle_id, plan)

            live = self.live_enabled and confirm_live
            if self.live_enabled or confirm_live:
                failures = self._readiness_failures(
                    confirm_live, snapshot, readiness_evidence or {}
                )
                if failures:
                    raise LiveReadinessError(failures)

            if not live:
                result = CycleResult(cycle_id, "completed", True, research, plan)
                self.store.finish_cycle(cycle_id, result)
                return result

            status, reason = self._execute(cycle_id, plan)
            result = CycleResult(cycle_id, status, False, research, plan, halted_reason=reason)
            self.store.finish_cycle(cycle_id, result)
            return result
        finally:
            self.store.release_lease(lease_scope, self.owner_id)

    def _universe(self, snapshot: Any) -> tuple[str, ...]:
        symbols = {_normalize_symbol(symbol) for symbol in self.watchlist}
        symbols.update(_normalize_symbol(_value(position, "symbol")) for position in _positions(snapshot))
        return tuple(sorted(symbols))

    def _research(
        self, cycle_id: str, symbols: Sequence[str], trading_date: date | str
    ) -> tuple[SymbolResearch, ...]:
        results: list[SymbolResearch] = []
        for symbol in symbols:
            final_state, normalized_rating = self.graph.propagate(symbol, str(trading_date))
            raw_decision = _value(final_state, "final_trade_decision", normalized_rating)
            rating, raw_rating, malformed, evidence = self._rating(
                normalized_rating, raw_decision
            )
            references = self._write_reports(final_state, symbol)
            item = SymbolResearch(symbol, rating, raw_rating, references, malformed, evidence)
            self.store.append_cycle_event(cycle_id, "research", _jsonable(item))
            results.append(item)
        return tuple(results)

    @staticmethod
    def _rating(normalized: Any, decision: Any | None = None) -> tuple[str, Any, bool, str | None]:
        raw = normalized if decision is None else decision
        candidate = _value(raw, "rating")
        if isinstance(candidate, Enum):
            candidate = candidate.value
        if candidate is None and isinstance(raw, str):
            match = re.search(
                r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?rating(?:\*\*)?\s*[:\-]\s*"
                r"(?:\*\*)?(Buy|Overweight|Hold|Underweight|Sell)\b",
                raw,
            )
            candidate = match.group(1) if match else None
        if candidate is None and normalized in RATINGS_5_TIER and decision is None:
            candidate = normalized
        if isinstance(candidate, str) and candidate in RATINGS_5_TIER:
            return candidate, _jsonable(raw), False, None
        return (
            "Hold",
            _jsonable(raw),
            True,
            "final portfolio rating was absent or outside the five-tier vocabulary",
        )

    def _write_reports(self, state: Any, symbol: str) -> tuple[str, ...]:
        writer = self.report_writer
        if writer is None and hasattr(self.graph, "save_reports"):
            writer = self.graph.save_reports
        if writer is None:
            return ()
        references = writer(state, symbol)
        if references is None:
            return ()
        if isinstance(references, (str, bytes)) or hasattr(references, "__fspath__"):
            return (str(references),)
        return tuple(str(reference) for reference in references)

    def _readiness_failures(
        self,
        confirm_live: bool,
        snapshot: Any,
        supplied: Mapping[str, bool | str],
    ) -> list[str]:
        checks: dict[str, bool | str] = dict(supplied)
        if self.readiness_check is not None:
            checks.update(self.readiness_check(self.broker, self.store, snapshot))
        failures: list[str] = []
        if not self.live_enabled:
            failures.append("persistent live mode is disabled")
        if not confirm_live:
            failures.append("per-invocation live confirmation is absent")
        required = (
            "credentials",
            "optional_dependency",
            "configuration",
            "database_ownership",
            "broker_connectivity",
            "reconciliation",
            "market_data",
            "hard_risk",
            "stop_compatibility",
        )
        for name in required:
            value = checks.get(name, False)
            if value is not True:
                failures.append(f"{name}: {value or 'missing'}")
        return failures

    def _execute(self, cycle_id: str, plan: CyclePlan) -> tuple[str, str | None]:
        orders = list(plan.orders)
        index = 0
        while index < len(orders):
            order = orders[index]
            before = self._reconcile_account()
            if hasattr(self.policy, "recalculate"):
                order = self.policy.recalculate(order, before)  # type: ignore[attr-defined]
            self.store.append_cycle_event(
                cycle_id, "order_pre_reconciliation", _jsonable(before)
            )
            self._update_intent(order.intent_id, "submitting")
            try:
                response = self._place_order(order)
            except Exception as exc:
                if getattr(exc, "outcome_unknown", False):
                    return self._halt_unknown(cycle_id, order, exc)
                raise
            self.store.append_cycle_event(
                cycle_id,
                "order_submission",
                {"intent_id": order.intent_id, "response": _jsonable(response)},
            )
            confirmed = response
            for attempt in range(self.order_reconciliation_attempts):
                confirmed = self._reconcile_order(order.intent_id, confirmed)
                if _terminal_order(confirmed) or _unknown_order(confirmed):
                    break
                if attempt + 1 < self.order_reconciliation_attempts:
                    self.sleep(0)
            self.store.append_cycle_event(cycle_id, "order_reconciliation", _jsonable(confirmed))
            if _unknown_order(confirmed) or not _terminal_order(confirmed):
                return self._halt_unknown(cycle_id, order, confirmed)

            self._update_intent(
                order.intent_id,
                _enum_text(_value(confirmed, "status", "confirmed")),
                confirmed,
            )

            filled = _filled_quantity(confirmed)
            if order.exposure_increasing and filled:
                try:
                    self._protect_fill(cycle_id, order, filled, confirmed)
                except UnprotectedPosition as exc:
                    return "halted", str(exc)

            after = self._reconcile_account()
            self.store.append_cycle_event(
                cycle_id, "order_post_reconciliation", _jsonable(after)
            )
            if hasattr(self.policy, "recalculate_remaining"):
                remaining = self.policy.recalculate_remaining(  # type: ignore[attr-defined]
                    tuple(orders[index + 1 :]), after
                )
                orders[index + 1 :] = list(remaining)
            index += 1
        return "completed", None

    def _reconcile_account(self) -> Any:
        reconcile = getattr(self.broker, "reconcile_account", None)
        snapshot = reconcile() if callable(reconcile) else self.broker.get_portfolio()  # type: ignore[attr-defined]
        if self.reconciler is not None:
            evidence = self.reconciler.reconcile(
                self.store.list_intents(self.account_scope),  # type: ignore[attr-defined]
                _positions(snapshot),
                self.broker.get_active_orders(),  # type: ignore[attr-defined]
                self.broker.get_order_history(),  # type: ignore[attr-defined]
                self.broker.get_fills(),  # type: ignore[attr-defined]
            )
            if _value(evidence, "blocked", False):
                raise CycleError("authoritative account reconciliation is blocked")
        return snapshot

    def _persist_plan(self, cycle_id: str, plan: CyclePlan) -> None:
        recorder = getattr(self.store, "record_intent", None)
        if not callable(recorder):
            return
        for order in plan.orders:
            recorder(
                order.intent_id,
                cycle_id,
                self.account_scope,
                order.symbol,
                order.kind,
                _jsonable(order),
                status="planned",
            )

    def _update_intent(self, intent_id: str, status: str, evidence: Any = None) -> None:
        updater = getattr(self.store, "update_intent_state", None)
        if not callable(updater):
            return
        broker_order_id = _value(evidence, "broker_order_id", _value(evidence, "order_id"))
        updater(
            intent_id,
            status,
            broker_order_id=broker_order_id,
            reconciliation=None if evidence is None else _jsonable(evidence),
        )

    def _place_order(self, order: OrderPlan) -> Any:
        method = self.broker.place_order
        try:
            return method(
                symbol=order.symbol,
                signed_quantity=order.signed_quantity,
                order_type=_enum_text(order.order_type),
                duration=order.duration,
                margin=order.margin,
                client_order_id=order.intent_id,
                limit_price=order.limit_price,
                reduce_only=order.kind.startswith(("reduce", "close")),
                position_reader=lambda symbol: next(
                    (
                        Decimal(str(_value(position, "quantity")))
                        for position in _positions(self._reconcile_account())
                        if _normalize_symbol(_value(position, "symbol")) == symbol
                    ),
                    Decimal("0"),
                ),
            )
        except TypeError as exc:
            if "unexpected keyword" not in str(exc) and "positional argument" not in str(exc):
                raise
            return method(order)

    def _reconcile_order(self, intent_id: str, response: Any) -> Any:
        if _terminal_order(response) or _unknown_order(response):
            return response
        reconcile = getattr(self.broker, "reconcile_order", None)
        if callable(reconcile):
            return reconcile(intent_id)
        orders: list[Any] = []
        for name in ("get_active_orders", "get_order_history"):
            reader = getattr(self.broker, name, None)
            if callable(reader):
                orders.extend(reader())
        matches = [order for order in orders if _value(order, "client_order_id") == intent_id]
        return matches[0] if len(matches) == 1 else {"status": "unknown", "matches": len(matches)}

    def _halt_unknown(self, cycle_id: str, order: OrderPlan, evidence: Any) -> tuple[str, str]:
        reason = f"unknown outcome for intent {order.intent_id}; reconciliation required"
        self.store.append_cycle_event(
            cycle_id,
            "unknown_order_outcome",
            {"intent_id": order.intent_id, "evidence": _jsonable(evidence)},
        )
        self._update_intent(order.intent_id, "unknown", evidence)
        return "unknown", reason

    def _protect_fill(
        self, cycle_id: str, order: OrderPlan, filled: Decimal, confirmation: Any
    ) -> None:
        price = order.initial_stop
        if self.initial_stop is not None:
            price = self.initial_stop(order, filled, confirmation)
        if price is None:
            self._unprotected(cycle_id, order, filled, "initial stop price is unavailable")
        transition_id = f"{order.intent_id}:initial-stop:{filled}"
        signed_filled = filled if order.signed_quantity > 0 else -filled
        try:
            response = self.broker.set_static_stop(
                symbol=order.symbol,
                signed_position_quantity=signed_filled,
                stop_price=price,
                transition_id=transition_id,
            )
        except TypeError as exc:
            if "unexpected keyword" not in str(exc):
                raise
            response = self.broker.set_static_stop(order.symbol, filled, price, transition_id)
        self.store.append_cycle_event(
            cycle_id,
            "initial_stop_submission",
            {"transition_id": transition_id, "response": _jsonable(response)},
        )
        reconcile = getattr(self.broker, "reconcile_stop", None)
        confirmed = response
        for attempt in range(self.stop_reconciliation_attempts):
            confirmed = reconcile(order.symbol, transition_id) if callable(reconcile) else response
            if _confirmed_stop(confirmed, filled, price):
                break
            if attempt + 1 < self.stop_reconciliation_attempts:
                self.sleep(0)
        self.store.append_cycle_event(cycle_id, "initial_stop_reconciliation", _jsonable(confirmed))
        if not _confirmed_stop(confirmed, filled, price):
            self._unprotected(cycle_id, order, filled, "broker stop was not confirmed")

    def _unprotected(
        self, cycle_id: str, order: OrderPlan, quantity: Decimal, reason: str
    ) -> None:
        message = f"{order.symbol} has {quantity} newly filled units unprotected: {reason}"
        self.store.raise_alert(
            "ERROR",
            message,
            {"cycle_id": cycle_id, "intent_id": order.intent_id, "quantity": str(quantity)},
        )
        raise UnprotectedPosition(message)


DailyPortfolioCycle = PortfolioCycle
