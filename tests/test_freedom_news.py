"""Offline contract tests for the bounded Freedom adapter."""

from datetime import datetime, timezone

import pytest
from requests import HTTPError, Response

import tradingagents.dataflows.vendors.freedom as freedom
from tradingagents.dataflows.news import NewsCoverage, NewsOutcome


class FakeClient:
    def __init__(self, pages=None, details=None, failures=None):
        self.calls = []
        self.pages = pages or [[{"id": "a"}]]
        self.details = details or {"a": {"providerAlias": "fbrokerkz", "title": "A",
            "text": "<p>body</p>", "url": "https://x/a", "dateTime": "2025-01-02T12:00:00",
            "timeZone": "Asia/Almaty", "lang": "kk"}}
        self.failures = failures or {}

    def authorized_request(self, command, params, version=3):
        self.calls.append((command, params, version))
        if command in self.failures:
            raise self.failures[command]
        if command == "getNewsList":
            return {"news": self.pages[min(params["skip"] // params["take"], len(self.pages) - 1)]}
        if command == "getNewsDetail":
            return self.details[str(params["id"])]
        raise AssertionError(command)


def config(**overrides):
    return {"freedom_news_page_size": 2, "freedom_news_max_pages": 3,
            "freedom_news_max_details": 4, "freedom_news_timeout_seconds": 3,
            **overrides}


def test_sdk_commands_provider_filter_and_bounded_parameters():
    client = FakeClient()
    result = freedom.fetch_freedom_news("AAPL", client=client,
        config=config(freedom_symbol_map={"AAPL": "AAPL.US"}))
    assert result.articles[0].text == "body"
    assert result.articles[0].language == "kk"
    assert result.articles[0].published_at == datetime(2025, 1, 2, 7, tzinfo=timezone.utc)
    assert result.articles[0].instrument == "AAPL.US"
    assert [call[0] for call in client.calls] == ["getNewsList", "getNewsDetail"]
    page = client.calls[0][1]
    assert page == {"provider": "fbrokerkz", "lang": "ru", "take": 2, "skip": 0,
                    "ticker": "AAPL.US"}
    assert all(call[2] == 3 for call in client.calls)


def test_foreign_provider_never_enters_results_and_marks_partial():
    client = FakeClient(details={"a": {"providerAlias": "oninvest", "title": "wrong"}})
    result = freedom.fetch_freedom_news(client=client, config=config(freedom_news_page_size=1))
    assert not result.articles and result.truncated
    assert "Unverified provider" in result.reasons[0]


def test_unknown_ticker_does_not_make_any_sdk_call():
    client = FakeClient()
    result = freedom.fetch_freedom_news("AAPL", client=client, config=config())
    assert result.outcome == NewsOutcome.UNAVAILABLE
    assert not client.calls


def test_unknown_timezone_is_undated_and_unqualified_requires_mapping():
    client = FakeClient(details={"a": {"providerAlias": "fbrokerkz", "title": "A",
        "dateTime": "2025-01-02T12:00:00", "timeZone": "Not/AZone"}})
    result = freedom.fetch_freedom_news(client=client, config=config(freedom_news_page_size=1))
    assert result.articles[0].published_at is None
    assert freedom.fetch_freedom_news("AAPL.US", client=FakeClient(), config=config()).articles


def test_repeated_page_stops_and_marks_truncated():
    client = FakeClient(pages=[[{"id": "a"}, {"id": "b"}], [{"id": "a"}, {"id": "b"}]])
    client.details.update({"b": {"providerAlias": "fbrokerkz", "title": "B"}})
    result = freedom.fetch_freedom_news(client=client, config=config())
    assert len([c for c in client.calls if c[0] == "getNewsList"]) == 2
    assert result.truncated


def test_budget_and_none_optional_defaults():
    client = FakeClient(pages=[[{"id": str(i)} for i in range(5)]])
    client.details.update({str(i): {"providerAlias": "fbrokerkz", "title": str(i)} for i in range(5)})
    result = freedom.fetch_freedom_news(start_date=None, end_date=None, client=client,
        config=config(freedom_news_page_size=5, freedom_news_max_details=2))
    assert len(result.articles) == 2 and result.truncated
    global_client = FakeClient()
    monkeypatch_result = freedom.fetch_freedom_news(client=global_client, config=config())
    assert monkeypatch_result.coverage == NewsCoverage.UNKNOWN


@pytest.mark.parametrize("status,needle", [
    (401, "401/403"), (403, "401/403"), (404, "HTTP 404"), (429, "429"), (500, "HTTP 500"),
])
def test_http_failures_sanitized(status, needle, caplog):
    response = Response()
    response.status_code = status
    response._content = b"SECRET PRIVATE KEY payload"
    exc = HTTPError("SECRET PRIVATE KEY", response=response)
    result = freedom.fetch_freedom_news(client=FakeClient(failures={"getNewsList": exc}),
                                        config=config())
    assert result.outcome == NewsOutcome.UNAVAILABLE
    assert needle in result.reasons[0]
    assert "SECRET" not in caplog.text and "PRIVATE KEY" not in str(result.reasons)


def test_credentials_are_lazy_and_missing_credentials_are_safe(monkeypatch):
    monkeypatch.delenv("TRADERNET_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("TRADERNET_PRIVATE_KEY", raising=False)
    result = freedom.fetch_freedom_news(config=config())
    assert result.outcome == NewsOutcome.UNAVAILABLE
    assert "credentials" in result.reasons[0]


def test_malformed_responses_and_partial_page_failure_keep_prior_data():
    client = FakeClient(pages=[[{"id": "a"}], [{"id": "b"}]])
    original = client.authorized_request
    def request(command, params, version=3):
        if command == "getNewsList" and params["skip"]:
            raise RuntimeError("secret-bearing payload")
        return original(command, params, version)
    client.authorized_request = request
    result = freedom.fetch_freedom_news(
        client=client, config=config(freedom_news_page_size=1)
    )
    assert len(result.articles) == 1 and result.truncated
    assert "secret-bearing" not in str(result.reasons)
    malformed = FakeClient()
    malformed.authorized_request = lambda command, params, version=3: "bad"
    assert freedom.fetch_freedom_news(client=malformed, config=config()).outcome == NewsOutcome.UNAVAILABLE


@pytest.mark.parametrize("bad_response", [{}, {"errMsg": "failure"}, {"news": "not a list"}])
def test_sdk_error_and_malformed_page_envelopes_fail_closed(bad_response):
    client = FakeClient()
    original = client.authorized_request

    def request(command, params, version=3):
        if command == "getNewsList":
            return bad_response
        return original(command, params, version)

    client.authorized_request = request
    result = freedom.fetch_freedom_news(client=client, config=config())
    assert result.outcome == NewsOutcome.UNAVAILABLE
    assert result.truncated and result.reasons
    assert not result.articles


@pytest.mark.parametrize("bad_response", [{}, {"errMsg": "failure"}, {"items": None}])
def test_sdk_error_and_malformed_later_page_preserve_prior_article(bad_response):
    client = FakeClient(pages=[[{"id": "a"}], [{"id": "b"}]])
    original = client.authorized_request

    def request(command, params, version=3):
        if command == "getNewsList" and params["skip"]:
            return bad_response
        return original(command, params, version)

    client.authorized_request = request
    result = freedom.fetch_freedom_news(
        client=client, config=config(freedom_news_page_size=1)
    )
    assert [article.source_id for article in result.articles] == ["a"]
    assert result.outcome == NewsOutcome.OK
    assert result.truncated and result.reasons


@pytest.mark.parametrize("limit_arg", ["omitted", None])
def test_global_news_uses_config_limit_when_limit_is_omitted_or_none(monkeypatch, limit_arg):
    rows = tuple(
        freedom.NewsArticle(str(index), ("freedom",), "Freedom", str(index), "", "",
                            None)
        for index in range(4)
    )
    monkeypatch.setattr(freedom, "get_config", lambda: {
        "global_news_lookback_days": 3,
        "global_news_article_limit": 2,
    })
    monkeypatch.setattr(freedom, "fetch_freedom_news", lambda **kwargs: freedom.NewsSourceResult(
        rows, NewsOutcome.OK, NewsCoverage.UNKNOWN
    ))
    if limit_arg == "omitted":
        result = freedom.get_global_news_freedom("2025-01-03")
    else:
        result = freedom.get_global_news_freedom("2025-01-03", limit=None)
    assert result.count("### ") == 2
    assert "Coverage: unknown" in result
    assert "truncated: yes" in result


def test_standalone_ticker_wrapper_formats_structured_status_and_applies_config_limit(monkeypatch):
    rows = tuple(
        freedom.NewsArticle(str(index), ("freedom",), "Freedom Finance", f"Article {index}",
                            "body", f"https://example.test/{index}",
                            datetime(2025, 1, 2, 12, tzinfo=timezone.utc))
        for index in range(3)
    )
    monkeypatch.setattr(freedom, "get_config", lambda: {"news_article_limit": 1})
    calls = []

    def fetch(ticker, start_date, end_date):
        calls.append((ticker, start_date, end_date))
        return freedom.NewsSourceResult(rows, NewsOutcome.OK, NewsCoverage.UNKNOWN,
                                        reasons=("coverage is unknown",))

    monkeypatch.setattr(freedom, "fetch_freedom_news", fetch)
    rendered = freedom.get_news_freedom("AAPL.US", "2025-01-01", "2025-01-03")
    assert calls == [("AAPL.US", "2025-01-01", "2025-01-03")]
    assert "Article 0" in rendered and "Article 1" not in rendered
    assert "channel: freedom" in rendered
    assert "Coverage: unknown" in rendered and "truncated: yes" in rendered


def test_standalone_global_wrapper_honors_explicit_limit(monkeypatch):
    rows = tuple(
        freedom.NewsArticle(str(index), ("freedom",), "Freedom Finance", f"Article {index}",
                            "body", f"https://example.test/{index}", None)
        for index in range(3)
    )
    monkeypatch.setattr(freedom, "get_config", lambda: {
        "global_news_lookback_days": 3, "global_news_article_limit": 3,
    })
    monkeypatch.setattr(freedom, "fetch_freedom_news", lambda **kwargs: freedom.NewsSourceResult(
        rows, NewsOutcome.OK, NewsCoverage.UNKNOWN
    ))
    rendered = freedom.get_global_news_freedom("2025-01-03", limit=1)
    assert rendered.count("### ") == 1
    assert "truncated: yes" in rendered


def test_transport_enforces_timeout_no_redirect_and_suppresses_sdk_logs(monkeypatch):
    instance = freedom._NewsTradernet("public", "private", 7)
    calls = []
    class Session:
        def request(self, *args, **kwargs):
            calls.append(kwargs)
            response = Response()
            response.status_code = 302
            return response
    monkeypatch.setattr(freedom, "HTTPSession", Session)
    with pytest.raises(RuntimeError, match="redirect refused"):
        instance.request("GET", "https://tradernet.global")
    assert calls[0]["timeout"] == 7 and calls[0]["allow_redirects"] is False
    assert instance.logger.disabled


def test_live_api_envelope_offset_and_integer_detail_id_without_discovery():
    class Client:
        def __init__(self):
            self.calls = []

        def authorized_request(self, command, params, version=3):
            self.calls.append((command, params, version))
            if command == "getNewsList":
                return {"list": [{"id": 48532103}], "total": 1, "take": 2, "skip": 0}
            if command == "getNewsDetail":
                assert params == {"id": 48532103}
                return {"id": 48532103, "providerAlias": "fbrokerkz", "title": "Market news",
                        "text": "<p>News body</p>", "dateTime": "2026-09-30 20:55:27",
                        "timeZone": "+0300", "lang": "ru"}
            raise AssertionError(f"Unexpected command: {command}")

    client = Client()
    result = freedom.fetch_freedom_news(start_date="2026-09-30", end_date="2026-09-30",
                                       client=client, config=config())
    assert result.outcome == NewsOutcome.OK
    assert not result.reasons and not result.truncated
    assert len(result.articles) == 1
    assert result.articles[0].published_at == datetime(2026, 9, 30, 17, 55, 27, tzinfo=timezone.utc)
    assert result.articles[0].text == "News body"
    assert [call[0] for call in client.calls] == ["getNewsList", "getNewsDetail"]


@pytest.mark.parametrize("zone, expected", [
    ("+0300", datetime(2025, 1, 1, 21, 30, tzinfo=timezone.utc)),
    ("-0430", datetime(2025, 1, 2, 5, tzinfo=timezone.utc)),
    ("+03:00", datetime(2025, 1, 1, 21, 30, tzinfo=timezone.utc)),
    ("+2500", None),
    ("+bad", None),
])
def test_numeric_timezone_offsets_filter_at_utc_day_boundary(zone, expected):
    published = freedom._published({"dateTime": "2025-01-02 00:30:00", "timeZone": zone})
    assert (published.astimezone(timezone.utc) if published else None) == expected


def test_live_empty_list_is_empty_not_unavailable():
    client = FakeClient()
    client.authorized_request = lambda command, params, version=3: {
        "list": [], "total": 0, "take": 2, "skip": 0,
    }
    result = freedom.fetch_freedom_news("FRHC.US", client=client, config=config())
    assert result.outcome == NewsOutcome.EMPTY
    assert not result.articles and not result.reasons and not result.truncated
