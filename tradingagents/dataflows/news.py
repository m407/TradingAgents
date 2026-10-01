"""Shared structured news records and deterministic merge helpers."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tradingagents.dataflows.date_window import in_window, to_utc


class NewsOutcome(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


class NewsCoverage(StrEnum):
    UNKNOWN = "unknown"
    OBSERVED = "observed"


@dataclass(frozen=True)
class NewsArticle:
    """One article, retaining its delivery provenance and observed metadata."""

    source_id: str
    delivery_sources: tuple[str, ...]
    publisher: str
    title: str
    text: str
    url: str
    published_at: datetime | None
    language: str | None = None
    instrument: str | None = None
    max_text_chars: int = 4000

    def __post_init__(self) -> None:
        if (isinstance(self.max_text_chars, bool) or not isinstance(self.max_text_chars, int)
                or self.max_text_chars <= 0):
            raise ValueError("max_text_chars must be a positive integer")
        if self.published_at is not None:
            object.__setattr__(self, "published_at", to_utc(self.published_at))
        object.__setattr__(self, "text", clean_text(self.text, self.max_text_chars))
        object.__setattr__(self, "url", normalize_url(self.url))


@dataclass(frozen=True)
class NewsSourceResult:
    articles: tuple[NewsArticle, ...] = ()
    outcome: NewsOutcome = NewsOutcome.OK
    coverage: NewsCoverage = NewsCoverage.UNKNOWN
    truncated: bool = False
    reasons: tuple[str, ...] = ()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_text(value: str | None, max_chars: int = 4000) -> str:
    """Strip markup and bound source-controlled text before it reaches a model."""
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    parser = _TextExtractor()
    parser.feed(value or "")
    text = html.unescape(" ".join(parser.parts))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def normalize_url(url: str) -> str:
    """Canonicalize URL for conservative cross-provider deduplication."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.port:
        host = f"{host}:{parts.port}"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in {"gclid", "fbclid"} and not k.lower().startswith("utm_")
    ]
    return urlunsplit((parts.scheme.lower(), host, parts.path, urlencode(query), ""))


def filter_news_window(articles: Iterable[NewsArticle], start: datetime,
                       end: datetime, *, exclude_undated: bool = True) -> list[NewsArticle]:
    """Keep articles in the existing half-open UTC date window."""
    start_utc, end_utc = to_utc(start), to_utc(end)
    return [a for a in articles if (a.published_at is not None or not exclude_undated)
            and in_window(a.published_at, start_utc, end_utc)]


def _sort_key(article: NewsArticle) -> tuple:
    # None is deliberately last; stable final key makes selection reproducible.
    timestamp = article.published_at.timestamp() if article.published_at else float("-inf")
    return (-timestamp, article.url, article.source_id)


def merge_news(freedom: Iterable[NewsArticle], yahoo: Iterable[NewsArticle],
               limit: int, *, start: datetime | None = None,
               end: datetime | None = None) -> list[NewsArticle]:
    """Deduplicate URLs and alternate source selection before one shared limit."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    def eligible(items: Iterable[NewsArticle]) -> list[NewsArticle]:
        values = list(items)
        if start is not None and end is not None:
            values = filter_news_window(values, start, end)
        return sorted(values, key=_sort_key)

    f_items, y_items = eligible(freedom), eligible(yahoo)
    f_items = _dedupe_source_ids(f_items)
    y_items = _dedupe_source_ids(y_items)
    by_url = merge_by_url(f_items, y_items)
    if limit == 1:
        combined = list(by_url.values())
        freshest = max((a.published_at for a in combined if a.published_at is not None), default=None)
        tied = [a for a in combined if a.published_at == freshest] if freshest else []
        if tied:
            return [min(tied, key=lambda a: (0 if "freedom" in a.delivery_sources else 1,
                                              a.url, a.source_id))]
        return sorted(combined, key=lambda a: (0 if "freedom" in a.delivery_sources else 1,
                                                a.url, a.source_id))[:1]
    def key_for(a: NewsArticle) -> str:
        return a.url or f"{a.delivery_sources}:{a.source_id}"

    f_keys = [key_for(a) for a in f_items]
    y_keys = [key_for(a) for a in y_items]
    chosen: list[NewsArticle] = []
    chosen_keys: set[str] = set()
    cursors = [0, 0]
    streams = [f_keys, y_keys]
    for index in range(limit):
        stream_idx = index % 2
        for candidate_idx in (stream_idx, 1 - stream_idx):
            while cursors[candidate_idx] < len(streams[candidate_idx]):
                key = streams[candidate_idx][cursors[candidate_idx]]
                cursors[candidate_idx] += 1
                if key not in chosen_keys:
                    chosen_keys.add(key)
                    chosen.append(by_url[key])
                    break
            else:
                continue
            break
        else:
            break
    return sorted(chosen, key=_sort_key)


def _dedupe_source_ids(items: list[NewsArticle]) -> list[NewsArticle]:
    """Keep the deterministically preferred record for each nonempty source ID."""
    result: dict[str, NewsArticle] = {}
    anonymous: list[NewsArticle] = []
    for item in sorted(items, key=_sort_key):
        if not item.source_id:
            anonymous.append(item)
        else:
            result.setdefault(item.source_id, item)
    return sorted([*result.values(), *anonymous], key=_sort_key)


def merge_by_url(f_items: list[NewsArticle], y_items: list[NewsArticle]) -> dict[str, NewsArticle]:
    """Collapse URL matches while retaining both delivery channels."""
    by_url: dict[str, NewsArticle] = {}
    for item in f_items + y_items:
        key = item.url or f"{item.delivery_sources}:{item.source_id}"
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = item
        else:
            sources = tuple(dict.fromkeys(existing.delivery_sources + item.delivery_sources))
            # Prefer Freedom's body/publisher for a cross-provider URL duplicate.
            preferred = existing if "freedom" in existing.delivery_sources else item
            fallback = item if preferred is existing else existing
            text = preferred.text or fallback.text
            by_url[key] = NewsArticle(preferred.source_id, sources, preferred.publisher,
                preferred.title, text, preferred.url, preferred.published_at,
                preferred.language, preferred.instrument)

    return by_url


def format_news(articles: Iterable[NewsArticle] | NewsSourceResult, *, title: str = "News",
                max_text_chars: int = 4000) -> str:
    """Format articles and retain coverage/partial-result status when available."""
    result = articles if isinstance(articles, NewsSourceResult) else None
    rows = list(result.articles if result else articles)
    status = ""
    if result:
        status = (f"\n\nCoverage: {result.coverage.value}; outcome: {result.outcome.value}; "
                  f"truncated: {'yes' if result.truncated else 'no'}")
        if result.reasons:
            safe_reasons = "; ".join(clean_text(reason, 240) for reason in result.reasons[:5])
            status += f"; reasons: {safe_reasons}"
    if not rows:
        empty = ("No articles found in the observed coverage." if result and
                 result.coverage == NewsCoverage.OBSERVED and result.outcome == NewsOutcome.EMPTY
                 else "No articles available; empty results do not establish absence of news.")
        return f"## {title}\n\n{empty}{status}"
    blocks = [f"## {title}"]
    for article in rows:
        channels = ", ".join(article.delivery_sources) or "unknown"
        when = article.published_at.isoformat() if article.published_at else "undated"
        blocks.append(f"### {clean_text(article.title, 300)} (source: {clean_text(article.publisher, 200)}; channel: {channels}; published: {when})\n"
                      f"{clean_text(article.text, max_text_chars)}\nURL: {article.url or 'unavailable'}")
    return "\n\n".join(blocks) + status
