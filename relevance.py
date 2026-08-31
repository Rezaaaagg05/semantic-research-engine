"""Deterministic lexical relevance analysis for retrieved papers.

This module answers a different question from :mod:`scoring`: whether a
retrieved paper is sufficiently related to the user's query to enter the
corpus at all.  It uses only metadata already returned by the provider and
never reads citation counts, publication year, or ``research_score``.

The 0..100 score is the sum of five transparent signals:

* query-term coverage in the title:    0..35
* query-term coverage in the abstract: 0..20
* query-term coverage in concepts:     0..20
* exact normalized phrase in title:    0 or 15
* exact normalized phrase in abstract: 0 or 10

Filtering is deliberately conservative.  Papers at or above the configured
minimum are retained.  For multi-term queries containing at least one
distinctive term, overlap made entirely from broad terms such as "risk" and
"management" is capped at Low confidence.  It is still retained when the
lexical evidence clears the cutoff, because ambiguity is not strong evidence
of irrelevance and recall is the priority.
"""

import re
import unicodedata
from typing import NamedTuple

import config
from concepts import concept_names, normalize_concepts


TITLE_TERM_WEIGHT = 35
ABSTRACT_TERM_WEIGHT = 20
CONCEPT_TERM_WEIGHT = 20
TITLE_PHRASE_WEIGHT = 15
ABSTRACT_PHRASE_WEIGHT = 10
MAX_RELEVANCE_SCORE = 100


# Grammatical and academic boilerplate does not identify the user's topic.
STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "in", "into", "is", "it", "of", "on", "or", "that", "the", "their",
        "this", "to", "using", "via", "was", "were", "with", "without",
        "analysis", "approach", "case", "data", "evidence", "method", "model",
        "paper", "research", "results", "review", "study",
    }
)


# These words can occur in many unrelated disciplines.  When a longer query
# also contains a more distinctive word, broad-only overlap is weak evidence.
BROAD_QUERY_TERMS = frozenset(
    {
        "application", "assessment", "development", "effect", "effects",
        "evaluation", "framework", "management", "performance", "process",
        "risk", "system", "systems",
    }
)


_WORD = re.compile(r"[a-z0-9]+")


class RelevanceResult(NamedTuple):
    score: int
    level: str
    reasons: list[str]
    matched_terms: list[str]
    retain: bool


class RelevanceBatch(NamedTuple):
    retained: list[dict]
    excluded: list[dict]
    retrieved: int


def normalize_text(value):
    """Lowercase text with punctuation and whitespace normalized."""

    if value is None:
        return ""

    text = unicodedata.normalize("NFKD", str(value).casefold())
    text = "".join(character for character in text if not unicodedata.combining(character))

    return " ".join(_WORD.findall(text))


def query_terms(query):
    """Meaningful, de-duplicated query tokens in their original order."""

    terms = []
    seen = set()

    for raw_term in normalize_text(query).split():
        term = _canonical_term(raw_term)

        if len(term) < 2 or term in STOPWORDS or term in seen:
            continue

        seen.add(term)
        terms.append(term)

    return terms


def _field_terms(value):
    return {_canonical_term(term) for term in normalize_text(value).split()}


def _canonical_term(term):
    """Normalize simple English plurals without introducing an NLP library."""

    if len(term) > 4 and term.endswith("ies"):
        return f"{term[:-3]}y"

    if (
        len(term) > 4
        and term.endswith("s")
        and not term.endswith(("ss", "is", "us"))
    ):
        return term[:-1]

    return term


def _concept_text(paper):
    concepts = paper.get("concepts") if isinstance(paper, dict) else None

    if not isinstance(concepts, (list, tuple)):
        concepts = normalize_concepts(concepts)
    elif concepts and not isinstance(concepts[0], dict):
        concepts = normalize_concepts(concepts)

    return " ".join(concept_names(concepts))


def _matches(terms, field_words):
    return [term for term in terms if term in field_words]


def _contains_phrase(normalized_field, normalized_query):
    if not normalized_field or not normalized_query:
        return False

    return f" {normalized_query} " in f" {normalized_field} "


def relevance_level(score):
    if score >= config.RELEVANCE_HIGH_SCORE:
        return "High"

    if score >= config.RELEVANCE_MEDIUM_SCORE:
        return "Medium"

    return "Low"


def calculate_relevance(paper, query, min_score=None):
    """Return the relevance score, level, reasons, and retention decision."""

    if min_score is None:
        min_score = config.RELEVANCE_MIN_SCORE

    paper = paper if isinstance(paper, dict) else {}
    terms = query_terms(query)

    if not terms:
        return RelevanceResult(
            score=0,
            level="Low",
            reasons=["empty query; relevance was not assessed"],
            matched_terms=[],
            retain=True,
        )

    normalized_query = " ".join(terms)
    normalized_title = normalize_text(paper.get("title"))
    normalized_abstract = normalize_text(paper.get("abstract"))
    normalized_concepts = normalize_text(_concept_text(paper))

    title_matches = _matches(terms, _field_terms(normalized_title))
    abstract_matches = _matches(terms, _field_terms(normalized_abstract))
    concept_matches = _matches(terms, _field_terms(normalized_concepts))

    term_count = len(terms)
    score = (
        TITLE_TERM_WEIGHT * len(title_matches) / term_count
        + ABSTRACT_TERM_WEIGHT * len(abstract_matches) / term_count
        + CONCEPT_TERM_WEIGHT * len(concept_matches) / term_count
    )

    reasons = []

    if title_matches:
        reasons.append(
            f"title matches {len(title_matches)}/{term_count} query terms: "
            f"{', '.join(title_matches)}"
        )

    if abstract_matches:
        reasons.append(
            f"abstract matches {len(abstract_matches)}/{term_count} query terms: "
            f"{', '.join(abstract_matches)}"
        )

    if concept_matches:
        reasons.append(
            f"concepts match {len(concept_matches)}/{term_count} query terms: "
            f"{', '.join(concept_matches)}"
        )

    if _contains_phrase(normalized_title, normalized_query):
        score += TITLE_PHRASE_WEIGHT
        reasons.insert(0, "exact normalized query phrase in title")

    if _contains_phrase(normalized_abstract, normalized_query):
        score += ABSTRACT_PHRASE_WEIGHT
        reasons.append("exact normalized query phrase in abstract")

    score = max(0, min(int(round(score)), MAX_RELEVANCE_SCORE))
    matched_terms = [
        term
        for term in terms
        if term in set(title_matches + abstract_matches + concept_matches)
    ]

    distinctive_terms = [term for term in terms if term not in BROAD_QUERY_TERMS]
    distinctive_match = any(term in matched_terms for term in distinctive_terms)
    broad_only = bool(distinctive_terms) and bool(matched_terms) and not distinctive_match

    if broad_only:
        low_confidence_ceiling = max(config.RELEVANCE_MEDIUM_SCORE - 1, 0)
        score = min(score, low_confidence_ceiling)
        reasons.append(
            "only broad query terms matched; relevance is capped at Low confidence"
        )

    retain = score >= int(min_score)

    if not reasons:
        reasons.append("no query terms found in title, abstract, or concepts")

    if score < int(min_score):
        reasons.append(f"below conservative relevance requirement ({int(min_score)}/100)")

    return RelevanceResult(
        score=score,
        level=relevance_level(score),
        reasons=reasons,
        matched_terms=matched_terms,
        retain=retain,
    )


def annotate_paper(paper, query, min_score=None):
    """Add relevance outputs to a paper dict in place and return the result."""

    if not isinstance(paper, dict):
        return None, calculate_relevance({}, query, min_score=min_score)

    analysis = calculate_relevance(paper, query, min_score=min_score)
    paper["relevance_score"] = analysis.score
    paper["relevance_level"] = analysis.level
    paper["relevance_reasons"] = list(analysis.reasons)

    return paper, analysis


def filter_papers(papers, query, min_score=None):
    """Annotate retrieved papers, separating retained from excluded records."""

    retained = []
    excluded = []

    for paper in papers or []:
        if not isinstance(paper, dict):
            continue

        annotated, analysis = annotate_paper(paper, query, min_score=min_score)

        if analysis.retain:
            retained.append(annotated)
        else:
            excluded.append(annotated)

    return RelevanceBatch(
        retained=retained,
        excluded=excluded,
        retrieved=len(retained) + len(excluded),
    )


def evaluate_labeled_papers(cases, query, min_score=None):
    """Calculate confusion-matrix metrics for deterministic labeled fixtures.

    Each case is ``{"paper": <dict>, "relevant": <bool>}``.  The utility is
    intentionally tiny so the thesis evaluation uses the exact same retention
    decision as the live search pipeline.
    """

    true_positives = false_positives = true_negatives = false_negatives = 0

    for case in cases or []:
        expected = bool(case.get("relevant"))
        predicted = calculate_relevance(
            case.get("paper") or {}, query, min_score=min_score
        ).retain

        if expected and predicted:
            true_positives += 1
        elif expected:
            false_negatives += 1
        elif predicted:
            false_positives += 1
        else:
            true_negatives += 1

    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives

    precision = (
        true_positives / precision_denominator if precision_denominator else 0.0
    )
    recall = true_positives / recall_denominator if recall_denominator else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "true_negatives": true_negatives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


__all__ = [
    "TITLE_TERM_WEIGHT",
    "ABSTRACT_TERM_WEIGHT",
    "CONCEPT_TERM_WEIGHT",
    "TITLE_PHRASE_WEIGHT",
    "ABSTRACT_PHRASE_WEIGHT",
    "MAX_RELEVANCE_SCORE",
    "RelevanceResult",
    "RelevanceBatch",
    "normalize_text",
    "query_terms",
    "relevance_level",
    "calculate_relevance",
    "annotate_paper",
    "filter_papers",
    "evaluate_labeled_papers",
]
