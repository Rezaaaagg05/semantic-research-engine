"""The search pipeline (search_service.py).

One entry point runs a search: pick a provider, fetch, score, store, report.
These tests inject a fake provider, so the pipeline is exercised end to end
without a single network call -- including every failure mode and the HTTP
status each one maps to.
"""

import pytest

import database
import search_service
from providers.errors import (
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
    SearchPipelineError,
    UnknownProvider,
)
from search_service import collect_papers, run_search, search_error_response


class FakeProvider:
    """A provider that returns a canned list, or raises a canned error."""

    name = "fakeprovider"
    label = "Fake provider"

    def __init__(self, papers=(), error=None):
        self.papers = list(papers)
        self.error = error
        self.calls = []

    def is_configured(self):
        return True

    def configuration_hint(self):
        return ""

    def search(self, keyword, pages=None, per_page=None, **kwargs):
        self.calls.append({"keyword": keyword, "pages": pages, "per_page": per_page})

        if self.error is not None:
            raise self.error

        return [dict(paper) for paper in self.papers]


@pytest.fixture
def two_papers(make_paper):
    return [
        make_paper(1, title="Portfolio risk measurement", year=2023, citation_count=120),
        make_paper(
            2,
            title="Systemic portfolio risk in networks",
            year=2015,
            citation_count=8,
        ),
    ]


class TestHappyPath:

    def test_returns_scored_ranked_papers(self, db_session, two_papers):
        provider = FakeProvider(two_papers)

        result = run_search(
            "portfolio risk",
            provider_instance=provider,
            database_session=db_session,
        )

        assert len(result.papers) == 2
        assert all(paper["research_score"] > 0 for paper in result.papers)

        scores = [paper["research_score"] for paper in result.papers]
        assert scores == sorted(scores, reverse=True)

    def test_reports_which_provider_ran(self, db_session, two_papers):
        result = run_search(
            "risk", provider_instance=FakeProvider(two_papers), database_session=db_session
        )

        assert result.provider == "fakeprovider"

    def test_the_keyword_reaches_the_provider_stripped(self, db_session, two_papers):
        provider = FakeProvider(two_papers)

        run_search("  portfolio risk  ", provider_instance=provider, database_session=db_session)

        assert provider.calls[0]["keyword"] == "portfolio risk"

    def test_paging_arguments_are_passed_through(self, db_session):
        provider = FakeProvider()

        run_search("risk", provider_instance=provider, pages=2, per_page=25, database_session=db_session)

        assert provider.calls[0]["pages"] == 2
        assert provider.calls[0]["per_page"] == 25

    def test_results_are_stored(self, db_session, two_papers):
        result = run_search(
            "risk", provider_instance=FakeProvider(two_papers), database_session=db_session
        )

        assert (result.inserted, result.updated) == (2, 0)
        assert result.total == 2
        assert database.count_papers(session=db_session) == 2

    def test_searching_again_updates_instead_of_duplicating(self, db_session, two_papers):
        provider = FakeProvider(two_papers)

        run_search("risk", provider_instance=provider, database_session=db_session)
        second = run_search("risk", provider_instance=provider, database_session=db_session)

        assert (second.inserted, second.updated) == (0, 2)
        assert second.total == 2

    def test_the_stored_score_is_the_computed_score(self, db_session, two_papers):
        result = run_search(
            "risk", provider_instance=FakeProvider(two_papers), database_session=db_session
        )

        stored = {
            paper["paper_id"]: paper["research_score"]
            for paper in database.load_papers(session=db_session)
        }

        for paper in result.papers:
            assert stored[paper["paper_id"]] == paper["research_score"]

    def test_a_reference_year_can_be_pinned(self, db_session, make_paper):
        # Same paper, two baselines: the older baseline cannot make a 2023 paper
        # look stale, so pinning the year must change the recency component.
        paper = make_paper(
            1,
            title="Risk",
            year=2005,
            citation_count=0,
            abstract=None,
            authors=[],
            concepts=[],
        )

        recent = run_search(
            "risk",
            provider_instance=FakeProvider([paper]),
            database_session=db_session,
            reference_year=2024,
            persist=False,
        )
        contemporary = run_search(
            "risk",
            provider_instance=FakeProvider([paper]),
            database_session=db_session,
            reference_year=2006,
            persist=False,
        )

        assert contemporary.papers[0]["research_score"] > recent.papers[0]["research_score"]


class TestEmptyResults:

    def test_no_results_is_not_an_error(self, db_session):
        result = run_search("nothing at all", provider_instance=FakeProvider([]), database_session=db_session)

        assert result.papers == []
        assert (result.inserted, result.updated) == (0, 0)
        assert result.total == 0

    def test_an_empty_keyword_is_rejected_before_the_provider_is_called(self, db_session):
        provider = FakeProvider()

        for keyword in ("", "   ", None):
            with pytest.raises(SearchPipelineError):
                run_search(keyword, provider_instance=provider, database_session=db_session)

        assert provider.calls == []


class TestRelevanceStage:

    def test_irrelevant_candidates_are_excluded_before_scoring_and_storage(
        self, db_session, make_paper
    ):
        relevant = make_paper(
            1,
            title="Portfolio risk management",
            concepts=[],
        )
        irrelevant = make_paper(
            2,
            title="Clinical outcomes after kidney transplantation",
            abstract="A medical cohort study.",
            concepts=[],
            citation_count=10000,
        )

        result = run_search(
            "portfolio risk management",
            provider_instance=FakeProvider([relevant, irrelevant]),
            database_session=db_session,
        )

        assert result.retrieved == 2
        assert result.excluded == 1
        assert [paper["paper_id"] for paper in result.papers] == [relevant["paper_id"]]
        assert result.papers[0]["relevance_score"] == 50
        assert result.papers[0]["research_score"] > 0
        assert database.count_papers(session=db_session) == 1

    def test_relevance_outputs_are_persisted(self, db_session, make_paper):
        result = run_search(
            "portfolio risk management",
            provider_instance=FakeProvider(
                [make_paper(1, title="Portfolio risk management", concepts=[])]
            ),
            database_session=db_session,
        )

        stored = database.load_papers(session=db_session)[0]

        assert stored["relevance_score"] == result.papers[0]["relevance_score"]
        assert stored["relevance_level"] == "High"
        assert stored["relevance_reasons"] == result.papers[0]["relevance_reasons"]


class TestPersistence:

    def test_persist_false_leaves_the_database_untouched(self, db_session, two_papers):
        result = run_search(
            "risk",
            provider_instance=FakeProvider(two_papers),
            database_session=db_session,
            persist=False,
        )

        assert len(result.papers) == 2
        assert (result.inserted, result.updated, result.total) == (0, 0, 0)
        assert database.count_papers(session=db_session) == 0

    def test_collect_papers_is_read_only_by_default(self, db_session, two_papers):
        papers = collect_papers(
            "risk", provider_instance=FakeProvider(two_papers), database_session=db_session
        )

        assert len(papers) == 2
        assert database.count_papers(session=db_session) == 0


class TestProviderSelection:

    def test_the_registry_supplies_the_provider_when_none_is_injected(self, db_session, monkeypatch):
        provider = FakeProvider()
        requested = []

        def fake_get_provider(name=None, **kwargs):
            requested.append(name)
            return provider

        monkeypatch.setattr(search_service, "get_provider", fake_get_provider)

        run_search("risk", provider="openalex", database_session=db_session)

        assert requested == ["openalex"]
        assert provider.calls

    def test_an_unknown_provider_name_raises(self, db_session):
        with pytest.raises(UnknownProvider):
            run_search("risk", provider="scopus", database_session=db_session)

    def test_nothing_is_stored_when_the_provider_cannot_be_resolved(self, db_session):
        with pytest.raises(UnknownProvider):
            run_search("risk", provider="scopus", database_session=db_session)

        assert database.count_papers(session=db_session) == 0


class TestProviderFailuresPropagate:

    @pytest.mark.parametrize(
        "error",
        [
            ProviderUnavailable("down", provider="openalex", status_code=503),
            ProviderRateLimited("slow down", provider="openalex", status_code=429),
            ProviderNotConfigured("needs a key", provider="semanticscholar"),
        ],
    )
    def test_the_error_reaches_the_caller_unchanged(self, db_session, error):
        with pytest.raises(type(error)):
            run_search("risk", provider_instance=FakeProvider(error=error), database_session=db_session)

    def test_a_failed_search_stores_nothing(self, db_session):
        error = ProviderUnavailable("down", provider="openalex", status_code=503)

        with pytest.raises(ProviderUnavailable):
            run_search("risk", provider_instance=FakeProvider(error=error), database_session=db_session)

        assert database.count_papers(session=db_session) == 0


class TestErrorResponseMapping:

    def test_unknown_provider_is_the_clients_mistake(self):
        status, detail = search_error_response(UnknownProvider("nope", provider="scopus"))

        assert status == 400
        assert "scopus" in detail

    def test_a_missing_provider_name_still_reads_sensibly(self):
        status, detail = search_error_response(UnknownProvider("nope", provider="None"))

        assert status == 400
        assert "None" not in detail

    def test_not_configured_is_a_server_side_gap(self):
        error = ProviderNotConfigured("Set SEMANTIC_SCHOLAR_API_KEY.", provider="semanticscholar")

        status, detail = search_error_response(error)

        assert status == 503
        assert "semanticscholar" in detail
        assert "SEMANTIC_SCHOLAR_API_KEY" in detail

    def test_rate_limited_is_reported_as_rate_limited(self):
        error = ProviderRateLimited("slow down", provider="openalex", status_code=429)

        status, detail = search_error_response(error)

        assert status == 429
        assert "rate-limiting" in detail

    def test_retry_after_is_surfaced_when_the_provider_gave_one(self):
        error = ProviderRateLimited(
            "slow down", provider="openalex", status_code=429, retry_after=45
        )

        _, detail = search_error_response(error)

        assert "45" in detail

    def test_unavailable_is_a_bad_gateway(self):
        error = ProviderUnavailable("down", provider="openalex", status_code=503)

        status, detail = search_error_response(error)

        assert status == 502
        assert "openalex" in detail

    def test_a_generic_provider_error_is_still_a_gateway_problem(self):
        status, _ = search_error_response(ProviderError("odd", provider="openalex"))

        assert status == 502

    def test_a_pipeline_error_is_ours(self):
        status, detail = search_error_response(SearchPipelineError("Keyword is empty."))

        assert status == 500
        assert detail == "Keyword is empty."

    def test_an_unexpected_exception_does_not_leak_internals(self):
        status, detail = search_error_response(ZeroDivisionError("division by zero"))

        assert status == 500
        assert "division" not in detail

    def test_every_mapped_status_is_a_real_http_status(self):
        errors = [
            UnknownProvider("x", provider="y"),
            ProviderNotConfigured("x", provider="y"),
            ProviderRateLimited("x", provider="y"),
            ProviderUnavailable("x", provider="y"),
            ProviderError("x", provider="y"),
            SearchPipelineError("x"),
            RuntimeError("x"),
        ]

        for error in errors:
            status, detail = search_error_response(error)

            assert status in {400, 429, 500, 502, 503}
            assert isinstance(detail, str) and detail
