import asyncio
from typing import Any

import structlog
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from newsbrief.bot.handlers import (
    BotServices,
    handle_admin_access,
    handle_callback,
    handle_current_topic,
    handle_free_text,
    handle_help,
    handle_history,
    handle_list_favorites,
    handle_list_schedules,
    handle_menu,
    handle_pause_resume,
    handle_remove_favorite,
    handle_save_topic,
    handle_schedule,
    handle_search,
    handle_start,
    handle_topic,
    handle_topic_param,
    handle_utility,
    run_scheduled_search,
)
from newsbrief.health import HealthServer
from newsbrief.scheduler.runner import ScheduleRunner

logger = structlog.get_logger(__name__)


def build_application(
    token: str,
    services: BotServices,
) -> Application[Any, Any, Any, Any, Any, Any]:
    stop_event = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    health_server: HealthServer | None = None

    async def initialize(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
        nonlocal health_server
        await services.database.initialize()
        if services.settings.health_port > 0:
            health_server = HealthServer(services.settings.health_port)
            await health_server.start()
        runner = ScheduleRunner(
            services.database,
            lambda sched: run_scheduled_search(services, sched, app.bot.send_message),
        )
        tasks.append(asyncio.create_task(runner.run(stop_event)))

    async def shutdown(_: Application[Any, Any, Any, Any, Any, Any]) -> None:
        stop_event.set()
        for task in tasks:
            task.cancel()
        if health_server is not None:
            await health_server.stop()
        try:
            await services.database.close()
        finally:
            await services.http_client.aclose()

    async def handle_error(
        _: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        logger.error(
            "telegram_update_failed",
            error=str(context.error),
        )

    application = (
        Application.builder()
        .token(token)
        .concurrent_updates(20)
        .post_init(initialize)
        .post_shutdown(shutdown)
        .build()
    )
    application.bot_data["services"] = services
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("menu", handle_menu))
    application.add_handler(CommandHandler(["ajuda", "help"], handle_help))
    application.add_handler(CommandHandler("tema", handle_topic))
    application.add_handler(CommandHandler("buscar", handle_search))
    application.add_handler(CommandHandler("atual", handle_current_topic))
    application.add_handler(CommandHandler("historico", handle_history))
    application.add_handler(CommandHandler("salvar", handle_save_topic))
    application.add_handler(CommandHandler("temas", handle_list_favorites))
    application.add_handler(CommandHandler("remover", handle_remove_favorite))
    application.add_handler(
        CommandHandler(
            ["tipo", "expandir", "limite", "periodo", "filtro", "idioma", "modo", "config", "fuso"],
            handle_topic_param,
        )
    )
    application.add_handler(CommandHandler("agendar", handle_schedule))
    application.add_handler(CommandHandler("agendamentos", handle_list_schedules))
    application.add_handler(CommandHandler(["pausar", "retomar"], handle_pause_resume))
    application.add_handler(
        CommandHandler(
            ["fontes", "status", "estatisticas", "apagar_meus_dados"],
            handle_utility,
        )
    )
    application.add_handler(CommandHandler(["autorizar", "revogar"], handle_admin_access))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))
    application.add_error_handler(handle_error)
    return application
