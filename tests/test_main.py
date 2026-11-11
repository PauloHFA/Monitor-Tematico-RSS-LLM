import httpx

import newsbrief.main as main_module
from newsbrief.config import Settings


def test_main_builds_polling_application_from_environment(monkeypatch) -> None:
    settings = Settings(telegram_bot_token="123:abc")
    app_calls: list[tuple[str, object]] = []

    class FakeClient:
        async def aclose(self) -> None:
            return None

    class FakeApplication:
        def run_polling(self) -> None:
            app_calls.append(("run", None))

    client = FakeClient()
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "configure_logging", lambda: None)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(main_module, "load_curated_feeds", lambda path: [])
    monkeypatch.setattr(
        main_module,
        "build_application",
        lambda token, services: (
            app_calls.append((token, services)) or FakeApplication()
        ),
    )

    main_module.main()

    assert app_calls[0][0] == "123:abc"
    assert app_calls[-1] == ("run", None)
    services = app_calls[0][1]
    assert services.http_client is client
    assert len(services.search._providers) == 1


def test_main_logs_when_allowlist_is_empty(monkeypatch) -> None:
    settings = Settings(telegram_bot_token="123:abc")

    class FakeClient(httpx.AsyncClient):
        pass

    client = FakeClient()
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "configure_logging", lambda: None)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(main_module, "load_curated_feeds", lambda path: [])
    monkeypatch.setattr(
        main_module,
        "build_application",
        lambda token, services: type(
            "FakeApplication",
            (),
            {"run_polling": lambda self: None},
        )(),
    )

    main_module.main()

    assert client.is_closed is False
