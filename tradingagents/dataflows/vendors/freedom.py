"""Bounded, structured Freedom Finance news access through Tradernet SDK."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from requests import Response
from tradernet import Tradernet
from tradernet.common.netutils import HTTPSession

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.news import (
    NewsArticle,
    NewsCoverage,
    NewsOutcome,
    NewsSourceResult,
    filter_news_window,
    format_news,
)

logger = logging.getLogger(__name__)
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


class _NewsTradernet(Tradernet):
    DOMAIN = "tradernet.global"

    def __init__(self, public: str, private: str, timeout: float):
        super().__init__(public, private)
        self.timeout = timeout
        self.logger.disabled = True

    def request(self, method, url, headers=None, params=None, data=None) -> Response:
        if not self.session:
            self.session = HTTPSession()
        response = self.session.request(method, url, headers=headers, params=params,
                                        data=data, timeout=self.timeout,
                                        allow_redirects=False)
        if 300 <= response.status_code < 400:
            raise RuntimeError("Tradernet API redirect refused")
        response.raise_for_status()
        return response


def _positive_int(config: dict, key: str, default: int, maximum: int | None = None) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid {key}")
    if maximum is not None and value > maximum:
        raise ValueError(f"invalid {key}")
    return value


def _safe_error(exc: Exception) -> str:
    """Map exception classes/statuses to safe public reasons, never include payloads."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in (401, 403):
        return "Freedom credentials or account permissions rejected (401/403)"
    if status == 429:
        return "Freedom rate limit reached (429)"
    if status is not None:
        return f"Freedom API request failed (HTTP {status})"
    if isinstance(exc, (TimeoutError,)) or "timeout" in type(exc).__name__.lower():
        return "Freedom API request timed out"
    return "Freedom API request failed"


def _request(client: Any, command: str, params: dict) -> Any:
    return client.authorized_request(command, params, version=3)


def _rows(response: Any) -> tuple[list[dict], bool, int | None]:
    if isinstance(response, list):
        if not all(isinstance(item, dict) for item in response):
            raise ValueError("malformed Freedom response")
        return response, False, None
    if not isinstance(response, dict):
        raise ValueError("malformed Freedom response")
    if (response.get("errMsg") or response.get("error")
            or response.get("code") not in (None, 0, "0")):
        raise ValueError("Freedom API reported an error")
    keys = ("list", "news", "items", "data", "providers")
    key = next((name for name in keys if name in response), None)
    if key is None:
        raise ValueError("malformed Freedom response")
    raw = response[key]
    if isinstance(raw, dict):
        nested_key = next((name for name in keys if name in raw), None)
        if nested_key is None:
            raise ValueError("malformed Freedom response")
        raw = raw[nested_key]
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("malformed Freedom response")
    total = response.get("total")
    return raw, bool(response.get("truncated")), total if isinstance(total, int) else None


def _published(row: dict) -> datetime | None:
    value = row.get("dateTime")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            zone = row.get("timeZone")
            if not isinstance(zone, str) or not zone:
                return None
            try:
                if zone.startswith(("+", "-")):
                    parsed = parsed.replace(tzinfo=datetime.strptime(zone, "%z").tzinfo)
                else:
                    parsed = parsed.replace(tzinfo=ZoneInfo(zone))
            except (ZoneInfoNotFoundError, ValueError):
                return None
        return parsed
    except (ValueError, OverflowError):
        return None


def _instrument(ticker: str, config: dict) -> str | None:
    if not ticker:
        return None
    mapped = config.get("freedom_symbol_map", {}).get(ticker)
    value = mapped if isinstance(mapped, str) else ticker
    if not _SYMBOL.fullmatch(value):
        return None
    # Unqualified user tickers are never guessed; only map them explicitly.
    if "." not in value and not isinstance(mapped, str):
        return None
    return value


def fetch_freedom_news(
    ticker: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    client: Any | None = None,
    config: dict | None = None,
) -> NewsSourceResult:
    """Fetch fbrokerkz news with bounded pagination/details and safe failures."""
    config = config or get_config()
    if ticker and _instrument(ticker, config) is None:
        return NewsSourceResult(outcome=NewsOutcome.UNAVAILABLE, coverage=NewsCoverage.UNKNOWN,
                                reasons=("Ticker is not explicitly mapped to a Tradernet instrument",))
    page_size = _positive_int(config, "freedom_news_page_size", 20, 100)
    max_pages = _positive_int(config, "freedom_news_max_pages", 3)
    max_details = _positive_int(config, "freedom_news_max_details", 40)
    timeout = config.get("freedom_news_timeout_seconds", 15)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("invalid freedom_news_timeout_seconds")
    language = config.get("freedom_news_language", "ru")
    if not isinstance(language, str) or not language:
        raise ValueError("invalid freedom_news_language")

    if client is None:
        public, private = os.getenv("TRADERNET_PUBLIC_KEY"), os.getenv("TRADERNET_PRIVATE_KEY")
        if not public or not private:
            return NewsSourceResult(outcome=NewsOutcome.UNAVAILABLE, coverage=NewsCoverage.UNKNOWN,
                                    reasons=("Freedom credentials are not configured",))
        client = _NewsTradernet(public, private, float(timeout))

    articles: list[NewsArticle] = []
    reasons: list[str] = []
    truncated = False
    detail_requests = 0
    seen_ids: set[str] = set()
    # Provider discovery is not deployed on every Tradernet domain. Request the
    # fixed provider directly and verify each detail's providerAlias below.
    page_failed = False
    try:
        for page in range(max_pages):
            params = {"provider": "fbrokerkz", "lang": language,
                      "take": min(page_size, 100), "skip": page * page_size}
            if ticker:
                params["ticker"] = _instrument(ticker, config)
            rows, response_truncated, total = _rows(_request(client, "getNewsList", params))
            truncated = truncated or response_truncated
            ids = [str(row.get("id", "")) for row in rows if row.get("id") is not None]
            if not rows:
                break
            new_ids = [ident for ident in ids if ident and ident not in seen_ids]
            if not new_ids:
                truncated = True
                reasons.append("Repeated page stopped pagination")
                break
            seen_ids.update(new_ids)
            for ident in new_ids:
                if detail_requests >= max_details:
                    truncated = True
                    reasons.append("Detail request budget reached")
                    break
                try:
                    detail_requests += 1
                    detail_id = int(ident) if ident.isdecimal() else ident
                    detail = _request(client, "getNewsDetail", {"id": detail_id})
                    if not isinstance(detail, dict) or detail.get("error") or detail.get("code") not in (None, 0, "0"):
                        raise ValueError("malformed Freedom detail")
                    if detail.get("providerAlias") != "fbrokerkz":
                        truncated = True
                        reasons.append("Unverified provider detail excluded")
                        continue
                    article = NewsArticle(
                        source_id=ident, delivery_sources=("freedom",),
                        publisher=str(detail.get("publisher") or "Freedom Finance"),
                        title=str(detail.get("title") or ""),
                        text=str(detail.get("text") or detail.get("body") or detail.get("content") or ""),
                        url=str(detail.get("url") or ""), published_at=_published(detail),
                        language=detail.get("lang") if isinstance(detail.get("lang"), str) else None,
                        instrument=_instrument(ticker, config) if ticker else None,
                        max_text_chars=_positive_int(config, "news_max_text_chars", 4000),
                    )
                    articles.append(article)
                except Exception as exc:
                    truncated = True
                    reasons.append(_safe_error(exc))
                    logger.warning("Freedom news detail failed: %s", _safe_error(exc))
            if detail_requests >= max_details:
                truncated = truncated or bool(rows)
                break
            if total is not None and page * page_size + len(rows) >= total:
                break
            if len(rows) < page_size:
                break
        else:
            truncated = True
            reasons.append("Page budget reached")
    except Exception as exc:
        page_failed = True
        truncated = True
        reasons.append(_safe_error(exc))
        logger.warning("Freedom news page failed: %s", _safe_error(exc))

    if start_date and end_date:
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
        articles = filter_news_window(articles, start, end)
    outcome = (NewsOutcome.OK if articles else
               NewsOutcome.UNAVAILABLE if page_failed or reasons else NewsOutcome.EMPTY)
    return NewsSourceResult(tuple(articles), outcome, NewsCoverage.UNKNOWN, truncated,
                            tuple(dict.fromkeys(reasons)))


def _limited_result(result: NewsSourceResult, limit: int) -> NewsSourceResult:
    return NewsSourceResult(result.articles[:limit], result.outcome, result.coverage,
                            result.truncated or len(result.articles) > limit, result.reasons)


def get_news_freedom(ticker: str, start_date: str, end_date: str) -> str:
    """Standalone ticker-news tool result, preserving source status and dates."""
    config = get_config()
    limit = _positive_int(config, "news_article_limit", 10)
    result = _limited_result(fetch_freedom_news(ticker, start_date, end_date), limit)
    text_limit = _positive_int(config, "news_max_text_chars", 4000)
    return format_news(result, title=f"Freedom News for {ticker}, {start_date} to {end_date}",
                       max_text_chars=text_limit)


def get_global_news_freedom(curr_date: str, look_back_days: int | None = None,
                            limit: int | None = None) -> str:
    from datetime import date, timedelta

    config = get_config()
    days = look_back_days if look_back_days is not None else config.get("global_news_lookback_days", 7)
    if limit is None:
        limit = config.get("global_news_article_limit", 10)
    today = date.fromisoformat(curr_date)
    result = fetch_freedom_news(start_date=(today - timedelta(days=days)).isoformat(), end_date=curr_date)
    result = _limited_result(result, limit)
    text_limit = _positive_int(config, "news_max_text_chars", 4000)
    return format_news(result, title=f"Freedom Global News, {days}-day lookback through {curr_date}",
                       max_text_chars=text_limit)
