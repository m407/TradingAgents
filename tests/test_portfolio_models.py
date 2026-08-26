from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradingagents.portfolio.models import (
    Balance,
    BrokerFailure,
    BrokerOrder,
    ExecutableQuote,
    FailureKind,
    Fill,
    IntentKind,
    MarginPolicy,
    MarketSession,
    MarketState,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    SignedPosition,
    StopState,
)

NOW = datetime(2026, 8, 11, 12, tzinfo=timezone.utc)


def test_balances_and_signed_positions_preserve_decimal_and_short_sign():
    balance = Balance(currency="USD", cash=Decimal("10.01"), available="9.99", equity="12.34")
    position = SignedPosition(
        position_id="position-1",
        symbol="AAPL",
        quantity="-2.5",
        average_price="201.13",
        market_value="-502.825",
        currency="USD",
        unrealized_pnl="0.005",
    )

    assert balance.cash == Decimal("10.01")
    assert isinstance(balance.available, Decimal)
    assert position.quantity == Decimal("-2.5")
    assert isinstance(position.market_value, Decimal)


def test_open_position_rejects_zero_quantity():
    with pytest.raises(ValidationError, match="non-zero"):
        SignedPosition(
            position_id="position-1",
            symbol="AAPL",
            quantity=0,
            average_price=1,
            market_value=0,
            currency="USD",
            unrealized_pnl=0,
        )


def test_quote_requires_executable_sides_and_aware_timestamp():
    quote = ExecutableQuote(symbol="AAPL", bid="201.10", ask="201.11", as_of=NOW)
    assert quote.bid == Decimal("201.10")

    with pytest.raises(ValidationError, match="bid must not exceed ask"):
        ExecutableQuote(symbol="AAPL", bid="201.12", ask="201.11", as_of=NOW)
    with pytest.raises(ValidationError, match="timezone"):
        ExecutableQuote(
            symbol="AAPL", bid="201.10", ask="201.11", as_of=datetime(2026, 8, 11)
        )


def test_market_state_is_explicit():
    state = MarketState(
        symbol="AAPL", status=MarketSession.HALTED, is_open=False, as_of=NOW
    )
    assert state.status is MarketSession.HALTED


def test_order_rejects_overfill_and_requires_limit_price():
    values = {
        "order_id": "order-1",
        "symbol": "AAPL",
        "side": OrderSide.BUY,
        "order_type": OrderType.LIMIT,
        "status": OrderStatus.PARTIALLY_FILLED,
        "quantity": "2",
        "filled_quantity": "1",
        "duration": "day",
        "created_at": NOW,
        "updated_at": NOW,
    }
    with pytest.raises(ValidationError, match="limit price"):
        BrokerOrder(**values)

    values.update(limit_price="200", filled_quantity="3")
    with pytest.raises(ValidationError, match="exceed"):
        BrokerOrder(**values)


def test_fill_and_stop_use_exact_values():
    fill = Fill(
        fill_id="fill-1",
        order_id="order-1",
        symbol="AAPL",
        side="sell",
        quantity="-0.3",
        price="199.99",
        commission="0.01",
        currency="USD",
        executed_at=NOW,
    )
    stop = StopState(
        order_id="stop-1",
        symbol="AAPL",
        side="buy",
        quantity="0.3",
        status="active",
        stop_price="205.01",
        trailing_percent="1.25",
    )

    assert fill.price == Decimal("199.99")
    assert stop.quantity == Decimal("0.3")


@pytest.mark.parametrize(
    "kind,quantity",
    [(IntentKind.OPEN_LONG, "1"), (IntentKind.REDUCE_LONG, "-1"), (IntentKind.CLOSE_SHORT, "1")],
)
def test_order_intent_validates_signed_direction(kind, quantity):
    intent = OrderIntent(
        intent_id="intent-1",
        account_scope="portfolio-a",
        symbol="AAPL",
        kind=kind,
        signed_quantity=quantity,
        order_type="market",
        duration="day",
        margin_policy=MarginPolicy.CASH_ONLY,
    )
    assert intent.signed_quantity == Decimal(quantity)


def test_order_intent_rejects_direction_mismatch():
    with pytest.raises(ValidationError, match="inconsistent with intent kind"):
        OrderIntent(
            intent_id="intent-1",
            account_scope="portfolio-a",
            symbol="AAPL",
            kind=IntentKind.OPEN_SHORT,
            signed_quantity="1",
            order_type="market",
            duration="day",
            margin_policy="allow-margin",
        )


def test_classified_failure_requires_consistent_unknown_outcome():
    failure = BrokerFailure(
        kind=FailureKind.UNKNOWN_OUTCOME,
        operation="place-order",
        message="response lost after transmission",
        retryable=False,
        outcome_unknown=True,
    )
    assert failure.outcome_unknown is True

    with pytest.raises(ValidationError, match="must match"):
        BrokerFailure(
            kind=FailureKind.AUTHORIZATION,
            operation="portfolio",
            message="denied",
            retryable=False,
            outcome_unknown=True,
        )
