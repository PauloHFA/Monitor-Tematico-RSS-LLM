from typing import Any

import structlog
from telegram.ext import Application, CommandHandler, ContextTypes

from newsbrief.bot.handlers import (
    BotServices,
    handle_current_topic,
    handle_help,
    handle_search,
    handle_start,
    handle_topic,
)

logger = structlog.get_logger(__name__)


def build_application(
    token: str,
    services: BotServices,
) -> Application[Any, Any, Any, Any, Any, Any]:
    async def initialize(_: Application[Any, Any, Any, Any, Any, Any]) -> None:
        await services.database.initialize()

    async def shutdown(_: Application[Any, Any, Any, Any, Any, Any]) -> None:
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
    application.add_handler(CommandHandler(["ajuda", "help"], handle_help))
    application.add_handler(CommandHandler("tema", handle_topic))
    application.add_handler(CommandHandler("buscar", handle_search))
    application.add_handler(CommandHandler("atual", handle_current_topic))
    application.add_error_handler(handle_error)
    return application
