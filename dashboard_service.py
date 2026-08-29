"""
Dashboard aggregation.

The dashboard answers four *conceptually separate* questions, and this module
computes each one independently so they cannot be confused for one another:

  A. Research Activity -- how much is published, and when?
     Papers per year, citations per year, coverage gaps.  Says nothing about
     which topics or how good the papers are.

  B. Research Trends -- which topics are rising, falling, or holding?
     Normalized topic shares over time (see trends.py).  Deliberately separate
     from activity: a topic's share can fall in a year when total output grows.

  C. Paper Score -- which papers does *our formula* rank highest?
     Ranked by research_score, with the component breakdown shown so the number
     is auditable rather than magic.  This is our opinion, not the world's.

  D. Citations -- which papers has the field actually cited most?
     Ranked by citation_count alone.  This is the world's opinion, not ours.
     C and D are separate sections precisely because they disagree, and that
     disagreement is informative.

Everything here tolerates an empty database, a single year, gaps between years,
missing concepts, missing scores and missing citation counts.
"""

import config
import scoring
import trends as trend_analysis
from concepts import count_topics


#: Rows shown in the two paper tables.
TOP_PAPERS = 10


def _int(value):
    if value is None or isinstance(value, bool):
        return 0

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _display_row(paper, include_breakdown=False, reference_year=None):
    """One row for the score/citation tables, safe against missing fields."""

    row = {
        "paper_id": paper.get("paper_id"),
        "title": paper.get("title") or "(untitled)",
        "year": paper.get("year"),
        "citation_count": _int(paper.get("citation_count")),
        "research_score": _int(paper.get("research_score")),
        "authors": paper.get("authors") or [],
        "doi": paper.get("doi"),
        "url": paper.get("url") or paper.get("paper_id"),
        "source": paper.get("source"),
        "keyword": paper.get("keyword"),
    }

    if include_breakdown:
        row["breakdown"] = scoring.score_breakdown(
            paper,
            keyword=paper.get("keyword"),
            reference_year=reference_year,
        )

    return row


# --------------------------------------------------------------------------
# Section A -- Research Activity
# --------------------------------------------------------------------------

def research_activity(papers):
    """Publication volume and citation volume over time."""

    activity = trend_analysis.yearly_activity(papers)

    rows = activity["years"]
    dated = [row for row in rows if row["papers"]]

    total_papers = len(papers or [])
    total_citations = sum(max(_int(p.get("citation_count")), 0) for p in papers or [])

    peak_year = None

    if dated:
        peak = max(dated, key=lambda row: (row["papers"], row["year"]))
        peak_year = {"year": peak["year"], "papers": peak["papers"]}

    return {
        "total_papers": total_papers,
        "total_citations": total_citations,
        "undated_papers": activity["undated_papers"],
        "first_year": activity["first_year"],
        "last_year": activity["last_year"],
        "years_covered": len(dated),
        "has_gaps": activity["has_gaps"],
        "peak_year": peak_year,
        "mean_citations": (
            round(total_citations / total_papers, 1) if total_papers else 0.0
        ),
        # Chart series, aligned by index. Gap years are present with 0 so the
        # x-axis stays chronological instead of compressing empty periods.
        "chart": {
            "labels": [row["year"] for row in rows],
            "papers": [row["papers"] for row in rows],
            "citations": [row["citations"] for row in rows],
        },
        "years": rows,
    }


# --------------------------------------------------------------------------
# Section B -- Research Trends
# --------------------------------------------------------------------------

def research_trends(papers):
    """Topic trend classification, with the thresholds that produced it."""

    classified = trend_analysis.classify_trends(papers)

    # Raw (un-normalized) totals are kept alongside, clearly labelled, so the
    # dashboard can show both views without ever mixing them up.
    raw_totals = count_topics(papers)

    classified["raw_top_topics"] = [
        {"name": name, "count": count}
        for name, count in raw_totals.most_common(config.TREND_TOP_N)
    ]

    classified["categories"] = [
        {
            "key": "emerging",
            "label": "Emerging",
            "blurb": "Absent from the earlier window, clearly present now.",
            "rows": classified["emerging"],
        },
        {
            "key": "growing",
            "label": "Growing",
            "blurb": (
                f"Share of yearly output up "
                f"{int(config.TREND_GROWTH_THRESHOLD * 100)}% or more."
            ),
            "rows": classified["growing"],
        },
        {
            "key": "declining",
            "label": "Declining",
            "blurb": (
                f"Share of yearly output down "
                f"{int(config.TREND_GROWTH_THRESHOLD * 100)}% or more."
            ),
            "rows": classified["declining"],
        },
        {
            "key": "persistent",
            "label": "Persistent",
            "blurb": (
                f"Present in at least "
                f"{int(config.TREND_PERSISTENCE_RATIO * 100)}% of measured "
                f"years, without a sharp move."
            ),
            "rows": classified["persistent"],
        },
    ]

    return classified


# --------------------------------------------------------------------------
# Section C -- Paper Score
# --------------------------------------------------------------------------

def paper_scores(papers, limit=TOP_PAPERS):
    """Papers ranked by our research score, with component breakdowns."""

    reference_year = scoring.corpus_reference_year(papers)

    ranked = sorted(
        papers or [],
        key=lambda paper: (
            -_int(paper.get("research_score")),
            -_int(paper.get("citation_count")),
            str(paper.get("paper_id") or ""),
        ),
    )

    scores = [_int(paper.get("research_score")) for paper in papers or []]

    distribution = []

    if scores:
        # Fixed 10-point buckets: comparable between runs and between corpora.
        buckets = {}

        for score in scores:
            bucket = min(score // 10 * 10, 90)
            buckets[bucket] = buckets.get(bucket, 0) + 1

        distribution = [
            {
                "label": f"{low}-{low + 9}",
                "count": buckets.get(low, 0),
            }
            for low in range(0, 100, 10)
        ]

    return {
        "papers": [
            _display_row(paper, include_breakdown=True, reference_year=reference_year)
            for paper in ranked[:limit]
        ],
        "count": len(scores),
        "mean_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "max_score": max(scores) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "distribution": distribution,
        "formula": {
            "citation": scoring.MAX_CITATION_POINTS,
            "relevance": scoring.MAX_RELEVANCE_POINTS,
            "recency": scoring.MAX_RECENCY_POINTS,
            "completeness": scoring.MAX_COMPLETENESS_POINTS,
            "max": scoring.MAX_SCORE,
        },
    }


# --------------------------------------------------------------------------
# Section D -- Citations
# --------------------------------------------------------------------------

def citation_impact(papers, limit=TOP_PAPERS):
    """Papers ranked purely by citation count -- the field's own verdict."""

    ranked = sorted(
        papers or [],
        key=lambda paper: (
            -_int(paper.get("citation_count")),
            -_int(paper.get("research_score")),
            str(paper.get("paper_id") or ""),
        ),
    )

    counts = sorted(
        (max(_int(paper.get("citation_count")), 0) for paper in papers or []),
        reverse=True,
    )

    total = sum(counts)

    median = 0.0

    if counts:
        middle = len(counts) // 2

        if len(counts) % 2:
            median = float(counts[middle])
        else:
            median = (counts[middle - 1] + counts[middle]) / 2

    # h-index over the stored corpus: h papers with at least h citations each.
    h_index = 0

    for position, count in enumerate(counts, start=1):
        if count >= position:
            h_index = position
        else:
            break

    uncited = sum(1 for count in counts if count == 0)

    return {
        "papers": [_display_row(paper) for paper in ranked[:limit]],
        "count": len(counts),
        "total_citations": total,
        "mean_citations": round(total / len(counts), 1) if counts else 0.0,
        "median_citations": median,
        "max_citations": counts[0] if counts else 0,
        "h_index": h_index,
        "uncited": uncited,
        "uncited_share": round(uncited / len(counts), 3) if counts else 0.0,
    }


# --------------------------------------------------------------------------
# Assembled view
# --------------------------------------------------------------------------

def build_dashboard(papers):
    """All four sections, computed independently from the same paper list."""

    papers = list(papers or [])

    return {
        "empty": not papers,
        "activity": research_activity(papers),
        "trends": research_trends(papers),
        "scores": paper_scores(papers),
        "citations": citation_impact(papers),
    }
