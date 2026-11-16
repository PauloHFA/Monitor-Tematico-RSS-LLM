# Monitor-Tematico-RSS-LLM (`newsbrief`)

Bot assíncrono do Telegram que monitora e busca notícias, vagas e editais em feeds RSS e APIs públicas pelo tema escolhido, classifica e filtra resultados, deduplica por tema/usuário e entrega resumos estruturados usando Ollama local, OpenAI ou Gemini (com entrega opcional no WhatsApp e agendamentos recorrentes).

> **Identificadores internos preservados:** o pacote Python (`newsbrief`), a distribuição (`newsbrief-ai`), o script CLI (`newsbrief`) e o esquema SQLite (`newsbrief.db`) permanecem inalterados para manter compatibilidade de imports e execução.

## Requisitos

- Python 3.11+ (o Dockerfile usa Python 3.12).
- Um bot criado pelo [@BotFather](https://t.me/BotFather).
- [Ollama](https://ollama.com/) instalado e um modelo baixado (ou chave `OPENAI_API_KEY` / `GEMINI_API_KEY`), por exemplo:

  ```powershell
  ollama pull qwen2.5:7b
  ```

## Configuração local

```powershell
git clone https://github.com/PauloHFA/Monitor-Tematico-RSS-LLM.git
cd Monitor-Tematico-RSS-LLM
Copy-Item .env.example .env
```

Preencha `TELEGRAM_BOT_TOKEN`. Para autorizar chats, informe seus IDs em
`ALLOWED_CHAT_IDS`, separados por vírgula. Com a lista vazia, todos os chats são
negados; o log de acesso não autorizado registra o ID do chat para facilitar a
configuração. Não compartilhe nem versione o arquivo `.env`.

Instale dependências e execute:

```powershell
uv sync --extra dev
uv run newsbrief
```

No modo local, o Ollama deve estar acessível em `OLLAMA_HOST` (padrão
`http://localhost:11434`). O SQLite é salvo em `DB_PATH`, por padrão
`data/newsbrief.db`.

## Docker Compose

Copie `.env.example` para `.env`, configure o token e a allowlist, e então execute:

```powershell
docker compose up --build
```

O Compose usa o volume `newsbrief-data` para persistir o banco. Em Docker Desktop,
`host.docker.internal` permite ao contêiner alcançar o Ollama executado no host.
O Compose usa esse endereço por padrão; se o Ollama estiver em outro local, adicione
ou ajuste `OLLAMA_HOST` no `.env`.

## Feeds

A busca temática padrão usa o RSS de pesquisa do Google News. Feeds adicionais
podem ser configurados em `configs/feeds.yaml`:

```yaml
feeds:
  - name: Nome da fonte
    url: https://exemplo.org/feed.xml
```

Use somente RSS/APIs cuja política permita esse acesso e confirme os termos de uso
e a disponibilidade de cada feed antes de ativá-lo. O Monitor-Tematico-RSS-LLM não faz scraping
de páginas de artigos.

## Comandos

- `/start`, `/menu` ou `/ajuda`: instruções e menu interativo.
- `/tema <assunto>`: define o tema ativo e inicia uma busca (ou envie texto livre se habilitado).
- `/buscar [tudo]`: busca apenas itens novos (ou reenvia todos com `tudo`).
- `/atual`, `/historico`, `/salvar [apelido]`, `/temas`, `/remover <n>`: tema ativo, histórico e favoritos.
- `/tipo <noticia|vaga|edital>`, `/expandir on|off`, `/limite <1-10>`, `/periodo <Nd>`, `/filtro <termos>`, `/idioma <pt|en|all>`: parâmetros e filtros.
- `/modo resumo-unico|individual`, `/config texto_livre on|off`, `/agendar <hh:mm> [dias]`, `/agendamentos`, `/pausar`, `/retomar`, `/fuso <zona>`.
- `/fontes`, `/status`, `/estatisticas`, `/autorizar <id>`, `/revogar <id>`, `/apagar_meus_dados`.

O tema é limitado a 120 caracteres. Cada busca retorna no máximo o limite configurado
em `MAX_RESULTS` (de 1 a 10; padrão 5), e pode não encontrar itens novos. Se uma fonte
RSS falhar, o bot registra e informa a falha, enquanto continua usando as demais fontes
disponíveis. Se o Ollama falhar, o bot registra o erro e usa um fallback com título,
trecho e link. A URL é normalizada (incluindo a remoção de parâmetros de rastreamento),
e o item só entra no histórico depois que o Telegram confirma a entrega.

O bot pode processar até 20 atualizações simultâneas. Buscas do mesmo chat são
serializadas para evitar o envio duplicado do mesmo item.

## Testes e qualidade

```powershell
uv run pytest
uv run ruff check .
uv run mypy
```

Tokens e chaves não devem ser incluídos no código ou em arquivos versionados. O
arquivo `.env` está excluído pelo `.gitignore`.
