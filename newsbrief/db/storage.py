import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from newsbrief.pipeline.normalize import topic_key, url_hash


@dataclass(frozen=True, slots=True)
class SentItem:
    url_hash: str
    title: str


@dataclass(frozen=True, slots=True)
class UserPreferences:
    chat_id: int
    timezone: str = "America/Fortaleza"
    lang: str = "pt"
    default_limit: int = 5
    free_text: bool = True
    single_summary_mode: bool = False
    expand_query: bool = True
    paused: bool = False
    authorized: bool | None = None


@dataclass(frozen=True, slots=True)
class TopicRecord:
    id: int
    chat_id: int
    query: str
    topic_type: str | None
    limit_items: int | None
    period_days: int | None
    filters: str
    lang: str | None
    alias: str | None
    is_favorite: bool
    last_used_at: str


@dataclass(frozen=True, slots=True)
class ScheduleRecord:
    id: int
    chat_id: int
    topic_query: str
    time_hhmm: str
    days_spec: str
    timezone: str
    enabled: bool
    last_run_date: str | None


@dataclass(frozen=True, slots=True)
class WeeklyTopicStat:
    topic_key: str
    searches: int
    items_total: int
    tokens_total: int


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def is_initialized(self) -> bool:
        return self._connection is not None

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
                CREATE TABLE IF NOT EXISTS user_preferences (
                    chat_id INTEGER PRIMARY KEY,
                    timezone TEXT NOT NULL DEFAULT 'America/Fortaleza',
                    lang TEXT NOT NULL DEFAULT 'pt',
                    default_limit INTEGER NOT NULL DEFAULT 5,
                    free_text INTEGER NOT NULL DEFAULT 1,
                    single_summary_mode INTEGER NOT NULL DEFAULT 0,
                    expand_query INTEGER NOT NULL DEFAULT 1,
                    paused INTEGER NOT NULL DEFAULT 0,
                    authorized INTEGER
                );
                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    query TEXT NOT NULL,
                    topic_key TEXT NOT NULL,
                    topic_type TEXT,
                    limit_items INTEGER,
                    period_days INTEGER,
                    filters TEXT NOT NULL DEFAULT '',
                    lang TEXT,
                    alias TEXT,
                    is_favorite INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_topics_chat_last ON topics (chat_id, last_used_at);
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    topic_query TEXT NOT NULL,
                    time_hhmm TEXT NOT NULL,
                    days_spec TEXT NOT NULL DEFAULT '*',
                    timezone TEXT NOT NULL DEFAULT 'America/Fortaleza',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_run_date TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS search_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    topic_key TEXT NOT NULL,
                    topic_type TEXT NOT NULL,
                    providers_used TEXT NOT NULL,
                    items_count INTEGER NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        key = topic_key(query)
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
            await connection.execute(
                """
                DELETE FROM topics
                WHERE chat_id = ? AND topic_key = ? AND is_favorite = 0
                """,
                (chat_id, key),
            )
            await connection.execute(
                """
                INSERT INTO topics (chat_id, query, topic_key, is_favorite, last_used_at)
                VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
                """,
                (chat_id, query, key),
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

    async def get_sent_items(self, chat_id: int, topic_key_val: str) -> list[SentItem]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT url_hash, title FROM sent_items
                WHERE chat_id = ? AND topic_key = ?
                """,
                (chat_id, topic_key_val),
            ) as cursor:
                rows = await cursor.fetchall()
        return [SentItem(url_hash=str(row[0]), title=str(row[1])) for row in rows]

    async def record_sent(
        self,
        chat_id: int,
        topic_key_val: str,
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
                (chat_id, topic_key_val, url_hash(url), title),
            )
            await connection.commit()

    async def get_preferences(
        self,
        chat_id: int,
        *,
        default_timezone: str = "America/Fortaleza",
        default_limit: int = 5,
    ) -> UserPreferences:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT chat_id, timezone, lang, default_limit, free_text,
                       single_summary_mode, expand_query, paused, authorized
                FROM user_preferences WHERE chat_id = ?
                """,
                (chat_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return UserPreferences(
                chat_id=chat_id,
                timezone=default_timezone,
                default_limit=default_limit,
            )
        return UserPreferences(
            chat_id=int(row[0]),
            timezone=str(row[1]),
            lang=str(row[2]),
            default_limit=int(row[3]),
            free_text=bool(row[4]),
            single_summary_mode=bool(row[5]),
            expand_query=bool(row[6]),
            paused=bool(row[7]),
            authorized=None if row[8] is None else bool(row[8]),
        )

    async def update_preferences(
        self,
        chat_id: int,
        *,
        timezone: str | None = None,
        lang: str | None = None,
        default_limit: int | None = None,
        free_text: bool | None = None,
        single_summary_mode: bool | None = None,
        expand_query: bool | None = None,
        paused: bool | None = None,
        authorized: bool | None = None,
    ) -> UserPreferences:
        current = await self.get_preferences(chat_id)
        updated = UserPreferences(
            chat_id=chat_id,
            timezone=timezone if timezone is not None else current.timezone,
            lang=lang if lang is not None else current.lang,
            default_limit=default_limit if default_limit is not None else current.default_limit,
            free_text=free_text if free_text is not None else current.free_text,
            single_summary_mode=(
                single_summary_mode
                if single_summary_mode is not None
                else current.single_summary_mode
            ),
            expand_query=expand_query if expand_query is not None else current.expand_query,
            paused=paused if paused is not None else current.paused,
            authorized=authorized if authorized is not None else current.authorized,
        )
        auth_val: int | None = None if updated.authorized is None else int(updated.authorized)
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                """
                INSERT INTO user_preferences (
                    chat_id, timezone, lang, default_limit, free_text,
                    single_summary_mode, expand_query, paused, authorized
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    timezone = excluded.timezone,
                    lang = excluded.lang,
                    default_limit = excluded.default_limit,
                    free_text = excluded.free_text,
                    single_summary_mode = excluded.single_summary_mode,
                    expand_query = excluded.expand_query,
                    paused = excluded.paused,
                    authorized = excluded.authorized
                """,
                (
                    chat_id,
                    updated.timezone,
                    updated.lang,
                    updated.default_limit,
                    int(updated.free_text),
                    int(updated.single_summary_mode),
                    int(updated.expand_query),
                    int(updated.paused),
                    auth_val,
                ),
            )
            await connection.commit()
        return updated

    async def set_topic_params(
        self,
        chat_id: int,
        query: str,
        *,
        topic_type: str | None = None,
        limit_items: int | None = None,
        period_days: int | None = None,
        filters: str | None = None,
        lang: str | None = None,
    ) -> None:
        key = topic_key(query)
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT id, topic_type, limit_items, period_days, filters, lang
                FROM topics WHERE chat_id = ? AND topic_key = ?
                ORDER BY is_favorite DESC, id DESC LIMIT 1
                """,
                (chat_id, key),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                await connection.execute(
                    """
                    INSERT INTO topics (
                        chat_id, query, topic_key, topic_type, limit_items,
                        period_days, filters, lang, is_favorite, last_used_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                    """,
                    (
                        chat_id,
                        query,
                        key,
                        topic_type,
                        limit_items,
                        period_days,
                        filters or "",
                        lang,
                    ),
                )
            else:
                await connection.execute(
                    """
                    UPDATE topics SET
                        topic_type = ?,
                        limit_items = ?,
                        period_days = ?,
                        filters = ?,
                        lang = ?,
                        last_used_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        topic_type if topic_type is not None else row[1],
                        limit_items if limit_items is not None else row[2],
                        period_days if period_days is not None else row[3],
                        filters if filters is not None else row[4],
                        lang if lang is not None else row[5],
                        int(row[0]),
                    ),
                )
            await connection.commit()

    async def get_topic_params(self, chat_id: int, query: str) -> TopicRecord | None:
        key = topic_key(query)
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT id, chat_id, query, topic_type, limit_items, period_days,
                       filters, lang, alias, is_favorite, last_used_at
                FROM topics WHERE chat_id = ? AND topic_key = ?
                ORDER BY is_favorite DESC, id DESC LIMIT 1
                """,
                (chat_id, key),
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_topic(row) if row is not None else None

    async def list_topic_history(self, chat_id: int, *, limit: int = 10) -> list[TopicRecord]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT id, chat_id, query, topic_type, limit_items, period_days,
                       filters, lang, alias, is_favorite, last_used_at
                FROM topics WHERE chat_id = ?
                ORDER BY last_used_at DESC, id DESC LIMIT ?
                """,
                (chat_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_topic(row) for row in rows]

    async def save_favorite_topic(
        self,
        chat_id: int,
        query: str,
        *,
        alias: str | None = None,
        overwrite: bool = False,
    ) -> tuple[bool, int]:
        """Save favorite topic. Returns (saved, existing_id_if_conflict)."""
        key = topic_key(query)
        clean_alias = alias.strip() if alias else None
        async with self._lock:
            connection = self._require_connection()
            if clean_alias:
                async with connection.execute(
                    """
                    SELECT id FROM topics
                    WHERE chat_id = ? AND is_favorite = 1 AND lower(alias) = lower(?)
                    """,
                    (chat_id, clean_alias),
                ) as cursor:
                    existing = await cursor.fetchone()
                if existing is not None:
                    existing_id = int(existing[0])
                    if not overwrite:
                        return False, existing_id
                    await connection.execute(
                        """
                        UPDATE topics
                        SET query = ?, topic_key = ?, last_used_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (query, key, existing_id),
                    )
                    await connection.commit()
                    return True, existing_id

            cursor = await connection.execute(
                """
                INSERT INTO topics (chat_id, query, topic_key, alias, is_favorite, last_used_at)
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                """,
                (chat_id, query, key, clean_alias),
            )
            await connection.commit()
            return True, int(cursor.lastrowid or 0)

    async def list_favorite_topics(self, chat_id: int) -> list[TopicRecord]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT id, chat_id, query, topic_type, limit_items, period_days,
                       filters, lang, alias, is_favorite, last_used_at
                FROM topics WHERE chat_id = ? AND is_favorite = 1
                ORDER BY id ASC
                """,
                (chat_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_topic(row) for row in rows]

    async def remove_favorite_topic(self, chat_id: int, index_1_based: int) -> bool:
        favorites = await self.list_favorite_topics(chat_id)
        if index_1_based < 1 or index_1_based > len(favorites):
            return False
        target = favorites[index_1_based - 1]
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                "DELETE FROM topics WHERE id = ? AND chat_id = ?",
                (target.id, chat_id),
            )
            await connection.commit()
        return True

    async def add_schedule(
        self,
        chat_id: int,
        topic_query: str,
        time_hhmm: str,
        *,
        days_spec: str = "*",
        timezone: str = "America/Fortaleza",
    ) -> int:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                INSERT INTO schedules (chat_id, topic_query, time_hhmm, days_spec, timezone)
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, topic_query, time_hhmm, days_spec, timezone),
            )
            await connection.commit()
            return int(cursor.lastrowid or 0)

    async def list_schedules(self, chat_id: int) -> list[ScheduleRecord]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT id, chat_id, topic_query, time_hhmm, days_spec,
                       timezone, enabled, last_run_date
                FROM schedules WHERE chat_id = ? ORDER BY id ASC
                """,
                (chat_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_schedule(row) for row in rows]

    async def list_enabled_schedules(self) -> list[ScheduleRecord]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT id, chat_id, topic_query, time_hhmm, days_spec,
                       timezone, enabled, last_run_date
                FROM schedules WHERE enabled = 1
                """
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_schedule(row) for row in rows]

    async def remove_schedule(self, chat_id: int, schedule_id: int) -> bool:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                "DELETE FROM schedules WHERE id = ? AND chat_id = ?",
                (schedule_id, chat_id),
            )
            await connection.commit()
            return cursor.rowcount > 0

    async def mark_schedule_run(self, schedule_id: int, local_date_iso: str) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                "UPDATE schedules SET last_run_date = ? WHERE id = ?",
                (local_date_iso, schedule_id),
            )
            await connection.commit()

    async def log_search(
        self,
        chat_id: int,
        topic_key_val: str,
        topic_type: str,
        providers_used: list[str],
        items_count: int,
        duration_ms: int,
        tokens_used: int = 0,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                """
                INSERT INTO search_log (
                    chat_id, topic_key, topic_type, providers_used,
                    items_count, duration_ms, tokens_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    topic_key_val,
                    topic_type,
                    ",".join(providers_used),
                    items_count,
                    duration_ms,
                    tokens_used,
                ),
            )
            await connection.commit()

    async def count_recent_searches(self, chat_id: int, *, hours: int = 1) -> int:
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT COUNT(*) FROM search_log
                WHERE chat_id = ? AND created_at >= ?
                """,
                (chat_id, cutoff),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def get_daily_tokens_used(self) -> int:
        today_prefix = datetime.now(UTC).strftime("%Y-%m-%d")
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT COALESCE(SUM(tokens_used), 0) FROM search_log
                WHERE substr(created_at, 1, 10) = ?
                """,
                (today_prefix,),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def weekly_stats(self, chat_id: int) -> list[WeeklyTopicStat]:
        cutoff = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT topic_key, COUNT(*), COALESCE(SUM(items_count), 0),
                       COALESCE(SUM(tokens_used), 0)
                FROM search_log
                WHERE chat_id = ? AND created_at >= ?
                GROUP BY topic_key
                ORDER BY COUNT(*) DESC
                """,
                (chat_id, cutoff),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            WeeklyTopicStat(
                topic_key=str(r[0]),
                searches=int(r[1]),
                items_total=int(r[2]),
                tokens_total=int(r[3]),
            )
            for r in rows
        ]

    async def erase_user_data(self, chat_id: int) -> None:
        """Delete all stored data for a chat_id (/apagar_meus_dados - RNF07)."""
        async with self._lock:
            connection = self._require_connection()
            for table in (
                "active_topics",
                "sent_items",
                "topics",
                "schedules",
                "search_log",
                "user_preferences",
            ):
                await connection.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
            await connection.commit()

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not initialized.")
        return self._connection


def _row_to_topic(row: aiosqlite.Row | tuple[object, ...]) -> TopicRecord:
    return TopicRecord(
        id=int(row[0]),  # type: ignore[arg-type]
        chat_id=int(row[1]),  # type: ignore[arg-type]
        query=str(row[2]),
        topic_type=str(row[3]) if row[3] is not None else None,
        limit_items=int(row[4]) if row[4] is not None else None,  # type: ignore[arg-type]
        period_days=int(row[5]) if row[5] is not None else None,  # type: ignore[arg-type]
        filters=str(row[6] or ""),
        lang=str(row[7]) if row[7] is not None else None,
        alias=str(row[8]) if row[8] is not None else None,
        is_favorite=bool(row[9]),
        last_used_at=str(row[10]),
    )


def _row_to_schedule(row: aiosqlite.Row | tuple[object, ...]) -> ScheduleRecord:
    return ScheduleRecord(
        id=int(row[0]),  # type: ignore[arg-type]
        chat_id=int(row[1]),  # type: ignore[arg-type]
        topic_query=str(row[2]),
        time_hhmm=str(row[3]),
        days_spec=str(row[4]),
        timezone=str(row[5]),
        enabled=bool(row[6]),
        last_run_date=str(row[7]) if row[7] is not None else None,
    )
