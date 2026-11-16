from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProviderName = Literal["ollama", "openai", "gemini"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    telegram_bot_token: str = ""
    admin_chat_id: int | None = None
    allowed_chat_ids_raw: str = Field(default="", validation_alias="ALLOWED_CHAT_IDS")
    llm_provider: LLMProviderName = "ollama"
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str = ""
    daily_llm_budget: int = 200
    hourly_searches_limit: int = 10
    default_timezone: str = "America/Fortaleza"
    db_path: Path = Path("data/newsbrief.db")
    rss_feeds_config: Path = Path("configs/feeds.yaml")
    providers_config: Path = Path("configs/providers.yaml")
    request_timeout_seconds: float = 8.0
    max_results: int = 5
    channels: str = "telegram"
    whatsapp_api_url: str = ""
    whatsapp_api_token: str = ""
    health_port: int = 0

    @field_validator("default_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        values = self.allowed_chat_ids_raw.split(",")
        try:
            ids = {int(value.strip()) for value in values if value.strip()}
        except ValueError as error:
            raise ValueError(
                "ALLOWED_CHAT_IDS must be a comma-separated list of integers."
            ) from error
        if self.admin_chat_id is not None:
            ids.add(self.admin_chat_id)
        return frozenset(ids)

    @property
    def active_channels(self) -> tuple[str, ...]:
        return tuple(ch.strip().casefold() for ch in self.channels.split(",") if ch.strip())
