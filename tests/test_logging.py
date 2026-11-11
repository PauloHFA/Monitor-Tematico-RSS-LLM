from newsbrief.logging_config import configure_logging


def test_logging_configuration_can_be_initialized() -> None:
    configure_logging()
