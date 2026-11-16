import re
import unicodedata

from newsbrief.providers.base import TopicType

_EDITAL_KEYWORDS = (
    "edital",
    "editais",
    "processo seletivo",
    "selecao",
    "pos-graduacao",
    "mestrado",
    "doutorado",
    "bolsa",
    "concurso",
    "inscricoes",
    "chamada publica",
    "vestibular",
)

_VAGA_KEYWORDS = (
    "vaga",
    "vagas",
    "emprego",
    "oportunidade",
    "contratacao",
    "estagio",
    "trainee",
    "recrutamento",
    "desenvolvedor",
    "programador",
    "engenheiro de software",
    "analista",
    "job",
    "jobs",
    "hiring",
    "remote",
    "remoto",
    "clt",
    "pj",
)

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "programador": ("desenvolvedor", "software engineer"),
    "desenvolvedor": ("programador", "software developer"),
    "emprego": ("vaga", "jobs"),
    "vaga": ("emprego", "hiring"),
    "vagas": ("emprego", "jobs"),
    "remoto": ("remote", "home office"),
    "estagio": ("internship", "trainee"),
    "inteligencia artificial": ("IA", "artificial intelligence"),
    "pos-graduacao": ("mestrado", "doutorado"),
    "selecao": ("edital", "processo seletivo"),
    "python": ("django", "fastapi"),
}


def classify_topic(query: str) -> TopicType:
    """Classify a topic into 'noticia', 'vaga', or 'edital' using deterministic rules."""
    folded = _fold(query)
    for keyword in _EDITAL_KEYWORDS:
        if _contains_phrase(folded, keyword):
            return "edital"
    for keyword in _VAGA_KEYWORDS:
        if _contains_phrase(folded, keyword):
            return "vaga"
    return "noticia"


def expand_query(query: str, *, enabled: bool = True) -> list[str]:
    """Return the original query plus deterministic synonyms when expansion is enabled."""
    cleaned = " ".join(query.split())
    if not cleaned or not enabled:
        return [cleaned] if cleaned else []

    folded = _fold(cleaned)
    extra_terms: list[str] = []
    for key, expansions in _SYNONYMS.items():
        if _contains_phrase(folded, key):
            for term in expansions:
                if _fold(term) not in folded and term not in extra_terms:
                    extra_terms.append(term)
    variants = [cleaned]
    if extra_terms:
        variants.append(f"{cleaned} {' '.join(extra_terms[:3])}")
    return variants


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _contains_phrase(folded_text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded_text) is not None
