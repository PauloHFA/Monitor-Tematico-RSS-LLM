import httpx
import structlog

from newsbrief.bot.application import build_application
from newsbrief.bot.handlers import BotServices
from newsbrief.config import Settings
from newsbrief.db.storage import Database
from newsbrief.llm.clients import create_summarizer
from newsbrief.logging_config import configure_logging
from newsbrief.notify.whatsapp import WhatsAppNotifier
from newsbrief.pipeline.search import NewsSearchService
from newsbrief.providers.base import SearchProvider
from newsbrief.providers.jobs_api import HackerNewsAlgoliaProvider, RemotiveJobsProvider
from newsbrief.providers.rss import (
    CuratedRSSProvider,
    GoogleNewsRSSProvider,
    load_curated_feeds,
    load_providers_config,
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
    provider_cfg = {cfg.name: cfg for cfg in load_providers_config(settings.providers_config)}
    providers: list[SearchProvider] = []
    provider_types: dict[str, tuple[str, ...]] = {}

    if provider_cfg.get("google_news_rss") is None or provider_cfg["google_news_rss"].enabled:
        gn = GoogleNewsRSSProvider(client)
        providers.append(gn)
        if "google_news_rss" in provider_cfg:
            provider_types[gn.name] = provider_cfg["google_news_rss"].types
    if provider_cfg.get("curated_rss") is None or provider_cfg["curated_rss"].enabled:
        for feed in feeds:
            cp = CuratedRSSProvider(client, feed)
            providers.append(cp)
            provider_types[cp.name] = (feed.topic_type,)
    if "remotive_jobs" in provider_cfg and provider_cfg["remotive_jobs"].enabled:
        rj = RemotiveJobsProvider(client)
        providers.append(rj)
        provider_types[rj.name] = provider_cfg["remotive_jobs"].types
    if "hn_algolia" in provider_cfg and provider_cfg["hn_algolia"].enabled:
        hn = HackerNewsAlgoliaProvider(client)
        providers.append(hn)
        provider_types[hn.name] = provider_cfg["hn_algolia"].types

    whatsapp = (
        WhatsAppNotifier(
            client,
            api_url=settings.whatsapp_api_url,
            api_token=settings.whatsapp_api_token,
        )
        if "whatsapp" in settings.active_channels and settings.whatsapp_api_url.strip()
        else None
    )

    services = BotServices(
        settings=settings,
        database=Database(settings.db_path),
        search=NewsSearchService(
            providers,
            max_results=settings.max_results,
            provider_types=provider_types,
        ),
        summarizer=create_summarizer(settings, client),
        http_client=client,
        whatsapp=whatsapp,
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
