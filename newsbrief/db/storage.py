import asyncio
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from newsbrief.pipeline.normalize import url_hash


@dataclass(frozen=True, slots=True)
class SentItem:
    url_hash: str
    title: str


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = await aiosqlite.connect(self._path)
            await self._connection.execute("PRAGMA journal_mode=WAL")
            await self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS active_topics (
                    chat_id INTEGER PRIMARY KEY,
                    query TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS sent_items (
                    chat_id INTEGER NOT NULL,
                    topic_key TEXT NOT NULL,
                    url_hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, topic_key, url_hash)
                );
                """
            )
            await self._connection.commit()

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                await self._connection.close()
                self._connection = None

    async def set_active_topic(self, chat_id: int, query: str) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                """
                INSERT INTO active_topics (chat_id, query, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(chat_id) DO UPDATE SET
                    query = excluded.query,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, query),
            )
            await connection.commit()

    async def get_active_topic(self, chat_id: int) -> str | None:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                "SELECT query FROM active_topics WHERE chat_id = ?",
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return str(row[0]) if row is not None else None

    async def get_sent_items(self, chat_id: int, topic_key: str) -> list[SentItem]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT url_hash, title FROM sent_items
                WHERE chat_id = ? AND topic_key = ?
                """,
                (chat_id, topic_key),
            ) as cursor:
                rows = await cursor.fetchall()
        return [SentItem(url_hash=str(row[0]), title=str(row[1])) for row in rows]

    async def record_sent(
        self,
        chat_id: int,
        topic_key: str,
        url: str,
        title: str,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                """
                INSERT OR IGNORE INTO sent_items (chat_id, topic_key, url_hash, title)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, topic_key, url_hash(url), title),
            )
            await connection.commit()

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not initialized.")
        return self._connection
