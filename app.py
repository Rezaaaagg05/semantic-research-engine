"""
HTTP layer.

Routes do three things and nothing more: read request parameters, call a
service, and render a template.  There is no provider logic, no scoring, no
topic counting and no aggregation in this file -- each of those lives in its
own module and is unit-tested there.
"""

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

import config
import dashboard_service
import database
import search_service
from providers import ProviderError, describe_providers
from providers.errors import SearchPipelineError


BASE_DIR = Path(__file__).resolve().parent


app = FastAPI(
    title="Semantic Research Engine",
    description="Search scholarly literature and analyse research trends.",
    version="2.0.0",
)


# Templates are resolved relative to this file, so the app runs correctly no
# matter which directory uvicorn was started from.
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _providers_context():
    """Provider registry state, shown in the UI so the default is never a mystery."""

    return {
        "providers": describe_providers(),
        "default_provider": config.DEFAULT_PROVIDER,
    }


# --------------------------------------------------------------------------
# HOME
# --------------------------------------------------------------------------

@app.get("/")
def home(request: Request):
    """Empty search page."""

    context = {
        "papers": [],
        "keyword": "",
        "result": None,
        "error": None,
        "total_stored": database.count_papers(),
    }

    context.update(_providers_context())

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=context,
    )


# --------------------------------------------------------------------------
# SEARCH
# --------------------------------------------------------------------------

@app.get("/search")
def search(
    request: Request,
    keyword: str = Query(..., min_length=1, description="Search phrase"),
    provider: str = Query(None, description="Provider name; blank uses the default"),
):
    """Run a search, store the results, and render them.

    A provider failure is reported on the page rather than raised as a 500:
    the user gets an explanation and a working form, not a stack trace.  The
    HTTP status still reflects what happened, so API clients see the truth too.
    """

    error = None
    result = None
    status_code = 200

    try:
        result = search_service.run_search(
            keyword,
            provider=provider,
            persist=True,
        )

    except (ProviderError, SearchPipelineError) as failure:
        status_code, detail = search_service.search_error_response(failure)

        error = {
            "kind": getattr(failure, "kind", "error"),
            "message": detail,
            "provider": getattr(failure, "provider", None),
        }

    context = {
        "papers": result.papers if result else [],
        "keyword": keyword,
        "result": result._asdict() if result else None,
        "error": error,
        "total_stored": database.count_papers(),
    }

    context.update(_providers_context())

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=context,
        status_code=status_code,
    )


# --------------------------------------------------------------------------
# DASHBOARD
# --------------------------------------------------------------------------

@app.get("/dashboard")
def dashboard(
    request: Request,
    keyword: str = Query(None, description="Restrict the dashboard to one search term"),
):
    """Four independent analytical views over the stored corpus."""

    papers = database.load_papers(keyword=keyword)

    context = {
        "dashboard": dashboard_service.build_dashboard(papers),
        "keyword": keyword,
    }

    context.update(_providers_context())

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=context,
    )


# --------------------------------------------------------------------------
# JSON endpoints (handy for scripts and for checking the pipeline)
# --------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """Liveness plus the facts worth knowing about this instance."""

    return {
        "status": "ok",
        "version": app.version,
        "default_provider": config.DEFAULT_PROVIDER,
        "providers": describe_providers(),
        "stored_papers": database.count_papers(),
        "database": str(config.DATABASE_PATH),
    }


@app.get("/api/search")
def api_search(
    keyword: str = Query(..., min_length=1),
    provider: str = Query(None),
    persist: bool = Query(True),
):
    """Search as JSON, with honest HTTP status codes on failure."""

    try:
        result = search_service.run_search(
            keyword,
            provider=provider,
            persist=persist,
        )

    except (ProviderError, SearchPipelineError) as failure:
        status_code, detail = search_service.search_error_response(failure)

        return JSONResponse(
            status_code=status_code,
            content={
                "error": detail,
                "kind": getattr(failure, "kind", "error"),
                "provider": getattr(failure, "provider", None),
            },
        )

    return {
        "keyword": keyword,
        "provider": result.provider,
        "count": len(result.papers),
        "inserted": result.inserted,
        "updated": result.updated,
        "total_stored": result.total,
        "papers": result.papers,
    }


@app.get("/api/dashboard")
def api_dashboard(keyword: str = Query(None)):
    """The dashboard data, unrendered."""

    papers = database.load_papers(keyword=keyword)

    return dashboard_service.build_dashboard(papers)
