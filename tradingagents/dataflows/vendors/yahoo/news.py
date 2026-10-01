"""yfinance-based news data fetching functions."""

import contextlib
from collections.abc import Iterator
from datetime import datetime, timezone

import yfinance as yf
from dateutil.relativedelta import relativedelta

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.date_window import coverage_gap, in_window
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.news import NewsArticle
from tradingagents.dataflows.symbols import normalize_symbol
from tradingagents.dataflows.vendors.yahoo.ohlcv import yf_retry


def _extract_article_data(article: dict) -> dict:
    """Extract article data from yfinance news format (handles nested 'content' structure)."""
    if "content" in article:
        content = article["content"]
        title = content.get("title", "No title")
        summary = content.get("summary", "")
        provider = content.get("provider", {})
        publisher = provider.get("displayName", "Unknown")

        url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
        link = url_obj.get("url", "")

        pub_date_str = content.get("pubDate", "")
        pub_date = None
        if pub_date_str:
            with contextlib.suppress(ValueError, AttributeError):
                pub_date = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))

        return {
            "title": title,
            "summary": summary,
            "publisher": publisher,
            "link": link,
            "pub_date": pub_date,
        }
    else:
        # Fallback for flat structure. Parse epoch timestamps as aware UTC.
        pub_date = None
        ts = article.get("providerPublishTime")
        if ts:
            with contextlib.suppress(ValueError, OSError, TypeError):
                pub_date = datetime.fromtimestamp(ts, tz=timezone.utc)
        return {
            "title": article.get("title", "No title"),
            "summary": article.get("summary", ""),
            "publisher": article.get("publisher", "Unknown"),
            "link": article.get("link", ""),
            "pub_date": pub_date,
        }


def _article_record(article: dict) -> NewsArticle:
    """Extract a Yahoo article without imposing standalone presentation rules."""
    data = _extract_article_data(article)
    content = article.get("content", article)
    article_id = str(content.get("id") or article.get("id") or "")
    language = content.get("language")
    max_text_chars = get_config().get("news_max_text_chars", 4000)
    if isinstance(max_text_chars, bool) or not isinstance(max_text_chars, int) or max_text_chars <= 0:
        raise ValueError("invalid news_max_text_chars")
    return NewsArticle(
        source_id=article_id,
        delivery_sources=("yahoo",),
        publisher=data["publisher"],
        title=data["title"],
        text=data["summary"],
        url=data["link"],
        published_at=data["pub_date"],
        language=language,
        max_text_chars=max_text_chars,
    )


def fetch_news_records_yfinance(ticker: str) -> list[NewsArticle]:
    """Fetch structured ticker news records for adapters such as combined news."""
    canonical = normalize_symbol(ticker)
    article_limit = get_config()["news_article_limit"]
    stock = yf.Ticker(canonical)
    raw_news = yf_retry(lambda: stock.get_news(count=article_limit)) or []
    return [_article_record(article) for article in raw_news]


def fetch_global_news_records_yfinance(
    queries: list[str], limit: int
) -> Iterator[NewsArticle]:
    """Fetch structured global-news records, preserving distinct same-title items."""
    for query in queries:
        search = yf_retry(lambda q=query: yf.Search(
            query=q, news_count=limit, enable_fuzzy_query=True,
        ))
        yield from (_article_record(article) for article in (search.news or []))
def get_news_yfinance(
    ticker: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    Retrieve news for a specific stock ticker using yfinance.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        Formatted string containing news articles
    """
    # Query Yahoo with the canonical symbol, like every other yfinance path —
    # a raw broker/forex/crypto alias (XAUUSD, BTCUSD) otherwise silently
    # returns no news. Keep the user's ticker in the report header.
    canonical = normalize_symbol(ticker)
    resolved = "" if canonical == ticker else f" (resolved to {canonical})"
    try:
        records = fetch_news_records_yfinance(ticker)

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        news_str = ""
        filtered_count = 0

        for record in records:
            data = {"title": record.title, "summary": record.text,
                    "publisher": record.publisher, "link": record.url,
                    "pub_date": record.published_at}

            # Keep only articles within the requested window (look-ahead safe).
            if not in_window(data["pub_date"], start_dt, end_dt):
                continue

            news_str += f"### {data['title']} (source: {data['publisher']})\n"
            if data["summary"]:
                news_str += f"{data['summary']}\n"
            if data["link"]:
                news_str += f"Link: {data['link']}\n"
            news_str += "\n"
            filtered_count += 1

        if filtered_count == 0:
            gap = coverage_gap(
                (record.published_at for record in records),
                start_date, end_date, "Yahoo Finance news", f"news for {ticker}{resolved}",
            )
            return gap or f"No news found for {ticker}{resolved} between {start_date} and {end_date}"

        return f"## {ticker}{resolved} News, from {start_date} to {end_date}:\n\n{news_str}"

    except Exception as e:
        raise NoMarketDataError(ticker, ticker, f"news unavailable: {e}") from e


def get_global_news_yfinance(
    curr_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """
    Retrieve global/macro economic news using yfinance Search.

    Args:
        curr_date: Current date in yyyy-mm-dd format
        look_back_days: Number of days to look back. ``None`` falls back to
            ``global_news_lookback_days`` from the active config.
        limit: Maximum number of articles to return. ``None`` falls back to
            ``global_news_article_limit`` from the active config.

    Returns:
        Formatted string containing global news articles
    """
    config = get_config()
    if look_back_days is None:
        look_back_days = config["global_news_lookback_days"]
    if limit is None:
        limit = config["global_news_article_limit"]
    search_queries = config["global_news_queries"]

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = curr_dt - relativedelta(days=look_back_days)
    start_date = start_dt.strftime("%Y-%m-%d")

    in_window_news = []
    seen_titles = set()

    try:
        for article in fetch_global_news_records_yfinance(search_queries, limit):
            # Keep the standalone historical title-deduplication behavior.
            data = {"title": article.title, "summary": article.text,
                    "publisher": article.publisher, "link": article.url,
                    "pub_date": article.published_at}
            if not in_window(data["pub_date"], start_dt, curr_dt):
                continue
            if data["title"] and data["title"] not in seen_titles:
                seen_titles.add(data["title"])
                in_window_news.append(data)

            if len(in_window_news) >= limit:
                break

        news_str = ""
        for data in in_window_news[:limit]:
            news_str += f"### {data['title']} (source: {data['publisher']})\n"
            if data["summary"]:
                news_str += f"{data['summary']}\n"
            if data["link"]:
                news_str += f"Link: {data['link']}\n"
            news_str += "\n"

        # Nothing fell inside the window -> say so rather than return an
        # empty-bodied report (#993).
        if not news_str:
            # Results merge several fuzzy searches, so their timestamps prove no
            # continuous coverage; judge the window against the present only.
            gap = coverage_gap((), start_date, curr_date, "Yahoo Finance global news", "market news")
            return gap or f"No global news found between {start_date} and {curr_date}"

        return f"## Global Market News, from {start_date} to {curr_date}:\n\n{news_str}"

    except Exception as e:
        raise NoMarketDataError("global news", "global news", f"unavailable: {e}") from e
