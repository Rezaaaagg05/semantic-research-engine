"""
OpenAlex provider -- the default data source.

OpenAlex has a public API and optionally accepts an API key for authenticated
traffic. Setting OPENALEX_MAILTO puts requests in the "polite pool", which
OpenAlex serves with higher throughput; it is optional and never required.

API reference: https://docs.openalex.org/api-entities/works
"""

import time

import config
from providers.base import Provider
from providers.errors import ProviderError, ProviderUnavailable
from providers.http import get_json


BASE_URL = "https://api.openalex.org/works"


#: Only the fields we actually use.  Asking for less is faster for both sides.
SELECT_FIELDS = ",".join(
    (
        "id",
        "title",
        "abstract_inverted_index",
        "publication_year",
        "cited_by_count",
        "authorships",
        "concepts",
        "doi",
    )
)


def reconstruct_abstract(inverted_index):
    """Rebuild plain text from OpenAlex's inverted index.

    OpenAlex ships abstracts as ``{word: [positions...]}`` for copyright
    reasons.  Malformed entries are skipped rather than raising.
    """

    if not isinstance(inverted_index, dict) or not inverted_index:
        return None

    positioned = []

    for word, positions in inverted_index.items():

        if not isinstance(positions, (list, tuple)):
            continue

        for position in positions:

            if isinstance(position, bool) or not isinstance(position, int):
                continue

            positioned.append((position, word))

    if not positioned:
        return None

    positioned.sort(key=lambda entry: entry[0])

    return " ".join(word for _, word in positioned) or None


def extract_authors(item):
    """Author display names in authorship order."""

    names = []

    for authorship in item.get("authorships") or []:

        if not isinstance(authorship, dict):
            continue

        author = authorship.get("author")

        if not isinstance(author, dict):
            continue

        name = author.get("display_name")

        if name:
            names.append(name)

    return names


def extract_concepts(item):
    """Concept dicts, kept whole so the concept pipeline sees level and score."""

    concepts = []

    for concept in item.get("concepts") or []:

        if not isinstance(concept, dict):
            continue

        if not concept.get("display_name"):
            continue

        concepts.append(
            {
                "name": concept.get("display_name"),
                "id": concept.get("id"),
                "level": concept.get("level"),
                "score": concept.get("score"),
            }
        )

    return concepts


def to_paper(item):
    """Translate one OpenAlex work into a provider-native paper dict."""

    if not isinstance(item, dict):
        return None

    identifier = item.get("id")

    if not identifier:
        return None

    return {
        "paper_id": identifier,
        "title": item.get("title"),
        "abstract": reconstruct_abstract(item.get("abstract_inverted_index")),
        "year": item.get("publication_year"),
        "citation_count": item.get("cited_by_count") or 0,
        "authors": extract_authors(item),
        "concepts": extract_concepts(item),
        "doi": item.get("doi"),
        "url": identifier,
    }


class OpenAlexProvider(Provider):

    name = "openalex"
    label = "OpenAlex"

    def __init__(self, session=None, sleep=None):
        # Injectable for tests; production uses requests + time.sleep.
        self._session = session
        self._sleep = sleep or time.sleep

    def is_configured(self):
        # No credentials required.
        return True

    def configuration_hint(self):
        return (
            "OpenAlex needs no API key for public access. Set OPENALEX_API_KEY "
            "when authenticated access is available, or OPENALEX_MAILTO to "
            "use the faster polite pool."
        )

    def fetch_raw(self, keyword, pages=None, per_page=None, **kwargs):

        keyword = (keyword or "").strip()

        if not keyword:
            return []

        if pages is None:
            pages = config.SEARCH_PAGES

        if per_page is None:
            per_page = config.SEARCH_PER_PAGE

        pages = max(1, int(pages))
        per_page = max(1, min(int(per_page), 200))

        collected = []
        first_page_failed_with = None

        for page in range(1, pages + 1):

            if page > 1 and config.INTER_PAGE_DELAY > 0:
                self._sleep(config.INTER_PAGE_DELAY)

            params = {
                "search": keyword,
                "page": page,
                "per-page": per_page,
                "sort": "relevance_score:desc",
                "select": SELECT_FIELDS,
            }

            if config.OPENALEX_MAILTO:
                params["mailto"] = config.OPENALEX_MAILTO

            if config.OPENALEX_API_KEY:
                params["api_key"] = config.OPENALEX_API_KEY

            try:
                payload = get_json(
                    BASE_URL,
                    provider=self.name,
                    params=params,
                    timeout=config.REQUEST_TIMEOUT,
                    session=self._session,
                    sleep=self._sleep,
                    retry_rate_limited=True,
                    rate_limit_retries=config.RATE_LIMIT_RETRIES,
                    max_rate_limit_wait=config.MAX_RATE_LIMIT_WAIT_SECONDS,
                )

            except ProviderError as error:
                # Failing on page 1 means we have nothing: surface the error.
                # Failing later means we already have usable results, so we
                # keep them and stop paging rather than discarding the lot.
                if page == 1:
                    first_page_failed_with = error
                    break

                break

            results = payload.get("results")

            if not isinstance(results, list):
                if page == 1:
                    first_page_failed_with = ProviderUnavailable(
                        "OpenAlex response had no 'results' list",
                        provider=self.name,
                    )
                break

            if not results:
                break

            for item in results:

                paper = to_paper(item)

                if paper is not None:
                    collected.append(paper)

            if len(results) < per_page:
                # Short page means the result set is exhausted.
                break

        if first_page_failed_with is not None:
            raise first_page_failed_with

        return collected
