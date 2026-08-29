"""The canonical paper structure (models.py).

Everything downstream -- database, scoring, trends, templates -- assumes every
paper carries every canonical field.  These tests hold that contract in place.
"""

import pytest

from models import (
    MAX_YEAR,
    MIN_YEAR,
    PAPER_FIELDS,
    clean_text,
    coerce_int,
    coerce_year,
    is_normalized,
    normalize_authors,
    normalize_doi,
    normalize_paper,
    normalize_papers,
)


class TestCleanText:

    def test_strips_whitespace(self):
        assert clean_text("  hello  ") == "hello"

    def test_empty_becomes_none(self):
        assert clean_text("") is None
        assert clean_text("   ") is None
        assert clean_text(None) is None

    def test_non_string_is_coerced(self):
        assert clean_text(42) == "42"


class TestCoerceInt:

    @pytest.mark.parametrize(
        "value,expected",
        [
            (5, 5),
            ("7", 7),
            ("  9 ", 9),
            (3.7, 3),
            (None, 0),
            ("", 0),
            ("abc", 0),
            ([], 0),
        ],
    )
    def test_values(self, value, expected):
        assert coerce_int(value) == expected

    def test_bool_is_not_an_integer_here(self):
        # A True in a citation-count column is corruption, not the number 1.
        assert coerce_int(True) == 0
        assert coerce_int(False) == 0

    def test_custom_default(self):
        assert coerce_int(None, default=None) is None


class TestCoerceYear:

    @pytest.mark.parametrize("value", [1500, 2023, 2100, "1999"])
    def test_plausible_years_pass(self, value):
        assert coerce_year(value) == int(value)

    @pytest.mark.parametrize("value", [0, 12, 1499, 2101, 99999, -2020, None, "soon"])
    def test_implausible_years_become_none(self, value):
        assert coerce_year(value) is None

    def test_bounds_are_inclusive(self):
        assert coerce_year(MIN_YEAR) == MIN_YEAR
        assert coerce_year(MAX_YEAR) == MAX_YEAR


class TestNormalizeAuthors:

    def test_list_of_strings(self):
        assert normalize_authors(["Ada", "Alan"]) == ["Ada", "Alan"]

    def test_list_of_dicts_both_key_styles(self):
        authors = normalize_authors([{"name": "Ada"}, {"display_name": "Alan"}])

        assert authors == ["Ada", "Alan"]

    def test_order_is_preserved(self):
        assert normalize_authors(["Zoe", "Ada", "Mo"]) == ["Zoe", "Ada", "Mo"]

    def test_case_insensitive_deduplication(self):
        assert normalize_authors(["Ada Lovelace", "ADA LOVELACE", "Ada"]) == [
            "Ada Lovelace",
            "Ada",
        ]

    def test_blanks_are_dropped(self):
        assert normalize_authors(["Ada", "", None, "   ", {"name": None}]) == ["Ada"]

    def test_bare_string_is_accepted(self):
        assert normalize_authors("Ada") == ["Ada"]

    def test_empty_input(self):
        assert normalize_authors(None) == []
        assert normalize_authors([]) == []


class TestNormalizeDoi:

    @pytest.mark.parametrize(
        "value",
        [
            "10.1234/abcd",
            "https://doi.org/10.1234/abcd",
            "http://doi.org/10.1234/abcd",
            "doi.org/10.1234/abcd",
            "doi:10.1234/abcd",
            "  https://doi.org/10.1234/abcd  ",
        ],
    )
    def test_prefixes_are_stripped(self, value):
        assert normalize_doi(value) == "10.1234/abcd"

    def test_missing_doi(self):
        assert normalize_doi(None) is None
        assert normalize_doi("") is None
        assert normalize_doi("https://doi.org/") is None


class TestNormalizePaper:

    def test_every_canonical_field_is_present(self):
        paper = normalize_paper({"id": "W1"}, source="openalex")

        assert is_normalized(paper)
        assert set(paper) == set(PAPER_FIELDS)

    def test_all_three_id_spellings_are_accepted(self):
        for key in ("paper_id", "paperId", "id"):
            paper = normalize_paper({key: "W1"}, source="openalex")

            assert paper is not None
            assert paper["paper_id"] == "W1"

    def test_a_paper_without_an_id_is_rejected(self):
        # A paper we cannot address is a paper we cannot de-duplicate or store.
        assert normalize_paper({"title": "Untitled"}, source="openalex") is None
        assert normalize_paper({"id": "   "}, source="openalex") is None

    def test_non_dict_input_is_rejected(self):
        for value in (None, "W1", 42, ["W1"]):
            assert normalize_paper(value, source="openalex") is None

    def test_citation_count_is_never_none_and_never_negative(self):
        assert normalize_paper({"id": "W1"}, "openalex")["citation_count"] == 0
        assert normalize_paper({"id": "W1", "citation_count": None}, "openalex")["citation_count"] == 0
        assert normalize_paper({"id": "W1", "citation_count": -7}, "openalex")["citation_count"] == 0
        assert normalize_paper({"id": "W1", "citationCount": 12}, "openalex")["citation_count"] == 12

    def test_year_is_sanity_checked(self):
        assert normalize_paper({"id": "W1", "year": 2023}, "openalex")["year"] == 2023
        assert normalize_paper({"id": "W1", "year": 12}, "openalex")["year"] is None
        assert normalize_paper({"id": "W1", "publication_year": 1999}, "openalex")["year"] == 1999

    def test_source_is_recorded_and_never_empty(self):
        assert normalize_paper({"id": "W1"}, source="openalex")["source"] == "openalex"
        assert normalize_paper({"id": "W1"}, source=None)["source"] == "unknown"

    def test_keyword_comes_from_the_record_first_then_the_argument(self):
        with_own = normalize_paper({"id": "W1", "keyword": "own"}, "openalex", keyword="arg")
        without = normalize_paper({"id": "W2"}, "openalex", keyword="arg")

        assert with_own["keyword"] == "own"
        assert without["keyword"] == "arg"

    def test_concepts_are_normalized_not_passed_through(self):
        paper = normalize_paper(
            {"id": "W1", "concepts": ["Value at risk", "Value At Risk"]},
            source="openalex",
        )

        assert len(paper["concepts"]) == 1
        assert paper["concepts"][0]["name"] == "Value at risk"
        assert paper["concepts"][0]["key"] == "value at risk"

    def test_doi_is_stored_bare(self):
        paper = normalize_paper(
            {"id": "W1", "doi": "https://doi.org/10.1/x"},
            source="openalex",
        )

        assert paper["doi"] == "10.1/x"


class TestNormalizePapers:

    def test_provider_order_is_preserved(self):
        papers = normalize_papers(
            [{"id": "W3"}, {"id": "W1"}, {"id": "W2"}],
            source="openalex",
        )

        assert [paper["paper_id"] for paper in papers] == ["W3", "W1", "W2"]

    def test_duplicates_are_merged_not_repeated(self):
        papers = normalize_papers(
            [
                {"id": "W1", "title": "First", "citation_count": 5},
                {"id": "W1", "abstract": "Recovered abstract", "citation_count": 9},
            ],
            source="openalex",
        )

        assert len(papers) == 1
        assert papers[0]["title"] == "First"
        assert papers[0]["abstract"] == "Recovered abstract"
        # The higher citation count wins: counts only ever grow.
        assert papers[0]["citation_count"] == 9

    def test_unusable_records_are_dropped_silently(self):
        papers = normalize_papers(
            [{"id": "W1"}, None, {"no_id": True}, "junk", {"id": ""}],
            source="openalex",
        )

        assert [paper["paper_id"] for paper in papers] == ["W1"]

    def test_empty_input(self):
        assert normalize_papers(None, source="openalex") == []
        assert normalize_papers([], source="openalex") == []

    def test_every_result_is_normalized(self):
        papers = normalize_papers(
            [{"id": f"W{index}"} for index in range(5)],
            source="openalex",
            keyword="risk",
        )

        assert all(is_normalized(paper) for paper in papers)
        assert all(paper["keyword"] == "risk" for paper in papers)


class TestIsNormalized:

    def test_rejects_partial_records(self):
        assert not is_normalized({"paper_id": "W1"})
        assert not is_normalized(None)
        assert not is_normalized("W1")

    def test_accepts_a_full_record(self):
        assert is_normalized(dict.fromkeys(PAPER_FIELDS))
