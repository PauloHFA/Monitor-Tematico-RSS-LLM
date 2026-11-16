import httpx
import pytest
import respx

from newsbrief.llm.ollama import OllamaSummarizer
from newsbrief.providers.base import NewsItem


@pytest.fixture
def item() -> NewsItem:
    return NewsItem(
        title="Título",
        url="https://example.com/story",
        source="Fonte",
        published_at=None,
        snippet="Trecho de notícia.",
    )


@pytest.mark.asyncio
@respx.mock
async def test_ollama_summarizes_with_local_api(item: NewsItem) -> None:
    route = respx.post("http://ollama:11434/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "• Resumo factual"})
    )
    async with httpx.AsyncClient() as client:
        summary = await OllamaSummarizer(client, "http://ollama:11434/", "modelo").summarize(item)

    assert summary == "• Resumo factual"
    assert route.called
    assert "não confiável" in route.calls.last.request.content.decode()


@pytest.mark.asyncio
@respx.mock
async def test_ollama_rejects_empty_response(item: NewsItem) -> None:
    respx.post("http://ollama:11434/api/generate").mock(
        return_value=httpx.Response(200, json={"response": " "})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="empty summary"):
            await OllamaSummarizer(client, "http://ollama:11434", "modelo").summarize(item)
