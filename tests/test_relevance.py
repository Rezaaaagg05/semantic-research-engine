"""Deterministic relevance scoring, filtering, and false-negative evaluation."""

import pytest

import relevance
import scoring


QUERY = "portfolio risk management"


def paper(index, title=None, abstract=None, concepts=None, **overrides):
    value = {
        "paper_id": f"fixture-{index}",
        "title": title,
        "abstract": abstract,
        "concepts": concepts or [],
        "citation_count": 0,
        "year": 2020,
        "authors": [],
        "research_score": 0,
    }
    value.update(overrides)
    return value


@pytest.fixture
def relevance_cases():
    """Small labeled corpus covering direct, indirect, weak, and bad matches."""

    return [
        {
            "relevant": True,
            "kind": "clearly relevant title",
            "paper": paper(
                1,
                title=(
                    "Portfolio Risk Management and Its Contribution to Project "
                    "Portfolio Success"
                ),
            ),
        },
        {
            "relevant": True,
            "kind": "strong concepts",
            "paper": paper(
                2,
                title="Governance under financial uncertainty",
                concepts=["Portfolio management", "Risk management"],
            ),
        },
        {
            "relevant": True,
            "kind": "strong abstract",
            "paper": paper(
                3,
                title="Governance of diversified investments",
                abstract=(
                    "We study portfolio risk management for institutional asset "
                    "owners under market stress."
                ),
            ),
        },
        {
            "relevant": True,
            "kind": "indirect CVaR paper",
            "paper": paper(
                4,
                title="Conditional value-at-risk allocation under market uncertainty",
                abstract="Downside risk allocation for diversified assets.",
                concepts=["Portfolio optimization", "Risk measure"],
            ),
        },
        {
            "relevant": True,
            "kind": "weak but plausible",
            "paper": paper(
                5,
                title="Portfolio selection under uncertainty",
                abstract="The model improves risk controls for asset allocation.",
                concepts=["Investment management"],
            ),
        },
        {
            "relevant": False,
            "kind": "medical one-word collision",
            "paper": paper(
                6,
                title="Intermediate-risk surgery for aortic valve disease",
                abstract="Clinical outcomes after valve replacement.",
                concepts=["Medicine", "Intensive care medicine"],
            ),
        },
        {
            "relevant": False,
            "kind": "education one-word overlap",
            "paper": paper(
                7,
                title="Portfolio assessment for student learning",
                abstract="A classroom assessment method for trainee teachers.",
                concepts=["Education", "Pedagogy"],
            ),
        },
        {
            "relevant": False,
            "kind": "unrelated engineering",
            "paper": paper(
                8,
                title="Thermal degradation of concrete bridge decks",
                abstract="Laboratory measurements of reinforced concrete specimens.",
                concepts=["Civil engineering", "Concrete"],
            ),
        },
    ]


class TestSignals:

    def test_exact_title_match_is_high(self):
        result = relevance.calculate_relevance(
            paper(1, title="Portfolio risk management"), QUERY
        )

        assert result.score == 50
        assert result.level == "High"
        assert result.retain is True
        assert "exact normalized query phrase in title" in result.reasons

    def test_strong_concept_match_is_relevant(self):
        result = relevance.calculate_relevance(
            paper(1, concepts=["Portfolio management", "Risk management"]), QUERY
        )

        assert result.score == relevance.CONCEPT_TERM_WEIGHT
        assert result.retain is True
        assert any(reason.startswith("concepts match 3/3") for reason in result.reasons)

    def test_strong_abstract_match_is_relevant(self):
        result = relevance.calculate_relevance(
            paper(1, abstract="A portfolio risk management framework."), QUERY
        )

        assert result.score == 30
        assert result.level == "Medium"
        assert result.retain is True

    def test_indirect_genuinely_relevant_paper_is_retained(self, relevance_cases):
        indirect = next(case for case in relevance_cases if case["kind"] == "indirect CVaR paper")

        result = relevance.calculate_relevance(indirect["paper"], QUERY)

        assert result.score >= relevance.config.RELEVANCE_MIN_SCORE
        assert result.retain is True
        assert "portfolio" in result.matched_terms
        assert "risk" in result.matched_terms

    def test_generic_keyword_overlap_is_not_high(self):
        result = relevance.calculate_relevance(
            paper(1, abstract="Risk is discussed as a general issue."), QUERY
        )

        assert result.level == "Low"
        assert result.retain is False

    def test_clearly_unrelated_paper_is_low(self):
        result = relevance.calculate_relevance(
            paper(1, title="Cell signaling in kidney disease", concepts=["Medicine"]),
            QUERY,
        )

        assert result.score == 0
        assert result.level == "Low"
        assert result.retain is False

    def test_broad_only_overlap_is_capped_at_low_confidence(self):
        result = relevance.calculate_relevance(
            paper(
                1,
                title="Risk management in intensive care units",
                abstract="Clinical risk management improves patient safety.",
                concepts=["Medicine", "Intensive care medicine"],
            ),
            QUERY,
        )

        assert result.score == relevance.config.RELEVANCE_MEDIUM_SCORE - 1
        assert result.level == "Low"
        assert result.retain is True
        assert any("capped at Low" in reason for reason in result.reasons)


class TestInputSafety:

    @pytest.mark.parametrize(
        "overrides",
        [
            {"abstract": None},
            {"concepts": None},
            {"title": None},
        ],
    )
    def test_missing_fields_do_not_crash(self, overrides):
        item = paper(1, title="Portfolio risk management")
        item.update(overrides)

        result = relevance.calculate_relevance(
            item, QUERY
        )

        assert 0 <= result.score <= 100

    def test_empty_query_is_safe_and_does_not_filter(self):
        result = relevance.calculate_relevance(paper(1), "")

        assert result.score == 0
        assert result.retain is True
        assert result.reasons

    def test_multiple_word_query_tokenization(self):
        assert relevance.query_terms("portfolio risk management") == [
            "portfolio",
            "risk",
            "management",
        ]

    def test_simple_plural_forms_match_query_terms(self):
        result = relevance.calculate_relevance(
            paper(
                1,
                title="Risk management for credit portfolios",
                concepts=["Financial risk"],
            ),
            QUERY,
        )

        assert set(result.matched_terms) == {"portfolio", "risk", "management"}
        assert result.retain is True

    def test_case_differences_do_not_change_the_score(self):
        item = paper(1, title="PORTFOLIO RISK MANAGEMENT")

        assert relevance.calculate_relevance(item, QUERY).score == relevance.calculate_relevance(
            item, QUERY.upper()
        ).score

    def test_punctuation_differences_do_not_change_the_score(self):
        punctuated = relevance.calculate_relevance(
            paper(1, title="Portfolio-risk: management"), QUERY
        )
        plain = relevance.calculate_relevance(
            paper(2, title="Portfolio risk management"), QUERY
        )

        assert punctuated.score == plain.score
        assert punctuated.level == plain.level


class TestFilteringAndEvaluation:

    def test_annotations_are_explainable(self):
        item = paper(1, title="Portfolio risk management")
        annotated, analysis = relevance.annotate_paper(item, QUERY)

        assert annotated["relevance_score"] == analysis.score
        assert annotated["relevance_level"] == analysis.level
        assert annotated["relevance_reasons"] == list(analysis.reasons)

    def test_false_negative_evaluation_retains_every_relevant_fixture(
        self, relevance_cases
    ):
        metrics = relevance.evaluate_labeled_papers(relevance_cases, QUERY)

        assert metrics == {
            "true_positives": 5,
            "false_positives": 0,
            "true_negatives": 3,
            "false_negatives": 0,
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
        }

    def test_research_score_is_independent_from_relevance_score(self):
        base = paper(
            1,
            title="Portfolio risk management",
            abstract="Portfolio risk management for investment funds.",
            concepts=["Portfolio optimization", "Risk management"],
            citation_count=25,
            year=2023,
            authors=["Ada Lovelace"],
        )

        low_relevance_field = dict(base, relevance_score=0)
        high_relevance_field = dict(base, relevance_score=100)

        assert scoring.calculate_research_score(
            low_relevance_field, keyword=QUERY, reference_year=2024
        ) == scoring.calculate_research_score(
            high_relevance_field, keyword=QUERY, reference_year=2024
        )

        high_citations = dict(base, citation_count=10000, research_score=99)
        assert relevance.calculate_relevance(base, QUERY).score == relevance.calculate_relevance(
            high_citations, QUERY
        ).score
