# Semantic Research Engine

A small FastAPI application that searches scholarly literature, stores what it
finds, scores it, and reports what the corpus actually says about a field over
time.

**OpenAlex is the default and only enabled provider.** Semantic Scholar has a
finished adapter that stays switched off until it is deliberately configured
(see [Semantic Scholar](#semantic-scholar) below).

## Requirements

* Python 3.11 or newer (developed and tested on 3.13)
* The pinned dependencies in `requirements.txt`

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

`requirements.txt` pins the exact versions the test suite is green against,
including the three test-only packages (`pytest`, `httpx2`, `httpcore2` —
`fastapi.testclient` is built on `httpx2`). Everything else is runtime.

## Running

```bash
uvicorn app:app --reload
```

| Route            | What it does                                                        |
| ---------------- | ------------------------------------------------------------------- |
| `/`              | Empty search page                                                   |
| `/search`        | `?keyword=<phrase>&provider=<name>` — searches, stores, renders      |
| `/dashboard`     | `?keyword=<phrase>` — four independent analytical views              |
| `/api/health`    | Status, default provider, registry state, stored paper count         |
| `/api/search`    | Same as `/search` as JSON; `&persist=false` to search without saving |
| `/api/dashboard` | The dashboard data, unrendered                                       |

`provider` and `keyword` are optional where the table says so. A provider
failure is rendered as an explanation on the page and reported with an honest
HTTP status (`429` rate limited, `503` not configured, `502` unavailable,
`400` unknown provider) rather than a 500 stack trace.

## Configuration

Every setting is read from the environment, optionally through a local `.env`
file. Copy `.env.example` to `.env` and edit what you need — no value is
required to run against OpenAlex.

Frequently useful settings:

| Variable                 | Default                       | Purpose                                     |
| ------------------------ | ----------------------------- | ------------------------------------------- |
| `RESEARCH_PROVIDER`      | `openalex`                    | Default provider for requests without one   |
| `OPENALEX_MAILTO`        | *(unset)*                     | Contact address for the OpenAlex polite pool |
| `SEARCH_PAGES`           | `3`                           | Pages requested per search (capped at 20)   |
| `SEARCH_PER_PAGE`        | `100`                         | Results per page (capped at 200)            |
| `RESEARCH_DATABASE_PATH` | `papers.db` next to the code  | SQLite file location                        |

`config.py` documents the rest — request timeouts, bounded retry and backoff,
the concept-pipeline thresholds, and every trend-analysis threshold.

### Contact address

`OPENALEX_MAILTO` is optional but recommended. OpenAlex is free and
community-funded, and it gives identified traffic better throughput.

## Semantic Scholar

Semantic Scholar is registered but **disabled by default**, because
unauthenticated traffic to its API is aggressively rate limited. The adapter
raises `ProviderNotConfigured` *before opening a socket* when it is not
configured, so an unconfigured instance can never provoke a 429.

To enable it, set an API key in the environment (request one from
[Semantic Scholar](https://www.semanticscholar.org/product/api)):

```bash
# .env — never committed, never shared
SEMANTIC_SCHOLAR_API_KEY=your-key-here
```

Then select it per request:

```
/search?keyword=portfolio+risk+management&provider=semanticscholar
```

The key is read from the environment only. It is never hard-coded, never
logged, and never included in an error message or a rendered page. If you
would rather run unauthenticated and accept the rate limits, there is a
deliberate opt-in:

```bash
SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED=1
```

Politeness is a design rule here, not a setting: one honest `User-Agent` that
is never rotated or disguised, bounded retries with a capped backoff, `429`
respected rather than retried around, and `4xx` never retried.

## Architecture

```
app.py                 FastAPI routes and template rendering only
search_service.py      search -> normalize -> score -> store pipeline
dashboard_service.py   four independent dashboard sections
scoring.py             research score (citations, relevance, recency, completeness)
trends.py              share-based trend analysis over the stored corpus
concepts.py            concept/topic normalization and noise filtering
database.py            SQLAlchemy models, upserts, queries
models.py              canonical paper shape
config.py              every setting, read from the environment
providers/
  __init__.py          registry: name -> provider class
  base.py              search() template method; subclasses implement fetch_raw()
  http.py              the shared, deliberately polite HTTP helper
  errors.py            ProviderError hierarchy
  openalex.py          OpenAlex adapter (default)
  semanticscholar.py   Semantic Scholar adapter (off until configured)
```

Adding a provider means adding one module with a `fetch_raw` method and one
registry entry. Normalization, scoring, storage, and the dashboard are shared.

## Tests

```bash
pytest
```

The suite is offline and deterministic: fake HTTP sessions, injected sleeps,
seeded randomness, and a temporary SQLite database created per run. It never
makes a network request and never touches `papers.db`.

`pytest.ini` sets `testpaths = tests` deliberately. The `test_*.py` scripts in
the repository root are older print-only exploration scripts that make live
network calls at import time; they are kept for reference and excluded from
collection.

## Data

Search results are stored in SQLite (`papers.db` by default, resolved next to
`config.py` so the database does not change when you start the server from a
different directory). The file is git-ignored. Re-running a search updates
existing rows in place and never discards a stored citation count or score
that the new response does not include.
