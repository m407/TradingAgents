"""Offline contract tests for structured Yahoo news records."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import tradingagents.dataflows.vendors.yahoo.news as ynews
from tradingagents.dataflows.errors import VendorUnavailableError


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
    assert records[0].published_at == datetime(2025, 5, 9, 12, tzinfo=UTC)
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


@pytest.mark.parametrize("raw", [
    {"id": "story", "content": None, "title": None, "summary": None, "publisher": None},
    {"content": {"id": "story", "title": None, "summary": None, "provider": None}},
])
def test_structured_records_keep_upstream_null_field_defaults(raw):
    record = ynews._article_record(raw)
    assert record.source_id == "story"
    assert record.title == "No title"
    assert record.publisher == "Unknown"
    assert record.text == "" and record.url == "" and record.published_at is None


def test_structured_ticker_feed_uses_tagged_search_fallback(monkeypatch):
    monkeypatch.setattr(ynews.yf, "Ticker", lambda symbol: SimpleNamespace(get_news=lambda **kw: []))
    monkeypatch.setattr(ynews.yf, "Search", lambda *args, **kwargs: SimpleNamespace(
        news=[{"id": "ours", "title": "Relevant", "relatedTickers": ["aapl"]},
              {"id": "other", "relatedTickers": ["MSFT"]}],
        quotes=[{"symbol": "AAPL"}],
    ))
    records = ynews.fetch_news_records_yfinance("AAPL")
    assert [record.source_id for record in records] == ["ours"]


def test_structured_ticker_outage_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(ynews, "yf_retry", lambda fn: None)
    with pytest.raises(VendorUnavailableError, match="no news response"):
        ynews.fetch_news_records_yfinance("AAPL")


def test_structured_global_outage_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(ynews, "yf_retry", lambda fn: None)
    with pytest.raises(VendorUnavailableError, match="no global news response"):
        list(ynews.fetch_global_news_records_yfinance(["markets"], 10))
