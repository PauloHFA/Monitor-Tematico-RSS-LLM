from pathlib import Path

import httpx
import pytest
import respx

from newsbrief.providers.rss import (
    CuratedFeed,
    CuratedRSSProvider,
    GoogleNewsRSSProvider,
    load_curated_feeds,
    parse_feed,
    request_with_retries,
)

FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Fonte</title><item>
  <title>Notícia</title><link>https://example.com/noticia</link>
  <description>Trecho</description>
</item></channel></rss>""".encode()


def test_load_curated_feeds_validates_and_normalizes_urls(tmp_path: Path) -> None:
    config = tmp_path / "feeds.yaml"
    config.write_text(
        "feeds:\n  - name: Exemplo\n    url: https://example.com/feed?utm_source=x\n",
        encoding="utf-8",
    )

    feeds = load_curated_feeds(config)

    assert feeds == [CuratedFeed("Exemplo", "https://example.com/feed")]


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        "feeds: not-a-list",
        "feeds:\n  - url: https://example.com/feed",
        "feeds:\n  - name: Invalid\n    url: file:///private.xml",
    ],
)
def test_load_curated_feeds_rejects_invalid_configuration(
    tmp_path: Path,
    content: str,
) -> None:
    config = tmp_path / "feeds.yaml"
    config.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_curated_feeds(config)


@pytest.mark.asyncio
@respx.mock
async def test_google_news_provider_requests_rss_for_the_topic() -> None:
    route = respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, content=FEED)
    )
    async with httpx.AsyncClient() as client:
        items = await GoogleNewsRSSProvider(client).search("Python")

    assert len(items) == 1
    assert route.calls.last.request.url.params["q"] == "Python"
    assert route.calls.last.request.url.params["hl"] == "pt-BR"


@pytest.mark.asyncio
@respx.mock
async def test_curated_provider_reads_its_configured_feed() -> None:
    route = respx.get("https://example.com/feed.xml").mock(
        side_effect=[
            httpx.Response(200, content=FEED),
            httpx.Response(200, content=FEED),
        ]
    )
    async with httpx.AsyncClient() as client:
        provider = CuratedRSSProvider(
            client,
            CuratedFeed("Comunidade", "https://example.com/feed.xml"),
        )
        items = await provider.search("Notícia")
        unrelated = await provider.search("astronomia")

    assert items[0].source == "Fonte"
    assert unrelated == []
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_request_with_retries_retries_server_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr("newsbrief.providers.rss.asyncio.sleep", no_wait)
    route = respx.get("https://example.com/feed.xml").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, content=FEED),
        ]
    )
    async with httpx.AsyncClient() as client:
        response = await request_with_retries(client, "GET", "https://example.com/feed.xml")

    assert response.status_code == 200
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_request_with_retries_does_not_retry_permanent_client_errors() -> None:
    route = respx.get("https://example.com/feed.xml").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await request_with_retries(client, "GET", "https://example.com/feed.xml")

    assert route.call_count == 1


def test_parse_feed_skips_missing_titles_and_invalid_urls() -> None:
    feed = b"""<rss><channel><item><title> </title>
      <link>https://example.com/no-title</link></item>
      <item><title>Invalid link</title><link>javascript:void(0)</link></item>
      </channel></rss>"""

    assert parse_feed(feed, "Feed") == []
