import asyncio
from dataclasses import dataclass
from datetime import UTC

import httpx
import structlog
from rapidfuzz.fuzz import ratio

from newsbrief.pipeline.normalize import tokenize_words, topic_key, url_hash
from newsbrief.providers.base import NewsItem, SearchProvider

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResult:
    items: list[NewsItem]
    failed_sources: list[str]


class NewsSearchService:
    def __init__(self, providers: list[SearchProvider], max_results: int = 5) -> None:
        self._providers = providers
        self._max_results = max_results

    async def search(self, query: str) -> SearchResult:
        provider_results = await asyncio.gather(
            *(self._search_provider(provider, query) for provider in self._providers)
        )
        items: list[NewsItem] = []
        failures: list[str] = []
        for provider_items, failure in provider_results:
            items.extend(provider_items)
            if failure is not None:
                failures.append(failure)

        ranked = sorted(items, key=lambda item: _rank(item, query), reverse=True)
        unique = deduplicate_items(ranked)
        return SearchResult(items=unique[: self._max_results], failed_sources=failures)

    async def _search_provider(
        self,
        provider: SearchProvider,
        query: str,
    ) -> tuple[list[NewsItem], str | None]:
        try:
            return await provider.search(query), None
        except httpx.HTTPError as error:
            logger.warning(
                "news_provider_failed",
                provider=provider.name,
                error=str(error),
            )
            return [], provider.name


def deduplicate_items(
    items: list[NewsItem],
    excluded_hashes: set[str] | None = None,
    excluded_titles: list[str] | None = None,
) -> list[NewsItem]:
    seen_hashes = set(excluded_hashes or ())
    seen_titles = list(excluded_titles or ())
    result: list[NewsItem] = []
    for item in items:
        item_hash = url_hash(item.url)
        if item_hash in seen_hashes:
            continue
        if any(ratio(item.title.casefold(), title.casefold()) >= 90 for title in seen_titles):
            continue
        seen_hashes.add(item_hash)
        seen_titles.append(item.title)
        result.append(item)
    return result


def _rank(item: NewsItem, query: str) -> tuple[int, float]:
    terms = tokenize_words(query)
    text_terms = tokenize_words(f"{item.title} {item.snippet}")
    relevance = len(terms & text_terms)
    published = item.published_at
    if published is None:
        recency = 0.0
    else:
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        recency = published.timestamp()
    return relevance, recency


def filter_already_sent(
    items: list[NewsItem],
    sent_hashes: set[str],
    sent_titles: list[str],
) -> list[NewsItem]:
    return deduplicate_items(items, sent_hashes, sent_titles)


def make_topic_key(query: str) -> str:
    return topic_key(query)
