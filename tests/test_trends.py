"""Trend analysis (trends.py).

The headline test in this file is ``test_a_topic_can_grow_in_count_while_its
_share_collapses``.  It encodes the mistake this module exists to fix: counting
mentions per year and calling the result a trend.  A topic can appear in more
papers than ever while commanding a smaller share of a corpus that grew faster
than it did -- that topic is declining, and only normalized shares can see it.
"""

import pytest

import config
import trends
from trends import (
    analyze_trends,
    build_year_index,
    classify_trends,
    yearly_activity,
)


@pytest.fixture
def build_corpus(make_paper, make_concept):
    """Build a corpus from ``[(year, [(count, (topics...)), ...]), ...]``.

    Per-year blocks are what make normalized shares testable: within one year
    some papers carry a topic and some do not, so the share is a real fraction
    rather than always 1.0.
    """

    def builder(spec):
        papers = []
        index = 0

        for year, blocks in spec:
            for count, topics in blocks:
                for _ in range(count):
                    index += 1
                    papers.append(
                        make_paper(
                            index,
                            year=year,
                            concepts=[make_concept(name) for name in topics],
                        )
                    )

        return papers

    return builder


def names(rows):
    return [row["name"] for row in rows]


def find(rows, name):
    return next(row for row in rows if row["name"] == name)


class TestThresholdAssumptions:

    def test_the_corpora_below_are_built_for_these_thresholds(self):
        # The hand-built corpora in this file assume these values.  If a
        # threshold changes, this test fails first and explains why.
        assert config.TREND_MIN_PAPERS_PER_YEAR == 5
        assert config.TREND_MIN_TOTAL_OCCURRENCES == 5
        assert config.TREND_MIN_RECENT_OCCURRENCES == 3
        assert config.TREND_GROWTH_THRESHOLD == 0.50
        assert config.TREND_WINDOW_YEARS == 5
        assert config.TREND_PERSISTENCE_RATIO == 0.60


class TestBuildYearIndex:

    def test_shares_are_papers_with_the_topic_over_papers_that_year(self, build_corpus):
        corpus = build_corpus([(2020, [(3, ("Value at risk",)), (1, ("Other topic",))])])

        years, undated = build_year_index(corpus)

        assert years[2020]["papers"] == 4
        assert years[2020]["topic_counts"]["Value at risk"] == 3
        assert years[2020]["shares"]["Value at risk"] == 0.75
        assert undated == 0

    def test_a_year_below_the_threshold_is_marked_unreliable(self, build_corpus):
        corpus = build_corpus(
            [(2020, [(4, ("Topic",))]), (2021, [(5, ("Topic",))])]
        )

        years, _ = build_year_index(corpus)

        assert years[2020]["reliable"] is False
        assert years[2021]["reliable"] is True

    @pytest.mark.parametrize("year", [None, "soon", True, 1200, 2500, [], {}])
    def test_papers_without_a_usable_year_are_counted_but_not_dated(self, make_paper, year):
        years, undated = build_year_index([make_paper(1, year=year)])

        assert years == {}
        assert undated == 1

    def test_empty_corpus(self):
        assert build_year_index([]) == ({}, 0)
        assert build_year_index(None) == ({}, 0)


class TestTheRawCountTrap:

    def test_a_topic_can_grow_in_count_while_its_share_collapses(self, build_corpus):
        # 2010-2014: 6 papers a year, 4 about value at risk  -> share 0.67
        # 2020-2024: 20 papers a year, 5 about value at risk -> share 0.25
        #
        # Raw mentions rose from 20 to 25.  Counting says "growing".
        # Share fell from 67% to 25%.  The field moved on.
        corpus = build_corpus(
            [(year, [(4, ("Value at risk",)), (2, ("Other topic",))]) for year in range(2010, 2015)]
            + [
                (year, [(5, ("Value at risk",)), (15, ("Other topic",))])
                for year in range(2020, 2025)
            ]
        )

        result = classify_trends(corpus)

        assert "Value at risk" in names(result["declining"])
        assert "Value at risk" not in names(result["growing"])

        record = find(result["declining"], "Value at risk")

        # The trap, stated as an assertion: more papers than before, and still
        # declining.  Both numbers are reported so the UI cannot mislead either.
        assert record["recent_papers"] > record["previous_papers"]
        assert record["recent_share"] < record["previous_share"]
        assert record["change"] == pytest.approx(-0.625, abs=0.001)

    def test_the_topic_that_actually_took_over_is_the_growing_one(self, build_corpus):
        corpus = build_corpus(
            [(year, [(4, ("Value at risk",)), (2, ("Other topic",))]) for year in range(2010, 2015)]
            + [
                (year, [(5, ("Value at risk",)), (15, ("Other topic",))])
                for year in range(2020, 2025)
            ]
        )

        result = classify_trends(corpus)

        assert "Other topic" in names(result["growing"])
        assert find(result["growing"], "Other topic")["change"] > 0


class TestClassification:

    def test_a_new_topic_is_emerging(self, build_corpus):
        # "Machine learning in finance", not "Machine learning": the bare
        # discipline is a container term and is filtered out by design.
        corpus = build_corpus(
            [(year, [(6, ("Staple",))]) for year in range(2010, 2015)]
            + [
                (year, [(2, ("Staple", "Machine learning in finance")), (4, ("Staple",))])
                for year in range(2020, 2025)
            ]
        )

        result = classify_trends(corpus)

        assert "Machine learning in finance" in names(result["emerging"])

        record = find(result["emerging"], "Machine learning in finance")

        assert record["previous_papers"] == 0
        assert record["recent_papers"] == 10
        # No previous share to divide by, so no ratio is invented.
        assert record["change"] is None

    def test_a_topic_that_vanished_is_declining(self, build_corpus):
        corpus = build_corpus(
            [
                (year, [(3, ("Retired topic", "Staple")), (3, ("Staple",))])
                for year in range(2010, 2015)
            ]
            + [(year, [(6, ("Staple",))]) for year in range(2020, 2025)]
        )

        result = classify_trends(corpus)

        record = find(result["declining"], "Retired topic")

        # Declining deliberately has no minimum-recent rule: disappearing is
        # exactly what a declining topic does.
        assert record["recent_papers"] == 0
        assert record["change"] == -1.0

    def test_a_stable_staple_is_persistent(self, build_corpus):
        corpus = build_corpus(
            [(year, [(3, ("Staple",)), (3, ("Other topic",))]) for year in range(2010, 2020)]
        )

        result = classify_trends(corpus)

        assert "Staple" in names(result["persistent"])

        record = find(result["persistent"], "Staple")

        assert record["presence_ratio"] == 1.0
        assert record["years_present"] == 10

    def test_a_topic_appears_in_at_most_one_movement_category(self, build_corpus):
        corpus = build_corpus(
            [(year, [(4, ("Value at risk",)), (2, ("Other topic",))]) for year in range(2010, 2015)]
            + [
                (year, [(5, ("Value at risk",)), (15, ("Other topic",))])
                for year in range(2020, 2025)
            ]
        )

        result = classify_trends(corpus)

        appearances = (
            names(result["emerging"])
            + names(result["growing"])
            + names(result["declining"])
            + names(result["persistent"])
        )

        assert len(appearances) == len(set(appearances))

    def test_a_rare_topic_is_not_a_trend(self, build_corpus):
        # Its share jumped, but on the strength of two papers.  A jump that
        # small is noise, and noise must not be reported as a movement.
        corpus = build_corpus(
            [(year, [(1, ("Rare topic",)), (19, ("Filler",))]) for year in range(2010, 2015)]
            + [
                (year, [(1, ("Rare topic",)), (4, ("Filler",))])
                for year in (2020, 2021)
            ]
            + [(year, [(5, ("Filler",))]) for year in (2022, 2023, 2024)]
        )

        result = classify_trends(corpus)

        assert result["sufficient_data"] is True

        everything = (
            names(result["emerging"])
            + names(result["growing"])
            + names(result["declining"])
            + names(result["persistent"])
        )

        assert "Rare topic" not in everything

    def test_a_topic_below_the_total_threshold_is_ignored(self, build_corpus):
        corpus = build_corpus(
            [(year, [(6, ("Staple",))]) for year in range(2010, 2015)]
            + [(2020, [(2, ("Staple", "Barely there")), (4, ("Staple",))])]
            + [(year, [(6, ("Staple",))]) for year in range(2021, 2025)]
        )

        result = classify_trends(corpus)

        # Two papers overall is below TREND_MIN_TOTAL_OCCURRENCES.
        assert "Barely there" not in names(result["emerging"])

    def test_generic_disciplines_are_excluded_by_default(self, build_corpus):
        corpus = build_corpus(
            [
                (year, [(6, ("Economics", "Value at risk"))])
                for year in range(2010, 2020)
            ]
        )

        result = classify_trends(corpus)

        assert "Economics" not in names(result["top_topics"])
        assert "Value at risk" in names(result["top_topics"])

    def test_generic_disciplines_can_be_kept_on_request(self, build_corpus):
        corpus = build_corpus(
            [
                (year, [(6, ("Economics", "Value at risk"))])
                for year in range(2010, 2020)
            ]
        )

        result = classify_trends(corpus, drop_generic=False)

        assert "Economics" in names(result["top_topics"])

    def test_the_thresholds_that_produced_the_result_are_reported(self, build_corpus):
        corpus = build_corpus([(year, [(6, ("Staple",))]) for year in range(2010, 2020)])

        thresholds = classify_trends(corpus)["thresholds"]

        assert thresholds["min_papers_per_year"] == config.TREND_MIN_PAPERS_PER_YEAR
        assert thresholds["growth_threshold"] == config.TREND_GROWTH_THRESHOLD
        assert thresholds["window_years"] == config.TREND_WINDOW_YEARS
        assert thresholds["persistence_ratio"] == config.TREND_PERSISTENCE_RATIO

    def test_the_windows_used_are_reported(self, build_corpus):
        corpus = build_corpus(
            [(year, [(6, ("Staple",))]) for year in range(2010, 2015)]
            + [(year, [(6, ("Staple",))]) for year in range(2020, 2025)]
        )

        result = classify_trends(corpus)

        assert result["previous_window"] == [2010, 2011, 2012, 2013, 2014]
        assert result["recent_window"] == [2020, 2021, 2022, 2023, 2024]

    def test_every_record_carries_counts_and_shares_together(self, build_corpus):
        corpus = build_corpus(
            [(year, [(4, ("Value at risk",)), (2, ("Other topic",))]) for year in range(2010, 2015)]
            + [
                (year, [(5, ("Value at risk",)), (15, ("Other topic",))])
                for year in range(2020, 2025)
            ]
        )

        result = classify_trends(corpus)

        for category in ("emerging", "growing", "declining", "persistent"):
            for row in result[category]:
                assert set(row) >= {
                    "name",
                    "recent_papers",
                    "previous_papers",
                    "total_papers",
                    "recent_share",
                    "previous_share",
                    "years_present",
                    "presence_ratio",
                    "change",
                }


class TestWindows:

    def test_two_full_windows_when_there_is_enough_history(self):
        previous, recent = trends._windows(list(range(2010, 2020)))

        assert previous == [2010, 2011, 2012, 2013, 2014]
        assert recent == [2015, 2016, 2017, 2018, 2019]

    def test_four_years_split_down_the_middle(self):
        # Regression: TREND_WINDOW_YEARS is 5, so four reliable years used to
        # land entirely in `recent`, leaving `previous` empty and classifying
        # nothing at all.
        assert trends._windows([2010, 2011, 2012, 2013]) == ([2010, 2011], [2012, 2013])

    def test_an_odd_split_favours_the_recent_side(self):
        assert trends._windows([2010, 2011, 2012]) == ([2010], [2011, 2012])

    def test_two_years_compare_one_to_one(self):
        assert trends._windows([2010, 2011]) == ([2010], [2011])

    def test_a_single_year_cannot_be_compared(self):
        assert trends._windows([2010]) == ([], [])
        assert trends._windows([]) == ([], [])

    def test_windows_are_built_from_years_that_exist(self):
        # A 40-year gap must not stretch a window across empty time.
        previous, recent = trends._windows([1980, 1981, 2020, 2021])

        assert previous == [1980, 1981]
        assert recent == [2020, 2021]

    def test_four_reliable_years_do_produce_a_classification(self, build_corpus):
        corpus = build_corpus(
            [
                (2010, [(6, ("Staple",))]),
                (2011, [(6, ("Staple",))]),
                (2012, [(3, ("Staple", "Newcomer")), (3, ("Staple",))]),
                (2013, [(3, ("Staple", "Newcomer")), (3, ("Staple",))]),
            ]
        )

        result = classify_trends(corpus)

        assert result["sufficient_data"] is True
        assert result["previous_window"] == [2010, 2011]
        assert result["recent_window"] == [2012, 2013]
        assert "Newcomer" in names(result["emerging"])


class TestThinData:

    def test_no_usable_years_says_so(self, make_paper):
        result = classify_trends([make_paper(1, year=None), make_paper(2, year="soon")])

        assert result["sufficient_data"] is False
        assert result["note"] == "No papers with a usable publication year."
        assert result["emerging"] == []
        assert result["undated_papers"] == 2

    def test_no_reliable_year_explains_the_threshold(self, build_corpus):
        corpus = build_corpus([(year, [(2, ("Staple",))]) for year in range(2010, 2015)])

        result = classify_trends(corpus)

        assert result["sufficient_data"] is False
        assert str(config.TREND_MIN_PAPERS_PER_YEAR) in result["note"]
        assert "more papers" in result["note"].lower()

    def test_a_thin_corpus_still_reports_its_top_topics(self, build_corpus):
        # The classification is unsafe, but showing what is in the corpus is not.
        corpus = build_corpus([(2020, [(2, ("Staple",))])])

        result = classify_trends(corpus)

        assert names(result["top_topics"]) == ["Staple"]

    def test_one_reliable_year_cannot_be_compared(self, build_corpus):
        corpus = build_corpus([(2020, [(9, ("Staple",))])])

        result = classify_trends(corpus)

        assert result["sufficient_data"] is False
        assert "At least two" in result["note"]
        assert result["growing"] == []

    def test_an_empty_corpus_is_reported_honestly(self):
        for corpus in ([], None):
            result = classify_trends(corpus)

            assert result["sufficient_data"] is False
            assert result["note"]

    def test_a_sufficient_corpus_carries_no_apology(self, build_corpus):
        corpus = build_corpus([(year, [(6, ("Staple",))]) for year in range(2010, 2020)])

        result = classify_trends(corpus)

        assert result["sufficient_data"] is True
        assert result["note"] == ""


class TestYearlyActivity:

    def test_counts_papers_and_citations_per_year(self, build_corpus):
        corpus = build_corpus([(2020, [(2, ("Staple",))]), (2021, [(1, ("Staple",))])])

        activity = yearly_activity(corpus)
        by_year = {row["year"]: row for row in activity["years"]}

        assert by_year[2020]["papers"] == 2
        assert by_year[2020]["citations"] == 20      # make_paper defaults to 10 each
        assert by_year[2021]["papers"] == 1

    def test_gap_years_are_filled_with_zero_not_hidden(self, build_corpus):
        corpus = build_corpus([(2010, [(1, ("Staple",))]), (2014, [(1, ("Staple",))])])

        activity = yearly_activity(corpus)

        assert [row["year"] for row in activity["years"]] == [2010, 2011, 2012, 2013, 2014]
        assert [row["papers"] for row in activity["years"]] == [1, 0, 0, 0, 1]
        assert activity["has_gaps"] is True

    def test_a_continuous_run_reports_no_gaps(self, build_corpus):
        corpus = build_corpus([(year, [(1, ("Staple",))]) for year in (2019, 2020, 2021)])

        assert yearly_activity(corpus)["has_gaps"] is False

    def test_first_and_last_year(self, build_corpus):
        corpus = build_corpus([(1999, [(1, ("Staple",))]), (2024, [(1, ("Staple",))])])

        activity = yearly_activity(corpus)

        assert activity["first_year"] == 1999
        assert activity["last_year"] == 2024

    def test_undated_papers_count_towards_the_total_only(self, build_corpus, make_paper):
        corpus = build_corpus([(2020, [(2, ("Staple",))])]) + [make_paper(99, year=None)]

        activity = yearly_activity(corpus)

        assert activity["total_papers"] == 3
        assert activity["undated_papers"] == 1
        assert sum(row["papers"] for row in activity["years"]) == 2

    def test_per_year_topics_are_ranked_and_capped(self, build_corpus):
        corpus = build_corpus(
            [
                (
                    2020,
                    [(1, tuple(f"Topic {index}" for index in range(8)))]
                    + [(3, ("Dominant topic",))],
                )
            ]
        )

        topics = yearly_activity(corpus)["years"][0]["topics"]

        assert len(topics) == config.TREND_TOPICS_PER_YEAR
        assert topics[0]["name"] == "Dominant topic"
        assert topics[0]["count"] == 3

    def test_each_topic_row_carries_both_count_and_share(self, build_corpus):
        corpus = build_corpus([(2020, [(3, ("Staple",)), (1, ("Other topic",))])])

        topics = {row["name"]: row for row in yearly_activity(corpus)["years"][0]["topics"]}

        assert topics["Staple"]["count"] == 3
        assert topics["Staple"]["share"] == 0.75

    def test_mean_score_per_year(self, build_corpus, make_paper):
        corpus = [
            make_paper(1, year=2020, research_score=40),
            make_paper(2, year=2020, research_score=61),
        ]

        assert yearly_activity(corpus)["years"][0]["mean_score"] == 50.5

    def test_an_empty_corpus(self):
        activity = yearly_activity([])

        assert activity["years"] == []
        assert activity["first_year"] is None
        assert activity["last_year"] is None
        assert activity["has_gaps"] is False
        assert activity["total_papers"] == 0

    def test_negative_citation_counts_do_not_subtract(self, make_paper):
        corpus = [make_paper(1, year=2020, citation_count=-50)]

        assert yearly_activity(corpus)["years"][0]["citations"] == 0


class TestAnalyzeTrends:

    def test_returns_activity_and_classification(self, build_corpus):
        corpus = build_corpus([(year, [(6, ("Staple",))]) for year in range(2010, 2020)])

        report = analyze_trends(corpus)

        assert set(report) == {"activity", "trends", "yearly_count", "topics_by_year"}
        assert report["trends"]["sufficient_data"] is True

    def test_the_legacy_yearly_count_shape_is_preserved(self, build_corpus):
        corpus = build_corpus([(2020, [(2, ("Staple",))]), (2021, [(1, ("Staple",))])])

        report = analyze_trends(corpus)

        # Old callers expect string keys and no zero-filled gap years.
        assert report["yearly_count"] == {"2020": 2, "2021": 1}

    def test_the_legacy_topics_by_year_shape_is_preserved(self, build_corpus):
        corpus = build_corpus([(2020, [(2, ("Staple",))])])

        report = analyze_trends(corpus)

        assert report["topics_by_year"] == {"2020": [("Staple", 2)]}

    def test_an_empty_corpus_returns_the_full_structure(self):
        report = analyze_trends([])

        assert report["yearly_count"] == {}
        assert report["topics_by_year"] == {}
        assert report["activity"]["years"] == []
        assert report["trends"]["sufficient_data"] is False
