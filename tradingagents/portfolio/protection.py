"""Pure profit-protection policy and reconciliation state transitions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from enum import Enum
from typing import Any

ZERO = Decimal("0")


def _decimal(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return result


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"

    @classmethod
    def from_quantity(cls, quantity: Any) -> PositionSide:
        value = _decimal(quantity, "quantity")
        if value == ZERO:
            raise ValueError("a flat position has no side")
        return cls.LONG if value > ZERO else cls.SHORT


@dataclass(frozen=True, order=True)
class LadderLevel:
    threshold: Decimal
    trailing_gap: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "threshold", _decimal(self.threshold, "threshold"))
        object.__setattr__(self, "trailing_gap", _decimal(self.trailing_gap, "trailing_gap"))


@dataclass(frozen=True)
class ProtectionPolicy:
    initial_stop_distance: Decimal
    break_even_activation: Decimal
    estimated_entry_cost_rate: Decimal
    estimated_exit_cost_rate: Decimal
    expected_slippage: Decimal
    break_even_buffer: Decimal
    ladder: tuple[LadderLevel, ...]
    minimum_improvement: Decimal
    tick_size: Decimal
    cooldown: timedelta
    quote_max_age: timedelta
    confirmation_timeout: timedelta

    def __post_init__(self) -> None:
        decimal_fields = (
            "initial_stop_distance", "break_even_activation", "estimated_entry_cost_rate",
            "estimated_exit_cost_rate", "expected_slippage", "break_even_buffer",
            "minimum_improvement", "tick_size",
        )
        for name in decimal_fields:
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        object.__setattr__(self, "ladder", tuple(
            level if isinstance(level, LadderLevel) else LadderLevel(*level) for level in self.ladder
        ))
        errors = self.validation_errors()
        if errors:
            raise ValueError("invalid protection policy: " + "; ".join(errors))

    def validation_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        positive = ("initial_stop_distance", "break_even_activation", "tick_size")
        nonnegative = (
            "estimated_entry_cost_rate", "estimated_exit_cost_rate", "expected_slippage",
            "break_even_buffer", "minimum_improvement",
        )
        errors.extend(f"{name} must be greater than zero" for name in positive
                      if getattr(self, name) <= ZERO)
        errors.extend(f"{name} cannot be negative" for name in nonnegative
                      if getattr(self, name) < ZERO)
        for name in ("initial_stop_distance", "break_even_activation",
                     "estimated_entry_cost_rate", "estimated_exit_cost_rate",
                     "expected_slippage", "break_even_buffer"):
            if getattr(self, name) >= Decimal("1"):
                errors.append(f"{name} must be less than one")
        if not isinstance(self.cooldown, timedelta) or self.cooldown.total_seconds() < 0:
            errors.append("cooldown must be a non-negative duration")
        for name in ("quote_max_age", "confirmation_timeout"):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value.total_seconds() <= 0:
                errors.append(f"{name} must be a positive duration")
        if not self.ladder:
            errors.append("ladder must contain at least one level")
        for index, level in enumerate(self.ladder):
            if level.threshold <= ZERO:
                errors.append(f"ladder[{index}].threshold must be greater than zero")
            if not ZERO < level.trailing_gap < Decimal("1"):
                errors.append(f"ladder[{index}].trailing_gap must be between zero and one")
            if index and level.threshold <= self.ladder[index - 1].threshold:
                errors.append("ladder thresholds must be strictly increasing")
            if index and level.trailing_gap > self.ladder[index - 1].trailing_gap:
                errors.append("ladder gaps must be non-increasing")
        return tuple(errors)

    @classmethod
    def from_config(cls, config: Any, *, tick_size: Any) -> ProtectionPolicy:
        """Adapt validated portfolio configuration to instrument-specific policy."""
        return cls(
            initial_stop_distance=config.initial_stop.loss_fraction,
            break_even_activation=config.break_even.activation_profit_fraction,
            estimated_entry_cost_rate=config.break_even.estimated_entry_cost_fraction,
            estimated_exit_cost_rate=config.break_even.estimated_exit_cost_fraction,
            expected_slippage=config.break_even.expected_slippage_fraction,
            break_even_buffer=config.break_even.buffer_fraction,
            ladder=tuple(
                LadderLevel(level.profit_fraction, level.trailing_gap_fraction)
                for level in config.profit_ladder
            ),
            minimum_improvement=config.stop_updates.minimum_improvement,
            tick_size=tick_size,
            cooldown=timedelta(seconds=float(config.stop_updates.cooldown_seconds)),
            quote_max_age=timedelta(
                seconds=float(config.reconciliation.maximum_quote_age_seconds)
            ),
            confirmation_timeout=timedelta(
                seconds=float(config.timeouts.stop_confirmation_seconds)
            ),
        )


def validate_policy(policy: ProtectionPolicy | Any) -> ProtectionPolicy:
    """Strict live-policy hook for config models and startup readiness checks."""
    if isinstance(policy, ProtectionPolicy):
        return policy
    names = ProtectionPolicy.__dataclass_fields__
    values = {name: getattr(policy, name) for name in names}
    return ProtectionPolicy(**values)


def protective_round(price: Any, tick_size: Any, side: PositionSide | str) -> Decimal:
    value = _decimal(price, "price")
    tick = _decimal(tick_size, "tick_size")
    if value <= ZERO or tick <= ZERO:
        raise ValueError("price and tick_size must be greater than zero")
    position_side = PositionSide(side)
    rounding = ROUND_CEILING if position_side is PositionSide.LONG else ROUND_FLOOR
    return (value / tick).to_integral_value(rounding=rounding) * tick


def initial_stop_price(
    policy: ProtectionPolicy,
    *,
    side: PositionSide | str,
    average_entry: Any,
) -> Decimal:
    """Calculate the configured initial downside stop in the protective direction."""
    position_side = PositionSide(side)
    entry = _decimal(average_entry, "average_entry")
    multiplier = (
        Decimal("1") - policy.initial_stop_distance
        if position_side is PositionSide.LONG
        else Decimal("1") + policy.initial_stop_distance
    )
    return protective_round(entry * multiplier, policy.tick_size, position_side)


def _fill_values(fill: Any) -> tuple[Decimal, Decimal]:
    if isinstance(fill, (tuple, list)) and len(fill) == 2:
        price, quantity = fill
    else:
        price, quantity = fill.price, fill.quantity
    return _decimal(price, "fill price"), abs(_decimal(quantity, "fill quantity"))


def weighted_average_fill(fills: Iterable[Any]) -> Decimal:
    total_quantity = ZERO
    total_value = ZERO
    for fill in fills:
        decimal_price, decimal_quantity = _fill_values(fill)
        if decimal_price <= ZERO or decimal_quantity <= ZERO:
            raise ValueError("fill price and quantity must be greater than zero")
        total_quantity += decimal_quantity
        total_value += decimal_price * decimal_quantity
    if total_quantity == ZERO:
        raise ValueError("at least one fill is required")
    return total_value / total_quantity


def economic_break_even(
    *,
    side: PositionSide | str,
    quantity: Any,
    average_entry: Any | None = None,
    fills: Iterable[Any] | None = None,
    actual_entry_costs: Any | None = None,
    actual_exit_costs: Any | None = None,
    estimated_entry_cost_rate: Any = ZERO,
    estimated_exit_cost_rate: Any = ZERO,
    expected_slippage: Any = ZERO,
    buffer: Any = ZERO,
    tick_size: Any,
) -> Decimal:
    """Return per-unit economic break-even with conservative unknown costs."""
    position_side = PositionSide(side)
    absolute_quantity = abs(_decimal(quantity, "quantity"))
    if absolute_quantity == ZERO:
        raise ValueError("quantity must be non-zero")
    entry = weighted_average_fill(fills) if fills is not None else _decimal(average_entry, "average_entry")
    if entry <= ZERO:
        raise ValueError("average_entry must be greater than zero")
    entry_cost = (_decimal(actual_entry_costs, "actual_entry_costs")
                  if actual_entry_costs is not None
                  else entry * absolute_quantity * _decimal(estimated_entry_cost_rate,
                                                            "estimated_entry_cost_rate"))
    exit_cost = (_decimal(actual_exit_costs, "actual_exit_costs")
                 if actual_exit_costs is not None
                 else entry * absolute_quantity * _decimal(estimated_exit_cost_rate,
                                                           "estimated_exit_cost_rate"))
    assumptions = {
        "entry costs": entry_cost,
        "exit costs": exit_cost,
        "expected slippage": _decimal(expected_slippage, "expected_slippage"),
        "buffer": _decimal(buffer, "buffer"),
    }
    if any(value < ZERO for value in assumptions.values()):
        names = ", ".join(name for name, value in assumptions.items() if value < ZERO)
        raise ValueError(f"{names} cannot be negative")
    adjustment = ((entry_cost + exit_cost) / absolute_quantity
                  + assumptions["expected slippage"]
                  + assumptions["buffer"])
    raw = entry + adjustment if position_side is PositionSide.LONG else entry - adjustment
    return protective_round(raw, tick_size, position_side)


def policy_break_even(
    policy: ProtectionPolicy,
    *,
    side: PositionSide | str,
    quantity: Any,
    average_entry: Any | None = None,
    fills: Iterable[Any] | None = None,
    actual_entry_costs: Any | None = None,
    actual_exit_costs: Any | None = None,
) -> Decimal:
    """Calculate break-even using the fraction-based configured assumptions."""
    entry = weighted_average_fill(fills) if fills is not None else _decimal(
        average_entry, "average_entry"
    )
    return economic_break_even(
        side=side,
        quantity=quantity,
        average_entry=entry,
        actual_entry_costs=actual_entry_costs,
        actual_exit_costs=actual_exit_costs,
        estimated_entry_cost_rate=policy.estimated_entry_cost_rate,
        estimated_exit_cost_rate=policy.estimated_exit_cost_rate,
        expected_slippage=entry * policy.expected_slippage,
        buffer=entry * policy.break_even_buffer,
        tick_size=policy.tick_size,
    )


@dataclass(frozen=True)
class WatermarkUpdate:
    watermark: Decimal | None
    accepted: bool
    reason: str | None = None


def update_watermark(
    *,
    side: PositionSide | str,
    current: Any | None,
    bid: Any | None,
    ask: Any | None,
    quote_time: datetime | None,
    now: datetime,
    max_age: timedelta,
) -> WatermarkUpdate:
    """Advance from executable bid/ask only; stale or one-sided quotes block."""
    existing = None if current is None else _decimal(current, "watermark")
    if now.tzinfo is None or quote_time is None or quote_time.tzinfo is None:
        return WatermarkUpdate(existing, False, "quote timestamp is unavailable or naive")
    if quote_time > now or now - quote_time > max_age:
        return WatermarkUpdate(existing, False, "executable quote is stale")
    position_side = PositionSide(side)
    executable = bid if position_side is PositionSide.LONG else ask
    if executable is None:
        quote_name = "bid" if position_side is PositionSide.LONG else "ask"
        return WatermarkUpdate(existing, False, f"executable {quote_name} is unavailable")
    price = _decimal(executable, "executable quote")
    if price <= ZERO:
        return WatermarkUpdate(existing, False, "executable quote must be positive")
    if existing is None:
        result = price
    elif position_side is PositionSide.LONG:
        result = max(existing, price)
    else:
        result = min(existing, price)
    return WatermarkUpdate(result, True)


class ProtectionPhase(str, Enum):
    INITIAL_PROTECTION = "initial-protection"
    BREAK_EVEN_READY = "break-even-ready"
    BREAK_EVEN_PROTECTED = "break-even-protected"
    TRAILING = "trailing"
    EXIT_PENDING = "exit-pending"
    CLOSED = "closed"
    ERROR = "error"


_ALLOWED_TRANSITIONS = {
    ProtectionPhase.INITIAL_PROTECTION: {
        ProtectionPhase.BREAK_EVEN_READY, ProtectionPhase.EXIT_PENDING,
        ProtectionPhase.CLOSED, ProtectionPhase.ERROR,
    },
    ProtectionPhase.BREAK_EVEN_READY: {
        ProtectionPhase.BREAK_EVEN_PROTECTED, ProtectionPhase.EXIT_PENDING,
        ProtectionPhase.CLOSED, ProtectionPhase.ERROR,
    },
    ProtectionPhase.BREAK_EVEN_PROTECTED: {
        ProtectionPhase.TRAILING, ProtectionPhase.EXIT_PENDING,
        ProtectionPhase.CLOSED, ProtectionPhase.ERROR,
    },
    ProtectionPhase.TRAILING: {
        ProtectionPhase.TRAILING, ProtectionPhase.EXIT_PENDING,
        ProtectionPhase.CLOSED, ProtectionPhase.ERROR,
    },
    ProtectionPhase.EXIT_PENDING: {ProtectionPhase.CLOSED, ProtectionPhase.ERROR},
    ProtectionPhase.CLOSED: set(),
    ProtectionPhase.ERROR: set(),
}


@dataclass(frozen=True)
class ProtectionState:
    incarnation_id: str
    symbol: str
    signed_quantity: Decimal
    average_entry: Decimal
    phase: ProtectionPhase = ProtectionPhase.INITIAL_PROTECTION
    watermark: Decimal | None = None
    economic_break_even: Decimal | None = None
    confirmed_stop: Decimal | None = None
    highest_ladder_index: int | None = None
    trailing_gap: Decimal | None = None
    last_update_at: datetime | None = None
    transition_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def side(self) -> PositionSide:
        return PositionSide.from_quantity(self.signed_quantity)


def transition_state(
    state: ProtectionState,
    phase: ProtectionPhase | str,
    *,
    broker_stop_confirmed: bool = False,
    position_closed: bool = False,
    transition_id: str | None = None,
) -> ProtectionState:
    target = ProtectionPhase(phase)
    if transition_id and transition_id in state.transition_ids:
        return state
    if target not in _ALLOWED_TRANSITIONS[state.phase]:
        raise ValueError(f"invalid transition: {state.phase.value} -> {target.value}")
    if target in {ProtectionPhase.BREAK_EVEN_PROTECTED, ProtectionPhase.TRAILING} and not broker_stop_confirmed:
        raise ValueError("broker stop confirmation is required")
    if target is ProtectionPhase.TRAILING and state.phase is not ProtectionPhase.BREAK_EVEN_PROTECTED:
        raise ValueError("break-even protection must precede trailing")
    if target is ProtectionPhase.CLOSED and not position_closed:
        raise ValueError("broker position closure must be confirmed")
    ids = state.transition_ids | ({transition_id} if transition_id else set())
    return replace(state, phase=target, transition_ids=frozenset(ids))


def favorable_profit(side: PositionSide | str, entry: Any, watermark: Any) -> Decimal:
    position_side = PositionSide(side)
    entry_price = _decimal(entry, "entry")
    favorable = _decimal(watermark, "watermark")
    if entry_price <= ZERO:
        raise ValueError("entry must be greater than zero")
    direction = Decimal("1") if position_side is PositionSide.LONG else Decimal("-1")
    return direction * (favorable - entry_price) / entry_price


def select_ladder_level(
    policy: ProtectionPolicy,
    profit: Any,
    highest_reached: int | None = None,
) -> int | None:
    reached = highest_reached
    decimal_profit = _decimal(profit, "profit")
    for index, level in enumerate(policy.ladder):
        if decimal_profit >= level.threshold:
            reached = index if reached is None else max(reached, index)
    return reached


def trailing_stop(
    side: PositionSide | str,
    watermark: Any,
    gap: Any,
    tick_size: Any,
) -> Decimal:
    position_side = PositionSide(side)
    favorable = _decimal(watermark, "watermark")
    decimal_gap = _decimal(gap, "gap")
    if not ZERO < decimal_gap < Decimal("1"):
        raise ValueError("gap must be between zero and one")
    raw = (favorable * (Decimal("1") - decimal_gap)
           if position_side is PositionSide.LONG
           else favorable * (Decimal("1") + decimal_gap))
    return protective_round(raw, tick_size, position_side)


def preserves_protection(
    side: PositionSide | str,
    proposed_stop: Any,
    confirmed_stop: Any | None,
    break_even: Any | None = None,
) -> bool:
    position_side = PositionSide(side)
    proposed = _decimal(proposed_stop, "proposed_stop")
    floors = [value for value in (confirmed_stop, break_even) if value is not None]
    if not floors:
        return True
    comparisons = [_decimal(value, "protected level") for value in floors]
    return (all(proposed >= value for value in comparisons)
            if position_side is PositionSide.LONG
            else all(proposed <= value for value in comparisons))


@dataclass(frozen=True)
class UpdateDecision:
    submit: bool
    reason: str
    rounded_stop: Decimal | None = None


def should_update_stop(
    *,
    side: PositionSide | str,
    proposed_stop: Any,
    confirmed_stop: Any | None,
    break_even: Any | None,
    tick_size: Any,
    minimum_improvement: Any,
    now: datetime,
    last_update_at: datetime | None,
    cooldown: timedelta,
    transition_id: str,
    applied_transition_ids: Iterable[str],
) -> UpdateDecision:
    rounded = protective_round(proposed_stop, tick_size, side)
    if transition_id in set(applied_transition_ids):
        return UpdateDecision(False, "transition already applied", rounded)
    if last_update_at is not None and now - last_update_at < cooldown:
        return UpdateDecision(False, "cooldown active", rounded)
    if not preserves_protection(side, rounded, confirmed_stop, break_even):
        return UpdateDecision(False, "stop would weaken protection", rounded)
    if confirmed_stop is not None:
        confirmed = _decimal(confirmed_stop, "confirmed_stop")
        direction = Decimal("1") if PositionSide(side) is PositionSide.LONG else Decimal("-1")
        improvement = direction * (rounded - confirmed)
        minimum = protective_round(
            max(_decimal(minimum_improvement, "minimum_improvement"), _decimal(tick_size, "tick_size")),
            tick_size,
            PositionSide.LONG,
        )
        if improvement < minimum:
            return UpdateDecision(False, "minimum improvement not reached", rounded)
    return UpdateDecision(True, "material monotonic improvement", rounded)


@dataclass(frozen=True)
class ReconciliationResult:
    state: ProtectionState
    new_incarnation: bool = False
    prior_state: ProtectionState | None = None


def reconcile_position(
    state: ProtectionState,
    *,
    signed_quantity: Any,
    average_entry: Any | None,
    incarnation_id: str,
    broker_stop_quantity: Any | None = None,
    recalculated_break_even: Any | None = None,
) -> ReconciliationResult:
    """Follow fills/reductions; close and isolate reversals or new incarnations."""
    quantity = _decimal(signed_quantity, "signed_quantity")
    if quantity == ZERO:
        closed = transition_state(state, ProtectionPhase.CLOSED, position_closed=True)
        return ReconciliationResult(closed, prior_state=state)
    side_changed = (quantity > ZERO) != (state.signed_quantity > ZERO)
    if side_changed or incarnation_id != state.incarnation_id:
        closed = (state if state.phase is ProtectionPhase.CLOSED else
                  transition_state(state, ProtectionPhase.CLOSED, position_closed=True))
        fresh = ProtectionState(
            incarnation_id=incarnation_id,
            symbol=state.symbol,
            signed_quantity=quantity,
            average_entry=_decimal(average_entry, "average_entry"),
        )
        return ReconciliationResult(fresh, True, closed)
    if broker_stop_quantity is not None and abs(_decimal(broker_stop_quantity, "broker_stop_quantity")) < abs(quantity):
        return ReconciliationResult(replace(state, phase=ProtectionPhase.ERROR), prior_state=state)
    break_even = (
        state.economic_break_even
        if recalculated_break_even is None
        else _decimal(recalculated_break_even, "recalculated_break_even")
    )
    return ReconciliationResult(replace(
        state,
        signed_quantity=quantity,
        average_entry=_decimal(average_entry, "average_entry"),
        economic_break_even=break_even,
    ))


@dataclass(frozen=True)
class GapFillAccounting:
    trigger_price: Decimal
    average_fill_price: Decimal
    quantity: Decimal
    actual_notional: Decimal
    slippage_per_unit: Decimal
    total_slippage: Decimal


def account_gap_fill(
    *,
    side: PositionSide | str,
    trigger_price: Any,
    fills: Iterable[Any],
) -> GapFillAccounting:
    """Account from actual fills, never treating a stop trigger as execution price."""
    fill_list = tuple(fills)
    average = weighted_average_fill(fill_list)
    quantity = sum((abs(_decimal(item[1], "fill quantity")) for item in fill_list), ZERO)
    trigger = _decimal(trigger_price, "trigger_price")
    slippage = (trigger - average if PositionSide(side) is PositionSide.LONG
                else average - trigger)
    fill_values = tuple(_fill_values(fill) for fill in fill_list)
    actual_notional = sum((price * fill_quantity for price, fill_quantity in fill_values), ZERO)
    total_slippage = sum(
        (((trigger - price)
          if PositionSide(side) is PositionSide.LONG
          else (price - trigger))
         * fill_quantity
         for price, fill_quantity in fill_values),
        ZERO,
    )
    return GapFillAccounting(
        trigger, average, quantity, actual_notional, slippage, total_slippage,
    )


# Integration-friendly aliases.
calculate_economic_break_even = economic_break_even
select_profit_ladder = select_ladder_level
record_gap_fill = account_gap_fill
