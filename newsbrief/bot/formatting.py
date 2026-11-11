from newsbrief.pipeline.normalize import normalize_url
from newsbrief.providers.base import NewsItem

MAX_ITEM_MESSAGE_LENGTH = 600


def format_news_message(item: NewsItem, summary: str) -> str:
    url = normalize_url(item.url)
    if url is None:
        raise ValueError("News items must have a valid http(s) URL.")

    title = " ".join(item.title.split()) or "Notícia"
    summary = " ".join(summary.split())
    fixed_length = len("📰 \n\n🔗 ") + len(url)
    if fixed_length >= MAX_ITEM_MESSAGE_LENGTH:
        raise ValueError("The news URL is too long to fit in a Telegram item message.")

    title_budget = MAX_ITEM_MESSAGE_LENGTH - fixed_length - 1
    title = _truncate(title, title_budget)
    summary_budget = MAX_ITEM_MESSAGE_LENGTH - fixed_length - len(title) - 2
    summary = _truncate(summary or item.snippet or "Resumo indisponível.", summary_budget)
    message = f"📰 {title}\n{summary}\n🔗 {url}"
    if len(message) > MAX_ITEM_MESSAGE_LENGTH:
        raise ValueError("Formatted Telegram item exceeds the configured character limit.")
    return message


def _truncate(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit == 1:
        return "…"
    return f"{value[: limit - 1].rstrip()}…"
