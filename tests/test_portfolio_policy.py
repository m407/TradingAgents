from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from tradingagents.portfolio.policy import (
    IntentKind,
    RiskContext,
    RiskLimits,
    build_universe,
    completed_candle_guard,
    evaluate_risk,
    map_rating_to_weight,
    plan_target_intents,
    signed_target_quantity,
)


def test_universe_contains_each_open_position_and_valid_watchlist_symbol_once():
    positions = [
        {"symbol": " aapl ", "quantity": "2"},
        {"symbol": "ZERO", "quantity": "0"},
        {"symbol": "held", "quantity": "-3"},
    ]

    assert build_universe(positions, ["AAPL", " msft ", "bad symbol", "HELD"]) == (
        "AAPL",
        "HELD",
        "MSFT",
    )


@pytest.mark.parametrize(
    ("now", "candle_date", "complete", "ready", "reason"),
    [
        (datetime(2026, 8, 11, 19, tzinfo=timezone.utc), datetime(2026, 8, 11).date(), True,
         False, "has not closed"),
        (datetime(2026, 8, 11, 22, tzinfo=timezone.utc), datetime(2026, 8, 11).date(), False,
         False, "still forming"),
        (datetime(2026, 8, 11, 22, tzinfo=timezone.utc), None, True, False, "unavailable"),
        (datetime(2026, 8, 11, 22, tzinfo=timezone.utc), datetime(2026, 8, 11).date(), True,
         True, None),
    ],
)
def test_closed_candle_and_market_timezone_guard(now, candle_date, complete, ready, reason):
    result = completed_candle_guard(
        now=now,
        market_timezone="America/New_York",
        session_close=time(16),
        candle_date=candle_date,
        candle_complete=complete,
    )
    assert result.ready is ready
    if reason:
        assert reason in result.reason


def test_candle_guard_rejects_stale_naive_and_unknown_timezone():
    stale = completed_candle_guard(
        now=datetime(2026, 8, 11, 22, tzinfo=timezone.utc),
        market_timezone="UTC",
        session_close=time(16),
        candle_date=datetime(2026, 8, 1).date(),
    )
    naive = completed_candle_guard(
        now=datetime(2026, 8, 11, 22),
        market_timezone="UTC",
        session_close=time(16),
        candle_date=datetime(2026, 8, 11).date(),
    )
    unknown = completed_candle_guard(
        now=datetime(2026, 8, 11, 22, tzinfo=timezone.utc),
        market_timezone="Mars/Olympus",
        session_close=time(16),
        candle_date=datetime(2026, 8, 11).date(),
    )
    assert "stale" in stale.reason
    assert "timezone-aware" in naive.reason
    assert "unknown market timezone" in unknown.reason


WEIGHTS = {
    "Buy": Decimal("0.20"),
    "Overweight": Decimal("0.10"),
    "Underweight": Decimal("-0.10"),
    "Sell": Decimal("-0.20"),
}


@pytest.mark.parametrize(
    ("rating", "expected"),
    [("Buy", "0.20"), ("Overweight", "0.10"), ("Underweight", "-0.10"),
     ("Sell", "-0.20")],
)
def test_five_tier_mapping_is_deterministic(rating, expected):
    assert map_rating_to_weight(rating, WEIGHTS).target_weight == Decimal(expected)


def test_held_hold_preserves_and_unheld_hold_stays_flat():
    assert map_rating_to_weight("Hold", WEIGHTS, current_weight="-0.07").target_weight == Decimal("-0.07")
    assert map_rating_to_weight("Hold", WEIGHTS).target_weight == 0


def test_malformed_or_incomplete_research_cannot_increase_exposure():
    held = map_rating_to_weight("BUY NOW", WEIGHTS, current_weight="0.08")
    unheld = map_rating_to_weight(None, WEIGHTS)
    missing = map_rating_to_weight("Buy", {}, current_weight="-0.04")
    assert (held.valid, held.target_weight) == (False, Decimal("0.08"))
    assert (unheld.valid, unheld.target_weight) == (False, Decimal("0"))
    assert (missing.valid, missing.target_weight) == (False, Decimal("-0.04"))


def test_signed_target_quantity_rounds_toward_zero_to_lot():
    assert signed_target_quantity("0.10", "10000", "33", "5") == Decimal("30")
    assert signed_target_quantity("-0.10", "10000", "33", "5") == Decimal("-30")


@pytest.mark.parametrize(
    ("current", "target", "kind", "order"),
    [
        (0, 10, IntentKind.OPEN_LONG, 10),
        (10, 15, IntentKind.EXPAND_LONG, 5),
        (10, 5, IntentKind.REDUCE_LONG, -5),
        (10, 0, IntentKind.CLOSE_LONG, -10),
        (0, -10, IntentKind.OPEN_SHORT, -10),
        (-10, -15, IntentKind.EXPAND_SHORT, -5),
        (-10, -5, IntentKind.REDUCE_SHORT, 5),
        (-10, 0, IntentKind.CLOSE_SHORT, 10),
    ],
)
def test_explicit_long_and_short_intents(current, target, kind, order):
    plan = plan_target_intents("ABC", current, target)
    assert plan.intents[0].kind is kind
    assert plan.intents[0].order_quantity == order


def test_reversals_are_explicit_close_then_open_with_lot_and_tick_rounding():
    long_to_short = plan_target_intents(
        "ABC", "12", "-8", lot_size="5", limit_price="10.037", tick_size="0.05"
    )
    short_to_long = plan_target_intents("ABC", "-12", "8", lot_size="5")
    assert [leg.kind for leg in long_to_short.intents] == [
        IntentKind.CLOSE_LONG,
        IntentKind.OPEN_SHORT,
    ]
    assert [leg.order_quantity for leg in long_to_short.intents] == [Decimal("-10"), Decimal("-5")]
    assert all(leg.reversal for leg in long_to_short.intents)
    assert long_to_short.intents[0].limit_price == Decimal("10.05")
    assert [leg.kind for leg in short_to_long.intents] == [
        IntentKind.CLOSE_SHORT,
        IntentKind.OPEN_LONG,
    ]


def _context(**changes):
    values = {
        "equity": Decimal("10000"),
        "gross_exposure": Decimal("1000"),
        "net_exposure": Decimal("1000"),
        "available_cash": Decimal("10000"),
        "available_margin": Decimal("10000"),
        "average_daily_notional": Decimal("1000000"),
        "quote_age": timedelta(seconds=1),
        "market_open": True,
        "instrument_type": "stock",
    }
    values.update(changes)
    return RiskContext(**values)


def _limits(**changes):
    values = {
        "max_position_weight": Decimal("0.2"),
        "max_gross_exposure": Decimal("1"),
        "max_net_exposure": Decimal("0.5"),
        "max_order_notional": Decimal("5000"),
        "max_concentration": Decimal("0.15"),
        "min_average_daily_notional": Decimal("10000"),
        "max_quote_age": timedelta(seconds=5),
        "allow_short": True,
        "permitted_instruments": frozenset({"ABC"}),
        "allow_margin": False,
        "permitted_instrument_types": frozenset({"stock"}),
        "max_position_notional": Decimal("5000"),
    }
    values.update(changes)
    return RiskLimits(**values)


def test_hard_size_limits_clip_deterministically():
    result = evaluate_risk(
        symbol="ABC", current_quantity=0, target_quantity=1000, price=10, lot_size=10,
        limits=_limits(), context=_context(),
    )
    assert result.approved
    assert result.clipped
    assert result.target_quantity == Decimal("150")
    assert result.adjustments == ("target clipped by position or concentration limit",)


@pytest.mark.parametrize(
    ("limit_changes", "context_changes", "target", "message"),
    [
        ({"max_gross_exposure": Decimal("0.1")}, {}, 100, "gross exposure"),
        ({"max_net_exposure": Decimal("0.01")}, {}, 100, "net exposure"),
        ({}, {"available_cash": Decimal("1")}, 100, "cash"),
        ({}, {"available_margin": Decimal("1")}, -100, "margin"),
        ({}, {"average_daily_notional": Decimal("1")}, 100, "liquidity"),
        ({}, {"quote_age": timedelta(minutes=1)}, 100, "stale"),
        ({}, {"market_open": False}, 100, "market"),
        ({}, {"instrument_permitted": False}, 100, "instrument"),
        ({}, {"conflicting_order": True}, 100, "conflicting"),
        ({"allow_short": False}, {}, -100, "short"),
    ],
)
def test_every_nondiscretionary_risk_gate(limit_changes, context_changes, target, message):
    result = evaluate_risk(
        symbol="ABC", current_quantity=0, target_quantity=target, price=10, lot_size=1,
        limits=_limits(**limit_changes), context=_context(**context_changes),
    )
    assert not result.approved
    assert any(message in violation for violation in result.violations)
    assert result.target_quantity == 0


def test_live_execution_refuses_any_missing_risk_configuration():
    result = evaluate_risk(
        symbol="ABC", current_quantity=5, target_quantity=10, price=10, lot_size=1,
        limits=RiskLimits(max_position_weight=Decimal("0.1")), context=_context(), live=True,
    )
    assert not result.approved
    assert result.target_quantity == 5
    assert result.violations[0].startswith("missing live risk configuration")
