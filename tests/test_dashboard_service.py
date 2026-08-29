"""Dashboard aggregation (dashboard_service.py).

The dashboard answers four separate questions, and the tests are grouped the
same way -- one class per section -- because the sections must not be able to
contaminate one another.  The clearest expression of that is
``test_the_two_paper_tables_can_disagree``: our score and the world's citation
count rank papers differently, and a dashboard that hid the disagreement would
be less useful, not more.
"""

import pytest

import scoring
from dashboard_service import (
    TOP_PAPERS,
    build_dashboard,
    citation_impact,
    paper_scores,
    research_activity,
    research_trends,
)


@pytest.fixture
def spread(make_paper):
    """Four papers across four years with distinct citation counts."""

    return [
        make_paper(1, year=2018, citation_count=10),
        make_paper(2, year=2019, citation_count=8),
        make_paper(3, year=2020, citation_count=5),
        make_paper(4, year=2021, citation_count=4),
    ]


class TestSectionAActivity:

    def test_counts_papers_and_citations(self, spread):
        activity = research_activity(spread)

        assert activity["total_papers"] == 4
        assert activity["total_citations"] == 27
        assert activity["mean_citations"] == 6.8

    def test_the_span_of_the_corpus(self, spread):
        activity = research_activity(spread)

        assert activity["first_year"] == 2018
        assert activity["last_year"] == 2021
        assert activity["years_covered"] == 4

    def test_the_chart_series_are_aligned_by_index(self, make_paper):
        papers = [make_paper(1, year=2010), make_paper(2, year=2013)]

        chart = research_activity(papers)["chart"]

        assert len(chart["labels"]) == len(chart["papers"]) == len(chart["citations"])
        assert chart["labels"] == [2010, 2011, 2012, 2013]
        # Gap years are plotted as zero rather than compressed away.
        assert chart["papers"] == [1, 0, 0, 1]

    def test_gaps_are_reported_not_hidden(self, make_paper):
        papers = [make_paper(1, year=2010), make_paper(2, year=2013)]

        assert research_activity(papers)["has_gaps"] is True

    def test_the_peak_year_is_the_busiest_one(self, make_paper):
        papers = [
            make_paper(1, year=2019),
            make_paper(2, year=2020),
            make_paper(3, year=2020),
        ]

        assert research_activity(papers)["peak_year"] == {"year": 2020, "papers": 2}

    def test_undated_papers_are_counted_but_not_plotted(self, make_paper):
        papers = [make_paper(1, year=2020), make_paper(2, year=None)]

        activity = research_activity(papers)

        assert activity["total_papers"] == 2
        assert activity["undated_papers"] == 1
        assert sum(activity["chart"]["papers"]) == 1

    def test_an_empty_corpus_says_nothing_rather_than_guessing(self):
        activity = research_activity([])

        assert activity["total_papers"] == 0
        assert activity["total_citations"] == 0
        assert activity["mean_citations"] == 0.0
        assert activity["peak_year"] is None
        assert activity["first_year"] is None
        assert activity["chart"]["labels"] == []

    def test_activity_says_nothing_about_topics_or_scores(self, spread):
        # Section A is volume only; topic and quality questions belong to B and C.
        activity = research_activity(spread)

        assert "emerging" not in activity
        assert "mean_score" not in activity


class TestSectionBTrends:

    def test_carries_the_classification_through(self, make_paper, make_concept):
        papers = [
            make_paper(index, year=2020, concepts=[make_concept("Value at risk")])
            for index in range(6)
        ]

        result = research_trends(papers)

        assert "emerging" in result
        assert "thresholds" in result
        assert result["sufficient_data"] is False   # one year cannot be compared

    def test_the_four_categories_are_presented_for_the_ui(self, spread):
        categories = research_trends(spread)["categories"]

        assert [entry["key"] for entry in categories] == [
            "emerging",
            "growing",
            "declining",
            "persistent",
        ]

    def test_each_category_explains_its_own_rule(self, spread):
        for entry in research_trends(spread)["categories"]:
            assert entry["label"]
            assert entry["blurb"]
            assert isinstance(entry["rows"], list)

    def test_the_category_rows_are_the_classified_rows(self, spread):
        result = research_trends(spread)

        for entry in result["categories"]:
            assert entry["rows"] is result[entry["key"]]

    def test_raw_counts_are_offered_alongside_shares_clearly_labelled(
        self, make_paper, make_concept
    ):
        papers = [
            make_paper(index, year=2020, concepts=[make_concept("Value at risk")])
            for index in range(3)
        ]

        result = research_trends(papers)

        assert result["raw_top_topics"] == [{"name": "Value at risk", "count": 3}]
        # Normalized and raw views live under different keys, never mixed.
        assert result["raw_top_topics"] is not result["top_topics"]

    def test_an_empty_corpus_still_returns_the_full_structure(self):
        result = research_trends([])

        assert result["sufficient_data"] is False
        assert result["note"]
        assert len(result["categories"]) == 4
        assert result["raw_top_topics"] == []


class TestSectionCScores:

    def test_ranked_by_our_score_highest_first(self, make_paper):
        papers = [
            make_paper(1, research_score=40),
            make_paper(2, research_score=80),
            make_paper(3, research_score=60),
        ]

        result = paper_scores(papers)

        assert [row["research_score"] for row in result["papers"]] == [80, 60, 40]

    def test_the_displayed_score_is_the_audited_score(self, make_paper):
        papers = [make_paper(1, year=2020), make_paper(2, year=2015)]
        scoring.score_papers(papers)

        for row in paper_scores(papers)["papers"]:
            assert row["research_score"] == row["breakdown"]["total"]

    def test_every_row_shows_how_the_number_was_reached(self, spread):
        for row in paper_scores(spread)["papers"]:
            breakdown = row["breakdown"]

            assert set(breakdown) == {
                "citation",
                "relevance",
                "recency",
                "completeness",
                "total",
            }
            assert (
                breakdown["citation"]
                + breakdown["relevance"]
                + breakdown["recency"]
                + breakdown["completeness"]
                == breakdown["total"]
            )

    def test_summary_statistics(self, make_paper):
        papers = [
            make_paper(1, research_score=40),
            make_paper(2, research_score=80),
            make_paper(3, research_score=60),
        ]

        result = paper_scores(papers)

        assert result["count"] == 3
        assert result["mean_score"] == 60.0
        assert result["max_score"] == 80
        assert result["min_score"] == 40

    def test_the_distribution_has_one_bucket_per_ten_points(self, make_paper):
        papers = [
            make_paper(1, research_score=5),
            make_paper(2, research_score=15),
            make_paper(3, research_score=95),
        ]

        distribution = paper_scores(papers)["distribution"]

        assert [entry["label"] for entry in distribution] == [
            "0-9",
            "10-19",
            "20-29",
            "30-39",
            "40-49",
            "50-59",
            "60-69",
            "70-79",
            "80-89",
            "90-99",
        ]

    def test_every_paper_lands_in_exactly_one_bucket(self, make_paper):
        papers = [make_paper(index, research_score=index * 7) for index in range(14)]

        distribution = paper_scores(papers)["distribution"]

        assert sum(entry["count"] for entry in distribution) == len(papers)

    def test_a_perfect_score_lands_in_the_top_bucket(self, make_paper):
        distribution = paper_scores([make_paper(1, research_score=100)])["distribution"]

        assert distribution[-1]["count"] == 1

    def test_the_formula_ceilings_are_published_with_the_result(self, spread):
        formula = paper_scores(spread)["formula"]

        assert formula["citation"] == scoring.MAX_CITATION_POINTS
        assert formula["relevance"] == scoring.MAX_RELEVANCE_POINTS
        assert formula["recency"] == scoring.MAX_RECENCY_POINTS
        assert formula["completeness"] == scoring.MAX_COMPLETENESS_POINTS
        assert formula["max"] == scoring.MAX_SCORE

    def test_the_table_is_capped(self, make_paper):
        papers = [make_paper(index, research_score=index) for index in range(40)]

        result = paper_scores(papers)

        assert len(result["papers"]) == TOP_PAPERS
        assert result["count"] == 40      # the statistics still cover everything

    def test_the_cap_can_be_raised(self, make_paper):
        papers = [make_paper(index) for index in range(40)]

        assert len(paper_scores(papers, limit=3)["papers"]) == 3

    def test_ties_are_broken_deterministically(self, make_paper):
        papers = [
            make_paper(1, research_score=50, citation_count=10),
            make_paper(2, research_score=50, citation_count=99),
        ]

        first = [row["paper_id"] for row in paper_scores(papers)["papers"]]
        second = [row["paper_id"] for row in paper_scores(list(reversed(papers)))["papers"]]

        assert first == second

    def test_an_empty_corpus(self):
        result = paper_scores([])

        assert result["papers"] == []
        assert result["count"] == 0
        assert result["mean_score"] == 0.0
        assert result["distribution"] == []


class TestSectionDCitations:

    def test_ranked_by_citations_highest_first(self, spread):
        result = citation_impact(spread)

        assert [row["citation_count"] for row in result["papers"]] == [10, 8, 5, 4]

    def test_summary_statistics(self, spread):
        result = citation_impact(spread)

        assert result["count"] == 4
        assert result["total_citations"] == 27
        assert result["mean_citations"] == 6.8
        assert result["max_citations"] == 10

    def test_the_h_index_is_h_papers_with_h_citations(self, make_paper):
        # 10, 8, 5, 4, 3 -> four papers have at least four citations, the fifth
        # has three, so h is 4.
        papers = [
            make_paper(index, citation_count=count)
            for index, count in enumerate((10, 8, 5, 4, 3))
        ]

        assert citation_impact(papers)["h_index"] == 4

    def test_the_h_index_of_an_uncited_corpus_is_zero(self, make_paper):
        papers = [make_paper(index, citation_count=0) for index in range(5)]

        assert citation_impact(papers)["h_index"] == 0

    def test_the_median_of_an_odd_corpus(self, make_paper):
        papers = [
            make_paper(index, citation_count=count)
            for index, count in enumerate((10, 8, 5, 4, 3))
        ]

        assert citation_impact(papers)["median_citations"] == 5.0

    def test_the_median_of_an_even_corpus_is_the_midpoint(self, make_paper):
        papers = [
            make_paper(index, citation_count=count)
            for index, count in enumerate((10, 8, 5, 4))
        ]

        assert citation_impact(papers)["median_citations"] == 6.5

    def test_uncited_papers_are_reported(self, make_paper):
        papers = [
            make_paper(1, citation_count=10),
            make_paper(2, citation_count=0),
            make_paper(3, citation_count=0),
            make_paper(4, citation_count=0),
        ]

        result = citation_impact(papers)

        assert result["uncited"] == 3
        assert result["uncited_share"] == 0.75

    def test_negative_citation_counts_do_not_distort_the_totals(self, make_paper):
        papers = [make_paper(1, citation_count=10), make_paper(2, citation_count=-5)]

        result = citation_impact(papers)

        assert result["total_citations"] == 10
        assert result["max_citations"] == 10

    def test_the_citation_table_carries_no_score_breakdown(self, spread):
        # Section D is the field's verdict; our formula has no place in it.
        for row in citation_impact(spread)["papers"]:
            assert "breakdown" not in row

    def test_the_table_is_capped(self, make_paper):
        papers = [make_paper(index, citation_count=index) for index in range(40)]

        result = citation_impact(papers)

        assert len(result["papers"]) == TOP_PAPERS
        assert result["count"] == 40

    def test_an_empty_corpus(self):
        result = citation_impact([])

        assert result["papers"] == []
        assert result["count"] == 0
        assert result["h_index"] == 0
        assert result["median_citations"] == 0.0
        assert result["uncited_share"] == 0.0


class TestTheSectionsAreIndependent:

    def test_the_two_paper_tables_can_disagree(self, make_paper):
        # A recent, well-described paper our formula likes, and an old classic
        # the field has cited thousands of times.  Both are worth showing, and
        # neither ranking is allowed to overwrite the other.
        ours = make_paper(1, year=2024, citation_count=5, research_score=90)
        theirs = make_paper(2, year=1990, citation_count=5000, research_score=20)

        papers = [ours, theirs]

        assert paper_scores(papers)["papers"][0]["paper_id"] == ours["paper_id"]
        assert citation_impact(papers)["papers"][0]["paper_id"] == theirs["paper_id"]

    def test_no_section_mutates_the_papers_it_is_given(self, make_paper):
        papers = [make_paper(1, year=2020, research_score=42)]
        before = [dict(paper) for paper in papers]

        research_activity(papers)
        research_trends(papers)
        paper_scores(papers)
        citation_impact(papers)

        assert papers == before


class TestBuildDashboard:

    def test_returns_all_four_sections(self, spread):
        dashboard = build_dashboard(spread)

        assert set(dashboard) == {"empty", "activity", "trends", "scores", "citations"}
        assert dashboard["empty"] is False

    def test_an_empty_database_is_flagged_not_faked(self):
        dashboard = build_dashboard([])

        assert dashboard["empty"] is True
        assert dashboard["activity"]["total_papers"] == 0
        assert dashboard["scores"]["papers"] == []
        assert dashboard["citations"]["papers"] == []
        assert dashboard["trends"]["sufficient_data"] is False

    def test_none_is_treated_as_empty(self):
        assert build_dashboard(None)["empty"] is True

    def test_a_single_paper_does_not_break_anything(self, make_paper):
        dashboard = build_dashboard([make_paper(1)])

        assert dashboard["empty"] is False
        assert dashboard["activity"]["years_covered"] == 1
        assert dashboard["scores"]["count"] == 1
        assert dashboard["citations"]["count"] == 1

    def test_a_paper_with_almost_no_metadata_still_renders(self):
        dashboard = build_dashboard([{"paper_id": "https://openalex.org/W1"}])

        row = dashboard["scores"]["papers"][0]

        assert row["title"] == "(untitled)"
        assert row["citation_count"] == 0
        assert row["research_score"] == 0
        assert row["authors"] == []
        assert dashboard["activity"]["undated_papers"] == 1

    def test_papers_with_every_field_none_still_render(self):
        paper = {
            "paper_id": "https://openalex.org/W1",
            "title": None,
            "abstract": None,
            "year": None,
            "citation_count": None,
            "authors": None,
            "concepts": None,
            "doi": None,
            "url": None,
            "source": None,
            "research_score": None,
            "keyword": None,
        }

        dashboard = build_dashboard([paper])

        assert dashboard["empty"] is False
        assert dashboard["scores"]["papers"][0]["research_score"] == 0
        assert dashboard["citations"]["papers"][0]["citation_count"] == 0

    def test_the_url_falls_back_to_the_paper_id(self):
        dashboard = build_dashboard([{"paper_id": "https://openalex.org/W1", "url": None}])

        assert dashboard["scores"]["papers"][0]["url"] == "https://openalex.org/W1"

    def test_a_realistic_corpus_populates_every_section(self, make_paper, make_concept):
        papers = []

        for year in range(2010, 2025):
            for index in range(6):
                papers.append(
                    make_paper(
                        len(papers) + 1,
                        year=year,
                        citation_count=(year - 2009) * index,
                        research_score=30 + index * 10,
                        concepts=[make_concept("Value at risk" if index < 3 else "Stress testing")],
                    )
                )

        dashboard = build_dashboard(papers)

        assert dashboard["activity"]["total_papers"] == 90
        assert dashboard["activity"]["years_covered"] == 15
        assert dashboard["trends"]["sufficient_data"] is True
        assert len(dashboard["scores"]["papers"]) == TOP_PAPERS
        assert len(dashboard["citations"]["papers"]) == TOP_PAPERS
        assert dashboard["citations"]["h_index"] > 0
