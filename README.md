# NewsBrief AI

Bot assíncrono do Telegram que busca notícias em RSS pelo tema escolhido e entrega
resumos curtos usando Ollama local. Esta primeira versão cobre notícias; vagas,
editais, favoritos, agendamentos e WhatsApp ficam para etapas futuras.

## Requisitos

- Python 3.11+ (o Dockerfile usa Python 3.12).
- Um bot criado pelo [@BotFather](https://t.me/BotFather).
- [Ollama](https://ollama.com/) instalado e um modelo baixado, por exemplo:

  ```powershell
  ollama pull qwen2.5:7b
  ```

## Configuração local

Instale o [uv](https://docs.astral.sh/uv/getting-started/installation/) e copie o
arquivo de exemplo para `.env`:

```powershell
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
e a disponibilidade de cada feed antes de ativá-lo. O NewsBrief AI não faz scraping
de páginas de artigos.

## Comandos

- `/start` ou `/ajuda`: instruções do MVP.
- `/tema <assunto>`: define o tema ativo e inicia uma busca.
- `/buscar`: busca somente itens ainda não entregues para o tema ativo.
- `/atual`: mostra o tema ativo.

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
