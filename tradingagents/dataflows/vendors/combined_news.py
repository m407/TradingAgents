"""Independent Yahoo and Freedom news aggregation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.news import (
    NewsArticle,
    NewsCoverage,
    NewsOutcome,
    NewsSourceResult,
    filter_news_window,
    format_news,
    merge_news,
)
from tradingagents.dataflows.vendors.freedom import fetch_freedom_news
from tradingagents.dataflows.vendors.yahoo.news import (
    fetch_global_news_records_yfinance,
    fetch_news_records_yfinance,
)


def _unavailable(reason: str) -> NewsSourceResult:
    return NewsSourceResult(outcome=NewsOutcome.UNAVAILABLE,
                            coverage=NewsCoverage.UNKNOWN, reasons=(reason,))


def _fetch(fetcher: Callable[[], NewsSourceResult | list[NewsArticle] | tuple[NewsArticle, ...]],
           source: str) -> NewsSourceResult:
    """Convert adapter exceptions to safe source-local failures."""
    try:
        result = fetcher()
        if isinstance(result, NewsSourceResult):
            return result
        articles = tuple(result)
        return NewsSourceResult(articles, NewsOutcome.OK if articles else NewsOutcome.EMPTY,
                                NewsCoverage.UNKNOWN)
    except Exception:
        # Do not expose exception text: adapters may include payloads or credentials.
        return _unavailable(f"{source} news request failed")


def _combine(freedom: NewsSourceResult, yahoo: NewsSourceResult, limit: int,
             start: datetime, end: datetime) -> NewsSourceResult:
    articles = merge_news(freedom.articles, yahoo.articles, limit, start=start, end=end)
    failures = [result for result in (freedom, yahoo)
                if result.outcome == NewsOutcome.UNAVAILABLE]
    reasons = tuple(dict.fromkeys(reason for result in (freedom, yahoo)
                                  for reason in result.reasons))
    truncated = any(result.truncated for result in (freedom, yahoo))
    if articles:
        outcome = NewsOutcome.OK
    elif failures:
        # Successful empty + failed source is partial, not evidence of no news.
        outcome = NewsOutcome.UNAVAILABLE
    else:
        outcome = NewsOutcome.EMPTY
    return NewsSourceResult(tuple(articles), outcome, NewsCoverage.UNKNOWN,
                            truncated, reasons)


def fetch_combined_news(ticker: str, start_date: str, end_date: str,
                        *, config: dict | None = None) -> NewsSourceResult:
    """Fetch each ticker source independently and apply one post-dedup limit."""
    config = config or get_config()
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    limit = config.get("news_article_limit", 20)
    freedom = _fetch(lambda: fetch_freedom_news(ticker, start_date, end_date,
                                                config=config), "Freedom")
    yahoo = _fetch(lambda: fetch_news_records_yfinance(ticker), "Yahoo")
    yahoo_rows = filter_news_window(yahoo.articles, start, end)
    yahoo = NewsSourceResult(tuple(yahoo_rows),
                             yahoo.outcome if yahoo_rows
                             else (NewsOutcome.EMPTY if yahoo.outcome != NewsOutcome.UNAVAILABLE
                                   else NewsOutcome.UNAVAILABLE),
                             yahoo.coverage, yahoo.truncated, yahoo.reasons)
    return _combine(freedom, yahoo, limit, start, end)


def fetch_combined_global_news(curr_date: str, look_back_days: int | None = None,
                               limit: int | None = None, *,
                               config: dict | None = None) -> NewsSourceResult:
    """Fetch global news independently; default lookback and limit from config."""
    config = config or get_config()
    days = look_back_days if look_back_days is not None else config.get("global_news_lookback_days", 7)
    limit = limit if limit is not None else config.get("global_news_article_limit", 10)
    end = datetime.fromisoformat(curr_date)
    start = end - timedelta(days=days)
    freedom = _fetch(lambda: fetch_freedom_news(start_date=start.date().isoformat(),
                                               end_date=curr_date, config=config), "Freedom")
    yahoo = _fetch(lambda: tuple(fetch_global_news_records_yfinance(
        config.get("global_news_queries", []), limit)), "Yahoo")
    yahoo_rows = filter_news_window(yahoo.articles, start, end)
    yahoo = NewsSourceResult(tuple(yahoo_rows), yahoo.outcome if yahoo_rows else
                             (NewsOutcome.EMPTY if yahoo.outcome != NewsOutcome.UNAVAILABLE
                              else NewsOutcome.UNAVAILABLE), yahoo.coverage,
                             yahoo.truncated, yahoo.reasons)
    return _combine(freedom, yahoo, limit, start, end)


def get_news_combined(ticker: str, start_date: str, end_date: str) -> str:
    result = fetch_combined_news(ticker, start_date, end_date)
    if result.outcome == NewsOutcome.UNAVAILABLE and not result.articles:
        return _data_unavailable(result)
    return format_news(result, title=f"Combined News for {ticker}, {start_date} to {end_date}",
                       max_text_chars=config_text_limit())


def get_global_news_combined(curr_date: str, look_back_days: int | None = None,
                             limit: int | None = None) -> str:
    result = fetch_combined_global_news(curr_date, look_back_days, limit)
    if result.outcome == NewsOutcome.UNAVAILABLE and not result.articles:
        return _data_unavailable(result)
    return format_news(result, title=f"Combined Global News through {curr_date}",
                       max_text_chars=config_text_limit())


def config_text_limit() -> int:
    value = get_config().get("news_max_text_chars", 4000)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("invalid news_max_text_chars")
    return value


def _data_unavailable(result: NewsSourceResult) -> str:
    detail = "; ".join(result.reasons) or "both news sources are unavailable"
    return ("DATA_UNAVAILABLE: no configured news source could be retrieved "
            f"({detail}). This says nothing about the instrument; report the data as "
            "unavailable and do not estimate or fabricate news.")
