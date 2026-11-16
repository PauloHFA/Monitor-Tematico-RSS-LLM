from newsbrief.pipeline.normalize import normalize_url
from newsbrief.providers.base import NewsItem

MAX_ITEM_MESSAGE_LENGTH = 600
MAX_TELEGRAM_MESSAGE_LENGTH = 4096


def format_news_message(item: NewsItem, summary: str) -> str:
    url = normalize_url(item.url)
    if url is None:
        raise ValueError("News items must have a valid http(s) URL.")

    icon = {"vaga": "💼", "edital": "🎓"}.get(item.content_type, "📰")
    title = " ".join(item.title.split()) or "Notícia"
    summary_clean = (
        summary.strip() if item.content_type in {"vaga", "edital"} else " ".join(summary.split())
    )
    fixed_length = len(f"{icon} \n\n🔗 ") + len(url)
    if fixed_length >= MAX_ITEM_MESSAGE_LENGTH:
        raise ValueError("The news URL is too long to fit in a Telegram item message.")

    title_budget = MAX_ITEM_MESSAGE_LENGTH - fixed_length - 1
    title = _truncate(title, title_budget)
    summary_budget = MAX_ITEM_MESSAGE_LENGTH - fixed_length - len(title) - 2
    summary_text = _truncate(
        summary_clean or item.snippet or "Resumo indisponível.",
        summary_budget,
    )
    message = f"{icon} {title}\n{summary_text}\n🔗 {url}"
    if len(message) > MAX_ITEM_MESSAGE_LENGTH:
        raise ValueError("Formatted Telegram item exceeds the configured character limit.")
    return message


def format_single_digest(entries: list[tuple[NewsItem, str]]) -> list[str]:
    """Combine multiple items into a single digest split safely under 4096 chars (RF17)."""
    blocks = [format_news_message(item, summary) for item, summary in entries]
    joined = "\n\n".join(blocks)
    return split_telegram_message(joined, limit=MAX_TELEGRAM_MESSAGE_LENGTH)


def split_telegram_message(text: str, *, limit: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(paragraph) <= limit:
                current = paragraph
            else:
                for start in range(0, len(paragraph), limit):
                    chunks.append(paragraph[start : start + limit])
                current = ""
    if current:
        chunks.append(current)
    return chunks


def _truncate(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit == 1:
        return "…"
    return f"{value[: limit - 1].rstrip()}…"
