from datetime import datetime, timezone

from tradingagents.dataflows.news import (
    NewsArticle,
    NewsCoverage,
    NewsOutcome,
    NewsSourceResult,
    clean_text,
    filter_news_window,
    format_news,
    merge_news,
)


def article(source, ident, title, url, day, text="body"):
    return NewsArticle(ident, (source,), source.title(), title, text, url,
                       datetime.fromisoformat(day).replace(tzinfo=timezone.utc))


def test_window_is_utc_half_open_and_excludes_next_midnight():
    rows = [article("yahoo", "a", "in", "https://x/a", "2025-01-02T23:59:59"),
            article("yahoo", "b", "out", "https://x/b", "2025-01-03T00:00:00")]
    assert [a.title for a in filter_news_window(rows, datetime(2025, 1, 1),
        datetime(2025, 1, 2))] == ["in"]


def test_url_tracking_duplicate_preserves_both_provenance_and_freedom_text():
    f = article("freedom", "f1", "Title", "https://X.test/story?utm_source=f", "2025-01-02T12:00:00", "<b>full</b>")
    y = article("yahoo", "y1", "Different headline", "https://x.test/story?gclid=abc", "2025-01-02T11:00:00", "summary")
    got = merge_news([f], [y], 2)
    assert len(got) == 1
    assert got[0].delivery_sources == ("freedom", "yahoo")
    assert got[0].text == "full"
    rendered = format_news(got)
    assert "channel: freedom, yahoo" in rendered and "Freedom" in rendered and got[0].url in rendered


def test_duplicate_uses_yahoo_summary_when_freedom_text_is_empty():
    f = article("freedom", "f1", "Freedom title", "https://x/story", "2025-01-02T12:00:00", "")
    y = article("yahoo", "y1", "Yahoo title", "https://x/story", "2025-01-02T11:00:00", "<p>Yahoo summary</p>")
    got = merge_news([f], [y], 2)
    assert len(got) == 1
    assert got[0].title == "Freedom title"
    assert got[0].text == "Yahoo summary"
    assert got[0].delivery_sources == ("freedom", "yahoo")


def test_same_title_different_urls_are_not_duplicates_and_round_robin():
    f = [article("freedom", "f", "same", "https://x/f", "2025-01-02T12:00:00")]
    y = [article("yahoo", "y", "same", "https://x/y", "2025-01-02T13:00:00")]
    assert {a.url for a in merge_news(f, y, 2)} == {"https://x/f", "https://x/y"}


def test_limit_one_selects_freshest_and_ties_prefer_freedom():
    f = article("freedom", "f", "f", "https://x/f", "2025-01-02T12:00:00")
    y = article("yahoo", "y", "y", "https://x/y", "2025-01-02T12:00:00")
    assert merge_news([f], [y], 1)[0].source_id == "f"
    newer = article("yahoo", "n", "newer", "https://x/n", "2025-01-03T12:00:00")
    assert merge_news([f], [newer], 1)[0].source_id == "n"
    reverse_url = article("yahoo", "z", "same time", "https://x/a", "2025-01-02T12:00:00")
    reverse_freedom_url = article("freedom", "f2", "same time", "https://x/z", "2025-01-02T12:00:00")
    assert merge_news([reverse_freedom_url], [reverse_url], 1)[0].source_id == "f2"


def test_duplicate_source_id_collapses_even_with_different_urls():
    older = article("yahoo", "duplicate", "old", "https://x/a", "2025-01-01T12:00:00")
    newer = article("yahoo", "duplicate", "new", "https://x/z", "2025-01-02T12:00:00")
    assert merge_news([], [older, newer], 5) == [newer]


def test_bounded_cleaning_and_partial_status_survive_articles():
    assert clean_text("<script>x</script><p>hello   world</p>", 5) == "x hel"
    result = NewsSourceResult((article("yahoo", "a", "t", "https://x", "2025-01-02T00:00:00"),),
                              NewsOutcome.OK, NewsCoverage.UNKNOWN, True, ("budget reached",))
    assert result.articles and result.coverage == NewsCoverage.UNKNOWN and result.truncated
    rendered = format_news(result)
    assert "published:" in rendered
    assert "Coverage: unknown" in rendered and "truncated: yes" in rendered
    assert "budget reached" in rendered


def test_empty_unknown_coverage_is_not_rendered_as_confirmed_absence():
    result = NewsSourceResult((), NewsOutcome.EMPTY, NewsCoverage.UNKNOWN)
    rendered = format_news(result)
    assert "do not establish absence" in rendered
    assert "Coverage: unknown" in rendered
    observed_empty = NewsSourceResult((), NewsOutcome.EMPTY, NewsCoverage.OBSERVED)
    assert "observed coverage" in format_news(observed_empty)
