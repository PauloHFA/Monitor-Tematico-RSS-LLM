import hashlib
import re
from html import unescape
from html.parser import HTMLParser
from unicodedata import combining, normalize
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_STOP_WORDS = {
    "a",
    "as",
    "ao",
    "aos",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "para",
    "por",
    "sobre",
    "um",
    "uma",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def clean_html(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(unescape(value))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def normalize_url(value: str) -> str | None:
    try:
        parts = urlsplit(value.strip())
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            return None
        port = parts.port
    except ValueError:
        return None

    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if port is not None and not (
        (parts.scheme.lower() == "http" and port == 80)
        or (parts.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"

    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            netloc,
            parts.path or "/",
            urlencode(sorted(query)),
            "",
        )
    )


def url_hash(value: str) -> str:
    normalized = normalize_url(value)
    if normalized is None:
        raise ValueError("Only valid http(s) URLs can be hashed.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def topic_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def tokenize_words(value: str) -> set[str]:
    decomposed = normalize("NFKD", value.casefold())
    without_accents = "".join(character for character in decomposed if not combining(character))
    return {word for word in re.findall(r"\w+", without_accents) if word not in _STOP_WORDS}
