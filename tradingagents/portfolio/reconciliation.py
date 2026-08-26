"""Authoritative reconciliation of local strategy intent with broker state.

The functions are structurally typed on purpose: broker models may be Pydantic
models, frozen dataclasses, or normalized mappings. Reconciliation never infers
success from submission alone and blocks automation on unexplained evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PositionLike(Protocol):
    symbol: str
    quantity: Any


@runtime_checkable
class OrderLike(Protocol):
    broker_order_id: str
    client_order_id: str | None
    symbol: str
    status: Any


@runtime_checkable
class FillLike(Protocol):
    broker_fill_id: str
    broker_order_id: str
    symbol: str


@runtime_checkable
class IntentLike(Protocol):
    intent_identity: str
    symbol: str
    status: str


@dataclass(frozen=True)
class Discrepancy:
    code: str
    message: str
    symbol: str | None = None
    intent_id: str | None = None
    broker_order_id: str | None = None
    blocking: bool = True


@dataclass(frozen=True)
class IntentResolution:
    intent_id: str
    state: str
    broker_order_id: str | None
    fill_ids: tuple[str, ...]
    evidence: str


@dataclass(frozen=True)
class ReconciliationResult:
    state: str
    positions: Mapping[str, Decimal]
    resolutions: tuple[IntentResolution, ...]
    discrepancies: tuple[Discrepancy, ...]

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"

    @property
    def safe_to_automate(self) -> bool:
        return not self.blocked

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "positions": {symbol: str(quantity) for symbol, quantity in self.positions.items()},
            "resolutions": [asdict(resolution) for resolution in self.resolutions],
            "discrepancies": [asdict(discrepancy) for discrepancy in self.discrepancies],
        }


_TERMINAL_SUCCESS = {"filled", "completed", "confirmed"}
_TERMINAL_FAILURE = {"cancelled", "canceled", "rejected", "expired", "failed"}
_UNKNOWN = {"unknown", "unknown-outcome", "outcome-unknown", "pending-unknown"}
_AWAITING_BROKER = {"submitted", "submitting", "pending", "sent", *_UNKNOWN}
_ACTIVE = {"active", "working", "partially-filled", "partially_filled", "pending"}


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _text(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value or "").strip()


def _status(value: Any) -> str:
    return _text(value).casefold().replace("_", "-").replace(" ", "-")


def _symbol(value: Any) -> str:
    return _text(value).upper()


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _intent_id(intent: Any) -> str:
    return _text(_value(intent, "intent_identity", "intent_id", "client_order_id"))


def _order_id(order: Any) -> str:
    return _text(_value(order, "broker_order_id", "order_id", "id"))


def _client_order_id(order: Any) -> str:
    return _text(
        _value(order, "client_order_id", "intent_identity", "intent_id", "caller_order_id")
    )


def _fill_id(fill: Any) -> str:
    return _text(_value(fill, "broker_fill_id", "fill_id", "id"))


def _fill_order_id(fill: Any) -> str:
    return _text(_value(fill, "broker_order_id", "order_id"))


def _expected_quantity(intent: Any) -> Decimal | None:
    return _decimal(
        _value(
            intent,
            "expected_position_quantity",
            "confirmed_target_quantity",
            "target_quantity",
            default=None,
        )
    )


def reconcile_authoritative_state(
    persisted_intents: Iterable[Any],
    positions: Iterable[Any],
    active_orders: Iterable[Any],
    historical_orders: Iterable[Any],
    fills: Iterable[Any],
) -> ReconciliationResult:
    """Compare local intent with complete normalized broker evidence.

    Unknown state-changing outcomes are resolved only by a matching caller ID,
    broker order ID, or fill. Missing and contradictory evidence is blocking.
    """
    intents = tuple(persisted_intents)
    active = tuple(active_orders)
    history = tuple(historical_orders)
    broker_fills = tuple(fills)
    discrepancies: list[Discrepancy] = []

    intents_by_id: dict[str, Any] = {}
    for intent in intents:
        identity = _intent_id(intent)
        if not identity:
            discrepancies.append(
                Discrepancy("intent_identity_missing", "persisted intent has no stable identity")
            )
        elif identity in intents_by_id:
            discrepancies.append(
                Discrepancy(
                    "duplicate_intent_identity",
                    f"multiple persisted intents use {identity}",
                    intent_id=identity,
                )
            )
        else:
            intents_by_id[identity] = intent

    position_map: dict[str, Decimal] = {}
    for position in positions:
        symbol = _symbol(_value(position, "symbol"))
        quantity = _decimal(_value(position, "quantity", "signed_quantity"))
        if not symbol or quantity is None:
            discrepancies.append(
                Discrepancy(
                    "invalid_position", "broker position has no valid symbol or signed quantity"
                )
            )
        elif symbol in position_map:
            discrepancies.append(
                Discrepancy(
                    "ambiguous_position",
                    f"broker returned multiple positions for {symbol}",
                    symbol=symbol,
                )
            )
        else:
            position_map[symbol] = quantity

    orders_by_id: dict[str, Any] = {}
    order_sources: dict[str, str] = {}
    for source, orders in (("active", active), ("history", history)):
        for order in orders:
            broker_id = _order_id(order)
            if not broker_id:
                discrepancies.append(
                    Discrepancy("order_identity_missing", f"{source} broker order has no identity")
                )
                continue
            prior = orders_by_id.get(broker_id)
            if prior is not None:
                prior_status = _status(_value(prior, "status"))
                current_status = _status(_value(order, "status"))
                # The same order may legitimately appear in active and history during a race,
                # but conflicting identity/symbol or two terminal statuses are not explainable.
                conflict = (
                    _symbol(_value(prior, "symbol")) != _symbol(_value(order, "symbol"))
                    or (_client_order_id(prior) and _client_order_id(order)
                        and _client_order_id(prior) != _client_order_id(order))
                    or (
                        order_sources[broker_id] == source
                        and prior_status != current_status
                    )
                    or (
                        prior_status in (_TERMINAL_SUCCESS | _TERMINAL_FAILURE)
                        and current_status in (_TERMINAL_SUCCESS | _TERMINAL_FAILURE)
                        and prior_status != current_status
                    )
                )
                if conflict:
                    discrepancies.append(
                        Discrepancy(
                            "contradictory_order",
                            f"broker order {broker_id} has contradictory records",
                            symbol=_symbol(_value(order, "symbol")) or None,
                            broker_order_id=broker_id,
                        )
                    )
                if source == "history":
                    orders_by_id[broker_id] = order
                    order_sources[broker_id] = source
                continue
            orders_by_id[broker_id] = order
            order_sources[broker_id] = source

    orders_by_intent: dict[str, list[Any]] = {}
    recognized_order_ids: set[str] = set()
    for broker_id, order in orders_by_id.items():
        local_id = _client_order_id(order)
        if not local_id:
            # A persisted broker_order_id also establishes local ownership.
            matches = [
                identity
                for identity, intent in intents_by_id.items()
                if _text(_value(intent, "broker_order_id")) == broker_id
            ]
            local_id = matches[0] if len(matches) == 1 else ""
        if not local_id or local_id not in intents_by_id:
            discrepancies.append(
                Discrepancy(
                    "unowned_broker_order",
                    f"broker order {broker_id} has no matching persisted intent",
                    symbol=_symbol(_value(order, "symbol")) or None,
                    broker_order_id=broker_id,
                )
            )
            continue
        recognized_order_ids.add(broker_id)
        orders_by_intent.setdefault(local_id, []).append(order)

    fills_by_order: dict[str, list[Any]] = {}
    seen_fill_ids: set[str] = set()
    persisted_order_owners: dict[str, list[str]] = {}
    for identity, intent in intents_by_id.items():
        broker_id = _text(_value(intent, "broker_order_id"))
        if broker_id:
            persisted_order_owners.setdefault(broker_id, []).append(identity)
    for fill in broker_fills:
        fill_id = _fill_id(fill)
        broker_id = _fill_order_id(fill)
        if not fill_id or not broker_id:
            discrepancies.append(
                Discrepancy("invalid_fill", "broker fill has no fill or order identity")
            )
            continue
        if fill_id in seen_fill_ids:
            discrepancies.append(
                Discrepancy(
                    "duplicate_fill", f"broker fill {fill_id} appears more than once", broker_order_id=broker_id
                )
            )
            continue
        seen_fill_ids.add(fill_id)
        fills_by_order.setdefault(broker_id, []).append(fill)
        if broker_id not in recognized_order_ids and len(persisted_order_owners.get(broker_id, ())) != 1:
            discrepancies.append(
                Discrepancy(
                    "unowned_fill",
                    f"fill {fill_id} references unexplained broker order {broker_id}",
                    symbol=_symbol(_value(fill, "symbol")) or None,
                    broker_order_id=broker_id,
                )
            )

    resolutions: list[IntentResolution] = []
    for identity, intent in intents_by_id.items():
        matching_orders = orders_by_intent.get(identity, [])
        symbol = _symbol(_value(intent, "symbol"))
        persisted_status = _status(_value(intent, "status", "state"))
        if len(matching_orders) > 1:
            discrepancies.append(
                Discrepancy(
                    "multiple_orders_for_intent",
                    f"intent {identity} maps to multiple broker orders",
                    symbol=symbol or None,
                    intent_id=identity,
                )
            )
            continue

        order = matching_orders[0] if matching_orders else None
        broker_id = (
            _order_id(order)
            if order is not None
            else _text(_value(intent, "broker_order_id")) or None
        )
        matching_fills = fills_by_order.get(broker_id or "", [])
        fill_ids = tuple(sorted(_fill_id(fill) for fill in matching_fills))
        if order is None:
            if fill_ids:
                resolutions.append(
                    IntentResolution(identity, "filled", broker_id, fill_ids, "broker fills")
                )
            elif persisted_status in _AWAITING_BROKER:
                discrepancies.append(
                    Discrepancy(
                        "unknown_outcome_unresolved",
                        f"intent {identity} has no matching active/history order or fill",
                        symbol=symbol or None,
                        intent_id=identity,
                    )
                )
                resolutions.append(
                    IntentResolution(identity, "unknown", None, (), "no broker evidence")
                )
            elif persisted_status in _TERMINAL_SUCCESS:
                discrepancies.append(
                    Discrepancy(
                        "confirmed_intent_without_evidence",
                        f"confirmed intent {identity} has no authoritative broker evidence",
                        symbol=symbol or None,
                        intent_id=identity,
                    )
                )
            else:
                resolutions.append(
                    IntentResolution(identity, persisted_status or "planned", None, (), "local only")
                )
            continue

        broker_status = _status(_value(order, "status"))
        if broker_status in _TERMINAL_SUCCESS or fill_ids:
            resolved_state = "filled" if broker_status == "filled" or fill_ids else "confirmed"
        elif broker_status in _TERMINAL_FAILURE or broker_status in _ACTIVE:
            resolved_state = broker_status
        else:
            resolved_state = "unknown"
            discrepancies.append(
                Discrepancy(
                    "unknown_broker_order_status",
                    f"broker order {broker_id} has unrecognized status {broker_status!r}",
                    symbol=symbol or None,
                    intent_id=identity,
                    broker_order_id=broker_id,
                )
            )
        resolutions.append(
            IntentResolution(identity, resolved_state, broker_id, fill_ids, "broker order/history/fills")
        )

        expected = _expected_quantity(intent)
        if expected is not None and position_map.get(symbol, Decimal(0)) != expected:
            discrepancies.append(
                Discrepancy(
                    "position_quantity_mismatch",
                    f"{symbol} quantity does not match confirmed target for intent {identity}",
                    symbol=symbol,
                    intent_id=identity,
                    broker_order_id=broker_id,
                )
            )

    state = "blocked" if any(item.blocking for item in discrepancies) else "reconciled"
    return ReconciliationResult(
        state,
        dict(sorted(position_map.items())),
        tuple(resolutions),
        tuple(discrepancies),
    )


class AuthoritativeReconciler:
    """Small object facade for dependency-injected orchestration code."""

    def reconcile(
        self,
        persisted_intents: Iterable[Any],
        positions: Iterable[Any],
        active_orders: Iterable[Any],
        historical_orders: Iterable[Any],
        fills: Iterable[Any],
    ) -> ReconciliationResult:
        return reconcile_authoritative_state(
            persisted_intents, positions, active_orders, historical_orders, fills
        )


reconcile_account = reconcile_authoritative_state
reconcile = reconcile_authoritative_state
