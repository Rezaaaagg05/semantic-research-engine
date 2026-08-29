"""
The research score: one canonical, deterministic implementation.

This replaces two divergent scorers that used to disagree badly (the same paper
scored 40 by provider.py and 80 by research_ranker.py).  There is now exactly
one formula, defined here, and every caller uses it.

Formula
=======
The score is an integer in 0..100, the sum of four independent components:

  1. Citation impact                                        0..50
     Log-scaled, because citation counts are heavy-tailed: the gap between
     0 and 100 citations matters far more than the gap between 5,000 and 5,100.

         points = round(50 * log10(1 + citations) / log10(1 + 10000))

     capped at 50, so a paper with 10,000+ citations earns the full 50.
     0 citations earns 0 -- an uncited paper gets no impact credit, but is
     never penalised below zero.

  2. Topical relevance to the query                         0..20
     Fraction of the query's meaningful words (stopwords removed, 3+ chars)
     that appear in the title or the paper's concept names.  A title match is
     worth full credit; a concept-only match is worth half.

         points = round(20 * coverage), coverage in 0..1

     With no keyword, this component contributes a neutral 10 rather than 0,
     so browsing the database does not make every paper look bad.

  3. Recency                                                0..20
     Papers age.  Full credit within 2 years of the corpus's reference year,
     tapering linearly to 0 at 20 years, then flat.

         age = reference_year - year
         age <= 2   -> 20
         age >= 20  -> 0
         otherwise  -> round(20 * (20 - age) / 18)

     A missing year earns 8 (slightly below the midpoint): unknown recency is
     a real weakness, but not evidence of being old.
     A year in the future is clamped to age 0 rather than trusted.

  4. Metadata completeness                                  0..10
     Whether we actually know enough about the paper to rank it: abstract
     present (4), at least one author (3), at least two concepts (3).
     This deliberately rewards records we can analyse over stubs.

Properties
==========
  * Deterministic: same inputs -> same output, always. No randomness, no clock
    reads (the reference year is passed in, defaulting to the corpus maximum).
  * Total: handles citations of 0/None/negative/string, missing year, missing
    concepts, missing title, missing abstract, empty keyword.
  * Bounded: always 0..100 inclusive.
  * Order-independent: scoring a list does not depend on list order.
"""

import math
import re

from concepts import concept_names, normalize_concepts


#: Component ceilings, exposed so tests assert the documented contract rather
#: than magic numbers copied out of the code.
MAX_CITATION_POINTS = 50
MAX_RELEVANCE_POINTS = 20
MAX_RECENCY_POINTS = 20
MAX_COMPLETENESS_POINTS = 10

MAX_SCORE = (
    MAX_CITATION_POINTS
    + MAX_RELEVANCE_POINTS
    + MAX_RECENCY_POINTS
    + MAX_COMPLETENESS_POINTS
)

#: Citation count that earns full impact credit.
CITATION_SATURATION = 10000

#: Recency taper boundaries, in years.
RECENCY_FULL_CREDIT_AGE = 2
RECENCY_ZERO_CREDIT_AGE = 20

#: Awarded when the publication year is unknown.
RECENCY_UNKNOWN_POINTS = 8

#: Awarded for relevance when no keyword was supplied.
RELEVANCE_NEUTRAL_POINTS = MAX_RELEVANCE_POINTS // 2


#: Words too common to signal topical relevance.
STOPWORDS = frozenset(
    (
        "the", "and", "for", "with", "from", "that", "this", "into", "onto",
        "are", "was", "were", "been", "being", "have", "has", "had", "its",
        "their", "these", "those", "such", "than", "then", "over", "under",
        "using", "used", "use", "based", "via", "per", "about", "between",
        "among", "within", "without", "toward", "towards", "against", "upon",
        "study", "studies", "paper", "papers", "research", "analysis",
        "approach", "approaches", "method", "methods", "review", "new",
        "novel", "case", "results", "effects", "effect", "role", "model",
        "models", "framework", "evidence", "data",
    )
)

_WORD = re.compile(r"[a-z0-9]+")


def _as_int(value, default=0):
    if value is None or isinstance(value, bool):
        return default

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return default
        return int(value)

    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def keyword_terms(keyword):
    """Meaningful lowercase words from a search phrase."""

    if not keyword:
        return []

    words = _WORD.findall(str(keyword).lower())

    return [word for word in words if len(word) >= 3 and word not in STOPWORDS]


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------

def citation_points(citation_count):
    """0..50, log-scaled. Negative and non-numeric input read as 0."""

    citations = max(_as_int(citation_count, 0), 0)

    if citations <= 0:
        return 0

    scaled = math.log10(1 + citations) / math.log10(1 + CITATION_SATURATION)

    return min(int(round(MAX_CITATION_POINTS * scaled)), MAX_CITATION_POINTS)


def relevance_points(paper, keyword):
    """0..20 from query-word coverage of the title and concept names."""

    terms = keyword_terms(keyword)

    if not terms:
        return RELEVANCE_NEUTRAL_POINTS

    title = paper.get("title")
    title_words = set(_WORD.findall(str(title).lower() if title else ""))

    concepts = paper.get("concepts")

    if not isinstance(concepts, (list, tuple)):
        # A JSON string, a single name, a lone dict -- or a scalar left in the
        # column by an older write.  normalize_concepts handles all of them.
        concepts = normalize_concepts(concepts)

    elif concepts and not isinstance(concepts[0], dict):
        concepts = normalize_concepts(concepts)

    concept_words = set()

    for name in concept_names(concepts):
        concept_words.update(_WORD.findall(name.lower()))

    credit = 0.0

    for term in terms:

        if term in title_words:
            credit += 1.0
        elif term in concept_words:
            credit += 0.5

    coverage = credit / len(terms)

    return min(
        int(round(MAX_RELEVANCE_POINTS * coverage)),
        MAX_RELEVANCE_POINTS,
    )


def recency_points(year, reference_year):
    """0..20, linear taper. Unknown year earns RECENCY_UNKNOWN_POINTS."""

    published = _as_int(year, None)

    if published is None:
        return RECENCY_UNKNOWN_POINTS

    if reference_year is None:
        return RECENCY_UNKNOWN_POINTS

    age = reference_year - published

    if age <= RECENCY_FULL_CREDIT_AGE:
        # Includes future-dated papers (negative age), clamped to full credit.
        return MAX_RECENCY_POINTS

    if age >= RECENCY_ZERO_CREDIT_AGE:
        return 0

    span = RECENCY_ZERO_CREDIT_AGE - RECENCY_FULL_CREDIT_AGE

    return int(round(MAX_RECENCY_POINTS * (RECENCY_ZERO_CREDIT_AGE - age) / span))


def completeness_points(paper):
    """0..10 for how much usable metadata the record carries."""

    points = 0

    abstract = paper.get("abstract")

    if abstract and str(abstract).strip():
        points += 4

    authors = paper.get("authors") or []

    if isinstance(authors, str):
        authors = [part for part in authors.split(",") if part.strip()]

    if not isinstance(authors, (list, tuple, set, frozenset)):
        # A scalar in an authors column is corruption, not metadata; it earns
        # nothing rather than crashing the scorer.
        authors = []

    if len(authors) >= 1:
        points += 3

    concepts = paper.get("concepts")

    if isinstance(concepts, str):
        concepts = normalize_concepts(concepts)

    if not isinstance(concepts, (list, tuple, set, frozenset)):
        concepts = []

    if len(concepts) >= 2:
        points += 3

    return min(points, MAX_COMPLETENESS_POINTS)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def score_breakdown(paper, keyword=None, reference_year=None):
    """Per-component scores plus the total. Useful for the UI and for tests."""

    if not isinstance(paper, dict):
        paper = {}

    if keyword is None:
        keyword = paper.get("keyword")

    citation = citation_points(paper.get("citation_count"))
    relevance = relevance_points(paper, keyword)
    recency = recency_points(paper.get("year"), reference_year)
    completeness = completeness_points(paper)

    total = citation + relevance + recency + completeness

    return {
        "citation": citation,
        "relevance": relevance,
        "recency": recency,
        "completeness": completeness,
        "total": max(0, min(total, MAX_SCORE)),
    }


def calculate_research_score(paper, keyword=None, reference_year=None):
    """The canonical research score for one paper: an int in 0..100."""

    return score_breakdown(paper, keyword, reference_year)["total"]


def corpus_reference_year(papers):
    """Newest plausible year in the corpus, used as the recency baseline.

    Deriving the baseline from the data instead of the system clock keeps
    scoring deterministic and makes historical corpora score sensibly.
    """

    years = []

    for paper in papers or []:

        if isinstance(paper, dict):
            year = _as_int(paper.get("year"), None)
        else:
            year = _as_int(getattr(paper, "year", None), None)

        if year is not None:
            years.append(year)

    if not years:
        return None

    return max(years)


def score_papers(papers, keyword=None, reference_year=None):
    """Score every paper in place and return them ranked, best first.

    Ranking is a stable, fully-determined ordering: score, then citations,
    then year, then paper_id -- so equal-scoring papers never shuffle between
    runs.

    Entries that are not paper dicts are dropped rather than carried along: a
    scored list is consumed by the templates and the database, and neither can
    do anything with a stray string.
    """

    papers = [paper for paper in (papers or []) if isinstance(paper, dict)]

    if reference_year is None:
        reference_year = corpus_reference_year(papers)

    for paper in papers:

        paper["research_score"] = calculate_research_score(
            paper,
            keyword=keyword if keyword is not None else paper.get("keyword"),
            reference_year=reference_year,
        )

    papers.sort(
        key=lambda paper: (
            -_as_int(paper.get("research_score"), 0),
            -_as_int(paper.get("citation_count"), 0),
            -(_as_int(paper.get("year"), 0)),
            str(paper.get("paper_id") or ""),
        )
    )

    return papers
