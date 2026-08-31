"""
The canonical paper structure used everywhere in this application.

Every provider returns papers in exactly this shape, so the database layer, the
scoring layer, the trend analysis and the templates never need to know which
API a paper came from.

Canonical fields
----------------
paper_id        str        Stable provider-scoped identifier (required).
title           str|None   Paper title.
abstract        str|None   Plain-text abstract (already de-inverted).
year            int|None   Publication year.
citation_count  int        Citation count, never None (0 when unknown).
authors         list[str]  Author display names, in order, de-duplicated.
concepts        list[dict] Normalized concepts (see concepts.py).
doi             str|None   Bare DOI, e.g. "10.1234/abcd" (no URL prefix).
url             str|None   Landing page for the paper, when known.
source          str        Provider name, e.g. "openalex".
research_score  int        Filled in by scoring.py, 0 until then.
relevance_score int        Query-match score filled in by relevance.py.
relevance_level str|None   High / Medium / Low after relevance analysis.
relevance_reasons list[str] Explainable matching signals.
keyword         str|None   Search term this paper was collected for.

Nothing downstream is allowed to assume a field is present but unset: every
normalized paper carries every key, always.
"""

from concepts import normalize_concepts


#: Declared once so tests and the database layer can iterate the contract.
PAPER_FIELDS = (
    "paper_id",
    "title",
    "abstract",
    "year",
    "citation_count",
    "authors",
    "concepts",
    "doi",
    "url",
    "source",
    "research_score",
    "relevance_score",
    "relevance_level",
    "relevance_reasons",
    "keyword",
)


#: Sanity bounds for a publication year.  Anything outside is treated as
#: missing rather than trusted, so one bad record cannot distort a trend chart.
MIN_YEAR = 1500
MAX_YEAR = 2100


def clean_text(value):
    """Return a stripped string, or None for anything empty/absent."""

    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    value = value.strip()

    return value or None


def coerce_int(value, default=0):
    """Best-effort integer conversion that never raises."""

    if value is None:
        return default

    if isinstance(value, bool):
        return default

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def coerce_year(value):
    """Return a plausible publication year, or None."""

    year = coerce_int(value, default=None)

    if year is None:
        return None

    if year < MIN_YEAR or year > MAX_YEAR:
        return None

    return year


def normalize_authors(values):
    """Return an ordered, de-duplicated list of author name strings.

    Accepts the two shapes providers actually produce: a list of strings, and
    a list of ``{"name": ...}`` / ``{"display_name": ...}`` dicts.
    """

    if not values:
        return []

    if isinstance(values, str):
        values = [values]

    authors = []
    seen = set()

    for entry in values:

        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("display_name")
        else:
            name = entry

        name = clean_text(name)

        if not name:
            continue

        key = name.casefold()

        if key in seen:
            continue

        seen.add(key)
        authors.append(name)

    return authors


def normalize_doi(value):
    """Return a bare DOI without the https://doi.org/ prefix, or None."""

    doi = clean_text(value)

    if not doi:
        return None

    lowered = doi.lower()

    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if lowered.startswith(prefix):
            doi = doi[len(prefix):]
            lowered = doi.lower()

    doi = doi.strip()

    return doi or None


def normalize_relevance_reasons(values):
    """Return a clean list of explanation strings."""

    if not values:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(values, (list, tuple, set, frozenset)):
        return []

    reasons = []

    for value in values:
        reason = clean_text(value)

        if reason and reason not in reasons:
            reasons.append(reason)

    return reasons


def normalize_paper(raw, source, keyword=None):
    """Coerce a provider dict into the canonical structure.

    Returns ``None`` when the record has no usable identifier -- a paper we
    cannot address is a paper we cannot de-duplicate or store.
    """

    if not isinstance(raw, dict):
        return None

    paper_id = clean_text(
        raw.get("paper_id")
        or raw.get("paperId")
        or raw.get("id")
    )

    if not paper_id:
        return None

    citation_count = coerce_int(
        raw.get("citation_count", raw.get("citationCount", 0)),
        default=0,
    )

    return {
        "paper_id": paper_id,
        "title": clean_text(raw.get("title")),
        "abstract": clean_text(raw.get("abstract")),
        "year": coerce_year(raw.get("year", raw.get("publication_year"))),
        "citation_count": max(citation_count, 0),
        "authors": normalize_authors(raw.get("authors")),
        "concepts": normalize_concepts(raw.get("concepts")),
        "doi": normalize_doi(raw.get("doi")),
        "url": clean_text(raw.get("url")),
        "source": clean_text(source) or "unknown",
        "research_score": coerce_int(raw.get("research_score", 0), default=0),
        "relevance_score": coerce_int(raw.get("relevance_score", 0), default=0),
        "relevance_level": clean_text(raw.get("relevance_level")),
        "relevance_reasons": normalize_relevance_reasons(
            raw.get("relevance_reasons")
        ),
        "keyword": clean_text(raw.get("keyword") or keyword),
    }


def normalize_papers(raw_papers, source, keyword=None):
    """Normalize a sequence of provider records, dropping unusable ones.

    De-duplicates on ``paper_id`` while preserving provider ordering; when the
    same id appears twice the record carrying more information wins.
    """

    normalized = {}
    order = []

    for raw in raw_papers or []:

        paper = normalize_paper(raw, source, keyword)

        if paper is None:
            continue

        paper_id = paper["paper_id"]

        if paper_id in normalized:
            normalized[paper_id] = _richer(normalized[paper_id], paper)
            continue

        normalized[paper_id] = paper
        order.append(paper_id)

    return [normalized[paper_id] for paper_id in order]


def _richer(existing, candidate):
    """Merge two records for the same paper, preferring populated fields."""

    merged = dict(existing)

    for field in ("title", "abstract", "year", "doi", "url"):
        if not merged.get(field) and candidate.get(field):
            merged[field] = candidate[field]

    for field in ("authors", "concepts"):
        if not merged.get(field) and candidate.get(field):
            merged[field] = candidate[field]

    merged["citation_count"] = max(
        merged.get("citation_count", 0),
        candidate.get("citation_count", 0),
    )

    return merged


def is_normalized(paper):
    """True when ``paper`` carries every canonical field."""

    if not isinstance(paper, dict):
        return False

    return all(field in paper for field in PAPER_FIELDS)
