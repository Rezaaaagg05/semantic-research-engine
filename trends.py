"""
Trend analysis.

The previous implementation counted concepts per year and called the result a
"trend".  Counting is not trend analysis: in a corpus where 2023 has 200 papers
and 1998 has 3, a topic appearing 8 times in 2023 and 2 times in 1998 looks
like explosive growth when it actually *halved* in share (4% vs 67%).

This module analyses normalized shares and states its thresholds explicitly.

Method
======
1. Bucket papers by publication year; a paper with no year is counted in the
   totals but excluded from every per-year calculation.

2. For each year compute:
     papers            number of papers
     topic_counts      papers mentioning each topic (not raw mentions)
     shares            topic_counts / papers  -- the normalized frequency
     reliable          papers >= TREND_MIN_PAPERS_PER_YEAR

   Only reliable years feed the classification.  This is the guard that stops
   a single 1962 paper from defining a trend.

3. Split the reliable years into two adjacent windows of at most
   TREND_WINDOW_YEARS each: ``recent`` (the newest) and ``previous`` (the block
   before it).  When the corpus holds fewer years than two full windows, the
   available years are split down the middle rather than abandoned, so four
   good years compare as 2-vs-2.  Windows are built from years that exist in
   the data, so gaps in coverage do not silently widen a window.

4. For each topic compute mean share in each window and the relative change:

       change = (recent_share - previous_share) / previous_share

   A topic absent from ``previous`` has no defined ratio, so it is handled by
   the "emerging" rule instead.

5. Classify, in priority order, using thresholds from config:

   emerging     absent (or below noise) in the previous window, present in the
                recent window with >= TREND_MIN_RECENT_OCCURRENCES papers.
                "New to this corpus."

   growing      present in both windows, change >= +TREND_GROWTH_THRESHOLD.

   declining    present in both windows, change <= -TREND_GROWTH_THRESHOLD.

   persistent   appears in >= TREND_PERSISTENCE_RATIO of reliable years and is
                classified neither growing nor declining -- a stable staple.

   Every topic must also clear TREND_MIN_TOTAL_OCCURRENCES papers overall.  A
   topic can appear in at most one of emerging/growing/declining; persistent is
   computed over what is left plus anything stable.

Every returned figure carries the raw counts alongside the normalized share, so
the dashboard can show both and never has to guess which one it is looking at.
"""

from collections import Counter, defaultdict

import config
from concepts import paper_topics


def _paper_year(paper):
    if isinstance(paper, dict):
        year = paper.get("year")
    else:
        year = getattr(paper, "year", None)

    if year is None or isinstance(year, bool):
        return None

    try:
        year = int(year)
    except (TypeError, ValueError):
        return None

    if year < 1500 or year > 2100:
        return None

    return year


def _numeric(paper, field):
    if isinstance(paper, dict):
        value = paper.get(field)
    else:
        value = getattr(paper, field, None)

    if value is None or isinstance(value, bool):
        return 0

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_year_index(papers, drop_generic=True):
    """Per-year aggregates: paper count, topic counts, and topic shares."""

    per_year_papers = Counter()
    per_year_topics = defaultdict(Counter)
    undated = 0

    for paper in papers or []:

        year = _paper_year(paper)

        if year is None:
            undated += 1
            continue

        per_year_papers[year] += 1
        per_year_topics[year].update(paper_topics(paper, drop_generic=drop_generic))

    years = {}

    for year, count in per_year_papers.items():

        topic_counts = dict(per_year_topics.get(year, {}))

        years[year] = {
            "year": year,
            "papers": count,
            "topic_counts": topic_counts,
            "shares": {
                topic: topic_count / count
                for topic, topic_count in topic_counts.items()
            },
            "reliable": count >= config.TREND_MIN_PAPERS_PER_YEAR,
        }

    return years, undated


def yearly_activity(papers, drop_generic=True):
    """Research activity per year: counts, citations, and dominant topics.

    Years with no papers between the first and last year of the corpus are
    filled in with zero, so a chart shows the gap instead of hiding it.
    """

    years, undated = build_year_index(papers, drop_generic=drop_generic)

    citations_by_year = Counter()
    scores_by_year = defaultdict(list)

    for paper in papers or []:

        year = _paper_year(paper)

        if year is None:
            continue

        citations_by_year[year] += max(_numeric(paper, "citation_count"), 0)
        scores_by_year[year].append(_numeric(paper, "research_score"))

    if not years:
        return {
            "years": [],
            "undated_papers": undated,
            "total_papers": len(papers or []),
            "first_year": None,
            "last_year": None,
            "has_gaps": False,
        }

    first_year = min(years)
    last_year = max(years)

    rows = []
    gaps = 0

    for year in range(first_year, last_year + 1):

        entry = years.get(year)

        if entry is None:
            gaps += 1
            rows.append(
                {
                    "year": year,
                    "papers": 0,
                    "citations": 0,
                    "mean_score": 0.0,
                    "topics": [],
                    "reliable": False,
                }
            )
            continue

        scores = scores_by_year.get(year) or []

        rows.append(
            {
                "year": year,
                "papers": entry["papers"],
                "citations": citations_by_year.get(year, 0),
                "mean_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
                "topics": [
                    {
                        "name": topic,
                        "count": count,
                        "share": round(entry["shares"][topic], 4),
                    }
                    for topic, count in sorted(
                        entry["topic_counts"].items(),
                        key=lambda item: (-item[1], item[0]),
                    )[: config.TREND_TOPICS_PER_YEAR]
                ],
                "reliable": entry["reliable"],
            }
        )

    return {
        "years": rows,
        "undated_papers": undated,
        "total_papers": len(papers or []),
        "first_year": first_year,
        "last_year": last_year,
        "has_gaps": gaps > 0,
    }


def _windows(reliable_years):
    """Split reliable years into (previous, recent) year lists.

    Each window holds at most TREND_WINDOW_YEARS years.  When the corpus has
    fewer years than two full windows, the available years are split down the
    middle instead of being abandoned -- four good years compare as 2-vs-2, and
    the odd year in an odd-length split goes to the recent side.  Windows are
    built only from years that exist in the data, so a gap in coverage never
    silently stretches a window across empty time.

    Returns ``([], [])`` when there is not enough history to compare anything.
    """

    ordered = sorted(reliable_years)
    count = len(ordered)

    if count < 2:
        return [], []

    span = config.TREND_WINDOW_YEARS

    recent_size = min(span, (count + 1) // 2)

    recent = ordered[-recent_size:]
    remaining = ordered[:-recent_size]

    if not remaining:
        return [], recent

    previous = remaining[-min(span, len(remaining)):]

    return previous, recent


def _mean_share(years_index, window, topic):
    if not window:
        return 0.0

    total = sum(years_index[year]["shares"].get(topic, 0.0) for year in window)

    return total / len(window)


def _window_papers(years_index, window, topic):
    return sum(years_index[year]["topic_counts"].get(topic, 0) for year in window)


def classify_trends(papers, drop_generic=True):
    """Classify topics as emerging / growing / declining / persistent.

    Returns a dict carrying the classified lists, the thresholds that produced
    them, and enough diagnostics for the UI to explain itself (or to say
    honestly that there is not enough data).
    """

    years_index, undated = build_year_index(papers, drop_generic=drop_generic)

    reliable_years = [
        year for year, entry in years_index.items() if entry["reliable"]
    ]

    thresholds = {
        "min_papers_per_year": config.TREND_MIN_PAPERS_PER_YEAR,
        "min_total_occurrences": config.TREND_MIN_TOTAL_OCCURRENCES,
        "min_recent_occurrences": config.TREND_MIN_RECENT_OCCURRENCES,
        "growth_threshold": config.TREND_GROWTH_THRESHOLD,
        "window_years": config.TREND_WINDOW_YEARS,
        "persistence_ratio": config.TREND_PERSISTENCE_RATIO,
    }

    total_counts = Counter()

    for entry in years_index.values():
        total_counts.update(entry["topic_counts"])

    empty = {
        "emerging": [],
        "growing": [],
        "declining": [],
        "persistent": [],
        "top_topics": [
            {"name": topic, "count": count}
            for topic, count in total_counts.most_common(config.TREND_TOP_N)
        ],
        "thresholds": thresholds,
        "reliable_years": sorted(reliable_years),
        "previous_window": [],
        "recent_window": [],
        "undated_papers": undated,
        "sufficient_data": False,
        "note": "",
    }

    if not years_index:
        empty["note"] = "No papers with a usable publication year."
        return empty

    if not reliable_years:
        empty["note"] = (
            f"No year has at least {config.TREND_MIN_PAPERS_PER_YEAR} papers, "
            f"so year-over-year comparison would not be meaningful. "
            f"Collect more papers to enable trend classification."
        )
        return empty

    previous, recent = _windows(reliable_years)

    if not previous or not recent:
        empty["recent_window"] = sorted(recent)
        empty["note"] = (
            f"Only {len(reliable_years)} year(s) have at least "
            f"{config.TREND_MIN_PAPERS_PER_YEAR} papers. At least two are "
            f"needed to compare windows."
        )
        return empty

    emerging = []
    growing = []
    declining = []
    persistent = []

    candidates = sorted(total_counts)

    for topic in candidates:

        if total_counts[topic] < config.TREND_MIN_TOTAL_OCCURRENCES:
            continue

        recent_papers = _window_papers(years_index, recent, topic)
        previous_papers = _window_papers(years_index, previous, topic)

        recent_share = _mean_share(years_index, recent, topic)
        previous_share = _mean_share(years_index, previous, topic)

        years_present = sum(
            1
            for year in reliable_years
            if years_index[year]["topic_counts"].get(topic, 0) > 0
        )

        presence_ratio = years_present / len(reliable_years)

        record = {
            "name": topic,
            "recent_papers": recent_papers,
            "previous_papers": previous_papers,
            "total_papers": total_counts[topic],
            "recent_share": round(recent_share, 4),
            "previous_share": round(previous_share, 4),
            "years_present": years_present,
            "presence_ratio": round(presence_ratio, 3),
            "change": None,
        }

        # Emerging: effectively absent before, clearly present now.
        if previous_papers == 0:

            if recent_papers >= config.TREND_MIN_RECENT_OCCURRENCES:
                record["change"] = None
                emerging.append(record)

            continue

        change = (recent_share - previous_share) / previous_share
        record["change"] = round(change, 4)

        if change >= config.TREND_GROWTH_THRESHOLD:

            if recent_papers >= config.TREND_MIN_RECENT_OCCURRENCES:
                growing.append(record)

            continue

        if change <= -config.TREND_GROWTH_THRESHOLD:
            declining.append(record)
            continue

        if presence_ratio >= config.TREND_PERSISTENCE_RATIO:
            persistent.append(record)

    top_n = config.TREND_TOP_N

    emerging.sort(key=lambda row: (-row["recent_papers"], row["name"]))
    growing.sort(key=lambda row: (-row["change"], -row["recent_papers"], row["name"]))
    declining.sort(key=lambda row: (row["change"], -row["previous_papers"], row["name"]))
    persistent.sort(key=lambda row: (-row["presence_ratio"], -row["total_papers"], row["name"]))

    return {
        "emerging": emerging[:top_n],
        "growing": growing[:top_n],
        "declining": declining[:top_n],
        "persistent": persistent[:top_n],
        "top_topics": [
            {"name": topic, "count": count}
            for topic, count in total_counts.most_common(top_n)
        ],
        "thresholds": thresholds,
        "reliable_years": sorted(reliable_years),
        "previous_window": sorted(previous),
        "recent_window": sorted(recent),
        "undated_papers": undated,
        "sufficient_data": True,
        "note": "",
    }


def analyze_trends(papers, drop_generic=True):
    """Full trend report: per-year activity plus topic classification.

    Also returns the two legacy keys ``yearly_count`` and ``topics_by_year`` in
    their original shapes, so any existing caller of the old
    ``trend_analyzer.analyze_trends`` keeps working.
    """

    activity = yearly_activity(papers, drop_generic=drop_generic)
    trends = classify_trends(papers, drop_generic=drop_generic)

    legacy_yearly = {
        str(row["year"]): row["papers"]
        for row in activity["years"]
        if row["papers"]
    }

    legacy_topics = {
        str(row["year"]): [(topic["name"], topic["count"]) for topic in row["topics"]]
        for row in activity["years"]
        if row["topics"]
    }

    return {
        "activity": activity,
        "trends": trends,
        "yearly_count": legacy_yearly,
        "topics_by_year": legacy_topics,
    }
