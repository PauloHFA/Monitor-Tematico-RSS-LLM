import pytest

from newsbrief.db.storage import Database


@pytest.mark.asyncio
async def test_topics_and_sent_items_persist_with_chat_and_topic_scope(tmp_path) -> None:
    path = tmp_path / "newsbrief.db"
    database = Database(path)
    await database.initialize()

    await database.set_active_topic(123, "Python remoto")
    await database.record_sent(
        123,
        "python remoto",
        "https://example.com/story?utm_source=rss",
        "Notícia",
    )

    assert await database.get_active_topic(123) == "Python remoto"
    assert await database.get_sent_items(123, "python remoto")
    assert await database.get_sent_items(456, "python remoto") == []
    assert await database.get_sent_items(123, "outro tema") == []
    await database.close()

    reopened = Database(path)
    await reopened.initialize()
    assert await reopened.get_active_topic(123) == "Python remoto"
    assert len(await reopened.get_sent_items(123, "python remoto")) == 1
    await reopened.close()


async def test_database_requires_initialization(tmp_path) -> None:
    database = Database(tmp_path / "missing.db")
    with pytest.raises(RuntimeError, match="not initialized"):
        await database.get_active_topic(1)
