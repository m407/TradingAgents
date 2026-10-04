from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tradernet_global.models import CashBalance, FreedomPortfolioValuation, PositionValuation
from tradingagents.freedom_portfolio import PortfolioContextConversionError, to_portfolio_context
from tradingagents.portfolio import load_portfolio


def valuation(positions=(), cash=(), currencies=None):
    currencies = currencies or {row.currency for row in positions} | {row.currency for row in cash}
    return FreedomPortfolioValuation(tuple(positions), tuple(cash), {c: object() for c in currencies}, True,
                                     datetime.now(timezone.utc))


def position(ticker, quantity):
    return PositionValuation(0, None, ticker, None, 1, "USD", quantity, None, None, None, None,
                             None, None)


def test_all_holdings_group_case_insensitive_full_tickers_and_keep_rp_suffixes():
    result = to_portfolio_context(valuation([position("ABC.US", Decimal("2")),
                                             position("abc.us", Decimal("3")),
                                             position("ABC.US_RP1", Decimal("4")),
                                             position("ABC.US_RP2", Decimal("5"))]))
    assert [(p.ticker, p.quantity, p.average_price) for p in result.positions] == [
        ("ABC.US", 5.0, None), ("ABC.US_RP1", 4.0, None), ("ABC.US_RP2", 5.0, None)]
    assert result.position_in("abc.us").quantity == 5.0


def test_cash_currency_auto_selection_and_explicit_selection():
    v = valuation([position("A.US", Decimal(1))],
                  [CashBalance(0, "USD", Decimal("2.5"))])
    assert to_portfolio_context(v).cash == 2.5
    v = valuation([position("A.US", Decimal(1))],
                  [CashBalance(0, "USD", Decimal("2")), CashBalance(1, "EUR", Decimal("3"))])
    assert (to_portfolio_context(v).cash, to_portfolio_context(v).currency) == (None, None)
    assert to_portfolio_context(v, cash_currency="EUR").cash == 3


def test_holdings_only_auto_selects_sole_currency_with_unknown_cash_value():
    result = to_portfolio_context(valuation([position("A.US", Decimal(1))]))
    assert result.currency == "USD"
    assert result.cash is None


def test_explicit_holdings_only_currency_does_not_require_cash_rows():
    result = to_portfolio_context(valuation([position("A.US", Decimal(1))]), cash_currency="USD")
    assert result.currency == "USD"
    assert result.cash is None


def test_unknown_valuation_does_not_drop_known_holding():
    row = position("ABC.US", Decimal("2"))
    row = PositionValuation(**{**row.__dict__, "value": None, "reason": "no_price"})
    assert to_portfolio_context(valuation([row])).positions[0].quantity == 2


def test_unknown_quantity_refuses_partial_flat_context():
    row = position("ABC.US", None)
    with pytest.raises(PortfolioContextConversionError, match="unknown_quantity"):
        to_portfolio_context(valuation([row]))


@pytest.mark.parametrize("amount", [Decimal("1e10000"), Decimal("1e-10000")])
def test_decimal_float_boundary_rejects_infinite_or_underflow(amount):
    with pytest.raises(PortfolioContextConversionError):
        to_portfolio_context(valuation([position("ABC.US", amount)]))


def test_json_round_trip_render_fingerprint_and_load_portfolio(tmp_path):
    context = to_portfolio_context(valuation([position("ABC.US", Decimal("2"))],
                                               [CashBalance(0, "USD", Decimal("3"))]))
    assert context.render("ABC.US") and context.fingerprint()
    path = tmp_path / "portfolio.json"
    path.write_text(context.model_dump_json())
    loaded = load_portfolio(path)
    assert loaded == context
    assert loaded.position_in("ABC.US").average_price is None
