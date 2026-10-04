from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradernet_global.models import FreedomPortfolioError
from tradernet_global.valuation import validate_portfolio_snapshot, value_freedom_portfolio

NOW = datetime(2025, 1, 1, tzinfo=timezone.utc)


def snapshot(pos=(), acc=()):
    return {"result": {"loaded": True, "pos": list(pos), "acc": list(acc)}}


def position(ticker="ABC.US", q="1", t=1, **kwargs):
    return {"ticker": ticker, "curr": "USD", "q": q, "t": t, **kwargs}


def test_rejects_error_envelopes_not_loaded_and_malformed_collections():
    for bad in ({"error": "private"}, snapshot().__class__({"result": {"loaded": False, "pos": [], "acc": []}}),
                {"result": {"loaded": True, "pos": None, "acc": []}}):
        with pytest.raises(FreedomPortfolioError):
            value_freedom_portfolio(bad, {}, fetched_at=NOW)


@pytest.mark.parametrize("bad", [
    {"code": 7, "result": {"ps": {"loaded": True, "pos": [], "acc": []}}},
    {"result": {"code": 7, "ps": {"loaded": True, "pos": [], "acc": []}}},
    {"result": {"ps": {"error": "failed", "loaded": True, "pos": [], "acc": []}}},
    {"errMsg": "failed", "result": {"ps": {"loaded": True, "pos": [], "acc": []}}},
    {"result": {"errMsg": "failed", "ps": {"loaded": True, "pos": [], "acc": []}}},
    {"result": {"ps": {"errMsg": "failed", "loaded": True, "pos": [], "acc": []}}},
])
def test_rejects_nonzero_or_nested_error_envelopes(bad):
    with pytest.raises(FreedomPortfolioError):
        validate_portfolio_snapshot(bad)
    with pytest.raises(FreedomPortfolioError):
        value_freedom_portfolio(bad, {}, fetched_at=NOW)


def test_shared_snapshot_validator_accepts_successful_nested_sdk_shape():
    payload = {"loaded": True, "pos": [], "acc": []}
    assert validate_portfolio_snapshot({"code": 0, "result": {"code": 0, "ps": payload}}) is payload


def test_preserves_duplicate_positions_and_prices_long_short_open_market():
    result = value_freedom_portfolio(
        snapshot([position(q="3", id="a", market_status="open"), position(q="-2", id="b", market_status="open")]),
        {"ABC.US": {"bbp": "20", "bap": "21", "ltp": "19"}}, fetched_at=NOW,
    )
    assert [p.position_id for p in result.positions] == ["a", "b"]
    assert [p.value for p in result.positions] == [Decimal("60"), Decimal("-42")]
    assert result.totals_by_currency["USD"].total == Decimal("18")


def test_closed_market_uses_last_and_unusable_quote_uses_portfolio_fallback():
    result = value_freedom_portfolio(snapshot([
        position(q="2", market_status="closed"),
        position("FALLBACK.US", q="2", mkt_price="7", market_status="mystery"),
    ]), {"ABC.US": {"ltp": "5"}, "FALLBACK.US": {"bbp": "4"}}, fetched_at=NOW)
    assert [p.value for p in result.positions] == [Decimal("10"), Decimal("14")]
    assert result.positions[0].price_source == "quote_ltp"
    assert result.positions[0].method == "closed_market_last_trade"
    assert result.positions[1].price_source == "portfolio_fallback"
    assert "unknown_market_status" in result.positions[1].warnings


def test_actual_nested_snapshot_and_trade_timestamp_offset_are_preserved_as_utc():
    actual_shape = {"result": {"ps": {"loaded": True, "pos": [position(q="2")], "acc": []}}}
    quote = {"ltp": "5", "market_status": "closed", "trade_time": "2024-12-31T23:00:00", "UTCOffset": 60}
    result = value_freedom_portfolio(actual_shape, {"ABC.US": quote}, fetched_at=NOW)
    assert result.positions[0].value == Decimal("10")
    assert result.positions[0].source_time == datetime(2024, 12, 31, 22, tzinfo=timezone.utc)
    assert result.fetched_at.date().isoformat() != result.positions[0].source_time.date().isoformat()


def test_ambiguous_trade_time_is_not_falsely_assigned_utc():
    result = value_freedom_portfolio(snapshot([position()]),
        {"ABC.US": {"ltp": "5", "market_status": "closed", "trade_time": "2024-12-31T23:00:00"}}, fetched_at=NOW)
    assert result.positions[0].source_time is None
    assert result.positions[0].source_time_raw == "2024-12-31T23:00:00"


def test_bond_factor_and_explicit_zero_coupon_win_over_snapshot_coupon():
    result = value_freedom_portfolio(snapshot([position(t=2, q="4", fv="1000", accruedint_a="9")]),
        {"ABC.US": {"ltp": "99.5", "acd": "0", "fv": "900", "market_status": "closed"}}, fetched_at=NOW)
    p = result.positions[0]
    assert p.value == Decimal("3980")
    assert p.accrued_interest == Decimal("0")
    assert p.factor == Decimal("1000")


@pytest.mark.parametrize("kind", [8, 9, 10, 14, 18])
def test_repo_swap_uses_own_price_abs_value_and_never_quote(kind):
    result = value_freedom_portfolio(snapshot([position("ABC.US_RP1", q="-5", t=kind,
        mkt_price="120", fv="100", accruedint_a="0")]), {"ABC.US": {"ltp": "150"}}, fetched_at=NOW)
    assert result.positions[0].value == Decimal("600")
    assert result.positions[0].price_source == "portfolio"


def test_unsupported_fx_invalid_numbers_and_cash_make_currency_incomplete():
    result = value_freedom_portfolio(snapshot([
        position("UNKNOWN", t=999, q="1"),
        position("FX", q="2", base_currency="EUR"),
        position("NAN", q="NaN"),
        position("BOOL", q=True),
    ], [{"curr": "USD", "s": "0.1"}, {"curr": "USD", "s": "0.2"}, {"curr": "EUR", "s": True}]), {}, fetched_at=NOW)
    assert len(result.positions) == 4
    assert [p.reason for p in result.positions] == ["unsupported_type", "currency_conversion_required", "invalid_quantity", "invalid_quantity"]
    assert result.totals_by_currency["USD"].known_subtotal == Decimal("0.3")
    assert result.totals_by_currency["USD"].total is None
    assert not result.totals_by_currency["USD"].complete
    assert result.totals_by_currency["EUR"].total is None


def test_zero_quantity_needs_no_price_and_empty_snapshot_is_complete():
    result = value_freedom_portfolio(snapshot([position(q="0")]), {}, fetched_at=NOW)
    assert result.positions[0].value == Decimal(0)
    assert result.complete
    empty = value_freedom_portfolio(snapshot(), {}, fetched_at=NOW)
    assert empty.complete and empty.totals_by_currency == {}
