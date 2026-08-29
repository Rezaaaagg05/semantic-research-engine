"""The OpenAlex provider (providers/openalex.py) -- the default data source.

Every test injects a fake session, so nothing here contacts OpenAlex.  The
fixtures use OpenAlex's real wire format (inverted-index abstracts, authorship
objects, scored concepts) so the translation is tested against the shape the API
actually sends.
"""

import pytest

import config
from providers.errors import ProviderRateLimited, ProviderUnavailable
from providers.openalex import (
    SELECT_FIELDS,
    OpenAlexProvider,
    extract_authors,
    extract_concepts,
    reconstruct_abstract,
    to_paper,
)


def provider(session=None, sleep=None):
    return OpenAlexProvider(session=session, sleep=sleep or (lambda seconds: None))


def page(works):
    return {"results": list(works)}


class TestReconstructAbstract:

    def test_words_are_placed_in_position_order(self):
        inverted = {"risk": [1], "Measuring": [0], "exposure": [2]}

        assert reconstruct_abstract(inverted) == "Measuring risk exposure"

    def test_repeated_words_appear_at_every_position(self):
        inverted = {"risk": [0, 2], "and": [1]}

        assert reconstruct_abstract(inverted) == "risk and risk"

    @pytest.mark.parametrize("value", [None, {}, [], "text", 42])
    def test_missing_or_wrong_shaped_index_gives_none(self, value):
        assert reconstruct_abstract(value) is None

    def test_malformed_positions_are_skipped_not_fatal(self):
        inverted = {"good": [0], "bad": "not a list", "worse": [None, "x", True], "end": [1]}

        assert reconstruct_abstract(inverted) == "good end"

    def test_an_index_with_no_usable_positions_gives_none(self):
        assert reconstruct_abstract({"word": [True, None, "x"]}) is None


class TestExtractAuthors:

    def test_authorship_order_is_preserved(self, make_openalex_work):
        work = make_openalex_work(authors=("Zoe", "Ada", "Mo"))

        assert extract_authors(work) == ["Zoe", "Ada", "Mo"]

    def test_malformed_authorships_are_skipped(self):
        work = {
            "authorships": [
                "not a dict",
                {},
                {"author": None},
                {"author": "string"},
                {"author": {"display_name": None}},
                {"author": {"display_name": "Ada"}},
            ]
        }

        assert extract_authors(work) == ["Ada"]

    def test_no_authorships(self):
        assert extract_authors({}) == []
        assert extract_authors({"authorships": None}) == []


class TestExtractConcepts:

    def test_level_and_score_are_preserved_for_the_concept_pipeline(self, make_openalex_work):
        work = make_openalex_work(concepts=("Value at risk",))

        concepts = extract_concepts(work)

        assert concepts[0]["name"] == "Value at risk"
        assert concepts[0]["level"] == 2
        assert concepts[0]["score"] == 0.75
        assert concepts[0]["id"].startswith("https://openalex.org/C")

    def test_entries_without_a_display_name_are_skipped(self):
        work = {"concepts": [{"id": "C1", "level": 1}, {"display_name": "Risk"}]}

        assert [concept["name"] for concept in extract_concepts(work)] == ["Risk"]

    def test_non_dict_entries_are_skipped(self):
        work = {"concepts": ["Risk", None, 42, {"display_name": "Real"}]}

        assert [concept["name"] for concept in extract_concepts(work)] == ["Real"]

    def test_no_concepts(self):
        assert extract_concepts({}) == []
        assert extract_concepts({"concepts": None}) == []


class TestToPaper:

    def test_full_translation(self, make_openalex_work):
        work = make_openalex_work(index=7, title="Risk", year=2019, citations=33)

        paper = to_paper(work)

        assert paper["paper_id"] == "https://openalex.org/W7"
        assert paper["title"] == "Risk"
        assert paper["year"] == 2019
        assert paper["citation_count"] == 33
        assert paper["url"] == "https://openalex.org/W7"
        assert paper["abstract"] == "Measuring portfolio risk"
        assert paper["doi"] == "https://doi.org/10.1234/example"

    def test_a_work_without_an_id_is_unusable(self):
        assert to_paper({"title": "No id"}) is None

    @pytest.mark.parametrize("value", [None, "work", 42, []])
    def test_non_dict_input(self, value):
        assert to_paper(value) is None

    def test_missing_citation_count_becomes_zero(self):
        assert to_paper({"id": "W1"})["citation_count"] == 0
        assert to_paper({"id": "W1", "cited_by_count": None})["citation_count"] == 0


class TestConfiguration:

    def test_openalex_needs_no_credentials(self):
        assert provider().is_configured() is True

    def test_the_hint_explains_the_optional_polite_pool(self):
        hint = provider().configuration_hint()

        assert "no API key" in hint
        assert "OPENALEX_MAILTO" in hint

    def test_registry_identity(self):
        assert OpenAlexProvider.name == "openalex"
        assert OpenAlexProvider.label == "OpenAlex"

    def test_mailto_is_sent_only_when_configured(
        self, fake_session, fake_response, monkeypatch
    ):
        monkeypatch.setattr(config, "OPENALEX_MAILTO", "")
        session = fake_session(fake_response(200, page([])))
        provider(session).fetch_raw("risk")
        assert "mailto" not in session.calls[0]["params"]

        monkeypatch.setattr(config, "OPENALEX_MAILTO", "me@example.test")
        session = fake_session(fake_response(200, page([])))
        provider(session).fetch_raw("risk")
        assert session.calls[0]["params"]["mailto"] == "me@example.test"

    def test_only_the_fields_we_use_are_requested(self, fake_session, fake_response):
        session = fake_session(fake_response(200, page([])))

        provider(session).fetch_raw("risk")

        assert session.calls[0]["params"]["select"] == SELECT_FIELDS
        assert "abstract_inverted_index" in SELECT_FIELDS


class TestSearch:

    def test_returns_canonical_papers(self, fake_session, fake_response, make_openalex_work):
        session = fake_session(fake_response(200, page([make_openalex_work(1)])))

        papers = provider(session).search("portfolio risk", pages=1, per_page=100)

        assert len(papers) == 1
        paper = papers[0]
        assert paper["source"] == "openalex"
        assert paper["keyword"] == "portfolio risk"
        assert paper["doi"] == "10.1234/example"          # bare, prefix stripped
        assert paper["research_score"] == 0                # provider does not score
        assert isinstance(paper["concepts"][0], dict)

    def test_an_empty_keyword_makes_no_request(self, fake_session, fake_response):
        session = fake_session(fake_response(200, page([])))

        assert provider(session).search("") == []
        assert provider(session).search("   ") == []
        assert session.call_count == 0

    def test_no_results_is_not_an_error(self, fake_session, fake_response):
        session = fake_session(fake_response(200, page([])))

        assert provider(session).search("nothing at all") == []

    def test_duplicates_across_pages_are_merged(
        self, fake_session, fake_response, make_openalex_work
    ):
        work = make_openalex_work(1)
        session = fake_session(
            [
                fake_response(200, page([work, make_openalex_work(2)])),
                fake_response(200, page([work])),
            ]
        )

        papers = provider(session).search("risk", pages=2, per_page=2)

        assert [paper["paper_id"] for paper in papers] == [
            "https://openalex.org/W1",
            "https://openalex.org/W2",
        ]


class TestPaging:

    def test_stops_on_a_short_page(self, fake_session, fake_response, make_openalex_work):
        session = fake_session(
            [
                fake_response(200, page([make_openalex_work(1), make_openalex_work(2)])),
                fake_response(200, page([make_openalex_work(3)])),
            ]
        )

        papers = provider(session).fetch_raw("risk", pages=5, per_page=2)

        # Page 2 came back short, so paging stopped instead of asking for page 3.
        assert session.call_count == 2
        assert len(papers) == 3

    def test_stops_on_an_empty_page(self, fake_session, fake_response, make_openalex_work):
        session = fake_session(
            [
                fake_response(200, page([make_openalex_work(1)])),
                fake_response(200, page([])),
            ]
        )

        provider(session).fetch_raw("risk", pages=5, per_page=1)

        assert session.call_count == 2

    def test_page_numbers_increment(self, fake_session, fake_response, make_openalex_work):
        session = fake_session(fake_response(200, page([make_openalex_work(1)])))

        provider(session).fetch_raw("risk", pages=3, per_page=1)

        assert [request["params"]["page"] for request in session.calls] == [1, 2, 3]

    def test_per_page_is_capped_at_the_api_maximum(self, fake_session, fake_response):
        session = fake_session(fake_response(200, page([])))

        provider(session).fetch_raw("risk", pages=1, per_page=100_000)

        assert session.calls[0]["params"]["per-page"] == 200

    def test_a_courtesy_pause_separates_pages(
        self, fake_session, fake_response, make_openalex_work, recorded_sleep, monkeypatch
    ):
        monkeypatch.setattr(config, "INTER_PAGE_DELAY", 0.25)
        session = fake_session(fake_response(200, page([make_openalex_work(1)])))

        OpenAlexProvider(session=session, sleep=recorded_sleep).fetch_raw(
            "risk", pages=3, per_page=1
        )

        # Three pages, two gaps between them.
        assert recorded_sleep.durations == [0.25, 0.25]

    def test_defaults_come_from_config(self, fake_session, fake_response):
        session = fake_session(fake_response(200, page([])))

        provider(session).fetch_raw("risk")

        assert session.calls[0]["params"]["per-page"] == config.SEARCH_PER_PAGE


class TestFailures:

    def test_a_first_page_failure_is_surfaced(self, fake_session, fake_response):
        session = fake_session(fake_response(503, {}))

        with pytest.raises(ProviderUnavailable):
            provider(session).search("risk", pages=2, per_page=1)

    def test_a_later_page_failure_keeps_what_we_already_have(
        self, fake_session, fake_response, make_openalex_work
    ):
        session = fake_session(
            [
                fake_response(200, page([make_openalex_work(1)])),
                fake_response(500, {}),
            ]
        )

        papers = provider(session).search("risk", pages=3, per_page=1)

        # Losing page 2 must not discard page 1.
        assert [paper["paper_id"] for paper in papers] == ["https://openalex.org/W1"]

    def test_rate_limiting_propagates(self, fake_session, fake_response):
        session = fake_session(fake_response(429, {}))

        with pytest.raises(ProviderRateLimited):
            provider(session).search("risk", pages=1)

    def test_a_payload_without_a_results_list_is_an_error(self, fake_session, fake_response):
        session = fake_session(fake_response(200, {"meta": {"count": 0}}))

        with pytest.raises(ProviderUnavailable) as raised:
            provider(session).search("risk", pages=1)

        assert "results" in raised.value.message

    def test_a_bad_results_type_on_a_later_page_is_tolerated(
        self, fake_session, fake_response, make_openalex_work
    ):
        session = fake_session(
            [
                fake_response(200, page([make_openalex_work(1)])),
                fake_response(200, {"results": "broken"}),
            ]
        )

        papers = provider(session).search("risk", pages=3, per_page=1)

        assert len(papers) == 1

    def test_unusable_records_inside_a_good_page_are_dropped(
        self, fake_session, fake_response, make_openalex_work
    ):
        session = fake_session(
            [
                fake_response(
                    200,
                    {"results": [make_openalex_work(1), None, "junk", {"title": "No id"}]},
                )
            ]
        )

        papers = provider(session).search("risk", pages=1, per_page=100)

        assert len(papers) == 1
