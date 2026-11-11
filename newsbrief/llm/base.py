from typing import Protocol

from newsbrief.providers.base import NewsItem


class Summarizer(Protocol):
    async def summarize(self, item: NewsItem) -> str: ...
