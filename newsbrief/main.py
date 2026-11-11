import httpx
import structlog

from newsbrief.bot.application import build_application
from newsbrief.bot.handlers import BotServices
from newsbrief.config import Settings
from newsbrief.db.storage import Database
from newsbrief.llm.ollama import OllamaSummarizer
from newsbrief.logging_config import configure_logging
from newsbrief.pipeline.search import NewsSearchService
from newsbrief.providers.base import SearchProvider
from newsbrief.providers.rss import (
    CuratedRSSProvider,
    GoogleNewsRSSProvider,
    load_curated_feeds,
)

logger = structlog.get_logger(__name__)


def main() -> None:
    settings = Settings()
    _validate_settings(settings)
    configure_logging()

    client = httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        headers={"User-Agent": "NewsBriefAI/0.1 (RSS reader)"},
    )
    feeds = load_curated_feeds(settings.rss_feeds_config)
    providers: list[SearchProvider] = [GoogleNewsRSSProvider(client)]
    providers.extend(CuratedRSSProvider(client, feed) for feed in feeds)
    services = BotServices(
        settings=settings,
        database=Database(settings.db_path),
        search=NewsSearchService(providers, max_results=settings.max_results),
        summarizer=OllamaSummarizer(client, settings.ollama_host, settings.llm_model),
        http_client=client,
    )
    if not settings.allowed_chat_ids:
        logger.warning(
            "no_authorized_chats_configured",
            message="All chats will be denied until ALLOWED_CHAT_IDS is configured.",
        )
    application = build_application(settings.telegram_bot_token, services)
    application.run_polling()


def _validate_settings(settings: Settings) -> None:
    if not settings.telegram_bot_token.strip():
        raise ValueError("TELEGRAM_BOT_TOKEN must be configured before starting the bot.")
    if settings.request_timeout_seconds <= 0:
        raise ValueError("REQUEST_TIMEOUT_SECONDS must be greater than zero.")
    if not 1 <= settings.max_results <= 10:
        raise ValueError("MAX_RESULTS must be between 1 and 10.")
    _ = settings.allowed_chat_ids


if __name__ == "__main__":
    main()
