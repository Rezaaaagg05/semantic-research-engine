"""
Search pipeline: the single entry point for every paper search.

The router knows about HTTP; this module knows about providers, scoring and
the database.  app.py calls ``run_search`` and never touches the provider
registry, the score formula or the ORM directly.

Error contract
--------------
Everything that can fail while searching raises a providers.errors.ProviderError
subclass (or a SearchError for pipeline-level problems).  A search that simply
finds nothing returns an empty ``SearchResult`` -- it is not an error.
"""

from collections import namedtuple

import database
import scoring
from providers import (
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
    UnknownProvider,
    get_provider,
)
from providers.errors import SearchPipelineError


#: Result of a completed search, returned instead of a bare list so callers can
#: distinguish "no results" from "provider failed" and report which provider ran.
SearchResult = namedtuple(
    "SearchResult",
    (
        "papers",          # list[dict] canonical papers, scored and ranked
        "provider",        # str name of the provider that ran
        "inserted",        # int rows newly stored
        "updated",         # int existing rows refreshed
        "total",           # int total papers in the database after this search
    ),
)


def search_error_response(error):
    """Best-effort HTTP status + user-facing message for a pipeline failure.

    Returns ``(status_code, detail)`` suitable for ``raise HTTPException(...)``;
    tests call this directly so nothing here needs a live request.
    """

    if isinstance(error, UnknownProvider):
        requested = getattr(error, "provider", None)

        if requested and requested != "None":
            return 400, f"Unknown provider '{requested}'."

        return 400, "Unknown provider requested."

    if isinstance(error, ProviderNotConfigured):
        return 503, (
            f"The {getattr(error, 'provider', 'provider')} provider is not "
            f"configured. {error.message}"
        )

    if isinstance(error, ProviderRateLimited):
        detail = (
            f"The {getattr(error, 'provider', 'provider')} provider is "
            f"rate-limiting us. Try again in a few minutes."
        )

        if getattr(error, "retry_after", None):
            detail += f" (Retry-After: {int(error.retry_after)}s)"

        return 429, detail

    if isinstance(error, ProviderUnavailable):
        return 502, (
            f"The {getattr(error, 'provider', 'provider')} provider could not "
            f"be reached right now. Please try again shortly."
        )

    if isinstance(error, ProviderError):
        return 502, error.message

    if isinstance(error, SearchPipelineError):
        return 500, error.message

    return 500, "An unexpected error occurred while searching."


def run_search(
    keyword,
    provider=None,
    pages=None,
    per_page=None,
    persist=True,
    database_session=None,
    provider_instance=None,
    reference_year=None,
):
    """Search, score, store, and return a SearchResult.

    ``provider``      name of a registered provider; None selects the default.
    ``persist``       False keeps this a pure read-only search (used by tests).
    ``database_session``  injectable for tests; handled like the sessionlib.
    ``provider_instance`` injectable for tests; never used in production.
    ``reference_year``    scoring baseline; None derives it from the corpus.
    """

    keyword = (keyword or "").strip()

    if not keyword:
        raise SearchPipelineError("Keyword is empty.")

    if provider_instance is None:
        provider_instance = get_provider(provider)

    papers = provider_instance.search(
        keyword,
        pages=pages,
        per_page=per_page,
    )

    papers = scoring.score_papers(
        papers,
        keyword=keyword,
        reference_year=reference_year,
    )

    inserted = updated = 0
    total = 0

    if persist:
        inserted, updated = database.save_papers(
            papers,
            keyword=keyword,
            session=database_session,
        )
        total = database.count_papers(session=database_session)

    return SearchResult(
        papers=papers,
        provider=provider_instance.name,
        inserted=inserted,
        updated=updated,
        total=total,
    )


#: Convenience kept for existing callers and tests that only want the list.
def collect_papers(
    keyword,
    provider=None,
    pages=None,
    per_page=None,
    persist=False,
    database_session=None,
    provider_instance=None,
):
    """Run a search and return just the ranked papers."""

    return run_search(
        keyword,
        provider=provider,
        pages=pages,
        per_page=per_page,
        persist=persist,
        database_session=database_session,
        provider_instance=provider_instance,
    ).papers


__all__ = [
    "SearchResult",
    "SearchPipelineError",
    "run_search",
    "collect_papers",
    "search_error_response",
]
