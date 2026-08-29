"""The concept / topic pipeline (concepts.py).

Concepts arrive in at least four shapes across providers and database
generations: OpenAlex dicts, Semantic Scholar field-of-study dicts, bare
strings, and JSON text read back out of the ``concepts`` column.  All four have
to converge on one canonical shape without ever raising.
"""

import json

import pytest

import config
from concepts import (
    ALWAYS_DROP,
    GENERIC_DISCIPLINES,
    HOMONYM_NOISE,
    concept_names,
    concepts_from_json,
    concepts_to_json,
    count_topics,
    filter_concepts,
    normalize_concept,
    normalize_concepts,
    normalize_name,
    paper_topics,
)


class TestNormalizeName:

    def test_collapses_internal_whitespace(self):
        assert normalize_name("Value   at\n risk") == "Value at risk"

    def test_empty_becomes_none(self):
        assert normalize_name("") is None
        assert normalize_name("   ") is None
        assert normalize_name(None) is None


class TestNormalizeConcept:

    def test_openalex_shape(self):
        result = normalize_concept(
            {
                "display_name": "Value at risk",
                "id": "https://openalex.org/C1",
                "level": 3,
                "score": 0.82,
            }
        )

        assert result == {
            "name": "Value at risk",
            "key": "value at risk",
            "id": "https://openalex.org/C1",
            "level": 3,
            "score": 0.82,
        }

    def test_semantic_scholar_shape(self):
        result = normalize_concept({"category": "Economics", "source": "s2-fos-model"})

        assert result["name"] == "Economics"
        assert result["level"] is None
        assert result["score"] is None

    def test_plain_string(self):
        result = normalize_concept("Portfolio optimization")

        assert result["name"] == "Portfolio optimization"
        assert result["key"] == "portfolio optimization"
        assert result["id"] is None

    def test_unknown_shapes_return_none_rather_than_raising(self):
        for value in (None, 42, [], object(), {"unexpected": "keys"}):
            assert normalize_concept(value) is None

    def test_score_is_clamped_to_zero_one(self):
        assert normalize_concept({"name": "X", "score": 5})["score"] == 1.0
        assert normalize_concept({"name": "X", "score": -3})["score"] == 0.0

    def test_nonsense_level_and_score_become_none(self):
        result = normalize_concept({"name": "X", "level": "deep", "score": "high"})

        assert result["level"] is None
        assert result["score"] is None

    def test_nan_score_becomes_none(self):
        assert normalize_concept({"name": "X", "score": float("nan")})["score"] is None


class TestNormalizeConcepts:

    def test_case_insensitive_deduplication(self):
        result = normalize_concepts(["Value at risk", "VALUE AT RISK", "value at risk"])

        assert len(result) == 1

    def test_the_more_informative_duplicate_wins(self):
        result = normalize_concepts(
            [
                {"name": "Risk"},
                {"name": "Risk", "id": "C9", "level": 2, "score": 0.9},
            ]
        )

        assert len(result) == 1
        assert result[0]["id"] == "C9"
        assert result[0]["score"] == 0.9

    def test_low_scoring_concepts_are_dropped(self):
        result = normalize_concepts(
            [
                {"name": "Strong", "score": 0.9},
                {"name": "Weak", "score": 0.01},
            ]
        )

        assert concept_names(result) == ["Strong"]

    def test_unscored_concepts_survive_the_threshold(self):
        # We cannot judge a concept the provider gave no score for, so it is
        # kept rather than silently discarded (this is the Semantic Scholar case).
        result = normalize_concepts([{"name": "Economics"}])

        assert concept_names(result) == ["Economics"]

    def test_ordering_is_by_descending_score_then_name(self):
        result = normalize_concepts(
            [
                {"name": "Beta", "score": 0.5},
                {"name": "Alpha", "score": 0.9},
                {"name": "Gamma", "score": 0.5},
            ]
        )

        assert concept_names(result) == ["Alpha", "Beta", "Gamma"]

    def test_per_paper_cap_is_enforced(self):
        result = normalize_concepts(
            [{"name": f"Topic {index}", "score": 0.9} for index in range(50)]
        )

        assert len(result) == config.CONCEPT_MAX_PER_PAPER

    def test_levels_beyond_the_hierarchy_are_dropped(self):
        result = normalize_concepts(
            [
                {"name": "Deep", "level": config.CONCEPT_MAX_LEVEL + 1, "score": 0.9},
                {"name": "Fine", "level": config.CONCEPT_MAX_LEVEL, "score": 0.9},
            ]
        )

        assert concept_names(result) == ["Fine"]

    def test_homonym_noise_is_always_dropped(self):
        result = normalize_concepts(
            [
                {"name": "Stock (firearms)", "score": 0.99},
                {"name": "Value at risk", "score": 0.5},
            ]
        )

        assert concept_names(result) == ["Value at risk"]

    def test_a_single_dict_is_accepted(self):
        assert concept_names(normalize_concepts({"name": "Risk"})) == ["Risk"]

    def test_a_single_string_is_accepted(self):
        assert concept_names(normalize_concepts("Risk")) == ["Risk"]

    @pytest.mark.parametrize(
        "value",
        [None, [], "", "   ", 12345, 0, True, False, 3.14, object()],
    )
    def test_scalars_and_junk_never_raise(self, value):
        # Regression: a stray number in the concepts column used to raise
        # TypeError: 'int' object is not iterable and take the request down.
        assert normalize_concepts(value) == []

    def test_a_list_containing_junk_keeps_the_good_entries(self):
        result = normalize_concepts([{"name": "Risk", "score": 0.8}, None, 42, "", []])

        assert concept_names(result) == ["Risk"]


class TestJsonRoundTrip:

    def test_round_trip_preserves_concepts(self):
        original = normalize_concepts([{"name": "Risk", "id": "C1", "level": 2, "score": 0.7}])

        restored = concepts_from_json(concepts_to_json(original))

        assert restored == original

    def test_reads_the_legacy_list_of_strings_format(self):
        restored = concepts_from_json(json.dumps(["Value at risk", "Portfolio optimization"]))

        assert concept_names(restored) == ["Portfolio optimization", "Value at risk"]

    def test_reads_the_current_list_of_dicts_format(self):
        stored = json.dumps([{"name": "Risk", "key": "risk", "id": None, "level": 2, "score": 0.7}])

        assert concept_names(concepts_from_json(stored)) == ["Risk"]

    @pytest.mark.parametrize("value", [None, "", "not json at all", "{", "[", 12345, b"bytes"])
    def test_unreadable_columns_yield_an_empty_list(self, value):
        assert concepts_from_json(value) == []

    def test_serializing_nothing_is_still_valid_json(self):
        assert json.loads(concepts_to_json(None)) == []
        assert json.loads(concepts_to_json([])) == []

    def test_non_ascii_names_survive(self):
        stored = concepts_to_json(normalize_concepts(["Économie", "Wärmeleitung"]))

        # Written unescaped, so the column stays readable to a human with sqlite3.
        assert "Économie" in stored
        # Order is by relevance then name, not input order, so compare as a set.
        assert set(concept_names(concepts_from_json(stored))) == {
            "Économie",
            "Wärmeleitung",
        }


class TestFilterConcepts:

    def test_generic_disciplines_are_dropped_for_trends(self, make_concept):
        result = filter_concepts(
            [make_concept("Economics"), make_concept("Value at risk")],
            drop_generic=True,
        )

        assert concept_names(result) == ["Value at risk"]

    def test_generic_disciplines_are_kept_when_not_dropping(self, make_concept):
        result = filter_concepts(
            [make_concept("Economics"), make_concept("Value at risk")],
            drop_generic=False,
        )

        assert concept_names(result) == ["Economics", "Value at risk"]

    def test_a_paper_is_never_left_with_no_topics(self, make_concept):
        # Every concept this paper has is generic.  A broad topic beats none.
        result = filter_concepts(
            [make_concept("Economics"), make_concept("Finance")],
            drop_generic=True,
        )

        assert concept_names(result) == ["Economics", "Finance"]

    def test_homonym_noise_is_dropped_even_by_the_never_empty_path(self, make_concept):
        result = filter_concepts([make_concept("Stock (firearms)")], drop_generic=True)

        assert result == []

    def test_min_level_filter(self, make_concept):
        result = filter_concepts(
            [make_concept("Broad", level=0), make_concept("Narrow", level=4)],
            drop_generic=False,
            min_level=2,
        )

        assert concept_names(result) == ["Narrow"]

    def test_accepts_raw_strings_too(self):
        assert concept_names(filter_concepts(["Value at risk"])) == ["Value at risk"]

    def test_empty_input(self):
        assert filter_concepts(None) == []
        assert filter_concepts([]) == []


class TestPaperTopics:

    def test_reads_a_normalized_paper(self, make_paper):
        assert paper_topics(make_paper(1)) == ["Value at risk"]

    def test_reads_a_json_column(self, make_paper):
        paper = make_paper(1, concepts=json.dumps(["Value at risk", "Systemic risk"]))

        assert sorted(paper_topics(paper)) == ["Systemic risk", "Value at risk"]

    def test_reads_an_object_with_attributes(self):
        class Row:
            concepts = json.dumps([{"name": "Risk", "score": 0.9}])

        assert paper_topics(Row()) == ["Risk"]

    def test_deduplicates_within_one_paper(self, make_paper):
        paper = make_paper(1, concepts=["Risk", "RISK", "risk"])

        assert paper_topics(paper) == ["Risk"]

    @pytest.mark.parametrize("value", [None, [], "", 12345, "garbage"])
    def test_missing_or_corrupt_concepts_give_no_topics(self, make_paper, value):
        assert paper_topics(make_paper(1, concepts=value)) == []


class TestCountTopics:

    def test_counts_papers_not_mentions(self, make_paper, make_concept):
        # One paper listing "Risk" three times is still one paper about risk.
        papers = [
            make_paper(1, concepts=[make_concept("Risk"), make_concept("RISK"), make_concept("risk")]),
            make_paper(2, concepts=[make_concept("Risk")]),
        ]

        assert count_topics(papers)["Risk"] == 2

    def test_generic_disciplines_are_excluded_by_default(self, make_paper, make_concept):
        papers = [make_paper(1, concepts=[make_concept("Economics"), make_concept("Risk")])]

        counts = count_topics(papers)

        assert "Economics" not in counts
        assert counts["Risk"] == 1

    def test_empty_corpus(self):
        assert count_topics([]) == {}
        assert count_topics(None) == {}


class TestNoiseLists:

    def test_always_drop_covers_the_homonyms(self):
        assert ALWAYS_DROP == HOMONYM_NOISE

    def test_noise_lists_are_casefolded(self):
        # Lookups are done on casefolded keys; an entry with capitals would
        # silently never match.
        assert all(name == name.casefold() for name in HOMONYM_NOISE)
        assert all(name == name.casefold() for name in GENERIC_DISCIPLINES)

    def test_the_two_lists_serve_different_purposes(self):
        # Homonyms are wrong for this corpus; generic disciplines are merely
        # uninformative.  Conflating them would drop real topics.
        assert not HOMONYM_NOISE & GENERIC_DISCIPLINES
