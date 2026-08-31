"""HTTP routes (app.py).

Every route is exercised through a real TestClient against real templates, so a
broken template is a failing test rather than a 500 in production.  No test
here reaches the network: the provider is replaced at the seam
(``search_service.get_provider``), which is the only place the routes can
acquire one.

The database these tests write to is the temporary one pinned by conftest.
"""

import pytest
from fastapi.testclient import TestClient

import config
import database
import search_service
from app import app
from providers.errors import (
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def empty_database():
    """Start every route test from an empty table in the temporary database."""

    assert "sre_tests_" in str(config.DATABASE_PATH), (
        "refusing to clear a database outside the test directory"
    )

    session = database.SessionLocal()

    try:
        session.query(database.Paper).delete()
        session.commit()
    finally:
        session.close()


class FakeProvider:
    """Stands in for a real provider; never opens a socket."""

    name = "fakeprovider"
    label = "Fake provider"

    def __init__(self, papers=(), error=None):
        self.papers = list(papers)
        self.error = error
        self.calls = []

    def is_configured(self):
        return True

    def configuration_hint(self):
        return "No configuration needed."

    def search(self, keyword, pages=None, per_page=None, **kwargs):
        self.calls.append(keyword)

        if self.error is not None:
            raise self.error

        return [dict(paper) for paper in self.papers]


@pytest.fixture
def install_provider(monkeypatch):
    """Make ``run_search`` resolve to a given fake provider."""

    def install(provider):
        monkeypatch.setattr(
            search_service, "get_provider", lambda name=None, **kwargs: provider
        )
        return provider

    return install


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


class TestHome:

    def test_the_home_page_renders(self, client):
        response = client.get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_it_offers_a_search_form(self, client):
        body = client.get("/").text

        assert "keyword" in body

    def test_it_names_the_default_provider(self, client):
        assert "openalex" in client.get("/").text

    def test_it_works_against_an_empty_database(self, client):
        assert client.get("/").status_code == 200


class TestSearchPage:

    def test_a_search_renders_its_results(self, client, install_provider, two_papers):
        install_provider(FakeProvider(two_papers))

        response = client.get("/search", params={"keyword": "portfolio risk"})

        assert response.status_code == 200
        assert "Portfolio risk measurement" in response.text
        assert "Relevance:" in response.text
        assert "Why it matched:" in response.text

    def test_the_keyword_reaches_the_provider(self, client, install_provider, two_papers):
        provider = install_provider(FakeProvider(two_papers))

        client.get("/search", params={"keyword": "portfolio risk"})

        assert provider.calls == ["portfolio risk"]

    def test_results_are_stored(self, client, install_provider, two_papers):
        install_provider(FakeProvider(two_papers))

        client.get("/search", params={"keyword": "portfolio risk"})

        assert database.count_papers() == 2

    def test_a_search_with_no_results_is_still_a_page(self, client, install_provider):
        install_provider(FakeProvider([]))

        response = client.get("/search", params={"keyword": "nothing at all"})

        assert response.status_code == 200

    @pytest.mark.parametrize("params", [{}, {"keyword": ""}])
    def test_a_missing_keyword_is_rejected_by_validation(self, client, params):
        assert client.get("/search", params=params).status_code == 422

    def test_an_unknown_provider_is_reported_as_a_bad_request(self, client):
        # The real registry runs here, and it refuses the name before any
        # network call is possible.
        response = client.get("/search", params={"keyword": "risk", "provider": "scopus"})

        assert response.status_code == 400
        assert "scopus" in response.text

    def test_rate_limiting_is_reported_as_429_on_a_working_page(
        self, client, install_provider
    ):
        install_provider(
            FakeProvider(
                error=ProviderRateLimited("slow down", provider="openalex", retry_after=30)
            )
        )

        response = client.get("/search", params={"keyword": "risk"})

        assert response.status_code == 429
        # A form to try again, not a stack trace.
        assert "keyword" in response.text

    def test_a_missing_api_key_is_reported_as_503(self, client, install_provider):
        install_provider(
            FakeProvider(
                error=ProviderNotConfigured(
                    "Set SEMANTIC_SCHOLAR_API_KEY to enable this provider.",
                    provider="semanticscholar",
                )
            )
        )

        response = client.get("/search", params={"keyword": "risk"})

        assert response.status_code == 503
        assert "SEMANTIC_SCHOLAR_API_KEY" in response.text

    def test_an_unreachable_provider_is_reported_as_502(self, client, install_provider):
        install_provider(
            FakeProvider(error=ProviderUnavailable("timed out", provider="openalex"))
        )

        response = client.get("/search", params={"keyword": "risk"})

        assert response.status_code == 502

    def test_a_failed_search_stores_nothing(self, client, install_provider):
        install_provider(
            FakeProvider(error=ProviderUnavailable("timed out", provider="openalex"))
        )

        client.get("/search", params={"keyword": "risk"})

        assert database.count_papers() == 0


class TestDashboardPage:

    def test_an_empty_dashboard_renders(self, client):
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_a_populated_dashboard_renders(self, client, install_provider, two_papers):
        install_provider(FakeProvider(two_papers))
        client.get("/search", params={"keyword": "portfolio risk"})

        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Portfolio risk measurement" in response.text

    def test_the_dashboard_can_be_restricted_to_one_keyword(
        self, client, install_provider, two_papers
    ):
        install_provider(FakeProvider(two_papers))
        client.get("/search", params={"keyword": "portfolio risk"})

        response = client.get("/dashboard", params={"keyword": "something else"})

        assert response.status_code == 200
        assert "Portfolio risk measurement" not in response.text

    def test_an_empty_keyword_filter_is_accepted(self, client):
        assert client.get("/dashboard", params={"keyword": ""}).status_code == 200


class TestHealth:

    def test_reports_liveness(self, client):
        payload = client.get("/api/health").json()

        assert payload["status"] == "ok"
        assert payload["version"]

    def test_names_the_default_provider(self, client):
        assert client.get("/api/health").json()["default_provider"] == "openalex"

    def test_lists_the_providers_and_their_configuration_state(self, client):
        providers = client.get("/api/health").json()["providers"]

        assert [entry["name"] for entry in providers] == ["openalex", "semanticscholar"]

    def test_reports_which_database_is_in_use(self, client):
        # Also a live check that this suite is not pointed at the real file.
        assert "sre_tests_" in client.get("/api/health").json()["database"]

    def test_reports_the_stored_paper_count(self, client, install_provider, two_papers):
        assert client.get("/api/health").json()["stored_papers"] == 0

        install_provider(FakeProvider(two_papers))
        client.get("/search", params={"keyword": "portfolio risk"})

        assert client.get("/api/health").json()["stored_papers"] == 2


class TestSearchApi:

    def test_returns_scored_papers_as_json(self, client, install_provider, two_papers):
        install_provider(FakeProvider(two_papers))

        payload = client.get("/api/search", params={"keyword": "portfolio risk"}).json()

        assert payload["keyword"] == "portfolio risk"
        assert payload["provider"] == "fakeprovider"
        assert payload["retrieved"] == 2
        assert payload["excluded"] == 0
        assert payload["count"] == 2
        assert payload["inserted"] == 2
        assert payload["updated"] == 0
        assert payload["total_stored"] == 2
        assert all(paper["research_score"] > 0 for paper in payload["papers"])
        assert all(paper["relevance_score"] > 0 for paper in payload["papers"])

    def test_results_are_ranked(self, client, install_provider, two_papers):
        install_provider(FakeProvider(two_papers))

        payload = client.get("/api/search", params={"keyword": "risk"}).json()
        scores = [paper["research_score"] for paper in payload["papers"]]

        assert scores == sorted(scores, reverse=True)

    def test_persist_false_returns_results_without_storing_them(
        self, client, install_provider, two_papers
    ):
        install_provider(FakeProvider(two_papers))

        payload = client.get(
            "/api/search", params={"keyword": "risk", "persist": "false"}
        ).json()

        assert payload["count"] == 2
        assert payload["inserted"] == 0
        assert database.count_papers() == 0

    def test_a_failure_is_json_with_an_honest_status(self, client, install_provider):
        install_provider(
            FakeProvider(error=ProviderRateLimited("slow down", provider="openalex"))
        )

        response = client.get("/api/search", params={"keyword": "risk"})

        assert response.status_code == 429
        assert response.json()["kind"] == "rate_limited"
        assert response.json()["provider"] == "openalex"

    def test_an_unknown_provider_is_a_400(self, client):
        response = client.get("/api/search", params={"keyword": "risk", "provider": "scopus"})

        assert response.status_code == 400
        assert response.json()["kind"] == "unknown_provider"

    def test_a_missing_keyword_is_rejected(self, client):
        assert client.get("/api/search").status_code == 422


class TestDashboardApi:

    def test_returns_the_four_sections(self, client):
        payload = client.get("/api/dashboard").json()

        assert set(payload) == {"empty", "activity", "trends", "scores", "citations"}

    def test_an_empty_corpus_is_flagged(self, client):
        assert client.get("/api/dashboard").json()["empty"] is True

    def test_a_populated_corpus_fills_the_sections(
        self, client, install_provider, two_papers
    ):
        install_provider(FakeProvider(two_papers))
        client.get("/search", params={"keyword": "portfolio risk"})

        payload = client.get("/api/dashboard").json()

        assert payload["empty"] is False
        assert payload["activity"]["total_papers"] == 2
        assert payload["scores"]["count"] == 2
        assert payload["citations"]["max_citations"] == 120

    def test_the_keyword_filter_is_applied(self, client, install_provider, two_papers):
        install_provider(FakeProvider(two_papers))
        client.get("/search", params={"keyword": "portfolio risk"})

        payload = client.get("/api/dashboard", params={"keyword": "other"}).json()

        assert payload["empty"] is True
