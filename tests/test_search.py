from datetime import UTC, datetime

import httpx

from newsbrief.pipeline.search import (
    NewsSearchService,
    deduplicate_items,
    filter_already_sent,
    make_topic_key,
)
from newsbrief.providers.base import NewsItem


class StaticProvider:
    name = "fixture"

    def __init__(self, items: list[NewsItem]) -> None:
        self._items = items

    async def search(self, query: str) -> list[NewsItem]:
        del query
        return self._items


class FailedProvider:
    name = "offline"

    async def search(self, query: str) -> list[NewsItem]:
        del query
        request = httpx.Request("GET", "https://example.com/feed.xml")
        raise httpx.ConnectError("offline", request=request)


def _item(title: str, url: str, snippet: str = "", year: int = 2026) -> NewsItem:
    return NewsItem(
        title=title,
        url=url,
        source="Fonte",
        published_at=datetime(year, 1, 1, tzinfo=UTC),
        snippet=snippet,
    )


async def test_search_ranks_results_deduplicates_and_isolates_failures() -> None:
    older = _item("Outro assunto", "https://example.com/older", year=2025)
    relevant = _item("Python para notícias", "https://example.com/story?utm_source=rss")
    duplicate_url = _item("Título diferente", "https://example.com/story")
    service = NewsSearchService(
        [StaticProvider([older, relevant, duplicate_url]), FailedProvider()],
        max_results=5,
    )

    result = await service.search("Python notícias")

    assert result.items == [relevant, older]
    assert result.failed_sources == ["offline"]


def test_deduplicate_items_removes_similar_titles_and_preserves_distinct_urls() -> None:
    items = [
        _item("Empresa lança novo produto", "https://example.com/one"),
        _item("Empresa lança novo produto!", "https://example.net/two"),
        _item("Outro anúncio", "https://example.org/three"),
    ]

    assert [item.title for item in deduplicate_items(items)] == [
        "Empresa lança novo produto",
        "Outro anúncio",
    ]


def test_filter_already_sent_excludes_url_and_near_duplicate_titles() -> None:
    current = [
        _item("Nova notícia sobre segurança", "https://example.com/one"),
        _item("Lançamento de produto", "https://example.com/two"),
    ]

    result = filter_already_sent(
        current,
        {"f4ae6dd5b40f57c3972b0d5f7c0e67da305bd13e22eb1411f9f56f7e4c4e9a47"},
        ["Nova notícia sobre segurança!"],
    )

    assert [item.title for item in result] == ["Lançamento de produto"]
    assert make_topic_key("  Python   remoto ") == "python remoto"
