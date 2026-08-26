"""Deterministic target sizing and pre-trade policy.

This module deliberately contains no broker calls.  It accepts broker-neutral
objects (dataclasses, Pydantic models, or mappings) and returns auditable plans.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tradingagents.portfolio.models import IntentKind

ZERO = Decimal("0")
RATINGS_5_TIER = ("Buy", "Overweight", "Hold", "Underweight", "Sell")


def _decimal(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return result


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def normalize_symbol(value: Any) -> str | None:
    """Return a conservative canonical symbol, or ``None`` if it is invalid."""
    if not isinstance(value, str):
        return None
    symbol = value.strip().upper()
    if not symbol or len(symbol) > 32:
        return None
    allowed = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/")
    return symbol if all(character in allowed for character in symbol) else None


def build_universe(positions: Iterable[Any], watchlist: Iterable[Any]) -> tuple[str, ...]:
    """Build a stable, de-duplicated union of open positions and watchlist symbols."""
    result: list[str] = []
    seen: set[str] = set()
    for item in (*tuple(positions), *tuple(watchlist)):
        if isinstance(item, str):
            symbol, quantity = item, Decimal("1")
        else:
            symbol = _value(item, "symbol", _value(item, "ticker"))
            try:
                quantity = _decimal(
                    _value(item, "quantity", _value(item, "signed_quantity", 1)),
                    "quantity",
                )
            except ValueError:
                continue
        normalized = normalize_symbol(symbol)
        if normalized and quantity != ZERO and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


@dataclass(frozen=True)
class CandleGuardResult:
    ready: bool
    reason: str | None = None
    trading_date: date | None = None


def completed_candle_guard(
    *,
    now: datetime,
    market_timezone: str,
    session_close: time,
    candle_date: date | None,
    candle_complete: bool = True,
    max_age: timedelta = timedelta(days=4),
) -> CandleGuardResult:
    """Require an aware clock, a closed local session, and a fresh completed candle."""
    if now.tzinfo is None or now.utcoffset() is None:
        return CandleGuardResult(False, "current time must be timezone-aware")
    try:
        local_now = now.astimezone(ZoneInfo(market_timezone))
    except ZoneInfoNotFoundError:
        return CandleGuardResult(False, f"unknown market timezone: {market_timezone}")
    intended_date = local_now.date()
    if local_now.time().replace(tzinfo=None) < session_close:
        return CandleGuardResult(False, "market session has not closed", intended_date)
    if candle_date is None:
        return CandleGuardResult(False, "daily candle is unavailable", intended_date)
    if not candle_complete:
        return CandleGuardResult(False, "daily candle is still forming", intended_date)
    if candle_date > intended_date:
        return CandleGuardResult(False, "daily candle is from the future", intended_date)
    candle_end = datetime.combine(candle_date, session_close, ZoneInfo(market_timezone))
    if local_now - candle_end > max_age:
        return CandleGuardResult(False, "daily candle is stale", intended_date)
    return CandleGuardResult(True, trading_date=intended_date)


@dataclass(frozen=True)
class WeightDecision:
    target_weight: Decimal
    rating: str | None
    valid: bool
    reason: str | None = None


def map_rating_to_weight(
    rating: Any,
    rating_weights: Mapping[str, Any],
    *,
    current_weight: Any = ZERO,
    held: bool | None = None,
) -> WeightDecision:
    """Map the exact five-tier vocabulary; malformed output cannot add exposure."""
    current = _decimal(current_weight, "current_weight")
    is_held = current != ZERO if held is None else held
    normalized = rating.value if hasattr(rating, "value") else rating
    canonical = next(
        (candidate for candidate in RATINGS_5_TIER if candidate.lower() == str(normalized).strip().lower()),
        None,
    )
    if canonical is None:
        return WeightDecision(current if is_held else ZERO, None, False, "malformed rating")
    if canonical == "Hold":
        return WeightDecision(current if is_held else ZERO, canonical, True)
    try:
        if isinstance(rating_weights, Mapping):
            configured = {str(key).lower(): value for key, value in rating_weights.items()}
            raw_target = configured[canonical.lower()]
        else:
            raw_target = getattr(rating_weights, canonical.lower())
        target = _decimal(raw_target, f"weight for {canonical}")
    except (KeyError, ValueError) as exc:
        return WeightDecision(current if is_held else ZERO, canonical, False, str(exc))
    return WeightDecision(target, canonical, True)


def round_to_increment(value: Any, increment: Any) -> Decimal:
    """Round magnitude toward zero to an exchange lot or quantity increment."""
    decimal_value = _decimal(value, "value")
    decimal_increment = _decimal(increment, "increment")
    if decimal_increment <= ZERO:
        raise ValueError("increment must be greater than zero")
    units = (abs(decimal_value) / decimal_increment).to_integral_value(rounding=ROUND_FLOOR)
    return decimal_increment * units * (Decimal("-1") if decimal_value < ZERO else Decimal("1"))


def signed_target_quantity(
    target_weight: Any,
    equity: Any,
    executable_price: Any,
    lot_size: Any,
) -> Decimal:
    """Convert signed portfolio weight to signed quantity, rounded toward zero."""
    weight = _decimal(target_weight, "target_weight")
    account_equity = _decimal(equity, "equity")
    price = _decimal(executable_price, "executable_price")
    if account_equity <= ZERO or price <= ZERO:
        raise ValueError("equity and executable_price must be greater than zero")
    return round_to_increment(weight * account_equity / price, lot_size)


@dataclass(frozen=True)
class PlannedIntent:
    symbol: str
    kind: IntentKind
    order_quantity: Decimal
    resulting_quantity: Decimal
    limit_price: Decimal | None = None
    reversal: bool = False

    @property
    def increases_exposure(self) -> bool:
        return abs(self.resulting_quantity) > abs(self.resulting_quantity - self.order_quantity)


@dataclass(frozen=True)
class TargetPlan:
    symbol: str
    current_quantity: Decimal
    target_quantity: Decimal
    intents: tuple[PlannedIntent, ...]

    @property
    def is_reversal(self) -> bool:
        return len(self.intents) == 2


def _rounded_limit(price: Any | None, tick_size: Any, order_quantity: Decimal) -> Decimal | None:
    if price is None:
        return None
    tick = _decimal(tick_size, "tick_size")
    value = _decimal(price, "limit_price")
    if tick <= ZERO or value <= ZERO:
        raise ValueError("tick_size and limit_price must be greater than zero")
    units = value / tick
    rounding = ROUND_FLOOR if order_quantity > ZERO else ROUND_CEILING
    return units.to_integral_value(rounding=rounding) * tick


def plan_target_intents(
    symbol: str,
    current_quantity: Any,
    target_quantity: Any,
    *,
    lot_size: Any = Decimal("1"),
    limit_price: Any | None = None,
    tick_size: Any = Decimal("0.01"),
) -> TargetPlan:
    """Create explicit side-aware legs; reversals always close before opening."""
    normalized = normalize_symbol(symbol)
    if normalized is None:
        raise ValueError("invalid symbol")
    current = round_to_increment(current_quantity, lot_size)
    target = round_to_increment(target_quantity, lot_size)
    if current == target:
        return TargetPlan(normalized, current, target, ())

    def intent(kind: IntentKind, order: Decimal, result: Decimal, reversal: bool = False):
        return PlannedIntent(
            normalized,
            kind,
            order,
            result,
            _rounded_limit(limit_price, tick_size, order),
            reversal,
        )

    if current > ZERO and target < ZERO:
        legs = (intent(IntentKind.CLOSE_LONG, -current, ZERO, True),
                intent(IntentKind.OPEN_SHORT, target, target, True))
    elif current < ZERO and target > ZERO:
        legs = (intent(IntentKind.CLOSE_SHORT, -current, ZERO, True),
                intent(IntentKind.OPEN_LONG, target, target, True))
    elif current == ZERO:
        kind = IntentKind.OPEN_LONG if target > ZERO else IntentKind.OPEN_SHORT
        legs = (intent(kind, target, target),)
    elif target == ZERO:
        kind = IntentKind.CLOSE_LONG if current > ZERO else IntentKind.CLOSE_SHORT
        legs = (intent(kind, -current, ZERO),)
    elif current > ZERO:
        kind = IntentKind.EXPAND_LONG if target > current else IntentKind.REDUCE_LONG
        legs = (intent(kind, target - current, target),)
    else:
        kind = IntentKind.EXPAND_SHORT if target < current else IntentKind.REDUCE_SHORT
        legs = (intent(kind, target - current, target),)
    return TargetPlan(normalized, current, target, legs)


@dataclass(frozen=True)
class RiskLimits:
    max_position_weight: Decimal | None = None
    max_gross_exposure: Decimal | None = None
    max_net_exposure: Decimal | None = None
    max_order_notional: Decimal | None = None
    max_concentration: Decimal | None = None
    min_average_daily_notional: Decimal | None = None
    max_quote_age: timedelta | None = None
    allow_short: bool | None = None
    permitted_instruments: frozenset[str] | None = None
    allow_margin: bool | None = None
    permitted_instrument_types: frozenset[str] | None = None
    max_position_notional: Decimal | None = None

    @classmethod
    def from_config(cls, config: Any) -> RiskLimits:
        """Combine hard limits and quote freshness from ``PortfolioConfig``."""
        hard = config.hard_risk_limits
        return cls(
            max_position_weight=hard.max_abs_position_weight,
            max_gross_exposure=hard.max_gross_exposure,
            max_net_exposure=hard.max_abs_net_exposure,
            max_order_notional=hard.max_order_notional,
            min_average_daily_notional=hard.min_average_daily_notional,
            max_quote_age=timedelta(
                seconds=float(config.reconciliation.maximum_quote_age_seconds)
            ),
            allow_short=hard.allow_short,
            allow_margin=hard.allow_margin,
            permitted_instrument_types=frozenset(hard.permitted_instrument_types),
            max_position_notional=hard.max_position_notional,
        )

    @classmethod
    def from_object(cls, value: Any) -> RiskLimits:
        if isinstance(value, cls):
            return value
        aliases = {
            "max_position_weight": "max_abs_position_weight",
            "max_net_exposure": "max_abs_net_exposure",
        }
        values = {}
        for name in cls.__dataclass_fields__:
            raw = _value(value, name, _value(value, aliases.get(name, "")))
            if raw is not None:
                if name in {"permitted_instruments", "permitted_instrument_types"}:
                    raw = frozenset(raw)
                values[name] = raw
        return cls(**values)

    def missing_live_fields(self) -> tuple[str, ...]:
        required = (
            "max_position_weight",
            "max_gross_exposure",
            "max_net_exposure",
            "max_order_notional",
            "max_position_notional",
            "min_average_daily_notional",
            "max_quote_age",
            "allow_short",
            "allow_margin",
            "permitted_instrument_types",
        )
        return tuple(name for name in required if getattr(self, name) is None)


@dataclass(frozen=True)
class RiskContext:
    equity: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    available_cash: Decimal
    available_margin: Decimal
    average_daily_notional: Decimal
    quote_age: timedelta
    market_open: bool
    instrument_permitted: bool = True
    instrument_type: str | None = None
    conflicting_order: bool = False


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    target_quantity: Decimal
    violations: tuple[str, ...] = field(default_factory=tuple)
    clipped: bool = False
    adjustments: tuple[str, ...] = field(default_factory=tuple)


def evaluate_risk(
    *,
    symbol: str,
    current_quantity: Any,
    target_quantity: Any,
    price: Any,
    lot_size: Any,
    limits: RiskLimits | Any,
    context: RiskContext,
    live: bool = False,
) -> RiskDecision:
    """Apply all hard gates, clipping only deterministic size limits."""
    risk = RiskLimits.from_object(limits)
    if live and risk.missing_live_fields():
        missing = ", ".join(risk.missing_live_fields())
        return RiskDecision(False, _decimal(current_quantity, "current_quantity"),
                            (f"missing live risk configuration: {missing}",))
    current = _decimal(current_quantity, "current_quantity")
    target = _decimal(target_quantity, "target_quantity")
    unit_price = _decimal(price, "price")
    equity = _decimal(context.equity, "equity")
    if equity <= ZERO or unit_price <= ZERO:
        return RiskDecision(False, current, ("invalid equity or price",))
    clipped = False
    adjustments: list[str] = []
    size_caps = [cap for cap in (risk.max_position_weight, risk.max_concentration) if cap is not None]
    if risk.max_position_notional is not None:
        size_caps.append(_decimal(risk.max_position_notional, "max_position_notional") / equity)
    if size_caps:
        max_quantity = min(_decimal(cap, "position limit") for cap in size_caps) * equity / unit_price
        bounded = max(-max_quantity, min(max_quantity, target))
        clipped_target = round_to_increment(bounded, lot_size)
        clipped = clipped_target != target
        if clipped:
            adjustments.append("target clipped by position or concentration limit")
        target = clipped_target
    order_notional = abs(target - current) * unit_price
    if risk.max_order_notional is not None and order_notional > risk.max_order_notional:
        max_change = round_to_increment(risk.max_order_notional / unit_price, lot_size)
        target = current + (max_change if target > current else -max_change)
        order_notional = abs(target - current) * unit_price
        clipped = True
        adjustments.append("target clipped by order notional limit")

    violations: list[str] = []
    target_notional = target * unit_price
    projected_gross = context.gross_exposure + abs(target_notional) - abs(current * unit_price)
    projected_net = context.net_exposure + target_notional - current * unit_price
    increasing = abs(target) > abs(current)
    checks = (
        (risk.max_gross_exposure is not None and projected_gross / equity > risk.max_gross_exposure,
         "gross exposure limit"),
        (risk.max_net_exposure is not None and abs(projected_net / equity) > risk.max_net_exposure,
         "net exposure limit"),
        (increasing and target > current
         and order_notional > context.available_cash
         + (context.available_margin if risk.allow_margin is True else ZERO),
         "insufficient cash"),
        (increasing and target < ZERO and order_notional > context.available_margin,
         "insufficient margin"),
        (risk.min_average_daily_notional is not None
         and context.average_daily_notional < risk.min_average_daily_notional,
         "liquidity limit"),
        (risk.permitted_instruments is not None and symbol not in risk.permitted_instruments,
         "instrument not configured"),
        (not context.instrument_permitted, "instrument not permitted"),
        (risk.permitted_instrument_types is not None
         and context.instrument_type not in risk.permitted_instrument_types,
         "instrument type not permitted"),
        (risk.max_quote_age is not None and context.quote_age > risk.max_quote_age,
         "stale quote"),
        (not context.market_open, "market is not open"),
        (context.conflicting_order, "conflicting active order"),
        (target < ZERO and risk.allow_short is not True, "short trading not permitted"),
    )
    violations.extend(message for failed, message in checks if failed)
    return RiskDecision(
        not violations,
        target if not violations else current,
        tuple(violations),
        clipped,
        tuple(adjustments),
    )


# Integration-friendly names used by orchestration code.
rating_to_weight = map_rating_to_weight
target_intents = plan_target_intents
pre_trade_checks = evaluate_risk
