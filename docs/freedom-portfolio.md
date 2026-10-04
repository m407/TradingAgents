# Freedom Broker portfolio valuation

`tradernet_global` provides an explicit, read-only interface for valuing one
loaded Freedom Broker portfolio snapshot. Importing it and evaluating supplied
data are independent of TradingAgents, broker credentials, and the network.
The result is an estimate from a portfolio response and supplied quotes; it is
not a broker statement, net liquidation value, guaranteed sale proceeds, or an
amount available to withdraw. The portfolio and quote requests are separate
observations and need not be atomic.

## Pure offline valuation

The pure evaluator accepts API-shaped dictionaries and explicit UTC-aware
fetch times. The following fully synthetic example runs without a broker,
credentials, or network:

```python
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from tradernet_global.valuation import value_freedom_portfolio

now = datetime(2025, 1, 2, 12, tzinfo=timezone.utc)
snapshot = {
    "loaded": True,
    "pos": [
        {"id": "synthetic-stock", "ticker": "DEMO.US", "t": 1,
         "curr": "USD", "q": "3"},
        {"id": "synthetic-bond", "ticker": "BOND.US", "t": 2,
         "curr": "USD", "q": "2", "fv": "1000",
         "accruedint_a": "1"},
        {"id": "synthetic-repo", "ticker": "DEMO.US_RP1", "t": 8,
         "curr": "USD", "q": "5", "mkt_price": "120",
         "fv": "100", "accruedint_a": "0"},
        {"id": "synthetic-swap", "ticker": "DEMO.US_RP2", "t": 14,
         "curr": "EUR", "q": "-2", "mkt_price": "50",
         "fv": "100", "accruedint_a": "0"},
    ],
    "acc": [
        {"curr": "USD", "s": "10.10"},
        {"curr": "EUR", "s": "0"},
    ],
}
quotes = {
    "DEMO.US": {"market_status": "open", "bbp": "20", "ltp": "20.5", "bap": "21"},
    "BOND.US": {"market_status": "closed", "ltp": "99.5", "fv": "1000", "acd": "2"},
}
result = value_freedom_portfolio(
    snapshot, quotes, fetched_at=now, quotes_fetched_at=now,
)

assert result.positions[0].value == Decimal("60")
assert result.positions[1].value == Decimal("1994")
assert result.positions[2].value == Decimal("600")
assert result.positions[3].value == Decimal("100")
assert result.totals_by_currency["USD"].total == Decimal("2664.10")
assert result.totals_by_currency["EUR"].total == Decimal("100")
```

Supported groups are ordinary shares (`t=1`), bonds (`t=2`), and broker-coded
REPO/swap instruments (`t` in `{8, 9, 10, 14, 18}`). For open-market shares and
bonds, the evaluator selects bid for a long position or ask for a short
position, then last trade and the opposite side as fallback. A closed market
uses last trade. When quotes cannot supply a usable price, the position's own
positive `mkt_price` may be used and is identified as `portfolio_fallback`.
REPO/swap positions use their own portfolio price and factor, never the price
of the similarly named underlying share; they are not included in quote
requests. Unsupported types and currency conversion requirements remain
explicitly unavailable rather than guessed. No FX conversion is performed.

Bond valuation uses `(price × factor / 100 + accrued_interest) × quantity`;
factor comes from the position or quote, and an explicitly supplied quote
coupon (including zero) takes precedence over the portfolio coupon. The
REPO/swap rule uses the absolute value of that expression, with the position's
own factor and accrued interest. Share factor is 100 and coupon is zero.
Calculations retain `Decimal` precision without per-position rounding. Format
for display only when needed, for example with
`amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`; do not replace the
stored valuation with a rounded display number.

## Currency totals, completeness, and provenance

`totals_by_currency` keeps positions and cash in separate currencies. Each
`CurrencyTotal` has a `known_subtotal`; its `total` is `None` unless every
position and cash row in that currency is numerically available. An incomplete
USD total does not prevent a complete EUR total. An empty successfully loaded
portfolio is complete with no currency totals. Cash includes only account
`s` balances, not forecast or settlement fields. A zero balance is known zero.

Each position retains its original row, ticker (including RP suffix), signed
quantity, selected price, method, price source, result, and safe reason or
warnings. `fetched_at` and `quotes_fetched_at` describe retrieval times, not
trade times. Where available, `source_time` identifies the quote trade time;
it is not a freshness guarantee. A fallback price can be old, and quote and
portfolio reads may reflect different instants. Neither completeness nor a
numeric total promises execution value or withdrawal availability.

For custom applications, import `FreedomPortfolioValuation` and related
result types from `tradernet_global.models`. This detailed valuation is a
Python result model, not a stable JSON persistence protocol.

## Explicitly read the current account

Network access happens only when `fetch_freedom_portfolio()` is explicitly
called. It reads the one current account exposed by the supplied credentials;
it does not enumerate or select among multiple accounts. The default timeout is
15 seconds per request and the default quote batch size is 50 tickers. Set
`timeout_seconds` to a finite positive value and `quote_batch_size` from 1 to
100 to choose different bounds. The client requests only supported nonzero
share and bond tickers, in bounded batches; REPO/swap rows use their own
portfolio prices and do not trigger underlying-share quote requests.

This example uses the existing root `fnox.toml` entries. Store credentials in
the configured keychain provider using names only—never paste values into a
command, source file, or repository. The Python package requires no new
dependency or fnox configuration change:

```bash
cd /path/to/TradingAgents
fnox set TRADERNET_PUBLIC_KEY
fnox set TRADERNET_PRIVATE_KEY
fnox check
fnox exec -- uv run python - <<'PY'
from tradernet_global import fetch_freedom_portfolio

valuation = fetch_freedom_portfolio(timeout_seconds=15, quote_batch_size=50)
for currency, total in valuation.totals_by_currency.items():
    print(currency, total.total if total.complete else "incomplete")
PY
```

The transport uses the fixed HTTPS domain `tradernet.global`; callers cannot
configure another host through this API. Credentials are read only during the
explicit call. The module's own client is closed when the operation finishes;
a caller-supplied SDK-compatible `client` is not closed. Failures are reported
with safe `FreedomPortfolioError` codes rather than response bodies, request
headers, keys, or account identifiers. A portfolio-read failure raises an
error. A quote-batch failure keeps the snapshot and any valid portfolio-price
fallbacks, with an unavailable-quote warning; affected rows may remain
unevaluable.

The portfolio snapshot and quote batches are separate requests, so prices and
holdings are not an atomic observation. Retrieval timestamps describe when
those requests were made, not a guarantee of synchronized market data. Results
are estimates, not a broker statement, withdrawal amount, or promised execution
value. The call does not place orders or alter the account.

### Offline example of the fetch API

The public `client` parameter allows an SDK-compatible stub for tests and dry
runs. This synthetic example exercises the same fetch-and-value path without
credentials or network access:

```python
from tradernet_global import fetch_freedom_portfolio


class SyntheticClient:
    def account_summary(self):
        return {
            "loaded": True,
            "pos": [{"id": "demo", "ticker": "DEMO.US", "t": 1,
                     "curr": "USD", "q": "2", "mkt_price": "19"}],
            "acc": [{"curr": "USD", "s": "1.25"}],
        }

    def get_quotes(self, symbols):
        assert symbols == ["DEMO.US"]
        return {"DEMO.US": {"market_status": "open", "bbp": "20"}}


valuation = fetch_freedom_portfolio(
    client=SyntheticClient(), timeout_seconds=2, quote_batch_size=10,
)
assert str(valuation.positions[0].value) == "40"
assert str(valuation.totals_by_currency["USD"].total) == "41.25"
```

The stub is invoked directly; it does not create the module-owned HTTP client.
Do not use a real client in an offline example or test.

## Explicitly adapt to `PortfolioContext`

The optional TradingAgents adapter is a separate, explicit step. It maps
holdings into the existing broker-neutral `PortfolioContext`; it does not
change the existing schema or make valuation part of the graph automatically.
All positions are retained even when their market value is unknown. Repeated
full tickers are combined case-insensitively for compatibility, while distinct
tickers—including different RP suffixes—remain separate. The detailed
`FreedomPortfolioValuation` still retains the original rows.

`PortfolioContext.average_price` means average entry price, not current market
price. The Freedom result does not provide a reliable entry price, so every
adapted position has `average_price=None`; do not fill it with the valuation
price. Likewise, `cash` means free cash only: asset values and portfolio totals
are never added to it. An automatic choice is made only when the valuation has
exactly one currency. For a multi-currency portfolio, specify `cash_currency`
to select one currency's known cash balance; positions in every currency are
still preserved. With no selection, both `cash` and `currency` are `None`.

The legacy schema stores numbers as binary `float`, whereas the valuation uses
decimal arithmetic. Conversion therefore preserves only the precision
representable by Python float (and rejects overflow or nonzero values that
underflow to zero). Keep the detailed Decimal valuation as the source for
precise totals; `PortfolioContext` is a compatibility view for holdings and
optional free cash, not a replacement for it.

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tradernet_global import fetch_freedom_portfolio
from tradingagents.freedom_portfolio import to_portfolio_context
from tradingagents.portfolio import PortfolioContext, load_portfolio


class SyntheticMultiCurrencyClient:
    def account_summary(self):
        return {
            "loaded": True,
            "pos": [
                {"id": "demo-us", "ticker": "DEMO.US", "t": 1,
                 "curr": "USD", "q": "2", "mkt_price": "20"},
                {"id": "demo-eu", "ticker": "DEMO.EU", "t": 1,
                 "curr": "EUR", "q": "3", "mkt_price": "10"},
                {"id": "demo-rp", "ticker": "DEMO.US_RP1", "t": 8,
                 "curr": "USD", "q": "1", "mkt_price": "5",
                 "fv": "100", "accruedint_a": "0"},
            ],
            "acc": [
                {"curr": "USD", "s": "4.25"},
                {"curr": "EUR", "s": "1.50"},
            ],
        }

    def get_quotes(self, symbols):
        # Quotes are not needed: the synthetic rows have portfolio prices.
        assert symbols == []
        return {}


valuation = fetch_freedom_portfolio(
    client=SyntheticMultiCurrencyClient(), timeout_seconds=2,
    quote_batch_size=10,
)
context = to_portfolio_context(valuation)
assert (context.cash, context.currency) == (None, None)
assert [(p.ticker, p.quantity, p.average_price) for p in context.positions] == [
    ("DEMO.US", 2.0, None), ("DEMO.EU", 3.0, None),
    ("DEMO.US_RP1", 1.0, None),
]

# The existing JSON model and file loader accept the compatible representation.
payload = context.model_dump_json()
assert PortfolioContext.model_validate_json(payload) == context
with TemporaryDirectory() as directory:
    path = Path(directory) / "portfolio.json"
    path.write_text(json.dumps(json.loads(payload)), encoding="utf-8")
    assert load_portfolio(path) == context
```

Adaptation is intentionally not point-in-time aware: `PortfolioContext` has no
snapshot date or per-position currency/market value. Do not pass a current
broker snapshot as the portfolio for a historical analysis. Doing so would
introduce look-ahead and misrepresent today's holdings as holdings on the
historical analysis date. Use this adapter only when the caller explicitly
intends to provide the current snapshot for a current-context use case.
