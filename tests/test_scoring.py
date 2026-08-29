"""The research score (scoring.py).

There used to be two scorers that disagreed by a factor of two on the same
paper.  There is now one formula, and these tests pin down every property the
module docstring claims: bounded, deterministic, total, order-independent.
"""

import math
import random

import pytest

import scoring
from scoring import (
    MAX_CITATION_POINTS,
    MAX_COMPLETENESS_POINTS,
    MAX_RECENCY_POINTS,
    MAX_RELEVANCE_POINTS,
    MAX_SCORE,
    RECENCY_UNKNOWN_POINTS,
    RELEVANCE_NEUTRAL_POINTS,
    calculate_research_score,
    citation_points,
    completeness_points,
    corpus_reference_year,
    keyword_terms,
    recency_points,
    relevance_points,
    score_breakdown,
    score_papers,
)


class TestComponentCeilings:

    def test_components_sum_to_the_documented_maximum(self):
        assert (
            MAX_CITATION_POINTS
            + MAX_RELEVANCE_POINTS
            + MAX_RECENCY_POINTS
            + MAX_COMPLETENESS_POINTS
        ) == MAX_SCORE

    def test_the_maximum_is_one_hundred(self):
        assert MAX_SCORE == 100


class TestCitationPoints:

    def test_uncited_earns_nothing_but_is_not_penalised(self):
        assert citation_points(0) == 0

    def test_saturation_earns_full_credit(self):
        assert citation_points(scoring.CITATION_SATURATION) == MAX_CITATION_POINTS

    def test_beyond_saturation_is_capped(self):
        assert citation_points(10_000_000) == MAX_CITATION_POINTS

    @pytest.mark.parametrize("value", [None, -5, -10_000, "abc", "", [], {}, True, False])
    def test_hostile_input_reads_as_zero(self, value):
        assert citation_points(value) == 0

    def test_numeric_strings_are_accepted(self):
        assert citation_points("100") == citation_points(100)

    def test_monotonically_non_decreasing(self):
        values = [citation_points(count) for count in (0, 1, 10, 100, 1000, 5000, 10000)]

        assert values == sorted(values)

    def test_scale_is_logarithmic_not_linear(self):
        # The whole point of the log scale: the first hundred citations matter
        # far more than a hundred more on top of five thousand.
        early_gain = citation_points(100) - citation_points(0)
        late_gain = citation_points(5100) - citation_points(5000)

        assert early_gain > late_gain

    def test_matches_the_documented_formula(self):
        expected = round(
            MAX_CITATION_POINTS
            * math.log10(1 + 250)
            / math.log10(1 + scoring.CITATION_SATURATION)
        )

        assert citation_points(250) == expected

    def test_always_within_bounds(self):
        for count in (0, 1, 7, 42, 999, 12345, 10**9):
            assert 0 <= citation_points(count) <= MAX_CITATION_POINTS


class TestKeywordTerms:

    def test_stopwords_and_short_words_are_removed(self):
        assert keyword_terms("the study of AI in finance") == ["finance"]

    def test_empty_keyword(self):
        assert keyword_terms(None) == []
        assert keyword_terms("") == []
        assert keyword_terms("   ") == []

    def test_punctuation_is_ignored(self):
        assert keyword_terms("risk-management, portfolio!") == [
            "risk",
            "management",
            "portfolio",
        ]


class TestRelevancePoints:

    def test_no_keyword_is_neutral_not_zero(self):
        # Browsing the database must not make every paper look bad.
        paper = {"title": "Anything", "concepts": []}

        assert relevance_points(paper, None) == RELEVANCE_NEUTRAL_POINTS
        assert relevance_points(paper, "") == RELEVANCE_NEUTRAL_POINTS

    def test_full_title_coverage_earns_full_credit(self):
        paper = {"title": "Portfolio risk management in practice", "concepts": []}

        assert relevance_points(paper, "portfolio risk management") == MAX_RELEVANCE_POINTS

    def test_no_match_earns_nothing(self):
        paper = {"title": "Photosynthesis in mosses", "concepts": []}

        assert relevance_points(paper, "portfolio risk management") == 0

    def test_a_concept_match_is_worth_half_a_title_match(self, make_concept):
        title_match = relevance_points({"title": "Systemic risk", "concepts": []}, "systemic")
        concept_match = relevance_points(
            {"title": "Untitled", "concepts": [make_concept("Systemic risk")]},
            "systemic",
        )

        assert title_match == MAX_RELEVANCE_POINTS
        assert concept_match == MAX_RELEVANCE_POINTS // 2

    def test_missing_title_does_not_raise(self, make_concept):
        assert relevance_points({"title": None, "concepts": None}, "risk") == 0

    def test_concepts_as_a_json_string_still_count(self):
        paper = {"title": "Untitled", "concepts": '[{"name": "Systemic risk"}]'}

        assert relevance_points(paper, "systemic") == MAX_RELEVANCE_POINTS // 2

    def test_concepts_as_plain_strings_still_count(self):
        paper = {"title": "Untitled", "concepts": ["Systemic risk"]}

        assert relevance_points(paper, "systemic") == MAX_RELEVANCE_POINTS // 2

    def test_always_within_bounds(self, make_concept):
        paper = {
            "title": "Risk risk risk risk risk",
            "concepts": [make_concept("Risk")],
        }

        assert 0 <= relevance_points(paper, "risk risk risk") <= MAX_RELEVANCE_POINTS


class TestRecencyPoints:

    def test_current_papers_earn_full_credit(self):
        assert recency_points(2023, 2024) == MAX_RECENCY_POINTS
        assert recency_points(2022, 2024) == MAX_RECENCY_POINTS

    def test_two_decades_old_earns_nothing(self):
        assert recency_points(2004, 2024) == 0
        assert recency_points(1960, 2024) == 0

    def test_the_taper_is_monotonic(self):
        values = [recency_points(year, 2024) for year in range(2004, 2025)]

        assert values == sorted(values)

    def test_a_future_year_is_clamped_not_trusted(self):
        assert recency_points(2030, 2024) == MAX_RECENCY_POINTS

    def test_unknown_year_earns_the_documented_middle_value(self):
        assert recency_points(None, 2024) == RECENCY_UNKNOWN_POINTS
        assert recency_points("unknown", 2024) == RECENCY_UNKNOWN_POINTS

    def test_no_reference_year_earns_the_unknown_value(self):
        assert recency_points(2023, None) == RECENCY_UNKNOWN_POINTS

    def test_midpoint_matches_the_documented_formula(self):
        expected = round(MAX_RECENCY_POINTS * (20 - 11) / 18)

        assert recency_points(2013, 2024) == expected

    def test_always_within_bounds(self):
        for year in range(1900, 2101, 7):
            assert 0 <= recency_points(year, 2024) <= MAX_RECENCY_POINTS


class TestCompletenessPoints:

    def test_a_fully_described_paper_earns_everything(self, make_concept):
        paper = {
            "abstract": "Something substantial.",
            "authors": ["Ada"],
            "concepts": [make_concept("A"), make_concept("B")],
        }

        assert completeness_points(paper) == MAX_COMPLETENESS_POINTS

    def test_a_bare_stub_earns_nothing(self):
        assert completeness_points({"abstract": None, "authors": [], "concepts": []}) == 0

    def test_each_component_is_independent(self, make_concept):
        abstract_only = completeness_points({"abstract": "x", "authors": [], "concepts": []})
        authors_only = completeness_points({"abstract": "", "authors": ["Ada"], "concepts": []})
        concepts_only = completeness_points(
            {"abstract": "", "authors": [], "concepts": [make_concept("A"), make_concept("B")]}
        )

        assert abstract_only == 4
        assert authors_only == 3
        assert concepts_only == 3

    def test_one_concept_is_not_enough_for_the_concept_credit(self, make_concept):
        assert completeness_points({"concepts": [make_concept("A")]}) == 0

    def test_whitespace_abstract_does_not_count(self):
        assert completeness_points({"abstract": "   ", "authors": [], "concepts": []}) == 0

    def test_comma_joined_authors_from_the_database_still_count(self):
        assert completeness_points({"authors": "Ada, Alan", "concepts": []}) == 3

    def test_concepts_as_json_string_still_count(self):
        paper = {"concepts": '[{"name": "A"}, {"name": "B"}]'}

        assert completeness_points(paper) == 3

    def test_missing_keys_do_not_raise(self):
        assert completeness_points({}) == 0


class TestScoreBreakdown:

    def test_the_total_is_the_sum_of_its_parts(self, make_paper):
        breakdown = score_breakdown(make_paper(1), keyword="portfolio risk")

        assert breakdown["total"] == (
            breakdown["citation"]
            + breakdown["relevance"]
            + breakdown["recency"]
            + breakdown["completeness"]
        )

    def test_every_component_is_reported(self, make_paper):
        breakdown = score_breakdown(make_paper(1))

        assert set(breakdown) == {"citation", "relevance", "recency", "completeness", "total"}

    def test_the_keyword_falls_back_to_the_papers_own(self, make_paper):
        paper = make_paper(1, title="Portfolio risk", keyword="portfolio risk")

        assert score_breakdown(paper)["relevance"] == MAX_RELEVANCE_POINTS

    def test_a_non_dict_scores_zero_rather_than_raising(self):
        assert score_breakdown(None)["total"] >= 0
        assert score_breakdown("paper")["total"] >= 0

    def test_a_perfect_paper_reaches_the_maximum(self, make_concept):
        paper = {
            "title": "Portfolio risk management",
            "abstract": "Full abstract.",
            "authors": ["Ada", "Alan"],
            "concepts": [make_concept("Value at risk"), make_concept("Portfolio optimization")],
            "citation_count": 20000,
            "year": 2024,
        }

        assert calculate_research_score(paper, "portfolio risk management", 2024) == MAX_SCORE


class TestCalculateResearchScore:

    def test_returns_an_int_in_range(self, make_paper):
        score = calculate_research_score(make_paper(1), "risk", 2024)

        assert isinstance(score, int)
        assert 0 <= score <= MAX_SCORE

    def test_deterministic_across_repeated_calls(self, make_paper):
        paper = make_paper(1)

        scores = {calculate_research_score(paper, "portfolio risk", 2024) for _ in range(200)}

        assert len(scores) == 1

    def test_bounded_for_hostile_input(self):
        random.seed(20240829)

        hostile_values = [None, -1, 0, 7, "abc", True, False, 10**9, 3.9, [], {}]

        for _ in range(2000):
            paper = {
                "title": random.choice([None, "", "Risk", 12345]),
                "abstract": random.choice([None, "", "text"]),
                "year": random.choice([None, 1200, 1999, 2024, 2500, "soon", True]),
                "citation_count": random.choice(hostile_values),
                "authors": random.choice([None, [], ["Ada"], "Ada, Alan", 5]),
                "concepts": random.choice([None, [], "junk", 42, [{"name": "Risk"}]]),
                "keyword": random.choice([None, "", "risk"]),
            }

            score = calculate_research_score(paper, paper["keyword"], 2024)

            assert isinstance(score, int)
            assert 0 <= score <= MAX_SCORE

    def test_missing_fields_never_raise(self):
        assert 0 <= calculate_research_score({}) <= MAX_SCORE

    def test_more_citations_never_lowers_the_score(self, make_paper):
        low = calculate_research_score(make_paper(1, citation_count=10), "risk", 2024)
        high = calculate_research_score(make_paper(1, citation_count=10000), "risk", 2024)

        assert high >= low


class TestCorpusReferenceYear:

    def test_uses_the_newest_year_in_the_corpus(self, make_paper):
        papers = [make_paper(1, year=1999), make_paper(2, year=2018), make_paper(3, year=2007)]

        assert corpus_reference_year(papers) == 2018

    def test_ignores_missing_years(self, make_paper):
        papers = [make_paper(1, year=None), make_paper(2, year=2005)]

        assert corpus_reference_year(papers) == 2005

    def test_no_years_at_all(self, make_paper):
        assert corpus_reference_year([]) is None
        assert corpus_reference_year([make_paper(1, year=None)]) is None

    def test_reads_objects_as_well_as_dicts(self):
        class Row:
            year = 2011

        assert corpus_reference_year([Row()]) == 2011

    def test_does_not_read_the_clock(self, make_paper):
        # A historical corpus must score against its own newest year, otherwise
        # every paper in it would decay to zero recency as time passed and the
        # scores stored yesterday would not match the scores computed today.
        historical = [make_paper(1, year=1970), make_paper(2, year=1975)]

        assert corpus_reference_year(historical) == 1975


class TestScorePapers:

    def test_scores_are_written_onto_the_papers(self, make_paper):
        papers = [make_paper(1), make_paper(2)]

        score_papers(papers, keyword="risk")

        assert all(paper["research_score"] > 0 for paper in papers)

    def test_result_is_ranked_best_first(self, make_paper):
        papers = [
            make_paper(1, citation_count=0, year=1980, abstract=None, authors=[], concepts=[]),
            make_paper(2, citation_count=9000, year=2024),
        ]

        ranked = score_papers(papers, keyword="risk")

        assert ranked[0]["paper_id"] == "https://openalex.org/W2"
        scores = [paper["research_score"] for paper in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_order_independent(self, make_paper):
        papers = [make_paper(index, citation_count=index * 37, year=2000 + index) for index in range(12)]

        forward = {
            paper["paper_id"]: paper["research_score"]
            for paper in score_papers([dict(paper) for paper in papers], keyword="risk")
        }
        backward = {
            paper["paper_id"]: paper["research_score"]
            for paper in score_papers([dict(paper) for paper in reversed(papers)], keyword="risk")
        }

        assert forward == backward

    def test_ties_break_deterministically(self, make_paper):
        papers = [make_paper(index) for index in range(6)]

        first = [paper["paper_id"] for paper in score_papers([dict(p) for p in papers], "risk")]
        second = [
            paper["paper_id"]
            for paper in score_papers([dict(p) for p in reversed(papers)], "risk")
        ]

        assert first == second

    def test_empty_input(self):
        assert score_papers([]) == []
        assert score_papers(None) == []

    def test_non_dict_entries_are_dropped_not_fatal(self, make_paper):
        # Regression: junk in the list used to survive the scoring loop and then
        # crash the sort. A scored list feeds the templates and the database, so
        # anything that is not a paper is dropped here rather than downstream.
        result = score_papers([make_paper(1), "junk", None, 42, []])

        assert len(result) == 1
        assert result[0]["research_score"] > 0

    def test_the_papers_own_keyword_is_used_when_none_is_given(self, make_paper):
        papers = [make_paper(1, title="Portfolio risk", keyword="portfolio risk")]

        score_papers(papers)

        breakdown = score_breakdown(papers[0], "portfolio risk", 2023)
        assert papers[0]["research_score"] == breakdown["total"]
