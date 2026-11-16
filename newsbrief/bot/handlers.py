import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from newsbrief.bot.formatting import format_news_message, format_single_digest
from newsbrief.config import Settings
from newsbrief.db.storage import Database, ScheduleRecord
from newsbrief.llm.base import Summarizer
from newsbrief.notify.whatsapp import WhatsAppNotifier
from newsbrief.pipeline.search import (
    NewsSearchService,
    filter_already_sent,
    make_topic_key,
    parse_filter_tokens,
)
from newsbrief.providers.base import LanguageFilter, NewsItem, SearchFilters, TopicType
from newsbrief.topics.classifier import classify_topic

logger = structlog.get_logger(__name__)
MAX_TOPIC_LENGTH = 120
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(slots=True)
class BotServices:
    settings: Settings
    database: Database
    search: NewsSearchService
    summarizer: Summarizer
    http_client: httpx.AsyncClient
    whatsapp: WhatsAppNotifier | None = None
    chat_locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    pending_prompts: dict[int, str] = field(default_factory=dict)

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
        reply_markup=_main_menu_markup(),
    )


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    await _reply(update, "Menu rápido:", reply_markup=_main_menu_markup())


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    await _reply(
        update,
        "/tema <assunto> — define o tema e busca agora.\n"
        "/buscar [tudo] — busca itens novos (ou reenvia todos).\n"
        "/atual | /historico — tema ativo e últimos 10 temas.\n"
        "/salvar [apelido] | /temas | /remover <n> — favoritos.\n"
        "/tipo <noticia|vaga|edital> | /expandir on|off\n"
        "/limite <1-10> | /periodo <Nd> | /filtro <termos> | /idioma <pt|en|all>\n"
        "/modo resumo-unico|individual | /config texto_livre on|off\n"
        "/agendar <hh:mm> [dias] | /agendamentos | /pausar | /retomar | /fuso <zona>\n"
        "/fontes | /status | /estatisticas | /apagar_meus_dados\n"
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


async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    message = update.effective_message
    raw_text = (message.text if message else "") or ""
    query = " ".join(raw_text.split()).strip()
    if not query:
        return
    chat_id = _chat_id(update)
    pending = services.pending_prompts.pop(chat_id, None)
    prefs = await services.database.get_preferences(
        chat_id,
        default_timezone=services.settings.default_timezone,
        default_limit=services.settings.max_results,
    )
    if pending != "awaiting_topic" and not prefs.free_text:
        await _reply(update, "Texto livre desabilitado. Use /tema <assunto>.")
        return
    if len(query) > MAX_TOPIC_LENGTH:
        await _reply(update, f"O tema deve ter no máximo {MAX_TOPIC_LENGTH} caracteres.")
        return
    await services.database.set_active_topic(chat_id, query)
    await _reply(update, f"Buscando: {query}...")
    await _search_and_send(update, services, chat_id, query)


async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    args = [a.strip().casefold() for a in (context.args or []) if a.strip()]
    include_sent = False
    if args == ["tudo"]:
        include_sent = True
    elif args:
        await _reply(update, "Use /buscar sem argumentos para repetir o tema ativo.")
        return

    chat_id = _chat_id(update)
    query = await services.database.get_active_topic(chat_id)
    if query is None:
        await _reply(update, "Você ainda não definiu um tema. Use /tema <assunto>.")
        return
    await _reply(update, f"Buscando: {query}...")
    await _search_and_send(update, services, chat_id, query, include_sent=include_sent)


async def handle_current_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    chat_id = _chat_id(update)
    query = await services.database.get_active_topic(chat_id)
    if query is None:
        await _reply(update, "Você ainda não definiu um tema. Use /tema <assunto>.")
        return
    await _reply(update, f"Tema ativo: {query}")


async def handle_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    records = await services.database.list_topic_history(_chat_id(update), limit=10)
    if not records:
        await _reply(update, "Seu histórico de temas está vazio.")
        return
    buttons = [
        [InlineKeyboardButton(text=rec.query[:40], callback_data=f"use_topic:{rec.query[:50]}")]
        for rec in records
    ]
    await _reply(
        update,
        "Últimos temas pesquisados:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_save_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    chat_id = _chat_id(update)
    query = await services.database.get_active_topic(chat_id)
    if query is None:
        await _reply(update, "Defina um tema antes de salvar usando /tema <assunto>.")
        return
    alias = " ".join(context.args or []).strip() or None
    saved, _ = await services.database.save_favorite_topic(chat_id, query, alias=alias)
    if not saved and alias:
        await _reply(
            update,
            f"Já existe um favorito com o apelido '{alias}'. Confirma sobrescrever?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            text="Sim, sobrescrever",
                            callback_data=f"overwrite:{alias[:40]}",
                        )
                    ]
                ]
            ),
        )
        return
    label = f"{alias} ({query})" if alias else query
    await _reply(update, f"Tema salvo nos favoritos: {label}")


async def handle_list_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    favs = await services.database.list_favorite_topics(_chat_id(update))
    if not favs:
        await _reply(update, "Nenhum tema favorito salvo. Use /salvar [apelido].")
        return
    lines = []
    buttons = []
    for idx, fav in enumerate(favs, start=1):
        label = f"{fav.alias}: {fav.query}" if fav.alias else fav.query
        lines.append(f"{idx}. {label}")
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"Buscar #{idx}", callback_data=f"use_topic:{fav.query[:50]}"
                ),
                InlineKeyboardButton(text=f"Remover #{idx}", callback_data=f"del_fav:{idx}"),
            ]
        )
    await _reply(
        update,
        "Temas favoritos:\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_remove_favorite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        await _reply(update, "Uso: /remover <número>")
        return
    ok = await services.database.remove_favorite_topic(_chat_id(update), int(args[0]))
    await _reply(
        update,
        "Tema removido dos favoritos." if ok else "Favorito não encontrado.",
    )


async def handle_topic_param(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    message = update.effective_message
    raw_cmd = ((message.text if message else "") or "").split()[0].split("@")[0].casefold()
    chat_id = _chat_id(update)
    args = context.args or []

    if raw_cmd == "/fuso":
        if len(args) != 1:
            await _reply(update, "Uso: /fuso America/Fortaleza")
            return
        try:
            ZoneInfo(args[0])
        except ZoneInfoNotFoundError:
            await _reply(update, f"Fuso horário inválido: {args[0]}")
            return
        await services.database.update_preferences(chat_id, timezone=args[0])
        await _reply(update, f"Fuso horário atualizado para {args[0]}.")
        return

    if raw_cmd == "/config":
        if (
            len(args) == 2
            and args[0].casefold() == "texto_livre"
            and args[1].casefold() in {"on", "off"}
        ):
            enabled = args[1].casefold() == "on"
            await services.database.update_preferences(chat_id, free_text=enabled)
            await _reply(update, f"Texto livre {'ativado' if enabled else 'desativado'}.")
            return
        await _reply(update, "Uso: /config texto_livre on|off")
        return

    if raw_cmd == "/expandir":
        if len(args) == 1 and args[0].casefold() in {"on", "off"}:
            enabled = args[0].casefold() == "on"
            await services.database.update_preferences(chat_id, expand_query=enabled)
            await _reply(update, f"Expansão de consulta {'ativada' if enabled else 'desativada'}.")
            return
        await _reply(update, "Uso: /expandir on|off")
        return

    if raw_cmd == "/modo":
        if len(args) == 1 and args[0].casefold() in {"resumo-unico", "individual"}:
            single = args[0].casefold() == "resumo-unico"
            await services.database.update_preferences(chat_id, single_summary_mode=single)
            await _reply(update, f"Modo de entrega ajustado para: {args[0].casefold()}.")
            return
        await _reply(update, "Uso: /modo resumo-unico|individual")
        return

    active = await services.database.get_active_topic(chat_id)
    if active is None:
        await _reply(update, "Defina um tema antes usando /tema <assunto>.")
        return

    if raw_cmd == "/tipo":
        if len(args) != 1 or args[0].casefold() not in {"noticia", "vaga", "edital"}:
            await _reply(update, "Uso: /tipo noticia|vaga|edital")
            return
        await services.database.set_topic_params(chat_id, active, topic_type=args[0].casefold())
        await _reply(update, f"Tipo do tema '{active}' definido como {args[0].casefold()}.")
    elif raw_cmd == "/limite":
        if len(args) != 1 or not args[0].isdigit() or not 1 <= int(args[0]) <= 10:
            await _reply(update, "Uso: /limite <1..10>")
            return
        await services.database.set_topic_params(chat_id, active, limit_items=int(args[0]))
        await _reply(update, f"Limite ajustado para {args[0]} itens por busca.")
    elif raw_cmd == "/periodo":
        if len(args) != 1 or not args[0].casefold().endswith("d") or not args[0][:-1].isdigit():
            await _reply(update, "Uso: /periodo <Nd> (ex.: /periodo 7d)")
            return
        days = int(args[0][:-1])
        await services.database.set_topic_params(chat_id, active, period_days=days)
        await _reply(update, f"Período ajustado para os últimos {days} dia(s).")
    elif raw_cmd == "/filtro":
        raw_filters = " ".join(args).strip()
        await services.database.set_topic_params(chat_id, active, filters=raw_filters)
        await _reply(update, f"Filtros atualizados: {raw_filters or '(nenhum)'}")
    elif raw_cmd == "/idioma":
        if len(args) != 1 or args[0].casefold() not in {"pt", "en", "all"}:
            await _reply(update, "Uso: /idioma pt|en|all")
            return
        await services.database.set_topic_params(chat_id, active, lang=args[0].casefold())
        await _reply(update, f"Idioma ajustado para {args[0].casefold()}.")


async def handle_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    chat_id = _chat_id(update)
    args = context.args or []
    if not args or not _TIME_RE.match(args[0]):
        await _reply(update, "Uso: /agendar <HH:MM> [dias ex.: seg,qua,sex]")
        return
    active = await services.database.get_active_topic(chat_id)
    if active is None:
        await _reply(update, "Defina um tema antes usando /tema <assunto>.")
        return
    days_spec = ",".join(args[1:]) if len(args) > 1 else "*"
    prefs = await services.database.get_preferences(
        chat_id, default_timezone=services.settings.default_timezone
    )
    sched_id = await services.database.add_schedule(
        chat_id, active, args[0], days_spec=days_spec, timezone=prefs.timezone
    )
    await _reply(
        update,
        f"Agendamento #{sched_id} criado para '{active}' às {args[0]} ({prefs.timezone}).",
    )


async def handle_list_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    schedules = await services.database.list_schedules(_chat_id(update))
    if not schedules:
        await _reply(update, "Nenhum agendamento ativo.")
        return
    lines = [
        f"#{s.id} — {s.topic_query} às {s.time_hhmm} ({s.days_spec}, {s.timezone})"
        for s in schedules
    ]
    buttons = [
        [InlineKeyboardButton(text=f"Cancelar #{s.id}", callback_data=f"del_sched:{s.id}")]
        for s in schedules
    ]
    await _reply(
        update,
        "Agendamentos:\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_pause_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    message = update.effective_message
    raw_cmd = ((message.text if message else "") or "").split()[0].split("@")[0].casefold()
    paused = raw_cmd == "/pausar"
    await services.database.update_preferences(_chat_id(update), paused=paused)
    await _reply(
        update,
        "Envios agendados pausados." if paused else "Envios agendados retomados.",
    )


async def handle_utility(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    if not await _is_authorized(update, services):
        return
    message = update.effective_message
    raw_cmd = ((message.text if message else "") or "").split()[0].split("@")[0].casefold()
    chat_id = _chat_id(update)

    if raw_cmd == "/fontes":
        active = await services.database.get_active_topic(chat_id)
        topic_type: TopicType = classify_topic(active) if active else "noticia"
        names = [p.name for p in services.search.providers]
        await _reply(
            update,
            f"Provedores configurados ({topic_type}): {', '.join(names) or 'nenhum'}.",
        )
    elif raw_cmd == "/status":
        used_tokens = await services.database.get_daily_tokens_used()
        prefs = await services.database.get_preferences(
            chat_id, default_timezone=services.settings.default_timezone
        )
        await _reply(
            update,
            f"Status: ativo | LLM: {services.settings.llm_provider} "
            f"({services.settings.llm_model})\n"
            f"Orçamento diário usado: {used_tokens}/{services.settings.daily_llm_budget} tokens\n"
            f"Fuso: {prefs.timezone} | Agendamentos: {'pausados' if prefs.paused else 'ativos'}",
        )
    elif raw_cmd == "/estatisticas":
        stats = await services.database.weekly_stats(chat_id)
        if not stats:
            await _reply(update, "Nenhuma busca registrada nos últimos 7 dias.")
            return
        lines = [
            (
                f"- {s.topic_key}: {s.searches} busca(s), "
                f"{s.items_total} itens, {s.tokens_total} tokens"
            )
            for s in stats
        ]
        await _reply(update, "Estatísticas da semana:\n" + "\n".join(lines))
    elif raw_cmd == "/apagar_meus_dados":
        await services.database.erase_user_data(chat_id)
        await _reply(
            update,
            "Todos os seus temas, preferências, histórico e agendamentos foram removidos.",
        )


async def handle_admin_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = get_services(context)
    chat_id = _chat_id(update)
    if services.settings.admin_chat_id is None or chat_id != services.settings.admin_chat_id:
        await _reply(update, "Apenas o administrador pode executar este comando.")
        return
    message = update.effective_message
    raw_cmd = ((message.text if message else "") or "").split()[0].split("@")[0].casefold()
    args = context.args or []
    if len(args) != 1 or not args[0].lstrip("-").isdigit():
        await _reply(update, f"Uso: {raw_cmd} <chat_id>")
        return
    target_id = int(args[0])
    authorized = raw_cmd == "/autorizar"
    await services.database.update_preferences(target_id, authorized=authorized)
    await _reply(
        update,
        f"Chat {target_id} {'autorizado' if authorized else 'revogado'}.",
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    services = get_services(context)
    if not await _is_authorized(update, services):
        await query.answer()
        return
    data = query.data or ""
    chat_id = _chat_id(update)
    action, _, payload = data.partition(":")

    if action == "menu":
        if payload == "change_topic":
            services.pending_prompts[chat_id] = "awaiting_topic"
            await _reply(update, "Envie o novo tema que deseja buscar:")
        elif payload == "search_again":
            active = await services.database.get_active_topic(chat_id)
            if active:
                await _reply(update, f"Buscando: {active}...")
                await _search_and_send(update, services, chat_id, active)
            else:
                await _reply(update, "Você ainda não definiu um tema. Use /tema <assunto>.")
        elif payload == "save_topic":
            active = await services.database.get_active_topic(chat_id)
            if active:
                await services.database.save_favorite_topic(chat_id, active)
                await _reply(update, f"Tema salvo nos favoritos: {active}")
    elif action == "use_topic" and payload:
        await services.database.set_active_topic(chat_id, payload)
        await _reply(update, f"Buscando: {payload}...")
        await _search_and_send(update, services, chat_id, payload)
    elif action == "del_fav" and payload.isdigit():
        await services.database.remove_favorite_topic(chat_id, int(payload))
        await _reply(update, "Tema removido dos favoritos.")
    elif action == "del_sched" and payload.isdigit():
        await services.database.remove_schedule(chat_id, int(payload))
        await _reply(update, f"Agendamento #{payload} cancelado.")
    elif action == "overwrite" and payload:
        active = await services.database.get_active_topic(chat_id)
        if active:
            await services.database.save_favorite_topic(
                chat_id, active, alias=payload, overwrite=True
            )
            await _reply(update, f"Favorito '{payload}' atualizado para: {active}")

    await query.answer()


async def run_scheduled_search(
    services: BotServices,
    schedule: ScheduleRecord,
    send_fn: Any,
) -> None:
    """Execute a scheduled search and send results without blocking other chats (RF19)."""

    async def reply_text(text: str) -> object:
        await send_fn(chat_id=schedule.chat_id, text=text)
        return object()

    class _ScheduledUpdate:
        effective_chat = type("Chat", (), {"id": schedule.chat_id})()
        effective_message = type("Msg", (), {"reply_text": staticmethod(reply_text)})()

    await _search_and_send(_ScheduledUpdate(), services, schedule.chat_id, schedule.topic_query)


async def _search_and_send(
    update: Any,
    services: BotServices,
    chat_id: int,
    query: str,
    *,
    include_sent: bool = False,
) -> None:
    started = time.monotonic()
    async with services.lock_for_chat(chat_id):
        recent = await services.database.count_recent_searches(chat_id, hours=1)
        if recent >= services.settings.hourly_searches_limit:
            await _reply(
                update,
                f"Limite de {services.settings.hourly_searches_limit} buscas por hora atingido. "
                "Aguarde alguns minutos.",
            )
            return

        prefs = await services.database.get_preferences(
            chat_id,
            default_timezone=services.settings.default_timezone,
            default_limit=services.settings.max_results,
        )
        topic_params = await services.database.get_topic_params(chat_id, query)
        resolved_type = _parse_topic_type(topic_params.topic_type if topic_params else None)
        include_terms, exclude_terms = parse_filter_tokens(
            topic_params.filters if topic_params else ""
        )
        lang_val = topic_params.lang if topic_params and topic_params.lang else prefs.lang
        lang_filter: LanguageFilter = (
            lang_val if lang_val in {"pt", "en", "all"} else "pt"  # type: ignore[assignment]
        )
        filters = SearchFilters(
            limit=(
                topic_params.limit_items
                if topic_params and topic_params.limit_items is not None
                else prefs.default_limit
            ),
            period_days=topic_params.period_days if topic_params else None,
            include_terms=include_terms,
            exclude_terms=exclude_terms,
            lang=lang_filter,
        )

        result = await services.search.search(
            query,
            topic_type=resolved_type,
            filters=filters,
            expand=prefs.expand_query,
        )
        if result.failed_sources:
            sources = ", ".join(result.failed_sources)
            await _reply(update, f"Não foi possível consultar estas fontes: {sources}.")

        key = make_topic_key(query)
        sent = await services.database.get_sent_items(chat_id, key)
        if include_sent:
            new_items = result.items
        else:
            new_items = filter_already_sent(
                result.items,
                {record.url_hash for record in sent},
                [record.title for record in sent],
            )
        if not new_items:
            await _reply(update, "Nada novo para esse tema.")
            return

        daily_tokens = await services.database.get_daily_tokens_used()
        budget_exhausted = daily_tokens >= services.settings.daily_llm_budget
        if budget_exhausted:
            await _reply(update, "Orçamento diário de IA atingido; exibindo resumo básico.")

        entries: list[tuple[NewsItem, str]] = []
        tokens_this_search = 0
        for item in new_items:
            summary = item.snippet or "Resumo indisponível."
            if not budget_exhausted:
                try:
                    summary = await services.summarizer.summarize(item)
                    tokens_this_search += max(
                        1, (len(item.title) + len(item.snippet) + len(summary)) // 4
                    )
                except (httpx.HTTPError, ValueError) as error:
                    logger.warning(
                        "summary_fallback_used",
                        chat_id=chat_id,
                        topic=key,
                        error=str(error),
                    )
            entries.append((item, summary))

        whatsapp_on = (
            services.whatsapp is not None and "whatsapp" in services.settings.active_channels
        )
        if prefs.single_summary_mode:
            delivered_ok = True
            for chunk in format_single_digest(entries):
                delivered = await _reply(update, chunk)
                if delivered is None:
                    delivered_ok = False
                elif whatsapp_on and services.whatsapp is not None:
                    await services.whatsapp.send(chat_id, chunk)
            if delivered_ok:
                for item, _ in entries:
                    await services.database.record_sent(chat_id, key, item.url, item.title)
        else:
            for item, summary in entries:
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
                if whatsapp_on and services.whatsapp is not None:
                    await services.whatsapp.send(chat_id, message)
                await services.database.record_sent(chat_id, key, item.url, item.title)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        await services.database.log_search(
            chat_id,
            key,
            result.topic_type,
            list(result.providers_used),
            len(entries),
            elapsed_ms,
            tokens_this_search,
        )


def _parse_topic_type(value: str | None) -> TopicType | None:
    match value:
        case "noticia" | "vaga" | "edital":
            return value
        case _:
            return None


def _main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Buscar de novo", callback_data="menu:search_again"),
                InlineKeyboardButton("Trocar tema", callback_data="menu:change_topic"),
            ],
            [
                InlineKeyboardButton("Salvar tema", callback_data="menu:save_topic"),
            ],
        ]
    )


async def _is_authorized(update: Any, services: BotServices) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    authorized_override: bool | None = None
    if services.database.is_initialized:
        prefs = await services.database.get_preferences(chat.id)
        authorized_override = prefs.authorized
    if authorized_override is True or (
        authorized_override is None and chat.id in services.settings.allowed_chat_ids
    ):
        return True
    logger.warning("unauthorized_chat", chat_id=chat.id)
    await _reply(update, "Este chat não está autorizado a usar o NewsBrief AI.")
    return False


async def _reply(
    update: Any,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    message = update.effective_message
    if message is None:
        return None
    if reply_markup is not None:
        return cast(Message | None, await message.reply_text(text, reply_markup=reply_markup))
    return cast(Message | None, await message.reply_text(text))


def _chat_id(update: Any) -> int:
    chat = update.effective_chat
    if chat is None:
        raise ValueError("Telegram update does not contain a chat.")
    return int(chat.id)
