"""Broker-neutral portfolio domain models using exact decimal values."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    TRAILING_STOP = "trailing_stop"


class OrderStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PARTIALLY_FILLED = "partially-filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class MarketSession(str, Enum):
    OPEN = "open"
    TRADING = "trading"
    CLOSED = "closed"
    PRE_MARKET = "pre-market"
    POST_MARKET = "post-market"
    AUCTION = "auction"
    HALTED = "halted"
    UNKNOWN = "unknown"


class IntentKind(str, Enum):
    OPEN_LONG = "open-long"
    EXPAND_LONG = "expand-long"
    REDUCE_LONG = "reduce-long"
    CLOSE_LONG = "close-long"
    OPEN_SHORT = "open-short"
    EXPAND_SHORT = "expand-short"
    REDUCE_SHORT = "reduce-short"
    CLOSE_SHORT = "close-short"
    REVERSE_TO_LONG = "reverse-to-long"
    REVERSE_TO_SHORT = "reverse-to-short"


class MarginPolicy(str, Enum):
    CASH_ONLY = "cash-only"
    ALLOW_MARGIN = "allow-margin"


class FailureKind(str, Enum):
    TRANSPORT = "transport"
    HTTP = "http"
    AUTHORIZATION = "authorization"
    VALIDATION = "validation"
    REJECTION = "rejection"
    UNKNOWN_OUTCOME = "unknown-outcome"
    UNKNOWN = "unknown"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


class Balance(_DomainModel):
    currency: str = Field(min_length=1)
    cash: Decimal
    available: Decimal
    equity: Decimal


class Position(_DomainModel):
    position_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    quantity: Decimal
    average_price: Decimal = Field(gt=0)
    market_value: Decimal
    currency: str = Field(min_length=1)
    unrealized_pnl: Decimal
    realized_pnl: Decimal | None = None

    @field_validator("quantity")
    @classmethod
    def _nonzero_quantity(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("an open position quantity must be non-zero")
        return value


class Quote(_DomainModel):
    symbol: str = Field(min_length=1)
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    last: Decimal | None = Field(default=None, gt=0)
    as_of: datetime
    currency: str | None = Field(default=None, min_length=1)

    _as_of_is_aware = field_validator("as_of")(_aware)

    @model_validator(mode="after")
    def _bid_not_above_ask(self) -> Quote:
        if self.bid > self.ask:
            raise ValueError("bid must not exceed ask")
        return self


class MarketState(_DomainModel):
    symbol: str | None = Field(default=None, min_length=1)
    status: MarketSession
    is_open: bool
    as_of: datetime

    _as_of_is_aware = field_validator("as_of")(_aware)

    @model_validator(mode="after")
    def _consistent_open_state(self) -> MarketState:
        open_status = self.status in {MarketSession.OPEN, MarketSession.TRADING}
        if self.is_open != open_status:
            raise ValueError("market status contradicts is_open")
        return self


class Order(_DomainModel):
    order_id: str = Field(min_length=1)
    client_order_id: str | None = Field(default=None, min_length=1)
    symbol: str = Field(min_length=1)
    side: OrderSide
    quantity: Decimal
    filled_quantity: Decimal
    order_type: OrderType
    status: OrderStatus
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    trailing_percent: Decimal | None = Field(default=None, gt=0)
    duration: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime

    _timestamps_are_aware = field_validator("created_at", "updated_at")(_aware)

    @model_validator(mode="after")
    def _consistent_order(self) -> Order:
        if self.quantity == 0:
            raise ValueError("order quantity must be non-zero")
        expected_positive = self.side is OrderSide.BUY
        if expected_positive != (self.quantity > 0):
            raise ValueError("signed quantity is inconsistent with order side")
        if self.filled_quantity and expected_positive != (self.filled_quantity > 0):
            raise ValueError("signed filled quantity is inconsistent with order side")
        if abs(self.filled_quantity) > abs(self.quantity):
            raise ValueError("filled quantity must not exceed order quantity")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require a limit price")
        if self.order_type in {OrderType.STOP, OrderType.TRAILING_STOP} and self.stop_price is None:
            raise ValueError("stop orders require a stop price")
        if self.order_type is OrderType.TRAILING_STOP and self.trailing_percent is None:
            raise ValueError("trailing stops require a trailing percentage")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class Fill(_DomainModel):
    fill_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: OrderSide
    quantity: Decimal
    price: Decimal = Field(gt=0)
    commission: Decimal = Field(ge=0)
    currency: str = Field(min_length=1)
    executed_at: datetime

    _executed_at_is_aware = field_validator("executed_at")(_aware)

    @model_validator(mode="after")
    def _consistent_fill(self) -> Fill:
        if self.quantity == 0:
            raise ValueError("fill quantity must be non-zero")
        if (self.side is OrderSide.BUY) != (self.quantity > 0):
            raise ValueError("signed quantity is inconsistent with fill side")
        return self


class StopState(_DomainModel):
    order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: OrderSide
    quantity: Decimal
    status: OrderStatus
    stop_price: Decimal = Field(gt=0)
    trailing_percent: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _consistent_stop(self) -> StopState:
        if self.quantity == 0:
            raise ValueError("stop state requires a non-zero signed quantity")
        if (self.side is OrderSide.BUY) != (self.quantity > 0):
            raise ValueError("signed quantity is inconsistent with stop side")
        return self


class PortfolioSnapshot(_DomainModel):
    balances: tuple[Balance, ...]
    positions: tuple[Position, ...]
    as_of: datetime

    _as_of_is_aware = field_validator("as_of")(_aware)


class OrderIntent(_DomainModel):
    intent_id: str = Field(min_length=1)
    account_scope: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    kind: IntentKind
    signed_quantity: Decimal
    order_type: OrderType
    limit_price: Decimal | None = Field(default=None, gt=0)
    duration: str = Field(min_length=1)
    margin_policy: MarginPolicy

    @model_validator(mode="after")
    def _consistent_intent(self) -> OrderIntent:
        if self.signed_quantity == 0:
            raise ValueError("order intent quantity must be non-zero")
        positive = {
            IntentKind.OPEN_LONG,
            IntentKind.EXPAND_LONG,
            IntentKind.REDUCE_SHORT,
            IntentKind.CLOSE_SHORT,
            IntentKind.REVERSE_TO_LONG,
        }
        if (self.kind in positive) != (self.signed_quantity > 0):
            raise ValueError("signed quantity is inconsistent with intent kind")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit intents require a limit price")
        return self


class BrokerFailure(_DomainModel):
    kind: FailureKind
    operation: str = Field(min_length=1)
    message: str = Field(min_length=1)
    broker_code: str | None = None
    retryable: bool
    outcome_unknown: bool = False

    @model_validator(mode="after")
    def _consistent_unknown_outcome(self) -> BrokerFailure:
        if self.outcome_unknown != (self.kind is FailureKind.UNKNOWN_OUTCOME):
            raise ValueError("outcome_unknown must match the unknown-outcome failure kind")
        return self


# Descriptive aliases retained at the public boundary.
SignedPosition = Position
ExecutableQuote = Quote
BrokerOrder = Order
