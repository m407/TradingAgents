from datetime import datetime, timezone

from tradingagents.dataflows.news import NewsArticle, NewsCoverage, NewsOutcome, NewsSourceResult
from tradingagents.dataflows.vendors import combined_news as combined


def article(source, ident, url, day="2025-01-02T12:00:00"):
    return NewsArticle(ident, (source,), source, ident, "text", url,
                       datetime.fromisoformat(day).replace(tzinfo=timezone.utc))


def result(*articles, outcome=NewsOutcome.OK, truncated=False, reasons=()):
    return NewsSourceResult(tuple(articles), outcome, NewsCoverage.UNKNOWN,
                            truncated, reasons)


def test_both_sources_called_even_when_freedom_raises(monkeypatch):
    calls = []

    def freedom(*args, **kwargs):
        calls.append("freedom")
        raise RuntimeError("sensitive payload")

    def yahoo(*args):
        calls.append("yahoo")
        return [article("yahoo", "y", "https://x/y")]

    monkeypatch.setattr(combined, "fetch_freedom_news", freedom)
    monkeypatch.setattr(combined, "fetch_news_records_yfinance", yahoo)
    got = combined.fetch_combined_news("AAPL", "2025-01-01", "2025-01-02",
                                       config={"news_article_limit": 10})
    assert calls == ["freedom", "yahoo"]
    assert [a.source_id for a in got.articles] == ["y"]
    assert got.reasons == ("Freedom news request failed",)


def test_both_public_wrappers_return_safe_data_unavailable(monkeypatch):
    ticker_calls = []

    def ticker_freedom(*args, **kwargs):
        ticker_calls.append("freedom")
        raise RuntimeError("secret")

    def ticker_yahoo(*args):
        ticker_calls.append("yahoo")
        raise RuntimeError("token")

    monkeypatch.setattr(combined, "fetch_freedom_news", ticker_freedom)
    monkeypatch.setattr(combined, "fetch_news_records_yfinance", ticker_yahoo)
    ticker_message = combined.get_news_combined("AAPL", "2025-01-01", "2025-01-02")
    assert ticker_calls == ["freedom", "yahoo"]
    assert "DATA_UNAVAILABLE" in ticker_message
    assert "secret" not in ticker_message and "token" not in ticker_message
    assert "invalid" not in ticker_message and "NO_DATA_AVAILABLE" not in ticker_message

    global_calls = []

    def global_freedom(**kwargs):
        global_calls.append("freedom")
        raise RuntimeError("private")

    def global_yahoo(*args):
        global_calls.append("yahoo")
        raise RuntimeError("credential")

    monkeypatch.setattr(combined, "fetch_freedom_news", global_freedom)
    monkeypatch.setattr(combined, "fetch_global_news_records_yfinance", global_yahoo)
    global_message = combined.get_global_news_combined("2025-01-02")
    assert global_calls == ["freedom", "yahoo"]
    assert "DATA_UNAVAILABLE" in global_message
    assert "private" not in global_message and "credential" not in global_message
    assert "invalid" not in global_message and "NO_DATA_AVAILABLE" not in global_message


def test_successful_empty_and_partial_status_are_preserved(monkeypatch):
    monkeypatch.setattr(combined, "fetch_freedom_news",
                        lambda *a, **k: result(outcome=NewsOutcome.EMPTY))
    monkeypatch.setattr(combined, "fetch_news_records_yfinance", lambda *a: [])
    empty = combined.fetch_combined_news("AAPL", "2025-01-01", "2025-01-02")
    assert empty.outcome == NewsOutcome.EMPTY
    monkeypatch.setattr(combined, "fetch_freedom_news",
                        lambda *a, **k: result(outcome=NewsOutcome.UNAVAILABLE,
                                               truncated=True, reasons=("unconfigured",)))
    monkeypatch.setattr(combined, "fetch_news_records_yfinance",
                        lambda *a: [article("yahoo", "y", "https://x/y")])
    partial = combined.fetch_combined_news("AAPL", "2025-01-01", "2025-01-02")
    assert partial.articles and partial.truncated and partial.reasons == ("unconfigured",)


def test_global_defaults_and_common_limit_after_dedup(monkeypatch):
    f = article("freedom", "f", "https://x/shared?utm_source=f")
    ydup = article("yahoo", "ydup", "https://x/shared?gclid=x")
    y = article("yahoo", "y", "https://x/y", "2025-01-02T11:00:00")
    seen = {}
    monkeypatch.setattr(combined, "fetch_freedom_news", lambda **kwargs: result(f))

    def yahoo(queries, limit):
        seen.update(queries=queries, limit=limit)
        return iter([ydup, y])

    monkeypatch.setattr(combined, "fetch_global_news_records_yfinance", yahoo)
    got = combined.fetch_combined_global_news("2025-01-02", config={
        "global_news_lookback_days": 7, "global_news_article_limit": 2,
        "global_news_queries": ["markets"],
    })
    assert seen == {"queries": ["markets"], "limit": 2}
    assert len(got.articles) == 2  # duplicate collapsed before the shared limit
    assert got.articles[0].delivery_sources == ("freedom", "yahoo")


def test_global_failure_still_attempts_both_sources(monkeypatch):
    calls = []

    def freedom(**kwargs):
        calls.append("freedom")
        raise ValueError("private")

    def yahoo(*args):
        calls.append("yahoo")
        return iter([])

    monkeypatch.setattr(combined, "fetch_freedom_news", freedom)
    monkeypatch.setattr(combined, "fetch_global_news_records_yfinance", yahoo)
    got = combined.fetch_combined_global_news("2025-01-02", config={})
    assert calls == ["freedom", "yahoo"]
    assert got.outcome == NewsOutcome.UNAVAILABLE and not got.articles
