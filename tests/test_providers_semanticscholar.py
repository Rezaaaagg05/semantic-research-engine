"""The Semantic Scholar provider (providers/semanticscholar.py).

Phase 10 readiness: the adapter exists, is registered, and is *off* until it is
configured.  The most important test in this file is the first one -- an
unconfigured provider must raise before opening a socket, because unauthenticated
Semantic Scholar traffic is rate-limited and hammering it is exactly the
behaviour this design avoids.

No test here performs a live request, configured or not.
"""

import pytest

import config
import providers.semanticscholar as s2
from providers.errors import (
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
)
from providers.semanticscholar import (
    MAX_LIMIT,
    SemanticScholarProvider,
    extract_authors,
    extract_concepts,
    extract_doi,
    to_paper,
)


SECRET = "test-key-not-a-real-credential"


def provider(session=None, sleep=None):
    return SemanticScholarProvider(session=session, sleep=sleep or (lambda seconds: None))


def page(papers):
    return {"data": list(papers)}


@pytest.fixture
def configured(monkeypatch):
    """Pretend a key is present, without ever putting one in the repository."""

    monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_API_KEY", SECRET)
    monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED", False)


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED", False)


class TestRefusesToRunUnconfigured:

    def test_no_network_call_is_made_without_configuration(
        self, unconfigured, fake_session, fake_response
    ):
        session = fake_session(fake_response(200, page([])))

        with pytest.raises(ProviderNotConfigured):
            provider(session).search("portfolio risk")

        # The whole point: zero requests, so we never provoke a 429.
        assert session.call_count == 0

    def test_is_configured_reports_false(self, unconfigured):
        assert provider().is_configured() is False

    def test_the_error_explains_how_to_enable_it(self, unconfigured):
        with pytest.raises(ProviderNotConfigured) as raised:
            provider().fetch_raw("risk")

        message = raised.value.message

        assert "SEMANTIC_SCHOLAR_API_KEY" in message
        assert raised.value.provider == "semanticscholar"
        assert raised.value.kind == "not_configured"

    def test_it_is_not_the_default_provider(self):
        assert config.DEFAULT_PROVIDER != SemanticScholarProvider.name


class TestConfiguration:

    def test_an_api_key_enables_it(self, configured):
        assert provider().is_configured() is True

    def test_explicit_opt_in_enables_it_without_a_key(self, monkeypatch):
        monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_API_KEY", "")
        monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED", True)

        assert provider().is_configured() is True

    def test_the_key_is_sent_as_the_documented_header(
        self, configured, fake_session, fake_response
    ):
        session = fake_session(fake_response(200, page([])))

        provider(session).fetch_raw("risk", pages=1)

        assert session.calls[0]["headers"]["x-api-key"] == SECRET

    def test_no_auth_header_is_sent_when_there_is_no_key(
        self, monkeypatch, fake_session, fake_response
    ):
        monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_API_KEY", "")
        monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED", True)
        session = fake_session(fake_response(200, page([])))

        provider(session).fetch_raw("risk", pages=1)

        assert "x-api-key" not in session.calls[0]["headers"]

    def test_registry_identity(self):
        assert SemanticScholarProvider.name == "semanticscholar"
        assert SemanticScholarProvider.label == "Semantic Scholar"


class TestTheKeyNeverLeaks:

    def test_not_in_the_configuration_hint(self, configured):
        assert SECRET not in provider().configuration_hint()

    def test_not_in_the_returned_papers(
        self, configured, fake_session, fake_response, make_s2_paper
    ):
        session = fake_session(fake_response(200, page([make_s2_paper(1)])))

        papers = provider(session).search("risk", pages=1)

        assert SECRET not in repr(papers)

    def test_not_in_a_rate_limit_error(self, configured, fake_session, fake_response):
        session = fake_session(fake_response(429, {}))

        with pytest.raises(ProviderRateLimited) as raised:
            provider(session).search("risk", pages=1)

        assert SECRET not in raised.value.message
        assert SECRET not in repr(raised.value.as_dict())

    def test_not_in_an_unavailable_error(self, configured, fake_session, fake_response):
        session = fake_session(fake_response(500, {}))

        with pytest.raises(ProviderUnavailable) as raised:
            provider(session).search("risk", pages=1)

        assert SECRET not in raised.value.message

    def test_the_source_file_contains_no_hard_coded_key(self):
        with open(s2.__file__, encoding="utf-8") as handle:
            source = handle.read()

        # Configuration is read from the environment, never embedded.
        assert "SEMANTIC_SCHOLAR_API_KEY" in source
        assert "x-api-key" in source
        assert 'x-api-key": "' not in source


class TestExtractors:

    def test_authors_from_dicts_and_strings(self):
        item = {"authors": [{"name": "Ada"}, "Alan", {"noname": True}, None]}

        assert extract_authors(item) == ["Ada", "Alan"]

    def test_no_authors(self):
        assert extract_authors({}) == []
        assert extract_authors({"authors": None}) == []

    def test_concepts_come_from_both_field_lists_without_duplicates(self):
        item = {
            "s2FieldsOfStudy": [{"category": "Economics"}, {"category": "economics"}],
            "fieldsOfStudy": ["Economics", "Mathematics"],
        }

        names = [concept["name"] for concept in extract_concepts(item)]

        assert names == ["Economics", "Mathematics"]

    def test_concepts_carry_no_invented_score_or_level(self, make_s2_paper):
        # S2 gives neither, so both stay None and the concept pipeline treats
        # them as unrated rather than low-rated.
        concepts = extract_concepts(make_s2_paper(1))

        assert all(concept["score"] is None for concept in concepts)
        assert all(concept["level"] is None for concept in concepts)

    def test_malformed_field_entries_are_skipped(self):
        item = {"s2FieldsOfStudy": ["string", None, {}, {"category": "Real"}], "fieldsOfStudy": [42]}

        assert [concept["name"] for concept in extract_concepts(item)] == ["Real"]

    def test_doi_from_external_ids(self):
        assert extract_doi({"externalIds": {"DOI": "10.1/x"}}) == "10.1/x"
        assert extract_doi({"externalIds": {"doi": "10.1/y"}}) == "10.1/y"

    def test_missing_doi(self):
        assert extract_doi({}) is None
        assert extract_doi({"externalIds": None}) is None
        assert extract_doi({"externalIds": "10.1/x"}) is None


class TestToPaper:

    def test_full_translation(self, make_s2_paper):
        paper = to_paper(make_s2_paper(3, year=2021, citations=88))

        assert paper["paper_id"] == "https://www.semanticscholar.org/paper/s2paper3"
        assert paper["year"] == 2021
        assert paper["citation_count"] == 88
        assert paper["doi"] == "10.5555/s2example"

    def test_the_id_is_namespaced_so_it_cannot_collide_with_openalex(self, make_s2_paper):
        paper = to_paper(make_s2_paper(1))

        assert paper["paper_id"].startswith("https://www.semanticscholar.org/paper/")

    def test_a_record_without_an_id_is_unusable(self):
        assert to_paper({"title": "No id"}) is None

    @pytest.mark.parametrize("value", [None, "paper", 42, []])
    def test_non_dict_input(self, value):
        assert to_paper(value) is None

    def test_missing_citation_count_becomes_zero(self):
        assert to_paper({"paperId": "x"})["citation_count"] == 0
        assert to_paper({"paperId": "x", "citationCount": None})["citation_count"] == 0


class TestSearch:

    def test_returns_canonical_papers(self, configured, fake_session, fake_response, make_s2_paper):
        session = fake_session(fake_response(200, page([make_s2_paper(1)])))

        papers = provider(session).search("systemic risk", pages=1)

        assert len(papers) == 1
        assert papers[0]["source"] == "semanticscholar"
        assert papers[0]["keyword"] == "systemic risk"
        assert papers[0]["research_score"] == 0

    def test_an_empty_keyword_makes_no_request(self, configured, fake_session, fake_response):
        session = fake_session(fake_response(200, page([])))

        assert provider(session).search("  ") == []
        assert session.call_count == 0

    def test_no_results_is_not_an_error(self, configured, fake_session, fake_response):
        session = fake_session(fake_response(200, page([])))

        assert provider(session).search("nothing", pages=1) == []


class TestPaging:

    def test_offsets_advance_by_the_page_size(
        self, configured, fake_session, fake_response, make_s2_paper
    ):
        session = fake_session(fake_response(200, page([make_s2_paper(1)])))

        provider(session).fetch_raw("risk", pages=3, per_page=1)

        assert [request["params"]["offset"] for request in session.calls] == [0, 1, 2]

    def test_limit_is_capped_at_the_api_maximum(self, configured, fake_session, fake_response):
        session = fake_session(fake_response(200, page([])))

        provider(session).fetch_raw("risk", pages=1, per_page=100_000)

        assert session.calls[0]["params"]["limit"] == MAX_LIMIT

    def test_paging_stops_at_the_offset_ceiling(
        self, configured, fake_session, fake_response, make_s2_paper, monkeypatch
    ):
        monkeypatch.setattr(s2, "MAX_OFFSET", 2)
        session = fake_session(fake_response(200, page([make_s2_paper(1)])))

        provider(session).fetch_raw("risk", pages=50, per_page=1)

        # Offsets 0, 1, 2 are allowed; offset 3 exceeds the ceiling.
        assert session.call_count == 3

    def test_stops_on_a_short_page(
        self, configured, fake_session, fake_response, make_s2_paper
    ):
        session = fake_session(
            [
                fake_response(200, page([make_s2_paper(1), make_s2_paper(2)])),
                fake_response(200, page([make_s2_paper(3)])),
            ]
        )

        provider(session).fetch_raw("risk", pages=9, per_page=2)

        assert session.call_count == 2

    def test_a_courtesy_pause_separates_pages(
        self, configured, fake_session, fake_response, make_s2_paper, recorded_sleep, monkeypatch
    ):
        monkeypatch.setattr(config, "INTER_PAGE_DELAY", 0.4)
        session = fake_session(fake_response(200, page([make_s2_paper(1)])))

        SemanticScholarProvider(session=session, sleep=recorded_sleep).fetch_raw(
            "risk", pages=2, per_page=1
        )

        assert recorded_sleep.durations == [0.4]


class TestFailures:

    def test_rate_limiting_is_reported_not_retried_around(
        self, configured, fake_session, fake_response
    ):
        session = fake_session(fake_response(429, {}))

        with pytest.raises(ProviderRateLimited):
            provider(session).search("risk", pages=3)

        # One attempt. No second identity, no backoff-and-hammer loop.
        assert session.call_count == 1

    def test_a_first_page_failure_is_surfaced(self, configured, fake_session, fake_response):
        session = fake_session(fake_response(503, {}))

        with pytest.raises(ProviderUnavailable):
            provider(session).search("risk", pages=2, per_page=1)

    def test_a_later_page_failure_keeps_what_we_have(
        self, configured, fake_session, fake_response, make_s2_paper
    ):
        session = fake_session(
            [
                fake_response(200, page([make_s2_paper(1)])),
                fake_response(500, {}),
            ]
        )

        papers = provider(session).search("risk", pages=3, per_page=1)

        assert len(papers) == 1

    def test_a_payload_without_data_yields_nothing_rather_than_raising(
        self, configured, fake_session, fake_response
    ):
        session = fake_session(fake_response(200, {"total": 0}))

        assert provider(session).search("risk", pages=1) == []
