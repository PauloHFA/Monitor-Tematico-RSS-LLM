from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    telegram_bot_token: str = ""
    allowed_chat_ids_raw: str = Field(default="", validation_alias="ALLOWED_CHAT_IDS")
    ollama_host: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b"
    db_path: Path = Path("data/newsbrief.db")
    rss_feeds_config: Path = Path("configs/feeds.yaml")
    request_timeout_seconds: float = 8.0
    max_results: int = 5

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        values = self.allowed_chat_ids_raw.split(",")
        try:
            return frozenset(int(value.strip()) for value in values if value.strip())
        except ValueError as error:
            raise ValueError(
                "ALLOWED_CHAT_IDS must be a comma-separated list of integers."
            ) from error
