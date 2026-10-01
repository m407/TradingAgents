"""Offline contract tests for structured Yahoo news records."""

from datetime import datetime, timezone

import tradingagents.dataflows.vendors.yahoo.news as ynews


def test_ticker_fetch_returns_structured_records(monkeypatch):
    monkeypatch.setattr(ynews, "get_config", lambda: {"news_article_limit": 10})

    class FakeTicker:
        def __init__(self, symbol):
            assert symbol == "AAPL"

        def get_news(self, count):
            return [{"content": {
                "id": "story-1", "title": "Same title", "summary": "Body",
                "provider": {"displayName": "Publisher"},
                "canonicalUrl": {"url": "https://example.test/a"},
                "pubDate": "2025-05-09T12:00:00Z", "language": "en",
            }}]

    monkeypatch.setattr(ynews.yf, "Ticker", FakeTicker)
    records = ynews.fetch_news_records_yfinance("AAPL")
    assert records[0].source_id == "story-1"
    assert records[0].delivery_sources == ("yahoo",)
    assert records[0].published_at == datetime(2025, 5, 9, 12, tzinfo=timezone.utc)
    assert records[0].text == "Body"


def test_global_fetch_keeps_distinct_same_title_records(monkeypatch):
    class FakeSearch:
        def __init__(self, query, **kwargs):
            assert kwargs["enable_fuzzy_query"] is True
            self.news = [{"id": query, "title": "Same title", "summary": "Body",
                          "publisher": "Publisher", "link": f"https://example.test/{query}",
                          "providerPublishTime": 1746792000}]

    monkeypatch.setattr(ynews.yf, "Search", FakeSearch)
    records = list(ynews.fetch_global_news_records_yfinance(["one", "two"], 10))
    assert len(records) == 2
    assert records[0].title == records[1].title
    assert records[0].url != records[1].url
