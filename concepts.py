"""
Concept / topic pipeline.

OpenAlex tags each work with "concepts": hierarchical subject terms, each with
a display name, an ID, a level (0 = broad discipline, 5 = very narrow) and a
relevance score between 0 and 1.  Other providers give us far less -- often
just a list of strings.

This module turns any of those into one canonical shape and provides the
counting helpers the trend analysis and the dashboard both use, so topic
aggregation exists in exactly one place.

Canonical concept
-----------------
{
    "name":  str,          # display name, whitespace-normalized
    "key":   str,          # casefolded name, used for de-duplication
    "id":    str | None,   # provider concept id (OpenAlex URI), when known
    "level": int | None,   # 0..5 for OpenAlex, None when unknown
    "score": float | None, # 0..1 relevance, None when unknown
}
"""

import json
import re
from collections import Counter

import config


#: Terms that are technically valid concepts but carry no research meaning in
#: this application.  Two groups:
#:
#:   * homonym artefacts -- OpenAlex disambiguates "stock" in a finance query to
#:     "Stock (firearms)", "capital" to "Capital (architecture)", and so on.
#:     These are simply wrong for our corpus.
#:   * container disciplines -- terms so broad that every second paper carries
#:     them, which makes them win every "top topic" ranking while saying
#:     nothing.  They are dropped from *trend* output but a paper is never left
#:     with zero concepts because of them (see filter_concepts).
HOMONYM_NOISE = frozenset(
    name.casefold()
    for name in (
        "Stock (firearms)",
        "Period (music)",
        "Capital (architecture)",
        "Meaning (existential)",
        "Metric (mathematics)",
        "Position (finance)",
        "Set (abstract data type)",
        "Context (archaeology)",
        "Order (biology)",
        "Face (sociological concept)",
        "Terminology",
        "Nothing",
    )
)


GENERIC_DISCIPLINES = frozenset(
    name.casefold()
    for name in (
        "Physics",
        "Acoustics",
        "Archaeology",
        "Biology",
        "Ecology",
        "Chemistry",
        "Mathematics",
        "Geology",
        "Geography",
        "Astronomy",
        "Engineering",
        "Materials science",
        "Political science",
        "Philosophy",
        "Theology",
        "Art",
        "Humanities",
        "Law",
        "Medicine",
        "Biochemistry",
        "Quantum mechanics",
        "Thermodynamics",
        "Botany",
        "Zoology",
        "Paleontology",
        "Optics",
        "Computer science",
        "Programming language",
        "Operating system",
        "Artificial intelligence",
        "Machine learning",
        "Business",
        "Economics",
        "Finance",
        "Management",
        "Marketing",
        "Sociology",
        "Psychology",
        "Statistics",
        "Mathematical economics",
        "Mathematical analysis",
        "Microeconomics",
        "Macroeconomics",
        "Econometrics",
        "Actuarial science",
        "Accounting",
        "Public relations",
        "Political economy",
        "Social science",
        "Natural resource economics",
        "Environmental science",
        "Biological system",
        "Computational biology",
        "Neuroscience",
    )
)


#: Everything we never want to see in a topic list, for any purpose.
ALWAYS_DROP = HOMONYM_NOISE


_WHITESPACE = re.compile(r"\s+")


def normalize_name(value):
    """Collapse whitespace and strip a concept name; return None if empty."""

    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    value = _WHITESPACE.sub(" ", value).strip()

    return value or None


def _coerce_level(value):
    if value is None:
        return None

    try:
        level = int(value)
    except (TypeError, ValueError):
        return None

    if level < 0 or level > 10:
        return None

    return level


def _coerce_score(value):
    if value is None:
        return None

    try:
        score = float(value)
    except (TypeError, ValueError):
        return None

    if score != score:  # NaN
        return None

    return max(0.0, min(score, 1.0))


def normalize_concept(raw):
    """Turn one provider concept into the canonical concept dict, or None.

    Accepts a plain string, an OpenAlex concept dict, or a Semantic Scholar
    field-of-study dict.  Unknown shapes return None rather than raising.
    """

    if raw is None:
        return None

    if isinstance(raw, str):
        name = normalize_name(raw)
        identifier = None
        level = None
        score = None

    elif isinstance(raw, dict):
        name = normalize_name(
            raw.get("name")
            or raw.get("display_name")
            or raw.get("category")
        )
        identifier = normalize_name(raw.get("id") or raw.get("concept_id"))
        level = _coerce_level(raw.get("level"))
        score = _coerce_score(raw.get("score"))

    else:
        return None

    if not name:
        return None

    return {
        "name": name,
        "key": name.casefold(),
        "id": identifier,
        "level": level,
        "score": score,
    }


def normalize_concepts(raw_concepts, min_score=None, max_per_paper=None):
    """Normalize a provider's concept list for one paper.

    Applies, in order: shape normalization, hard noise removal, relevance
    threshold, de-duplication by name, ordering by relevance, and a per-paper
    cap.  The relevance threshold is skipped for concepts whose provider gave
    no score, since we cannot judge those.
    """

    if min_score is None:
        min_score = config.CONCEPT_MIN_SCORE

    if max_per_paper is None:
        max_per_paper = config.CONCEPT_MAX_PER_PAPER

    if isinstance(raw_concepts, str):
        # A single name, or a JSON payload round-tripped from the database.
        raw_concepts = _from_json_or_single(raw_concepts)

    if not raw_concepts:
        return []

    if isinstance(raw_concepts, dict):
        raw_concepts = [raw_concepts]

    if not isinstance(raw_concepts, (list, tuple, set, frozenset)):
        # A scalar in the concepts column (a stray number, a bool) is data
        # corruption, not a concept list.  Treat it as "no concepts".
        return []

    by_key = {}

    for raw in raw_concepts:

        concept = normalize_concept(raw)

        if concept is None:
            continue

        if concept["key"] in ALWAYS_DROP:
            continue

        if concept["level"] is not None and concept["level"] > config.CONCEPT_MAX_LEVEL:
            continue

        if concept["score"] is not None and concept["score"] < min_score:
            continue

        existing = by_key.get(concept["key"])

        if existing is None:
            by_key[concept["key"]] = concept
            continue

        # Same concept twice: keep the more informative copy.
        if _informativeness(concept) > _informativeness(existing):
            by_key[concept["key"]] = concept

    concepts = sorted(
        by_key.values(),
        key=lambda concept: (
            -(concept["score"] if concept["score"] is not None else 0.5),
            concept["name"],
        ),
    )

    return concepts[:max_per_paper]


def _informativeness(concept):
    """Rank two records of the same concept: more populated fields wins."""

    return (
        1 if concept.get("score") is not None else 0,
        1 if concept.get("level") is not None else 0,
        1 if concept.get("id") else 0,
    )


def _from_json_or_single(value):
    """Accept a JSON list/dict string, else treat the value as one name."""

    text = value.strip()

    if not text:
        return []

    if text[0] in "[{":
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    return [text]


def concepts_from_json(value):
    """Read a concepts column back out of the database.

    Handles all three historical formats: the current list-of-dicts, the older
    list-of-strings, and a NULL/garbage column.  Never raises.
    """

    if not value:
        return []

    if isinstance(value, (list, tuple)):
        return normalize_concepts(value)

    if not isinstance(value, str):
        return []

    try:
        loaded = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []

    return normalize_concepts(loaded)


def concepts_to_json(concepts):
    """Serialize canonical concepts for storage."""

    return json.dumps(concepts or [], ensure_ascii=False)


def concept_names(concepts):
    """Extract display names from canonical concepts."""

    names = []

    for concept in concepts or []:

        if isinstance(concept, dict):
            name = concept.get("name")
        else:
            name = concept

        name = normalize_name(name)

        if name:
            names.append(name)

    return names


def filter_concepts(concepts, drop_generic=True, min_level=None):
    """Drop low-information terms while never emptying a paper's topic list.

    ``drop_generic`` removes container disciplines ("Economics", "Physics").
    If that would leave the paper with nothing, the original list is returned
    instead -- a broad topic beats no topic at all.
    """

    kept = []

    for concept in concepts or []:

        if not isinstance(concept, dict):
            concept = normalize_concept(concept)

            if concept is None:
                continue

        key = concept.get("key") or (concept.get("name") or "").casefold()

        if key in ALWAYS_DROP:
            continue

        if drop_generic and key in GENERIC_DISCIPLINES:
            continue

        level = concept.get("level")

        if min_level is not None and level is not None and level < min_level:
            continue

        kept.append(concept)

    if not kept:
        return [
            concept
            for concept in (concepts or [])
            if isinstance(concept, dict)
            and (concept.get("key") or "") not in ALWAYS_DROP
        ]

    return kept


def paper_topics(paper, drop_generic=True):
    """Topic names for one paper, de-duplicated, ready for counting.

    Accepts a normalized paper dict or a database row; reads the concepts
    column in whatever historical format it holds.
    """

    raw = None

    if isinstance(paper, dict):
        raw = paper.get("concepts")
    else:
        raw = getattr(paper, "concepts", None)

    if isinstance(raw, str):
        concepts = concepts_from_json(raw)
    else:
        concepts = normalize_concepts(raw)

    concepts = filter_concepts(concepts, drop_generic=drop_generic)

    names = []
    seen = set()

    for name in concept_names(concepts):

        key = name.casefold()

        if key in seen:
            continue

        seen.add(key)
        names.append(name)

    return names


def count_topics(papers, drop_generic=True):
    """Count how many papers each topic appears in (not raw mentions)."""

    counter = Counter()

    for paper in papers or []:
        counter.update(paper_topics(paper, drop_generic=drop_generic))

    return counter
