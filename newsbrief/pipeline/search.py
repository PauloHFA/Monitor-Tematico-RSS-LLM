import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from rapidfuzz.fuzz import ratio

from newsbrief.pipeline.normalize import tokenize_words, topic_key, url_hash
from newsbrief.providers.base import NewsItem, SearchFilters, SearchProvider, TopicType
from newsbrief.topics.classifier import classify_topic, expand_query

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SearchResult:
    items: list[NewsItem]
    failed_sources: list[str]
    topic_type: TopicType = "noticia"
    providers_used: tuple[str, ...] = ()


class NewsSearchService:
    def __init__(
        self,
        providers: list[SearchProvider],
        max_results: int = 5,
        *,
        provider_types: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._providers = providers
        self._max_results = max_results
        self._provider_types = provider_types or {}

    @property
    def providers(self) -> list[SearchProvider]:
        return list(self._providers)

    async def search(
        self,
        query: str,
        *,
        topic_type: TopicType | None = None,
        filters: SearchFilters | None = None,
        expand: bool = False,
    ) -> SearchResult:
        resolved_type: TopicType = topic_type or classify_topic(query)
        active_filters = filters or SearchFilters(limit=self._max_results)
        queries = expand_query(query, enabled=expand) or [query]

        selected_providers = [
            provider
            for provider in self._providers
            if not self._provider_types
            or provider.name not in self._provider_types
            or resolved_type in self._provider_types[provider.name]
        ]
        if not selected_providers:
            selected_providers = list(self._providers)

        tasks = [
            self._search_provider(provider, q_variant)
            for provider in selected_providers
            for q_variant in queries
        ]
        provider_results = await asyncio.gather(*tasks)
        items: list[NewsItem] = []
        failures: list[str] = []
        for provider_items, failure in provider_results:
            items.extend(provider_items)
            if failure is not None and failure not in failures:
                failures.append(failure)

        filtered = _apply_filters(items, active_filters)
        ranked = sorted(filtered, key=lambda item: _rank(item, query), reverse=True)
        unique = deduplicate_items(ranked)
        limit = active_filters.limit if filters is not None else self._max_results
        return SearchResult(
            items=unique[:limit],
            failed_sources=failures,
            topic_type=resolved_type,
            providers_used=tuple(p.name for p in selected_providers),
        )

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


def parse_filter_tokens(raw_filters: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    include: list[str] = []
    exclude: list[str] = []
    for token in raw_filters.split():
        clean = token.strip()
        if not clean:
            continue
        if clean.startswith("-") and len(clean) > 1:
            exclude.append(clean[1:].casefold())
        else:
            include.append(clean.casefold())
    return tuple(include), tuple(exclude)


def _apply_filters(items: list[NewsItem], filters: SearchFilters) -> list[NewsItem]:
    cutoff: datetime | None = None
    if filters.period_days is not None and filters.period_days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=filters.period_days)

    result: list[NewsItem] = []
    for item in items:
        if cutoff is not None and item.published_at is not None:
            pub = (
                item.published_at
                if item.published_at.tzinfo is not None
                else item.published_at.replace(tzinfo=UTC)
            )
            if pub < cutoff:
                continue
        haystack = f"{item.title} {item.snippet}".casefold()
        if any(term not in haystack for term in filters.include_terms):
            continue
        if any(term in haystack for term in filters.exclude_terms):
            continue
        result.append(item)
    return result
