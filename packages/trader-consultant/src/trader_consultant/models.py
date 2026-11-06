"""Validated inputs and immutable results; no broker or credential objects."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Amount = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Positive = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Weight = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
Ticker = Annotated[str, Field(min_length=1, pattern=r"^[A-Z0-9.^_+=-]+$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Position(Model):
    """Include BUY candidates with quantity=0; use exact, caller-resolved identities."""

    ticker: Ticker
    kind: Literal["stock", "etf"]
    currency: Currency
    quantity: Amount
    available_quantity: Amount
    price: Positive
    price_at: AwareDatetime
    quantity_step: Positive

    @model_validator(mode="after")
    def available(self):
        if self.available_quantity > self.quantity:
            raise ValueError("available_quantity exceeds quantity")
        if self.quantity % self.quantity_step or self.available_quantity % self.quantity_step:
            raise ValueError("quantities must be multiples of quantity_step")
        return self


class ExcludedAsset(Model):
    """Reported, never silently valued at zero or included in managed NAV."""

    identifier: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Snapshot(Model):
    snapshot_id: str = Field(min_length=1)
    as_of: AwareDatetime
    currency: Currency
    free_cash: Amount
    positions: tuple[Position, ...]
    excluded_assets: tuple[ExcludedAsset, ...] = ()
    complete: bool
    has_active_orders: bool

    @model_validator(mode="after")
    def identities(self):
        tickers = [p.ticker for p in self.positions]
        if len(tickers) != len(set(tickers)):
            raise ValueError("duplicate position ticker")
        if any(p.currency != self.currency for p in self.positions):
            raise ValueError("managed positions must use the snapshot currency")
        return self


class Recommendation(Model):
    ticker: Ticker
    action: Literal["BUY", "SELL", "KEEP"]
    rationale: str = Field(default="", max_length=12000)


class Policy(Model):
    buy_budget: Amount
    cash_reserve: Amount
    max_weights: dict[Ticker, Weight]
    commission_rate: Weight
    slippage_rate: Weight
    minimum_trade: Positive
    max_snapshot_age_seconds: int = Field(gt=0, strict=True)
    max_price_age_seconds: int = Field(gt=0, strict=True)
    order_ttl_seconds: int = Field(gt=0, strict=True)
    max_attempts: int = Field(default=10, ge=1, le=10, strict=True)

    @model_validator(mode="after")
    def bounds(self):
        if any(w > 1 for w in self.max_weights.values()):
            raise ValueError("maximum weights must be <= 1")
        if self.commission_rate >= 1 or self.slippage_rate >= 1:
            raise ValueError("cost rates must be < 1")
        return self


class ConsultationRequest(Model):
    snapshot: Snapshot
    recommendations: tuple[Recommendation, ...]
    policy: Policy


class AllocationProposal(Model):
    """The only LLM output; the model never writes executable quantities."""

    purchase_amount: Amount = Field(description="Total purchases at snapshot prices, before costs.")
    sell_target_weights: dict[Ticker, Weight] = Field(
        description="Exactly one target weight for each SELL, relative to initial managed NAV."
    )
    explanation: str = Field(min_length=1, max_length=12000)
    reasons: dict[Ticker, str] = Field(description="A reason for every recommendation.")


class Issue(Model):
    code: str
    detail: str
    ticker: str | None = None


class Trade(Model):
    ticker: Ticker
    side: Literal["BUY", "SELL"]
    quantity: Positive
    reference_price: Positive
    estimated_execution_price: Positive
    reference_notional: Positive
    estimated_notional: Positive
    estimated_fee: Amount
    reason: str


class Allocation(Model):
    ticker: Ticker
    current_quantity: Amount
    resulting_quantity: Amount
    current_weight: Weight
    target_weight: Weight
    achieved_weight: Weight
    maximum_weight: Weight
    reason: str


class OrderDraft(Model):
    status: Literal["awaiting_confirmation"] = "awaiting_confirmation"
    order_id: str
    snapshot_id: str
    input_fingerprint: str
    created_at: AwareDatetime
    expires_at: AwareDatetime
    currency: Currency
    weight_basis_nav: Positive
    trades: tuple[Trade, ...]
    allocations: tuple[Allocation, ...]
    selected_purchase_amount: Amount
    unused_purchase_amount: Amount
    estimated_cash_after: Amount
    cash_reserved_for_buys_and_fees: Amount
    explanation: str
    warnings: tuple[Issue, ...]
    checks: tuple[str, ...]
    excluded_assets: tuple[ExcludedAsset, ...]


class ConsultationResult(Model):
    status: Literal["awaiting_confirmation", "blocked", "no_changes"]
    attempts: int = 0
    issues: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()
    draft: OrderDraft | None = None
    excluded_assets: tuple[ExcludedAsset, ...] = ()
