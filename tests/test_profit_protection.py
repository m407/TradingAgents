from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.portfolio.protection import (
    LadderLevel,
    PositionSide,
    ProtectionPhase,
    ProtectionPolicy,
    ProtectionState,
    account_gap_fill,
    economic_break_even,
    favorable_profit,
    initial_stop_price,
    preserves_protection,
    reconcile_position,
    select_ladder_level,
    should_update_stop,
    trailing_stop,
    transition_state,
    update_watermark,
)


def _policy(**changes):
    values = {
        "initial_stop_distance": Decimal("0.05"),
        "break_even_activation": Decimal("0.02"),
        "estimated_entry_cost_rate": Decimal("0.001"),
        "estimated_exit_cost_rate": Decimal("0.002"),
        "expected_slippage": Decimal("0.03"),
        "break_even_buffer": Decimal("0.02"),
        "ladder": (
            LadderLevel(Decimal("0.03"), Decimal("0.02")),
            LadderLevel(Decimal("0.05"), Decimal("0.015")),
            LadderLevel(Decimal("0.10"), Decimal("0.01")),
        ),
        "minimum_improvement": Decimal("0.05"),
        "tick_size": Decimal("0.05"),
        "cooldown": timedelta(seconds=10),
        "quote_max_age": timedelta(seconds=5),
        "confirmation_timeout": timedelta(seconds=30),
    }
    values.update(changes)
    return ProtectionPolicy(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"initial_stop_distance": 0}, "initial_stop_distance"),
        ({"break_even_activation": 1}, "break_even_activation"),
        ({"estimated_entry_cost_rate": -1}, "estimated_entry_cost_rate"),
        ({"expected_slippage": -1}, "expected_slippage"),
        ({"expected_slippage": 1}, "expected_slippage"),
        ({"break_even_buffer": -1}, "break_even_buffer"),
        ({"break_even_buffer": 1}, "break_even_buffer"),
        ({"minimum_improvement": -1}, "minimum_improvement"),
        ({"tick_size": 0}, "tick_size"),
        ({"cooldown": timedelta(seconds=-1)}, "cooldown"),
        ({"quote_max_age": timedelta(0)}, "quote_max_age"),
        ({"confirmation_timeout": timedelta(0)}, "confirmation_timeout"),
        ({"ladder": ()}, "ladder"),
        ({"ladder": (LadderLevel(Decimal(".03"), Decimal(".02")),
                      LadderLevel(Decimal(".03"), Decimal(".01")))}, "strictly increasing"),
        ({"ladder": (LadderLevel(Decimal(".03"), Decimal(".01")),
                      LadderLevel(Decimal(".05"), Decimal(".02")))}, "non-increasing"),
    ],
)
def test_strict_policy_validation(changes, message):
    with pytest.raises(ValueError, match=message):
        _policy(**changes)


def test_weighted_fill_break_even_includes_costs_slippage_buffer_and_rounds_protectively():
    common = {
        "quantity": 4,
        "fills": [(Decimal("99"), 1), (Decimal("101"), 3)],
        "actual_entry_costs": Decimal("0.20"),
        "actual_exit_costs": Decimal("0.40"),
        "expected_slippage": Decimal("0.03"),
        "buffer": Decimal("0.02"),
        "tick_size": Decimal("0.05"),
    }
    assert economic_break_even(side="long", **common) == Decimal("100.70")
    assert economic_break_even(side="short", **common) == Decimal("100.30")


def test_unknown_costs_use_conservative_rates_and_actual_costs_replace_estimates():
    estimated = economic_break_even(
        side="long", quantity=10, average_entry=100,
        estimated_entry_cost_rate="0.01", estimated_exit_cost_rate="0.01",
        tick_size="0.01",
    )
    actual = economic_break_even(
        side="long", quantity=10, average_entry=100,
        actual_entry_costs=1, actual_exit_costs=1,
        estimated_entry_cost_rate="0.01", estimated_exit_cost_rate="0.01",
        tick_size="0.01",
    )
    assert estimated == Decimal("102")
    assert actual == Decimal("100.2")


def test_initial_stop_is_symmetric_and_rounded_protectively():
    policy = _policy(initial_stop_distance=Decimal("0.033"))
    assert initial_stop_price(policy, side="long", average_entry=100) == Decimal("96.70")
    assert initial_stop_price(policy, side="short", average_entry=100) == Decimal("103.30")


def test_bid_and_ask_watermarks_are_durable_and_stale_quotes_block():
    now = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
    long = update_watermark(
        side="long", current=105, bid=104, ask=106, quote_time=now, now=now,
        max_age=timedelta(seconds=5),
    )
    short = update_watermark(
        side="short", current=95, bid=94, ask=96, quote_time=now, now=now,
        max_age=timedelta(seconds=5),
    )
    stale = update_watermark(
        side="long", current=105, bid=110, ask=111,
        quote_time=now - timedelta(seconds=6), now=now, max_age=timedelta(seconds=5),
    )
    missing = update_watermark(
        side="short", current=95, bid=90, ask=None, quote_time=now, now=now,
        max_age=timedelta(seconds=5),
    )
    assert long.watermark == Decimal("105")
    assert short.watermark == Decimal("95")
    assert not stale.accepted and stale.watermark == Decimal("105")
    assert not missing.accepted and "ask" in missing.reason


def _state(quantity=10, phase=ProtectionPhase.INITIAL_PROTECTION):
    return ProtectionState("inc-1", "ABC", Decimal(quantity), Decimal("100"), phase=phase)


def test_state_machine_requires_break_even_confirmation_before_trailing():
    ready = transition_state(_state(), ProtectionPhase.BREAK_EVEN_READY)
    with pytest.raises(ValueError, match="confirmation"):
        transition_state(ready, ProtectionPhase.BREAK_EVEN_PROTECTED)
    protected = transition_state(
        ready, ProtectionPhase.BREAK_EVEN_PROTECTED, broker_stop_confirmed=True,
    )
    trailing = transition_state(protected, ProtectionPhase.TRAILING, broker_stop_confirmed=True)
    assert trailing.phase is ProtectionPhase.TRAILING
    with pytest.raises(ValueError, match="invalid transition"):
        transition_state(trailing, ProtectionPhase.INITIAL_PROTECTION)
    with pytest.raises(ValueError, match="closure"):
        transition_state(trailing, ProtectionPhase.CLOSED)


def test_transition_ids_are_idempotent():
    state = transition_state(_state(), ProtectionPhase.BREAK_EVEN_READY, transition_id="be-1")
    assert transition_state(state, ProtectionPhase.BREAK_EVEN_READY, transition_id="be-1") is state


@pytest.mark.parametrize(
    ("side", "watermark", "profit"),
    [(PositionSide.LONG, 110, Decimal("0.10")), (PositionSide.SHORT, 90, Decimal("0.10"))],
)
def test_long_short_profit_and_trailing_are_symmetric(side, watermark, profit):
    policy = _policy()
    assert favorable_profit(side, 100, watermark) == profit
    assert select_ladder_level(policy, profit) == 2
    expected = Decimal("108.90") if side is PositionSide.LONG else Decimal("90.90")
    assert trailing_stop(side, watermark, policy.ladder[2].trailing_gap, policy.tick_size) == expected


def test_highest_ladder_level_never_downgrades_when_profit_retraces():
    policy = _policy()
    assert select_ladder_level(policy, Decimal("0.04"), highest_reached=2) == 2
    assert select_ladder_level(policy, Decimal("0.06"), highest_reached=0) == 1


@pytest.mark.parametrize(
    ("side", "proposed", "confirmed", "break_even", "expected"),
    [("long", 101, 102, 100, False), ("long", 103, 102, 100, True),
     ("short", 99, 98, 100, False), ("short", 97, 98, 100, True)],
)
def test_stops_cannot_weaken_confirmed_or_break_even_protection(
    side, proposed, confirmed, break_even, expected
):
    assert preserves_protection(side, proposed, confirmed, break_even) is expected


def test_cooldown_minimum_improvement_and_duplicate_transition_suppress_updates():
    now = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
    common = {
        "side": "long",
        "confirmed_stop": Decimal("100"),
        "break_even": Decimal("99"),
        "tick_size": Decimal("0.05"),
        "minimum_improvement": Decimal("0.10"),
        "now": now,
        "cooldown": timedelta(seconds=10),
        "transition_id": "level-2",
        "applied_transition_ids": (),
    }
    cooldown = should_update_stop(
        proposed_stop="101", last_update_at=now - timedelta(seconds=1), **common
    )
    tiny = should_update_stop(
        proposed_stop="100.049", last_update_at=now - timedelta(seconds=20), **common
    )
    duplicate = should_update_stop(
        proposed_stop="101", last_update_at=None,
        **{**common, "applied_transition_ids": ("level-2",)},
    )
    material = should_update_stop(
        proposed_stop="100.11", last_update_at=now - timedelta(seconds=20), **common
    )
    assert not cooldown.submit and cooldown.reason == "cooldown active"
    assert not tiny.submit and tiny.reason == "minimum improvement not reached"
    assert not duplicate.submit and duplicate.reason == "transition already applied"
    assert material.submit and material.rounded_stop == Decimal("100.15")


def test_partial_fill_and_reduction_recalculate_quantity_and_entry_without_resetting_ladder():
    state = replace(
        _state(), highest_ladder_index=2, watermark=Decimal("110"), confirmed_stop=Decimal("105")
    )
    result = reconcile_position(
        state, signed_quantity=6, average_entry="101", incarnation_id="inc-1",
        broker_stop_quantity=6, recalculated_break_even="101.5",
    )
    assert result.state.signed_quantity == 6
    assert result.state.average_entry == 101
    assert result.state.highest_ladder_index == 2
    assert result.state.watermark == 110
    assert result.state.economic_break_even == Decimal("101.5")


def test_underprotected_partial_fill_enters_error():
    result = reconcile_position(
        _state(), signed_quantity=12, average_entry=100, incarnation_id="inc-1",
        broker_stop_quantity=10,
    )
    assert result.state.phase is ProtectionPhase.ERROR


def test_closure_and_reversal_do_not_carry_state_to_new_incarnation():
    protected = replace(_state(), watermark=Decimal("110"), highest_ladder_index=2)
    closed = reconcile_position(
        protected, signed_quantity=0, average_entry=None, incarnation_id="inc-1"
    )
    reversed_position = reconcile_position(
        protected, signed_quantity=-4, average_entry=95, incarnation_id="inc-2"
    )
    assert closed.state.phase is ProtectionPhase.CLOSED
    assert reversed_position.new_incarnation
    assert reversed_position.prior_state.phase is ProtectionPhase.CLOSED
    assert reversed_position.state.side is PositionSide.SHORT
    assert reversed_position.state.watermark is None
    assert reversed_position.state.highest_ladder_index is None


@pytest.mark.parametrize(
    ("side", "fills", "average", "slippage"),
    [
        ("long", [(98, 2), (97, 1)], Decimal("97.66666666666666666666666667"),
         Decimal("2.33333333333333333333333333")),
        ("short", [(102, 2), (103, 1)], Decimal("102.3333333333333333333333333"),
         Decimal("2.3333333333333333333333333")),
    ],
)
def test_gap_through_stop_records_actual_fill_economics(side, fills, average, slippage):
    result = account_gap_fill(side=side, trigger_price=100, fills=fills)
    assert result.average_fill_price == average
    assert result.quantity == 3
    assert result.actual_notional == sum(Decimal(str(price)) * quantity for price, quantity in fills)
    assert result.slippage_per_unit == slippage
    assert result.total_slippage == Decimal("7")
