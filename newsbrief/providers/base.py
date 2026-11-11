from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class NewsItem:
    title: str
    url: str
    source: str
    published_at: datetime | None
    snippet: str
    content_type: str = "noticia"


class SearchProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def search(self, query: str) -> list[NewsItem]: ...
