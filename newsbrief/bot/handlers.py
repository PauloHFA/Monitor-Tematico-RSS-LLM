import asyncio
from dataclasses import dataclass, field
from typing import cast

import httpx
import structlog
from telegram import Message, Update
from telegram.ext import ContextTypes

from newsbrief.bot.formatting import format_news_message
from newsbrief.config import Settings
from newsbrief.db.storage import Database
from newsbrief.llm.base import Summarizer
from newsbrief.pipeline.search import (
    NewsSearchService,
    filter_already_sent,
    make_topic_key,
)

logger = structlog.get_logger(__name__)
MAX_TOPIC_LENGTH = 120


@dataclass(slots=True)
class BotServices:
    settings: Settings
    database: Database
    search: NewsSearchService
    summarizer: Summarizer
    http_client: httpx.AsyncClient
    chat_locks: dict[int, asyncio.Lock] = field(default_factory=dict)

    def lock_for_chat(self, chat_id: int) -> asyncio.Lock:
        return self.chat_locks.setdefault(chat_id, asyncio.Lock())


def get_services(context: ContextTypes.DEFAULT_TYPE) -> BotServices:
    return cast(BotServices, context.application.bot_data["services"])


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    await _reply(
        update,
        "Olá! Use /tema <assunto> para buscar notícias, /buscar para repetir "
        "a busca ou /atual para ver seu tema ativo. Use /ajuda para ver os comandos.",
    )


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    await _reply(
        update,
        "/tema <assunto> — define o tema e busca notícias agora.\n"
        "/buscar — busca itens novos do tema ativo.\n"
        "/atual — mostra o tema ativo.\n"
        "/ajuda — mostra esta ajuda.",
    )


async def handle_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    query = " ".join(context.args or []).strip()
    if not query:
        await _reply(update, "Informe um tema. Exemplo: /tema inteligência artificial")
        return
    if len(query) > MAX_TOPIC_LENGTH:
        await _reply(update, f"O tema deve ter no máximo {MAX_TOPIC_LENGTH} caracteres.")
        return

    chat_id = _chat_id(update)
    await services.database.set_active_topic(chat_id, query)
    await _reply(update, f"Buscando: {query}...")
    await _search_and_send(update, services, chat_id, query)


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    if context.args:
        await _reply(update, "Use /buscar sem argumentos para repetir o tema ativo.")
        return

    chat_id = _chat_id(update)
    query = await services.database.get_active_topic(chat_id)
    if query is None:
        await _reply(update, "Você ainda não definiu um tema. Use /tema <assunto>.")
        return
    await _reply(update, f"Buscando: {query}...")
    await _search_and_send(update, services, chat_id, query)


async def handle_current_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    query = await services.database.get_active_topic(_chat_id(update))
    if query is None:
        await _reply(update, "Você ainda não definiu um tema. Use /tema <assunto>.")
        return
    await _reply(update, f"Tema ativo: {query}")


async def _search_and_send(
    update: Update,
    services: BotServices,
    chat_id: int,
    query: str,
) -> None:
    async with services.lock_for_chat(chat_id):
        result = await services.search.search(query)
        if result.failed_sources:
            sources = ", ".join(result.failed_sources)
            await _reply(update, f"Não foi possível consultar estas fontes: {sources}.")

        key = make_topic_key(query)
        sent = await services.database.get_sent_items(chat_id, key)
        new_items = filter_already_sent(
            result.items,
            {record.url_hash for record in sent},
            [record.title for record in sent],
        )
        if not new_items:
            await _reply(update, "Nada novo para esse tema.")
            return

        for item in new_items:
            summary = item.snippet or "Resumo indisponível."
            try:
                summary = await services.summarizer.summarize(item)
            except (httpx.HTTPError, ValueError) as error:
                logger.warning(
                    "summary_fallback_used",
                    chat_id=chat_id,
                    topic=key,
                    error=str(error),
                )
            message = format_news_message(item, summary)
            delivered = await _reply(update, message)
            if delivered is None:
                logger.error(
                    "telegram_delivery_not_confirmed",
                    chat_id=chat_id,
                    topic=key,
                    url=item.url,
                )
                continue
            await services.database.record_sent(chat_id, key, item.url, item.title)


async def _is_authorized(update: Update, services: BotServices) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    if chat.id in services.settings.allowed_chat_ids:
        return True
    logger.warning("unauthorized_chat", chat_id=chat.id)
    await _reply(update, "Este chat não está autorizado a usar o NewsBrief AI.")
    return False


async def _reply(update: Update, text: str) -> Message | None:
    message = update.effective_message
    if message is None:
        return None
    return await message.reply_text(text)


def _chat_id(update: Update) -> int:
    chat = update.effective_chat
    if chat is None:
        raise ValueError("Telegram update does not contain a chat.")
    return chat.id
