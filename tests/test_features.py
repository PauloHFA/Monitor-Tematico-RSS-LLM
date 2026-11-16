import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import respx

from newsbrief.bot.formatting import (
    format_news_message,
    format_single_digest,
    split_telegram_message,
)
from newsbrief.bot.handlers import (
    BotServices,
    handle_admin_access,
    handle_callback,
    handle_free_text,
    handle_history,
    handle_list_favorites,
    handle_list_schedules,
    handle_menu,
    handle_pause_resume,
    handle_remove_favorite,
    handle_save_topic,
    handle_schedule,
    handle_search,
    handle_topic,
    handle_topic_param,
    handle_utility,
    run_scheduled_search,
)
from newsbrief.config import Settings
from newsbrief.db.storage import Database
from newsbrief.health import HealthServer
from newsbrief.llm.clients import GeminiSummarizer, OpenAISummarizer, create_summarizer
from newsbrief.llm.ollama import OllamaSummarizer
from newsbrief.notify.whatsapp import WhatsAppNotifier
from newsbrief.pipeline.search import NewsSearchService
from newsbrief.providers.base import NewsItem
from newsbrief.providers.jobs_api import HackerNewsAlgoliaProvider, RemotiveJobsProvider
from newsbrief.providers.rss import load_providers_config
from newsbrief.scheduler.runner import ScheduleRunner, is_schedule_due
from newsbrief.topics.classifier import classify_topic, expand_query


class _FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.sent: list[str] = []

    async def reply_text(self, text: str, **kwargs: object) -> object:
        del kwargs
        self.sent.append(text)
        return object()


def _make_update(chat_id: int, text: str = "") -> SimpleNamespace:
    msg = _FakeMessage(text)
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=msg,
        message=msg,
    )


def _make_ctx(services: BotServices, args: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        args=args or [],
        application=SimpleNamespace(bot_data={"services": services}),
    )


def test_classifier_and_query_expander() -> None:
    assert classify_topic("emprego programador python") == "vaga"
    assert classify_topic("seleção pós-graduação mestrado") == "edital"
    assert classify_topic("lançamento do Docker") == "noticia"
    expanded = expand_query("programador remoto", enabled=True)
    assert len(expanded) == 2
    assert "desenvolvedor" in expanded[1]
    assert expand_query("programador remoto", enabled=False) == ["programador remoto"]


@respx.mock
async def test_remotive_and_hn_providers_and_provider_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "providers.yaml"
    cfg_path.write_text(
        "providers:\n  - name: remotive_jobs\n    enabled: true\n"
        "    priority: 1\n    types: [vaga]\n",
        encoding="utf-8",
    )
    configs = load_providers_config(cfg_path)
    assert len(configs) == 1
    assert configs[0].types == ("vaga",)

    respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "title": "Python Engineer",
                        "url": "https://remotive.com/job/1?utm_source=api",
                        "company_name": "Acme",
                        "candidate_required_location": "Worldwide",
                        "salary": "",
                        "publication_date": "2026-11-15T10:00:00Z",
                        "description": "<p>FastAPI and SQLite</p>",
                    }
                ]
            },
        )
    )
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "objectID": "99",
                        "title": "Ask HN: Who is hiring?",
                        "url": None,
                        "author": "whoishiring",
                        "points": 120,
                        "created_at": "2026-11-15T12:00:00Z",
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        jobs = await RemotiveJobsProvider(client).search("python")
        hn_items = await HackerNewsAlgoliaProvider(client).search("python")

    assert len(jobs) == 1
    assert jobs[0].content_type == "vaga"
    assert jobs[0].url == "https://remotive.com/job/1"
    assert len(hn_items) == 1
    assert hn_items[0].content_type == "vaga"


@respx.mock
async def test_structured_llm_retries_and_multi_providers() -> None:
    vaga_item = NewsItem(
        title="Dev Python",
        url="https://example.com/vaga",
        source="Remotive",
        published_at=None,
        snippet="Empresa Acme, remoto, Python e FastAPI.",
        content_type="vaga",
    )
    edital_item = NewsItem(
        title="Edital Mestrado",
        url="https://example.com/edital",
        source="UF",
        published_at=None,
        snippet="Inscrições até 10/12 na UFCG.",
        content_type="edital",
    )
    respx.post("http://localhost:11434/api/generate").mock(
        side_effect=[
            httpx.Response(200, json={"response": "not-json"}),
            httpx.Response(
                200,
                json={
                    "response": json.dumps(
                        {
                            "cargo": "Dev Python",
                            "empresa": "Acme",
                            "local_modalidade": "Remoto",
                            "faixa_salarial": "não informado",
                            "requisitos": ["Python", "FastAPI"],
                        }
                    )
                },
            ),
        ]
    )
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "instituicao": "UFCG",
                                    "programa_nivel": "Mestrado",
                                    "prazo_inscricao": "10/12",
                                    "vagas_linhas": "não informado",
                                }
                            )
                        }
                    }
                ]
            },
        )
    )
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "Resumo curto Gemini."}]}}]},
        )
    )
    async with httpx.AsyncClient() as client:
        ollama = OllamaSummarizer(client, "http://localhost:11434", "qwen2.5:7b")
        vaga_summary = await ollama.summarize(vaga_item)
        assert "Dev Python — Acme" in vaga_summary
        assert "💼" in format_news_message(vaga_item, vaga_summary)

        openai_sum = OpenAISummarizer(client, api_key="sk-test", model="gpt-4o-mini")
        edital_summary = await openai_sum.summarize(edital_item)
        assert "Mestrado — UFCG" in edital_summary
        assert "🎓" in format_news_message(edital_item, edital_summary)

        gemini_sum = GeminiSummarizer(client, api_key="gm-test")
        news_item = NewsItem("Notícia", "https://example.com/n", "G1", None, "Texto")
        assert await gemini_sum.summarize(news_item) == "Resumo curto Gemini."
        assert isinstance(
            create_summarizer(Settings(llm_provider="ollama"), client), OllamaSummarizer
        )


async def test_bot_full_command_suite_scheduler_health_and_whatsapp(tmp_path: Path) -> None:
    db = Database(tmp_path / "newsbrief.db")
    await db.initialize()

    class _FakeProvider:
        name = "MockRSS"

        async def search(self, query: str) -> list[NewsItem]:
            del query
            return [
                NewsItem(
                    title="Vaga Python Remoto",
                    url="https://example.com/job1",
                    source="MockRSS",
                    published_at=datetime.now(UTC),
                    snippet="Vaga Python remoto com FastAPI.",
                    content_type="vaga",
                )
            ]

    class _FakeSum:
        async def summarize(self, item: NewsItem) -> str:
            del item
            return (
                "Dev Python — Acme\n- Local: Remoto | Salário: não informado\n- Requisitos: Python"
            )

    wa_sent: list[str] = []

    class _FakeWA(WhatsAppNotifier):
        async def send(self, chat_id: int, text: str) -> bool:
            wa_sent.append(f"{chat_id}:{text[:15]}")
            return True

    settings = Settings(
        telegram_bot_token="tok",
        admin_chat_id=1,
        allowed_chat_ids_raw="42",
        channels="telegram,whatsapp",
        hourly_searches_limit=20,
    )
    async with httpx.AsyncClient() as client:
        services = BotServices(
            settings=settings,
            database=db,
            search=NewsSearchService(
                [_FakeProvider()], provider_types={"MockRSS": ("vaga", "noticia")}
            ),
            summarizer=_FakeSum(),
            http_client=client,
            whatsapp=_FakeWA(client, api_url="https://wa.local", api_token="t"),
        )

        # Admin authorizes chat 77
        admin_upd = _make_update(1, "/autorizar 77")
        await handle_admin_access(admin_upd, _make_ctx(services, ["77"]))
        assert "autorizado" in admin_upd.effective_message.sent[0]

        upd = _make_update(77, "/tema vagas python")
        await handle_topic(upd, _make_ctx(services, ["vagas", "python"]))
        assert any("💼" in m for m in upd.effective_message.sent)
        assert wa_sent

        # /buscar tudo re-sends delivered items
        upd_all = _make_update(77, "/buscar tudo")
        await handle_search(upd_all, _make_ctx(services, ["tudo"]))
        assert any("💼" in m for m in upd_all.effective_message.sent)

        # Free text topic + single digest mode + params
        for cmd, args in [
            ("/tipo", ["vaga"]),
            ("/limite", ["3"]),
            ("/periodo", ["7d"]),
            ("/filtro", ["python"]),
            ("/idioma", ["pt"]),
            ("/expandir", ["off"]),
            ("/modo", ["resumo-unico"]),
            ("/fuso", ["America/Fortaleza"]),
            ("/config", ["texto_livre", "on"]),
        ]:
            u = _make_update(77, f"{cmd} {' '.join(args)}")
            await handle_topic_param(u, _make_ctx(services, args))
            assert u.effective_message.sent

        u_free = _make_update(77, "python remoto")
        await handle_free_text(u_free, _make_ctx(services))
        assert any("Buscando: python remoto" in m for m in u_free.effective_message.sent)

        # Favorites, history, menu, schedule, pause/resume, stats, erase
        await handle_save_topic(_make_update(77), _make_ctx(services, ["dev"]))
        fav_upd = _make_update(77)
        await handle_list_favorites(fav_upd, _make_ctx(services))
        assert "dev: python remoto" in fav_upd.effective_message.sent[0]
        rem_upd = _make_update(77)
        await handle_remove_favorite(rem_upd, _make_ctx(services, ["1"]))
        assert "removido" in rem_upd.effective_message.sent[0]

        hist_upd = _make_update(77)
        await handle_history(hist_upd, _make_ctx(services))
        assert "Últimos temas" in hist_upd.effective_message.sent[0]

        menu_upd = _make_update(77)
        await handle_menu(menu_upd, _make_ctx(services))
        assert "Menu rápido" in menu_upd.effective_message.sent[0]

        await handle_schedule(_make_update(77), _make_ctx(services, ["08:00"]))
        sched_upd = _make_update(77)
        await handle_list_schedules(sched_upd, _make_ctx(services))
        assert "08:00" in sched_upd.effective_message.sent[0]

        # Utility commands
        for cmd in ("/fontes", "/status", "/estatisticas"):
            uu = _make_update(77, cmd)
            await handle_utility(uu, _make_ctx(services))
            assert uu.effective_message.sent

        # Callback query
        cb_upd = SimpleNamespace(
            effective_chat=SimpleNamespace(id=77),
            effective_message=_FakeMessage(),
            callback_query=SimpleNamespace(
                data="menu:search_again",
                answer=lambda: _noop(),
            ),
        )
        await handle_callback(cb_upd, _make_ctx(services))

        # Pause/resume and schedule runner tick
        await handle_pause_resume(_make_update(77, "/retomar"), _make_ctx(services))
        schedules = await db.list_enabled_schedules()
        assert len(schedules) == 1
        due_utc = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)  # 08:00 America/Fortaleza
        assert is_schedule_due(schedules[0], due_utc) is True

        dispatched: list[int] = []

        async def _send_fn(**kwargs: object) -> None:
            del kwargs

        runner = ScheduleRunner(
            db,
            lambda sched: _record_and_run(dispatched, sched.id, services, sched, _send_fn),
        )
        assert await runner.tick(now_utc=due_utc) == 1
        assert dispatched == [schedules[0].id]

        # Erase data
        erase_upd = _make_update(77, "/apagar_meus_dados")
        await handle_utility(erase_upd, _make_ctx(services))
        assert await db.get_active_topic(77) is None

    # HealthServer /healthz check
    server = HealthServer(port=0)
    await server.start()
    try:
        async with httpx.AsyncClient() as hc:
            resp = await hc.get(f"http://127.0.0.1:{server.port}/healthz")
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}
    finally:
        await server.stop()

    # Single digest & splitter
    chunks = format_single_digest(
        [
            (
                NewsItem("Título", "https://example.com/1", "Fonte", None, "Trecho"),
                "Resumo",
            )
        ]
    )
    assert len(chunks) == 1
    assert len(split_telegram_message("a" * 5000, limit=4096)) == 2
    await db.close()


async def _noop() -> None:
    return None


async def _record_and_run(
    dispatched: list[int],
    sched_id: int,
    services: BotServices,
    sched: object,
    send_fn: object,
) -> None:
    dispatched.append(sched_id)
    await run_scheduled_search(services, sched, send_fn)  # type: ignore[arg-type]
