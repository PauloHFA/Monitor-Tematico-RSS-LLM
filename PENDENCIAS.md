# Pendências e Próximos Passos — NewsBrief AI (`newsbrief`)

Documento técnico consolidando as implementações realizadas, o estado validado em testes/análise estática, as evoluções opcionais restantes e o que depende de credenciais, feeds ou decisões fornecidas por você.

---

## Implementações Concluídas nesta Rodada

1. **Classificação de temas, expansão de consulta e provedores plugáveis (`newsbrief/topics/classifier.py`, `newsbrief/providers/jobs_api.py`, `newsbrief/providers/rss.py`, `configs/providers.yaml`):**
   - Classificador determinístico (`noticia | vaga | edital`) e expansor de sinônimos `pt/en` (`expand_query`), respeitando `/tipo` e `/expandir on|off`.
   - Novos provedores gratuitos baseados em API pública: `RemotiveJobsProvider` (vagas remotas) e `HackerNewsAlgoliaProvider` (discussões e hiring), roteados conforme `configs/providers.yaml`.
   - Filtros por período (`period_days`), termos incluídos/excluídos (`include_terms`, `exclude_terms`) e idioma (`pt|en|all`).
2. **Multi-LLM e extração estruturada validada (`newsbrief/llm/schemas.py`, `newsbrief/llm/ollama.py`, `newsbrief/llm/clients.py`):**
   - Schemas Pydantic `JobSummarySchema` e `GrantSummarySchema` com fallback obrigatório `"não informado"` e até **2 retentativas corretivas** em JSON inválido (`RF15`).
   - Clientes intercambiáveis para **Ollama local** (`LLM_PROVIDER=ollama`), **OpenAI-compatible** (`LLM_PROVIDER=openai`) e **Google Gemini** (`LLM_PROVIDER=gemini`).
3. **Bot completo, preferências persistentes e agendador (`newsbrief/db/storage.py`, `newsbrief/bot/handlers.py`, `newsbrief/bot/application.py`, `newsbrief/scheduler/runner.py`):**
   - Persistência SQLite para preferências (`user_preferences`), histórico dos últimos 10 temas e favoritos (`topics`), agendamentos recorrentes (`schedules`) e métricas/auditoria de buscas (`search_log`).
   - Novos comandos e interações: `/menu`, `/buscar tudo`, `/historico`, `/salvar`, `/temas`, `/remover`, `/tipo`, `/expandir`, `/limite`, `/periodo`, `/filtro`, `/idioma`, `/modo resumo-unico|individual`, `/config texto_livre on|off`, `/agendar`, `/agendamentos`, `/pausar`, `/retomar`, `/fuso`, `/fontes`, `/status`, `/estatisticas`, `/autorizar`, `/revogar`, `/apagar_meus_dados`, busca por texto livre e botões inline.
   - Proteções operacionais: limite de 10 buscas/hora por usuário e fallback automático quando atingir `DAILY_LLM_BUDGET`.
4. **Canal opcional WhatsApp, `/healthz` e CI (`newsbrief/notify/whatsapp.py`, `newsbrief/health.py`, `.github/workflows/ci.yml`):**
   - Dispatcher HTTP opcional para WhatsApp (`CHANNELS=telegram,whatsapp`) com isolamento de falhas.
   - Servidor assíncrono opcional `/healthz` (`HEALTH_PORT`) e workflow de CI (`ruff`, `mypy`, `pytest`).

---

## 1. O que ainda falta implementar (Evoluções Opcionais / Backlog)

### 1.1 Extração do corpo completo de artigos HTML (`trafilatura` / `readability-lxml`)
- **Situação atual:** Os resumos utilizam título e `snippet` entregues pelos feeds RSS e APIs públicas.
- **O que falta:** Caso deseje resumir artigos completos quando o RSS trouxer apenas 1 frase, adicionar extração opcional respeitando `robots.txt` e termos de uso de cada fonte.

### 1.2 Conjunto dourado de 30 amostras e métrica automática no CI (`RNF14`)
- **Situação atual:** A validação estrutural garante que campos ausentes em vagas/editais recebam `"não informado"`, e os prompts proíbem invenção de dados.
- **O que falta:** Montar um dataset dourado com 30 itens reais (10 notícias, 10 vagas, 10 editais) e script de regressão automática para avaliar qualidade de resumo antes de trocar de modelo LLM.

### 1.3 Webhook HTTPS no Telegram e `gitleaks` / `pip-audit` no CI (`RNF06`, `RNF09`)
- **Situação atual:** O bot opera via long-polling (`run_polling`), sem exigir porta pública nem certificado.
- **O que falta:** Se quiser operar por webhook em VPS, adicionar suporte a `run_webhook` e incluir passos de `gitleaks` e `pip-audit` no `.github/workflows/ci.yml`.

---

## 2. O que depende de você (Credenciais, Fontes e Ambiente)

| Item | O que precisa ser definido / fornecido | Impacto direto |
|---|---|---|
| **1. Bot Telegram e Acesso (`.env`)** | Criar o bot no `@BotFather` (`TELEGRAM_BOT_TOKEN`), preencher `ALLOWED_CHAT_IDS` e `ADMIN_CHAT_ID`. | Obrigatório para o bot responder e habilitar `/autorizar` e `/revogar`. |
| **2. Provedor de LLM** | Ter o Ollama ativo localmente (`ollama pull qwen2.5:7b` em `OLLAMA_HOST`) ou configurar `LLM_PROVIDER=openai|gemini` + `OPENAI_API_KEY` / `GEMINI_API_KEY`. | Sem LLM ativo, o bot cai no fallback de título + trecho + link. |
| **3. Feeds curados em `configs/feeds.yaml`** | Preencher `configs/feeds.yaml` com URLs RSS de notícias, vagas e editais permitidas pelos termos de uso de cada fonte. | Amplia a cobertura além de Google News RSS, Remotive e Hacker News Algolia. |
| **4. WhatsApp opcional** | Caso ative `CHANNELS=telegram,whatsapp`, fornecer endpoint e token oficiais em `WHATSAPP_API_URL` e `WHATSAPP_API_TOKEN`. | Habilita entrega simultânea no WhatsApp. |
| **5. Execução em Docker** | Configurar `.env` e executar `docker compose up --build` no ambiente com Docker ativo. | Valida o container em produção/VPS com volume persistente. |
| **6. Nome e Push no GitHub** | Confirmar se deseja renomear este repositório no padrão do portfólio (ex.: `Assistente-Noticias-Vagas-Telegram`) e fazer `commit`/`push`. | Alinha o projeto aos demais repositórios do seu portfólio. |

---

## 3. Referência rápida de verificação

```bash
uv run ruff check .
uv run mypy
uv run pytest
```
