import httpx

from newsbrief.config import Settings
from newsbrief.llm.base import Summarizer
from newsbrief.llm.ollama import OllamaSummarizer, summarize_with_retries
from newsbrief.providers.base import NewsItem
from newsbrief.providers.rss import request_with_retries


class OpenAISummarizer:
    """OpenAI-compatible /chat/completions summarizer."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY must be configured for openai provider.")
        self._client = client
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    async def summarize(self, item: NewsItem) -> str:
        return await summarize_with_retries(item, self._generate)

    async def _generate(self, prompt: str) -> str:
        response = await request_with_retries(
            self._client,
            "POST",
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
        )
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("OpenAI returned an invalid response document.")
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("OpenAI returned no choices.")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenAI returned an empty summary.")
        return content.strip()


class GeminiSummarizer:
    """Google Gemini generateContent summarizer."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        model: str = "gemini-2.0-flash",
    ) -> None:
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY must be configured for gemini provider.")
        self._client = client
        self._api_key = api_key
        self._model = model

    async def summarize(self, item: NewsItem) -> str:
        return await summarize_with_retries(item, self._generate)

    async def _generate(self, prompt: str) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        )
        response = await request_with_retries(
            self._client,
            "POST",
            url,
            params={"key": self._api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
        )
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Gemini returned an invalid response document.")
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("Gemini returned no candidates.")
        parts = candidates[0].get("content", {}).get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("Gemini returned empty parts.")
        text = parts[0].get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Gemini returned an empty summary.")
        return text.strip()


def create_summarizer(settings: Settings, client: httpx.AsyncClient) -> Summarizer:
    if settings.llm_provider == "openai":
        return OpenAISummarizer(
            client,
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            base_url=settings.openai_base_url,
        )
    if settings.llm_provider == "gemini":
        return GeminiSummarizer(
            client,
            api_key=settings.gemini_api_key,
            model=settings.llm_model,
        )
    return OllamaSummarizer(client, settings.ollama_host, settings.llm_model)
