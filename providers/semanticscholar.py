"""
Semantic Scholar provider -- available, but off by default.

Why this is not the default
---------------------------
The Semantic Scholar Graph API rate-limits unauthenticated traffic hard: a
single anonymous search reliably returns HTTP 429 with
``x-amz-ErrorType: TooManyRequestsException``.  The correct fix is a free API
key (https://www.semanticscholar.org/product/api#api-key-form), not more
retries -- so this adapter refuses to make network calls unless it is
configured, and raises ProviderNotConfigured instead of silently returning an
empty list.

Configuration
-------------
    SEMANTIC_SCHOLAR_API_KEY=...            enables the provider
    SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED=1  explicit opt-in without a key

The key is read from the environment only.  It is never logged, never written
to disk, and never included in error messages.

Rate limits are respected, never worked around: no header spoofing, no IP
rotation, no retry-until-it-works loop.  A 429 propagates to the caller as
ProviderRateLimited so the UI can say "try again later".

API reference: https://api.semanticscholar.org/api-docs/graph
"""

import time

import config
from providers.base import Provider
from providers.errors import ProviderError, ProviderNotConfigured
from providers.http import get_json


BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


#: Requested fields, chosen to line up with the canonical paper structure.
FIELDS = ",".join(
    (
        "paperId",
        "title",
        "abstract",
        "year",
        "citationCount",
        "authors",
        "fieldsOfStudy",
        "s2FieldsOfStudy",
        "externalIds",
        "url",
    )
)


#: Semantic Scholar caps offset+limit for the free search endpoint.
MAX_LIMIT = 100
MAX_OFFSET = 9900


def extract_authors(item):
    names = []

    for author in item.get("authors") or []:

        if isinstance(author, dict):
            name = author.get("name")
        else:
            name = author

        if name:
            names.append(name)

    return names


def extract_concepts(item):
    """Map Semantic Scholar's fields of study onto concept dicts.

    S2 gives no relevance score or hierarchy level, so those stay None and the
    concept pipeline treats them as unrated rather than low-rated.
    """

    concepts = []
    seen = set()

    for entry in item.get("s2FieldsOfStudy") or []:

        if not isinstance(entry, dict):
            continue

        name = entry.get("category")

        if not name or name.casefold() in seen:
            continue

        seen.add(name.casefold())
        concepts.append({"name": name, "id": None, "level": None, "score": None})

    for name in item.get("fieldsOfStudy") or []:

        if not isinstance(name, str) or name.casefold() in seen:
            continue

        seen.add(name.casefold())
        concepts.append({"name": name, "id": None, "level": None, "score": None})

    return concepts


def extract_doi(item):
    external = item.get("externalIds")

    if isinstance(external, dict):
        return external.get("DOI") or external.get("doi")

    return None


def to_paper(item):
    """Translate one Semantic Scholar record into a provider-native dict."""

    if not isinstance(item, dict):
        return None

    identifier = item.get("paperId")

    if not identifier:
        return None

    return {
        "paper_id": f"https://www.semanticscholar.org/paper/{identifier}",
        "title": item.get("title"),
        "abstract": item.get("abstract"),
        "year": item.get("year"),
        "citation_count": item.get("citationCount") or 0,
        "authors": extract_authors(item),
        "concepts": extract_concepts(item),
        "doi": extract_doi(item),
        "url": item.get("url"),
    }


class SemanticScholarProvider(Provider):

    name = "semanticscholar"
    label = "Semantic Scholar"

    def __init__(self, session=None, sleep=None):
        self._session = session
        self._sleep = sleep or time.sleep

    def is_configured(self):
        return config.semantic_scholar_enabled()

    def configuration_hint(self):
        return (
            "Semantic Scholar rate-limits anonymous traffic. Set "
            "SEMANTIC_SCHOLAR_API_KEY in your environment (free key from "
            "semanticscholar.org/product/api), or set "
            "SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED=1 to try without one."
        )

    def _headers(self):
        """Auth header when a key is configured; nothing otherwise.

        The key value never appears anywhere else -- not in logs, not in
        exception messages, not in the returned papers.
        """

        if config.SEMANTIC_SCHOLAR_API_KEY:
            return {"x-api-key": config.SEMANTIC_SCHOLAR_API_KEY}

        return None

    def fetch_raw(self, keyword, pages=None, per_page=None, **kwargs):

        if not self.is_configured():
            raise ProviderNotConfigured(
                self.configuration_hint(),
                provider=self.name,
            )

        keyword = (keyword or "").strip()

        if not keyword:
            return []

        if pages is None:
            pages = config.SEARCH_PAGES

        if per_page is None:
            per_page = config.SEARCH_PER_PAGE

        pages = max(1, int(pages))
        limit = max(1, min(int(per_page), MAX_LIMIT))

        collected = []
        first_page_failed_with = None

        for page in range(pages):

            offset = page * limit

            if offset > MAX_OFFSET:
                break

            if page > 0 and config.INTER_PAGE_DELAY > 0:
                self._sleep(config.INTER_PAGE_DELAY)

            params = {
                "query": keyword,
                "offset": offset,
                "limit": limit,
                "fields": FIELDS,
            }

            try:
                payload = get_json(
                    BASE_URL,
                    provider=self.name,
                    params=params,
                    headers=self._headers(),
                    timeout=config.REQUEST_TIMEOUT,
                    session=self._session,
                    sleep=self._sleep,
                )

            except ProviderError as error:
                if page == 0:
                    first_page_failed_with = error

                break

            results = payload.get("data")

            if not isinstance(results, list) or not results:
                break

            for item in results:

                paper = to_paper(item)

                if paper is not None:
                    collected.append(paper)

            if len(results) < limit:
                break

        if first_page_failed_with is not None:
            raise first_page_failed_with

        return collected
