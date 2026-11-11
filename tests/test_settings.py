import pytest

from newsbrief.config import Settings
from newsbrief.main import _validate_settings


def test_allowed_chat_ids_are_parsed_from_csv() -> None:
    settings = Settings(
        telegram_bot_token="token",
        allowed_chat_ids_raw="123, 456",
    )

    assert settings.allowed_chat_ids == frozenset({123, 456})


def test_invalid_allowed_chat_id_is_reported() -> None:
    settings = Settings(allowed_chat_ids_raw="not-a-number")
    with pytest.raises(ValueError, match="comma-separated"):
        _ = settings.allowed_chat_ids


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (Settings(), "TELEGRAM_BOT_TOKEN"),
        (
            Settings(telegram_bot_token="token", request_timeout_seconds=0),
            "REQUEST_TIMEOUT_SECONDS",
        ),
        (
            Settings(telegram_bot_token="token", max_results=11),
            "MAX_RESULTS",
        ),
    ],
)
def test_runtime_settings_are_validated(settings: Settings, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_settings(settings)
