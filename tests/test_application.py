import httpx
import pytest

from newsbrief.bot.application import build_application
from newsbrief.bot.handlers import BotServices
from newsbrief.config import Settings
from newsbrief.db.storage import Database
from newsbrief.pipeline.search import NewsSearchService


class EmptySummarizer:
    async def summarize(self, item: object) -> str:
        del item
        return ""


@pytest.mark.asyncio
async def test_application_registers_commands_and_closes_services(tmp_path) -> None:
    database = Database(tmp_path / "newsbrief.db")
    client = httpx.AsyncClient()
    services = BotServices(
        settings=Settings(telegram_bot_token="123:abc"),
        database=database,
        search=NewsSearchService([]),
        summarizer=EmptySummarizer(),
        http_client=client,
    )

    application = build_application("123:abc", services)
    assert len(application.handlers[0]) == 5
    assert application.update_processor.max_concurrent_updates == 20
    assert application.post_init is not None
    assert application.post_shutdown is not None
    await application.post_init(application)
    await database.set_active_topic(42, "teste")
    await application.post_shutdown(application)

    assert client.is_closed
    with pytest.raises(RuntimeError, match="not initialized"):
        await database.get_active_topic(42)
