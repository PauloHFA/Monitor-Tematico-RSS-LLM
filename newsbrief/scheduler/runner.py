import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from newsbrief.db.storage import Database, ScheduleRecord

logger = structlog.get_logger(__name__)

_DAY_TOKENS = {
    "seg": 0,
    "ter": 1,
    "qua": 2,
    "qui": 3,
    "sex": 4,
    "sab": 5,
    "dom": 6,
}


def is_schedule_due(schedule: ScheduleRecord, now_utc: datetime) -> bool:
    if not schedule.enabled:
        return False
    local_now = now_utc.astimezone(ZoneInfo(schedule.timezone))
    today_iso = local_now.date().isoformat()
    if schedule.last_run_date == today_iso:
        return False
    if schedule.time_hhmm != local_now.strftime("%H:%M"):
        return False
    if schedule.days_spec in {"", "*"}:
        return True
    allowed_days = {
        _DAY_TOKENS[token.strip().casefold()[:3]]
        for token in schedule.days_spec.split(",")
        if token.strip().casefold()[:3] in _DAY_TOKENS
    }
    if not allowed_days:
        return True
    return local_now.weekday() in allowed_days


class ScheduleRunner:
    def __init__(
        self,
        database: Database,
        dispatch_fn: Callable[[ScheduleRecord], Awaitable[None]],
        *,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self._database = database
        self._dispatch_fn = dispatch_fn
        self._poll_interval = poll_interval_seconds

    async def tick(self, *, now_utc: datetime | None = None) -> int:
        current = now_utc or datetime.now(ZoneInfo("UTC"))
        schedules = await self._database.list_enabled_schedules()
        ran = 0
        for schedule in schedules:
            if not is_schedule_due(schedule, current):
                continue
            prefs = await self._database.get_preferences(schedule.chat_id)
            if prefs.paused:
                continue
            local_date = current.astimezone(ZoneInfo(schedule.timezone)).date().isoformat()
            try:
                await self._dispatch_fn(schedule)
                await self._database.mark_schedule_run(schedule.id, local_date)
                ran += 1
            except Exception as exc:
                logger.warning(
                    "scheduled_search_failed",
                    schedule_id=schedule.id,
                    error=str(exc),
                )
        return ran

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval)
            except TimeoutError:
                continue
