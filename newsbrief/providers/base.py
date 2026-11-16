from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

TopicType = Literal["noticia", "vaga", "edital"]
LanguageFilter = Literal["pt", "en", "all"]


@dataclass(frozen=True, slots=True)
class SearchFilters:
    limit: int = 5
    period_days: int | None = None
    include_terms: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    lang: LanguageFilter = "pt"


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
