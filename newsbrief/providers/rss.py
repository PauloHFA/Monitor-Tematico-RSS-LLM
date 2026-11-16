import asyncio
import calendar
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import struct_time
from typing import Any

import feedparser
import httpx
import yaml

from newsbrief.pipeline.normalize import clean_html, normalize_url, tokenize_words
from newsbrief.providers.base import NewsItem


@dataclass(frozen=True, slots=True)
class CuratedFeed:
    name: str
    url: str
    topic_type: str = "noticia"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    enabled: bool
    priority: int
    types: tuple[str, ...]


def load_providers_config(path: Path) -> list[ProviderConfig]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as file:
        document = yaml.safe_load(file)
    if not isinstance(document, dict) or not isinstance(document.get("providers", []), list):
        raise ValueError("Providers configuration must contain a 'providers' list.")
    configs: list[ProviderConfig] = []
    for entry in document.get("providers", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("Each provider entry requires a string name.")
        raw_types = entry.get("types", ["noticia"])
        types = tuple(str(t) for t in raw_types) if isinstance(raw_types, list) else ("noticia",)
        configs.append(
            ProviderConfig(
                name=entry["name"].strip(),
                enabled=bool(entry.get("enabled", True)),
                priority=int(entry.get("priority", 10)),
                types=types,
            )
        )
    return sorted(configs, key=lambda item: item.priority)


def load_curated_feeds(path: Path) -> list[CuratedFeed]:
    with open(path, encoding="utf-8") as file:
        document = yaml.safe_load(file)
    if not isinstance(document, dict) or not isinstance(document.get("feeds", []), list):
        raise ValueError("Feed configuration must contain a 'feeds' list.")

    feeds: list[CuratedFeed] = []
    for entry in document.get("feeds", []):
        if not isinstance(entry, dict):
            raise ValueError("Each curated feed must be a mapping with name and url.")
        name, url = entry.get("name"), entry.get("url")
        if not isinstance(name, str) or not name.strip() or not isinstance(url, str):
            raise ValueError("Each curated feed requires a non-empty name and URL.")
        normalized_url = normalize_url(url)
        if normalized_url is None:
            raise ValueError(f"Curated feed {name!r} must use an http(s) URL.")
        topic_type = str(entry.get("type", "noticia")).strip() or "noticia"
        feeds.append(CuratedFeed(name=name.strip(), url=normalized_url, topic_type=topic_type))
    return feeds


async def request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    for attempt in range(3):
        response: httpx.Response | None = None
        try:
            response = await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == 2:
                raise
        else:
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response
            if attempt == 2:
                response.raise_for_status()

        await asyncio.sleep(0.25 * (2**attempt) + random.uniform(0, 0.15))
    raise RuntimeError("Request retries exhausted without a response.")


def parse_feed(content: bytes, source_name: str) -> list[NewsItem]:
    feed = feedparser.parse(content)
    feed_title = clean_html(str(feed.feed.get("title", ""))).strip() or source_name
    items: list[NewsItem] = []
    for entry in feed.entries:
        title = clean_html(str(entry.get("title", ""))).strip()
        raw_url = entry.get("link")
        if not title or not isinstance(raw_url, str):
            continue
        url = normalize_url(raw_url)
        if url is None:
            continue
        parsed_date = entry.get("published_parsed") or entry.get("updated_parsed")
        published_at = _as_utc(parsed_date) if parsed_date else None
        snippet = clean_html(str(entry.get("summary", entry.get("description", "")))).strip()
        items.append(
            NewsItem(
                title=title[:500],
                url=url,
                source=feed_title[:120],
                published_at=published_at,
                snippet=snippet[:2000],
            )
        )
    return items


class GoogleNewsRSSProvider:
    def __init__(self, client: httpx.AsyncClient, *, lang: str = "pt") -> None:
        self._client = client
        self._lang = lang

    @property
    def name(self) -> str:
        return "Google News RSS"

    async def search(self, query: str) -> list[NewsItem]:
        params = {"q": query, "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"}
        if self._lang == "en":
            params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        response = await request_with_retries(
            self._client,
            "GET",
            "https://news.google.com/rss/search",
            params=params,
        )
        return parse_feed(response.content, self.name)


class CuratedRSSProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        feed: CuratedFeed,
    ) -> None:
        self._client = client
        self._feed = feed

    @property
    def name(self) -> str:
        return self._feed.name

    async def search(self, query: str) -> list[NewsItem]:
        response = await request_with_retries(self._client, "GET", self._feed.url)
        items = parse_feed(response.content, self._feed.name)
        terms = tokenize_words(query)
        if not terms:
            return items
        return [item for item in items if terms & tokenize_words(f"{item.title} {item.snippet}")]


def _as_utc(value: struct_time) -> datetime:
    return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
