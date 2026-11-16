import asyncio
from types import SimpleNamespace

import httpx
import pytest

from newsbrief.bot.formatting import format_news_message
from newsbrief.bot.handlers import (
    BotServices,
    handle_current_topic,
    handle_help,
    handle_search,
    handle_start,
    handle_topic,
)
from newsbrief.config import Settings
from newsbrief.db.storage import Database
from newsbrief.pipeline.search import NewsSearchService
from newsbrief.providers.base import NewsItem


class StaticProvider:
    name = "fixture"

    def __init__(self, item: NewsItem) -> None:
        self._item = item

    async def search(self, query: str) -> list[NewsItem]:
        del query
        return [self._item]


class StaticSummarizer:
    async def summarize(self, item: NewsItem) -> str:
        del item
        return "Resumo curto."


class FailingSummarizer:
    async def summarize(self, item: NewsItem) -> str:
        del item
        raise httpx.ConnectError(
            "offline",
            request=httpx.Request("POST", "http://localhost:11434/api/generate"),
        )


class FakeMessage:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_news_delivery = False

    async def reply_text(self, text: str, **kwargs: object) -> object:
        del kwargs
        self.sent.append(text)
        if self.fail_news_delivery and text.startswith("📰 "):
            return None
        return object()


def _context(services: BotServices, args: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        args=args,
        application=SimpleNamespace(bot_data={"services": services}),
    )


def _update(chat_id: int) -> SimpleNamespace:
    message = FakeMessage()
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=message,
        message=message,
    )


def _services(
    settings: Settings,
    database: Database,
    item: NewsItem | None = None,
    summarizer: object | None = None,
) -> BotServices:
    providers = [StaticProvider(item)] if item is not None else []
    return BotServices(
        settings=settings,
        database=database,
        search=NewsSearchService(providers),
        summarizer=summarizer or StaticSummarizer(),
        http_client=httpx.AsyncClient(),
    )


async def test_topic_search_delivers_then_deduplicates(tmp_path) -> None:
    item = NewsItem(
        title="Python no mercado de tecnologia",
        url="https://example.com/python",
        source="Fonte",
        published_at=None,
        snippet="Uma notícia sobre Python.",
    )
    settings = Settings(telegram_bot_token="token", allowed_chat_ids_raw="42")
    database = Database(tmp_path / "newsbrief.db")
    await database.initialize()
    async with httpx.AsyncClient() as client:
        services = BotServices(
            settings=settings,
            database=database,
            search=NewsSearchService([StaticProvider(item)]),
            summarizer=StaticSummarizer(),
            http_client=client,
        )
        update = _update(42)

        await handle_topic(update, _context(services, ["Python", "tecnologia"]))
        await handle_search(update, _context(services, []))

    assert await database.get_active_topic(42) == "Python tecnologia"
    assert len(await database.get_sent_items(42, "python tecnologia")) == 1
    assert update.effective_message.sent[0] == "Buscando: Python tecnologia..."
    assert "🔗 https://example.com/python" in update.effective_message.sent[1]
    assert update.effective_message.sent[-1] == "Nada novo para esse tema."
    await database.close()


async def test_unauthorized_chat_is_denied(tmp_path) -> None:
    settings = Settings(telegram_bot_token="token", allowed_chat_ids_raw="42")
    services = BotServices(
        settings=settings,
        database=Database(tmp_path / "newsbrief.db"),
        search=NewsSearchService([]),
        summarizer=StaticSummarizer(),
        http_client=httpx.AsyncClient(),
    )
    update = _update(99)
    await handle_start(update, _context(services, []))
    await services.http_client.aclose()

    assert update.effective_message.sent == ["Este chat não está autorizado a usar o NewsBrief AI."]


async def test_topic_validation_and_missing_active_topic(tmp_path) -> None:
    settings = Settings(telegram_bot_token="token", allowed_chat_ids_raw="42")
    database = Database(tmp_path / "newsbrief.db")
    await database.initialize()
    async with httpx.AsyncClient() as client:
        services = BotServices(
            settings=settings,
            database=database,
            search=NewsSearchService([]),
            summarizer=StaticSummarizer(),
            http_client=client,
        )
        update = _update(42)

        await handle_topic(update, _context(services, ["a" * 121]))
        await handle_search(update, _context(services, []))

    assert update.effective_message.sent == [
        "O tema deve ter no máximo 120 caracteres.",
        "Você ainda não definiu um tema. Use /tema <assunto>.",
    ]
    await database.close()


async def test_start_help_and_current_topic_commands(tmp_path) -> None:
    settings = Settings(telegram_bot_token="token", allowed_chat_ids_raw="42")
    database = Database(tmp_path / "newsbrief.db")
    await database.initialize()
    await database.set_active_topic(42, "notícias locais")
    services = _services(settings, database)
    update = _update(42)

    await handle_start(update, _context(services, []))
    await handle_help(update, _context(services, []))
    await handle_current_topic(update, _context(services, []))

    assert update.effective_message.sent[0].startswith("Olá!")
    assert "/tema <assunto>" in update.effective_message.sent[1]
    assert update.effective_message.sent[2] == "Tema ativo: notícias locais"
    await services.http_client.aclose()
    await database.close()


async def test_empty_topic_and_search_arguments_are_explained(tmp_path) -> None:
    settings = Settings(telegram_bot_token="token", allowed_chat_ids_raw="42")
    database = Database(tmp_path / "newsbrief.db")
    await database.initialize()
    services = _services(settings, database)
    update = _update(42)

    await handle_topic(update, _context(services, []))
    await handle_search(update, _context(services, ["invalido"]))

    assert update.effective_message.sent == [
        "Informe um tema. Exemplo: /tema inteligência artificial",
        "Use /buscar sem argumentos para repetir o tema ativo.",
    ]
    await services.http_client.aclose()
    await database.close()


async def test_provider_and_ollama_failures_use_visible_fallback(tmp_path) -> None:
    item = NewsItem(
        title="Notícia sem resumo disponível",
        url="https://example.com/fallback",
        source="Fonte",
        published_at=None,
        snippet="Trecho original da notícia.",
    )

    class BrokenProvider:
        name = "fonte offline"

        async def search(self, query: str) -> list[NewsItem]:
            del query
            raise httpx.ConnectError(
                "offline",
                request=httpx.Request("GET", "https://example.com/feed.xml"),
            )

    settings = Settings(telegram_bot_token="token", allowed_chat_ids_raw="42")
    database = Database(tmp_path / "newsbrief.db")
    await database.initialize()
    services = _services(settings, database, item, FailingSummarizer())
    services.search = NewsSearchService([StaticProvider(item), BrokenProvider()])
    update = _update(42)

    await handle_topic(update, _context(services, ["fallback"]))

    assert any("fonte offline" in text for text in update.effective_message.sent)
    assert any("Trecho original da notícia." in text for text in update.effective_message.sent)
    assert len(await database.get_sent_items(42, "fallback")) == 1
    await services.http_client.aclose()
    await database.close()


async def test_unconfirmed_telegram_delivery_is_not_marked_sent(tmp_path) -> None:
    item = NewsItem(
        title="Notícia pendente",
        url="https://example.com/pending",
        source="Fonte",
        published_at=None,
        snippet="Trecho.",
    )
    settings = Settings(telegram_bot_token="token", allowed_chat_ids_raw="42")
    database = Database(tmp_path / "newsbrief.db")
    await database.initialize()
    services = _services(settings, database, item)
    update = _update(42)
    update.effective_message.fail_news_delivery = True

    await handle_topic(update, _context(services, ["pendente"]))

    assert await database.get_sent_items(42, "pendente") == []
    await services.http_client.aclose()
    await database.close()


async def test_concurrent_searches_for_one_chat_deliver_item_once(tmp_path) -> None:
    item = NewsItem(
        title="Notícia para busca concorrente",
        url="https://example.com/concurrent",
        source="Fonte",
        published_at=None,
        snippet="Trecho.",
    )
    settings = Settings(telegram_bot_token="token", allowed_chat_ids_raw="42")
    database = Database(tmp_path / "newsbrief.db")
    await database.initialize()
    await database.set_active_topic(42, "concorrência")
    services = _services(settings, database, item)
    first_update = _update(42)
    second_update = _update(42)

    await asyncio.gather(
        handle_search(first_update, _context(services, [])),
        handle_search(second_update, _context(services, [])),
    )

    delivered = [
        text
        for update in (first_update, second_update)
        for text in update.effective_message.sent
        if text.startswith("📰 ")
    ]
    assert len(delivered) == 1
    await services.http_client.aclose()
    await database.close()


async def test_chat_without_message_does_not_raise(tmp_path) -> None:
    settings = Settings(telegram_bot_token="token", allowed_chat_ids_raw="42")
    database = Database(tmp_path / "newsbrief.db")
    await database.initialize()
    services = _services(settings, database)
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=42),
        effective_message=None,
    )

    await handle_current_topic(update, _context(services, []))

    await services.http_client.aclose()
    await database.close()


def test_formatting_is_plain_text_and_limited_to_600_characters() -> None:
    item = NewsItem(
        title="Título " * 100,
        url="https://example.com/story",
        source="Fonte",
        published_at=None,
        snippet="",
    )

    message = format_news_message(item, "Resumo " * 200)

    assert len(message) <= 600
    assert "<b>" not in message


def test_formatting_rejects_invalid_or_unreasonably_long_links() -> None:
    invalid = NewsItem("Título", "javascript:void(0)", "Fonte", None, "")
    too_long = NewsItem("Título", f"https://example.com/{'a' * 600}", "Fonte", None, "")

    with pytest.raises(ValueError, match="valid http"):
        format_news_message(invalid, "Resumo.")
    with pytest.raises(ValueError, match="URL is too long"):
        format_news_message(too_long, "Resumo.")
