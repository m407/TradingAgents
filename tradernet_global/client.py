"""Explicit, read-only access to a Freedom Broker portfolio via Tradernet."""

from __future__ import annotations

import math
import os
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from requests import Response
from tradernet import Tradernet
from tradernet.common.netutils import HTTPSession

from .models import FreedomPortfolioError
from .valuation import value_freedom_portfolio

_DOMAIN = "tradernet.global"
_COMMANDS = {"getPositionJson", "getStockQuotesJson"}
_QUOTE_TYPES = {1, 2}


class _FreedomTradernet(Tradernet):
    """The SDK signer, constrained to exact v3 read endpoints."""

    DOMAIN = _DOMAIN

    def __init__(self, public: str, private: str, timeout_seconds: float):
        super().__init__(public, private)
        self.timeout_seconds = timeout_seconds
        self.logger.disabled = True
        self._freedom_session = HTTPSession()

    def authorized_request(self, cmd: str, params: dict | None = None, version: int | None = 3) -> Any:
        if cmd not in _COMMANDS or version != 3:
            raise FreedomPortfolioError("command_not_allowed")
        return super().authorized_request(cmd, params, version)

    def request(self, method, url, headers=None, params=None, data=None) -> Response:
        parsed = urlsplit(url)
        expected_path = f"/api/{next((c for c in _COMMANDS if url.endswith('/api/' + c)), '')}"
        if (method.lower() != "post" or parsed.scheme != "https" or parsed.netloc != _DOMAIN
                or parsed.path != expected_path or not expected_path.endswith(tuple(_COMMANDS))):
            raise FreedomPortfolioError("request_not_allowed")
        response = self._freedom_session.request(
            method, url, headers=headers, params=params, data=data,
            timeout=self.timeout_seconds, allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise FreedomPortfolioError("redirect_refused")
        if response.status_code >= 400:
            status = response.status_code
            code = {401: "unauthorized", 403: "forbidden", 429: "rate_limited"}.get(status, "api_request_failed")
            raise FreedomPortfolioError(code)
        return response

    def close(self) -> None:
        self._freedom_session.close()


def _validate_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("invalid timeout_seconds")
    return float(value)


def _validate_batch_size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("invalid quote_batch_size")
    return value


def _payload(response: Any, *, quotes: bool = False) -> Any:
    if isinstance(response, dict) and ("error" in response or response.get("errMsg")
                                       or response.get("code") not in (None, 0, "0")):
        raise ValueError("api_error")
    payload = response.get("result", response) if isinstance(response, dict) else response
    if isinstance(payload, dict) and ("error" in payload or payload.get("errMsg")
                                      or payload.get("code") not in (None, 0, "0")):
        raise ValueError("api_error")
    if not quotes:
        return response
    if isinstance(payload, dict) and isinstance(payload.get("quotes"), (list, dict)):
        payload = payload["quotes"]
    if isinstance(payload, list):
        if any(not isinstance(row, dict) for row in payload):
            raise ValueError("malformed_quotes")
        return {row.get("ticker", row.get("symbol", row.get("instr"))): row for row in payload
                if isinstance(row.get("ticker", row.get("symbol", row.get("instr"))), str)}
    if isinstance(payload, dict):
        # SDK may return a symbol-keyed mapping or a single quote record.
        if any(key in payload for key in ("ticker", "symbol", "instr")):
            ticker = payload.get("ticker", payload.get("symbol", payload.get("instr")))
            return {ticker: payload} if isinstance(ticker, str) else {}
        return payload
    raise ValueError("malformed_quotes")


def _quote_symbols(positions: Any) -> list[str]:
    """Select quotes from validated evaluator rows, not raw snapshot fields."""
    symbols = []
    for position in positions:
        if (position.instrument_type in _QUOTE_TYPES and position.quantity is not None
                and position.quantity != 0 and position.reason != "currency_conversion_required"
                and position.ticker not in symbols):
            symbols.append(position.ticker)
    return symbols


def fetch_freedom_portfolio(*, client: Any | None = None, timeout_seconds: float = 15,
                            quote_batch_size: int = 50):
    """Read one current-account snapshot, then bounded quote batches, offline-testable with client."""
    timeout = _validate_timeout(timeout_seconds)
    batch_size = _validate_batch_size(quote_batch_size)
    owned = client is None
    if owned:
        public, private = os.getenv("TRADERNET_PUBLIC_KEY"), os.getenv("TRADERNET_PRIVATE_KEY")
        if not public or not private:
            raise FreedomPortfolioError("credentials_not_configured")
        client = _FreedomTradernet(public, private, timeout)
    try:
        try:
            snapshot = client.account_summary()
            # The pure evaluator is the shared validator for API envelopes,
            # required collections, row currencies and tickers. Validate the
            # entire snapshot before any quote selection or transport call.
            fetched_at = datetime.now(timezone.utc)
            validated = value_freedom_portfolio(snapshot, {}, fetched_at=fetched_at)
            symbols = _quote_symbols(validated.positions)
            quotes: dict[str, dict] = {}
            warnings: list[str] = []
            for start in range(0, len(symbols), batch_size):
                batch = symbols[start:start + batch_size]
                try:
                    result = _payload(client.get_quotes(batch), quotes=True)
                    quotes.update(result)
                except Exception:
                    warnings.append("quote_batch_unavailable")
            valuation = value_freedom_portfolio(snapshot, quotes, fetched_at=fetched_at,
                                                quotes_fetched_at=datetime.now(timezone.utc) if symbols else None)
            if warnings:
                from dataclasses import replace
                valuation = replace(valuation, warnings=tuple(warnings))
            return valuation
        except FreedomPortfolioError:
            raise
        except Exception:
            raise FreedomPortfolioError("portfolio_fetch_failed") from None
    finally:
        if owned:
            with suppress(Exception):
                client.close()
