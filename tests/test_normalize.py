from datetime import UTC, datetime

import pytest

from newsbrief.pipeline.normalize import (
    clean_html,
    normalize_url,
    tokenize_words,
    topic_key,
    url_hash,
)
from newsbrief.providers.rss import parse_feed


def test_normalize_url_removes_tracking_and_fragment() -> None:
    assert (
        normalize_url("HTTPS://Example.COM:443/story?utm_source=feed&id=3#part")
        == "https://example.com/story?id=3"
    )


@pytest.mark.parametrize(
    "value",
    ["javascript:alert(1)", "https://user:pass@example.com/story", "not a url"],
)
def test_normalize_url_rejects_non_http_or_credential_urls(value: str) -> None:
    assert normalize_url(value) is None


def test_url_hash_uses_normalized_url() -> None:
    assert url_hash("https://example.com/story?utm_medium=rss") == url_hash(
        "https://example.com/story"
    )
    with pytest.raises(ValueError):
        url_hash("file:///private.txt")


def test_clean_html_removes_markup_and_collapses_whitespace() -> None:
    assert clean_html("<p>News&nbsp; &amp; updates</p><p>More</p>") == "News & updates More"


def test_topic_key_collapses_spaces_and_ignores_case() -> None:
    assert topic_key("  IA   em   Portugal ") == "ia em portugal"


def test_tokenize_words_ignores_accents() -> None:
    assert tokenize_words("Notícias sobre inteligência artificial") == {
        "noticias",
        "inteligencia",
        "artificial",
    }


def test_parse_feed_normalizes_and_extracts_item_fields() -> None:
    feed = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Fonte</title><item>
      <title>Noticia &amp; RSS</title>
      <link>https://example.com/item?utm_source=rss</link>
      <pubDate>Tue, 10 Nov 2026 12:00:00 GMT</pubDate>
      <description>&lt;p&gt;Trecho &amp;amp; contexto&lt;/p&gt;</description>
    </item></channel></rss>"""

    items = parse_feed(feed, "Feed")

    assert len(items) == 1
    assert items[0].title == "Noticia & RSS"
    assert items[0].source == "Fonte"
    assert items[0].url == "https://example.com/item"
    assert items[0].snippet == "Trecho & contexto"
    assert items[0].published_at == datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
