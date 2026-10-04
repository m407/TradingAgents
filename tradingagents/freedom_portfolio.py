"""Explicit conversion of Freedom valuation data to the legacy portfolio schema."""

from __future__ import annotations

import math
from decimal import Decimal

from tradernet_global.models import FreedomPortfolioValuation
from tradingagents.portfolio import PortfolioContext, Position


class PortfolioContextConversionError(ValueError):
    """Safe error raised when a valuation cannot be represented faithfully."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _float(value: Decimal, code: str) -> float:
    result = float(value)
    if not math.isfinite(result) or (value != 0 and result == 0):
        raise PortfolioContextConversionError(code)
    return result


def to_portfolio_context(
    valuation: FreedomPortfolioValuation, *, cash_currency: str | None = None
) -> PortfolioContext:
    """Convert holdings and optionally one known cash currency; never infer entry prices."""
    grouped: dict[str, tuple[str, Decimal]] = {}
    for row in valuation.positions:
        if row.quantity is None:
            raise PortfolioContextConversionError("unknown_quantity")
        key = row.ticker.upper()
        ticker, quantity = grouped.get(key, (row.ticker, Decimal(0)))
        grouped[key] = (ticker, quantity + row.quantity)

    positions = [
        Position(ticker=ticker, quantity=_float(quantity, "quantity_out_of_range"))
        for ticker, quantity in grouped.values()
    ]

    currencies = set(valuation.totals_by_currency)
    selected = cash_currency
    if selected is None and len(currencies) == 1:
        selected = next(iter(currencies))
    if selected is None:
        return PortfolioContext(positions=positions)
    if selected not in currencies:
        raise PortfolioContextConversionError("unknown_cash_currency")

    rows = [balance for balance in valuation.cash_balances if balance.currency == selected]
    if any(row.amount is None for row in rows):
        raise PortfolioContextConversionError("unknown_cash")
    if not rows:
        return PortfolioContext(positions=positions, currency=selected)
    cash = sum((row.amount for row in rows if row.amount is not None), Decimal(0))
    return PortfolioContext(cash=_float(cash, "cash_out_of_range"), currency=selected, positions=positions)
