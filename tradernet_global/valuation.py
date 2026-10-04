"""Deterministic, offline valuation of Tradernet portfolio snapshots."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import (
    CashBalance,
    CurrencyTotal,
    FreedomPortfolioError,
    FreedomPortfolioValuation,
    PositionValuation,
)

_SUPPORTED_REPO = {8, 9, 10, 14, 18}
_MISSING = object()


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _required_number(row: dict, key: str) -> Decimal | None:
    return _decimal(row.get(key))


def _currency(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FreedomPortfolioError("invalid_currency")
    return value


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise FreedomPortfolioError("invalid_fetched_at")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FreedomPortfolioError("invalid_fetched_at")
    return value.astimezone(timezone.utc)


def _trade_time(quote: dict | None) -> tuple[datetime | None, str | None]:
    """Return a UTC trade time when certain, otherwise retain its raw form."""
    if quote is None:
        return None, None
    raw = quote.get("trade_time", quote.get("ltp_time", quote.get("time")))
    if not isinstance(raw, str):
        return None, None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, raw
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(timezone.utc), None
    offset = _decimal(quote.get("UTCOffset"))
    if offset is None or offset != offset.to_integral_value():
        return None, raw
    return parsed.replace(tzinfo=timezone(timedelta(minutes=int(offset)))).astimezone(timezone.utc), None


def validate_portfolio_snapshot(snapshot: dict) -> dict:
    """Validate a successful SDK portfolio envelope and return its ``ps`` data.

    Reusable by the transport before requesting quotes: accepts either a raw
    payload or a ``result.ps`` envelope, but rejects nonzero codes/errors at
    every envelope level and requires loaded position/account row collections.
    """
    if not isinstance(snapshot, dict):
        raise FreedomPortfolioError("invalid_snapshot")
    payload = snapshot
    if "result" in payload:
        payload = payload["result"]
    if not isinstance(payload, dict):
        raise FreedomPortfolioError("invalid_snapshot")
    levels = [snapshot, payload]
    if isinstance(payload.get("ps"), dict):
        levels.append(payload["ps"])
        payload = payload["ps"]
    for level in levels:
        if "error" in level and level["error"] not in (None, "", False, 0):
            raise FreedomPortfolioError("invalid_snapshot")
        if "errMsg" in level and level["errMsg"] not in (None, "", False, 0):
            raise FreedomPortfolioError("invalid_snapshot")
        if "code" in level:
            code = level["code"]
            if isinstance(code, bool) or _decimal(code) != Decimal(0):
                raise FreedomPortfolioError("invalid_snapshot")
    if payload.get("loaded") is not True:
        raise FreedomPortfolioError("portfolio_not_loaded")
    positions, accounts = payload.get("pos"), payload.get("acc")
    if not isinstance(positions, list) or not isinstance(accounts, list):
        raise FreedomPortfolioError("invalid_snapshot")
    if any(not isinstance(row, dict) for row in positions + accounts):
        raise FreedomPortfolioError("invalid_snapshot")
    return payload


def _price(quote: dict | None, quantity: Decimal, market: Any) -> tuple[Decimal | None, str | None, tuple[str, ...]]:
    warnings: list[str] = []
    selected = None
    source = None
    if quote:
        status = market if market is not None else quote.get("market_status", quote.get("market"))
        if status in (1, "1", "open", "OPEN", True):
            keys = ("bbp", "ltp", "bap") if quantity > 0 else ("bap", "ltp", "bbp")
        elif status in (0, "0", "closed", "CLOSED", False):
            keys = ("ltp",)
        else:
            keys = ()
            warnings.append("unknown_market_status")
        for key in keys:
            candidate = _decimal(quote.get(key))
            if candidate is not None and candidate > 0:
                selected, source = candidate, "quote_" + key
                break
    if selected is None:
        # Unknown market status or unusable quote must not guess a side.
        return None, None, tuple(warnings)
    return selected, source, tuple(warnings)


def value_freedom_portfolio(
    snapshot: dict,
    quotes: dict[str, dict],
    *,
    fetched_at: datetime,
    quotes_fetched_at: datetime | None = None,
) -> FreedomPortfolioValuation:
    """Value a loaded API-like snapshot using supplied quotes; performs no I/O."""
    payload = validate_portfolio_snapshot(snapshot)
    positions, accounts = payload["pos"], payload["acc"]
    if not isinstance(quotes, dict):
        raise FreedomPortfolioError("invalid_quotes")

    cash: list[CashBalance] = []
    total_currencies: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(accounts):
        currency = _currency(row.get("curr", row.get("currency")))
        amount = _required_number(row, "s")
        cash.append(CashBalance(i, currency, amount, None if amount is not None else "invalid_amount"))
        entry = total_currencies.setdefault(currency, {"positions": Decimal(0), "cash": Decimal(0), "bad": []})
        if amount is not None:
            entry["cash"] += amount
        else:
            entry["bad"].append(i)

    valued: list[PositionValuation] = []
    for i, row in enumerate(positions):
        ticker = row.get("ticker", row.get("instr", row.get("symbol")))
        currency = _currency(row.get("curr", row.get("currency")))
        if not isinstance(ticker, str) or not ticker.strip():
            raise FreedomPortfolioError("invalid_ticker")
        entry = total_currencies.setdefault(currency, {"positions": Decimal(0), "cash": Decimal(0), "bad": []})
        q = _required_number(row, "q")
        if q is None:
            q = _required_number(row, "quantity")
        raw_type = row.get("t", row.get("type"))
        kind = raw_type if isinstance(raw_type, int) and not isinstance(raw_type, bool) else None
        pos_id = row.get("id", row.get("position_id"))
        if pos_id is not None:
            pos_id = str(pos_id)
        name = row.get("name") if isinstance(row.get("name"), str) else None
        value = price = factor = accrued = None
        method = source = reason = None
        quote = None
        warnings: tuple[str, ...] = ()
        base_currency = row.get("base_currency")
        if isinstance(base_currency, str) and base_currency and base_currency != currency:
            reason = "currency_conversion_required"
        elif q is None:
            reason = "invalid_quantity"
        elif kind not in ({1, 2} | _SUPPORTED_REPO):
            reason = "unsupported_type"
        elif q == 0:
            value, price, factor, accrued, method, source = Decimal(0), Decimal(0), Decimal(100), Decimal(0), "zero_quantity", "not_required"
        elif kind in _SUPPORTED_REPO:
            price = _required_number(row, "mkt_price")
            factor = _required_number(row, "fv")
            accrued = _required_number(row, "accruedint_a")
            if price is None or price <= 0:
                reason = "missing_portfolio_price"
            elif factor is None or factor <= 0:
                reason = "missing_factor"
            elif accrued is None:
                reason = "missing_accrued_interest"
            else:
                value = abs((price * factor / Decimal(100) + accrued) * q)
                method, source = "repo_swap", "portfolio"
        else:
            quote = quotes.get(ticker)
            if quote is not None and not isinstance(quote, dict):
                quote = None
            market = row.get("market_status")
            price, source, warnings = _price(quote, q, market)
            if price is None:
                fallback = _required_number(row, "mkt_price")
                if fallback is not None and fallback > 0:
                    price, source = fallback, "portfolio_fallback"
                    warnings += ("quote_unavailable_or_unusable",)
            if kind == 1:
                factor, accrued = Decimal(100), Decimal(0)
            else:
                factor = _required_number(row, "fv")
                if factor is None and quote is not None:
                    factor = _required_number(quote, "fv")
                quote_coupon = _decimal(quote.get("acd")) if quote is not None and "acd" in quote else _MISSING
                accrued = quote_coupon if quote_coupon is not _MISSING else _required_number(row, "accruedint_a")
                if factor is None or factor <= 0:
                    reason = "missing_factor"
                elif accrued is None:
                    reason = "missing_accrued_interest"
            if reason is None and price is None:
                reason = "missing_price"
            if reason is None:
                value = (price * factor / Decimal(100) + accrued) * q
                closed = (market if market is not None else (quote or {}).get("market_status", (quote or {}).get("market"))) in (0, "0", "closed", "CLOSED", False)
                method = "closed_market_last_trade" if closed and source == "quote_ltp" else (
                    "market_quote" if source and source.startswith("quote_") else "portfolio_fallback"
                )
        if value is not None:
            entry["positions"] += value
        else:
            entry["bad"].append(i)
        source_time, source_time_raw = _trade_time(quote)
        valued.append(PositionValuation(
            i, pos_id, ticker, name, kind, currency, q, price, factor, accrued,
            value, method, source, source_time=source_time,
            source_time_raw=source_time_raw, reason=reason, warnings=warnings,
        ))

    totals: dict[str, CurrencyTotal] = {}
    for currency, entry in total_currencies.items():
        subtotal = entry["positions"] + entry["cash"]
        missing = tuple(entry["bad"])
        totals[currency] = CurrencyTotal(currency, entry["positions"], entry["cash"], subtotal, None if missing else subtotal, not missing, missing)
    return FreedomPortfolioValuation(tuple(valued), tuple(cash), totals, all(t.complete for t in totals.values()), _time(fetched_at), _time(quotes_fetched_at) if quotes_fetched_at is not None else None)
