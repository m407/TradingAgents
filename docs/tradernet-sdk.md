# Tradernet SDK transport contract

The Freedom news adapter uses `tradernet-sdk>=2.2.0,<3`. In SDK 2.2.0,
`Tradernet` inherits `Core.request` from `tradernet.common.netutils.NetUtils`.
That hook constructs the SDK's `HTTPSession` (a `requests.Session`) lazily and
passes `timeout=self.timeout or None` to its `request` call. `NetUtils.__init__`
defaults that timeout to 300 seconds. The SDK hook does not expose
`allow_redirects`; `requests.Session.request` defaults it to true.

Therefore configure the SDK subclass's `request` hook (not a separate HTTP
client) to pass a finite timeout and `allow_redirects=False`. Keep SDK methods
such as `authorized_request` responsible for request formatting and signing.
The following illustrates the SDK 2.2.0 hook that the adapter must implement;
it preserves the SDK's session and response/status handling while preventing
redirect following:

```python
from requests import Response
from tradernet import Tradernet
from tradernet.common.netutils import HTTPSession


class NewsTradernet(Tradernet):
    DOMAIN = "tradernet.global"

    def __init__(self, public: str, private: str, timeout: float):
        super().__init__(public, private)
        self.timeout = timeout
        # The SDK logs signed headers and request content at DEBUG and logs
        # exceptions with traceback at ERROR. Suppress this instance logger;
        # report only sanitized errors through the application logger.
        self.logger.disabled = True

    def request(self, method, url, headers=None, params=None, data=None) -> Response:
        if not self.session:
            self.session = HTTPSession()
        response = self.session.request(
            method,
            url,
            headers=headers,
            params=params,
            data=data,
            timeout=self.timeout,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise RuntimeError("Tradernet API redirect refused")
        response.raise_for_status()
        return response
```

This is a narrow SDK transport-hook override, not an alternative HTTP/HMAC
transport. SDK 2.2.0's inherited `authorized_request` calls `self.request`, so
the override covers signed calls too. Redirect responses are returned without
following them. `Response.raise_for_status()` does not reject 3xx responses, so
the explicit 300–399 check rejects them before the SDK can parse a redirect
body as JSON. This preserves the SDK's signing and request-send path while
refusing redirects. Do not log SDK exceptions, response bodies, signed headers,
keys, or `repr(client)`.

## Offline verification

Check that the pinned SDK imports and that the expected hook contract still
exists without constructing a client, reading credentials, or making requests:

```bash
PYTHON_DOTENV_DISABLED=1 uv run --with tradernet-sdk==2.2.0 --no-project python - <<'PY'
import inspect
from tradernet import Tradernet
from tradernet.core import Core
from tradernet.common.netutils import NetUtils

assert Tradernet is not None
assert "self.timeout or None" in inspect.getsource(NetUtils.request)
assert "self.request(" in inspect.getsource(Core.authorized_request)
print("Tradernet 2.2.0 import and SDK request-hook contract verified offline")
PY
```

This validates installed package structure only; it does not establish network
reachability, credentials, permissions, channel availability, or live news
access. Never run an authenticated request as part of offline verification.

## TradingAgents adapter contract

`tradingagents.dataflows.vendors.freedom.fetch_freedom_news` implements the
bounded adapter on top of `Tradernet.authorized_request(..., version=3)`: it
checks `getNewsProvidersList`, requests pages with `getNewsList` using
`provider="fbrokerkz"` on every call, and obtains bounded article bodies via
`getNewsDetail`. It rejects a detail unless `providerAlias` confirms
`fbrokerkz`; it does not substitute Oninvest or another provider. Optional
symbols must be explicitly mapped in `freedom_symbol_map`, or supplied as a
qualified Tradernet symbol such as `AAPL.US`. There is no suffix guessing.

Public/private keys are read only when a Freedom fetch is actually requested,
from `TRADERNET_PUBLIC_KEY` and `TRADERNET_PRIVATE_KEY`. The adapter defaults
to three pages of twenty records, at most forty details, a 15-second timeout,
and `ru`; page size cannot exceed 100. It bounds text through the shared news
record, preserves known timezone offsets, leaves unknown timezone values
undated, and always reports historical coverage as unknown. Page/detail
failures retain earlier articles as partial results. Error summaries use only
safe classifications/status codes; SDK logging is disabled on that client
instance because signed request data can otherwise be logged. The narrow SDK
transport override uses the SDK session and signing path while enforcing the
timeout and refusing redirects.

SDK response envelopes are validated fail-closed: `errMsg`/error envelopes,
missing row collections, and malformed row collections are treated as source
failures rather than successful empty pages. `get_global_news_freedom` uses
`global_news_article_limit` when its optional `limit` is omitted or `None`,
matching the Yahoo standalone contract.

Downstream aggregators use the structured `fetch_freedom_news` API. The
standalone public functions `get_news_freedom(ticker, start_date, end_date)`
and `get_global_news_freedom(curr_date, look_back_days=None, limit=None)`
return formatted strings via the shared news formatter, including coverage,
outcome, truncation, and safe failure reasons. Ticker and global limits default
to `news_article_limit` and `global_news_article_limit`, respectively; global
lookback defaults to `global_news_lookback_days`.

Offline adapter verification is:

```bash
PYTHON_DOTENV_DISABLED=1 uv run pytest -q tests/test_freedom_news.py
```

These tests use fake credentials/client transport and make no network calls.
They prove the adapter contract, not credentials, account permissions, live
endpoint availability, channel access, or permission to pass article text to
an LLM. Do not run a real request without separate user authorization.
