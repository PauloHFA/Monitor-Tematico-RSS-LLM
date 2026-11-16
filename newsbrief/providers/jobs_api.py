from datetime import UTC, datetime

import httpx

from newsbrief.pipeline.normalize import clean_html, normalize_url
from newsbrief.providers.base import NewsItem
from newsbrief.providers.rss import request_with_retries


class RemotiveJobsProvider:
    """Public Remotive API provider for remote tech jobs (RF10/RF11)."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "Remotive Jobs API"

    async def search(self, query: str) -> list[NewsItem]:
        response = await request_with_retries(
            self._client,
            "GET",
            "https://remotive.com/api/remote-jobs",
            params={"search": query, "limit": 20},
        )
        data = response.json()
        raw_jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(raw_jobs, list):
            return []

        items: list[NewsItem] = []
        for job in raw_jobs:
            if not isinstance(job, dict):
                continue
            raw_url = job.get("url")
            title = str(job.get("title") or "").strip()
            if not title or not isinstance(raw_url, str):
                continue
            url = normalize_url(raw_url)
            if url is None:
                continue
            company = str(job.get("company_name") or "não informada").strip()
            location = str(job.get("candidate_required_location") or "Remoto").strip()
            salary = str(job.get("salary") or "não informado").strip() or "não informado"
            published_at = _parse_iso_datetime(job.get("publication_date"))
            desc = clean_html(str(job.get("description") or "")).strip()
            snippet = f"Empresa: {company}. Local: {location}. Salário: {salary}. {desc}"
            items.append(
                NewsItem(
                    title=f"{title} — {company}"[:500],
                    url=url,
                    source=self.name,
                    published_at=published_at,
                    snippet=snippet[:2000],
                    content_type="vaga",
                )
            )
        return items


class HackerNewsAlgoliaProvider:
    """Public Hacker News Algolia search API provider for tech news and hiring threads."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "Hacker News Algolia"

    async def search(self, query: str) -> list[NewsItem]:
        response = await request_with_retries(
            self._client,
            "GET",
            "https://hn.algolia.com/api/v1/search",
            params={"query": query, "tags": "story", "hitsPerPage": 15},
        )
        data = response.json()
        hits = data.get("hits") if isinstance(data, dict) else None
        if not isinstance(hits, list):
            return []

        items: list[NewsItem] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            title = str(hit.get("title") or "").strip()
            raw_url = hit.get("url") or (
                f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                if hit.get("objectID")
                else None
            )
            if not title or not isinstance(raw_url, str):
                continue
            url = normalize_url(raw_url)
            if url is None:
                continue
            published_at = _parse_iso_datetime(hit.get("created_at"))
            snippet = clean_html(str(hit.get("story_text") or "")).strip()
            if not snippet:
                author = hit.get("author") or "HN"
                points = hit.get("points") or 0
                snippet = f"Discussão no Hacker News por {author} ({points} pontos)."
            content_type = "vaga" if "hiring" in title.casefold() else "noticia"
            items.append(
                NewsItem(
                    title=title[:500],
                    url=url,
                    source=self.name,
                    published_at=published_at,
                    snippet=snippet[:2000],
                    content_type=content_type,
                )
            )
        return items


def _parse_iso_datetime(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
