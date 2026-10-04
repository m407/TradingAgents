"""Typed, broker-specific portfolio valuation results."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


class FreedomPortfolioError(Exception):
    """Safe public error for portfolio retrieval or malformed responses."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PositionValuation:
    """Valuation (or explicit unavailable status) for one source row."""

    index: int
    position_id: str | None
    ticker: str
    name: str | None
    instrument_type: int | None
    currency: str
    quantity: Decimal | None
    price: Decimal | None
    factor: Decimal | None
    accrued_interest: Decimal | None
    value: Decimal | None
    method: str | None
    price_source: str | None
    source_time: datetime | None = None
    reason: str | None = None
    warnings: tuple[str, ...] = ()
    source_time_raw: str | None = None


@dataclass(frozen=True)
class CashBalance:
    """One cash-account row, preserving unknown values."""

    index: int
    currency: str
    amount: Decimal | None
    reason: str | None = None


@dataclass(frozen=True)
class CurrencyTotal:
    """Known subtotal and optional complete total for a currency."""

    currency: str
    positions_total: Decimal
    cash_total: Decimal
    known_subtotal: Decimal
    total: Decimal | None
    complete: bool
    unavailable_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class FreedomPortfolioValuation:
    """Complete row-level valuation and totals grouped by currency."""

    positions: tuple[PositionValuation, ...]
    cash_balances: tuple[CashBalance, ...]
    totals_by_currency: dict[str, CurrencyTotal]
    complete: bool
    fetched_at: datetime
    quotes_fetched_at: datetime | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
