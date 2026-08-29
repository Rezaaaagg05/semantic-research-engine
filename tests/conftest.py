"""Shared test fixtures.

Two things happen at import time, before any test module is loaded, and both
matter:

1. ``RESEARCH_DATABASE_PATH`` is pointed at a throwaway file in a temporary
   directory.  ``config`` resolves the database path at import time and
   ``database`` opens it at import time, so this has to be set before anything
   under test is imported -- conftest is imported first, which is exactly the
   hook we need.  The consequence is that a test run can never read, write or
   delete the real ``papers.db``.

2. The repository root is put on ``sys.path`` so ``import database`` works the
   same way it does when the application runs.

Every fixture here is offline: no test in this suite performs a network call, so
the suite is deterministic and runs in well under a second.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# Import-time environment setup (order matters -- see the module docstring)
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


#: Temporary directory holding the database used by the whole test session.
TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="sre_tests_"))
TEST_DB_PATH = TEST_DB_DIR / "test_papers.db"

os.environ["RESEARCH_DATABASE_PATH"] = str(TEST_DB_PATH)

# Deterministic provider behaviour regardless of the developer's own settings.
os.environ.setdefault("RESEARCH_PROVIDER", "openalex")
os.environ["INTER_PAGE_DELAY"] = "0"
os.environ["OPENALEX_MAILTO"] = ""

# Semantic Scholar must be *off* by default in tests, so the adapter's
# "refuses to call without configuration" behaviour is what gets exercised.
os.environ.pop("SEMANTIC_SCHOLAR_API_KEY", None)
os.environ.pop("SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED", None)


# Imported only after the environment above is in place.
import config  # noqa: E402
import database  # noqa: E402


# Belt and braces: if the path juggling above ever breaks, fail loudly at
# collection time rather than quietly operating on the user's real database.
assert str(config.DATABASE_PATH) == str(TEST_DB_PATH), (
    f"tests are pointed at {config.DATABASE_PATH}, not the temporary database"
)
assert "sre_tests_" in str(config.DATABASE_PATH), (
    "refusing to run: the test database is not inside a temporary directory"
)


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover - teardown
    """Remove the temporary database directory when the run ends."""

    shutil.rmtree(TEST_DB_DIR, ignore_errors=True)


# --------------------------------------------------------------------------
# Database fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def db_session():
    """A session on an isolated, empty SQLite database in a temp directory.

    A brand-new engine per test means no state leaks between tests and the
    session-wide database file is left alone.
    """

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    handle, path = tempfile.mkstemp(suffix=".db", prefix="sre_case_", dir=TEST_DB_DIR)
    os.close(handle)

    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    database.Base.metadata.create_all(bind=engine)

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = factory()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        Path(path).unlink(missing_ok=True)


@pytest.fixture
def legacy_database():
    """Factory building a database with one of the historical schemas.

    Used by the migration tests: they need a *real* old database file, not a
    mock, to prove that migrating one keeps every row.
    """

    from sqlalchemy import create_engine, text

    created = []

    def build(columns, rows=()):
        handle, path = tempfile.mkstemp(suffix=".db", prefix="sre_legacy_", dir=TEST_DB_DIR)
        os.close(handle)

        engine = create_engine(f"sqlite:///{path}")
        created.append((engine, path))

        column_ddl = ", ".join(columns)

        with engine.begin() as connection:
            connection.execute(text(f"CREATE TABLE papers ({column_ddl})"))

            for row in rows:
                names = ", ".join(row)
                placeholders = ", ".join(f":{name}" for name in row)
                connection.execute(
                    text(f"INSERT INTO papers ({names}) VALUES ({placeholders})"),
                    row,
                )

        return engine

    yield build

    for engine, path in created:
        engine.dispose()
        Path(path).unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Offline HTTP fixtures
# --------------------------------------------------------------------------

class FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code=200, payload=None, headers=None, body_error=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload
        self._body_error = body_error

    def json(self):
        if self._body_error is not None:
            raise self._body_error

        return self._payload


class FakeSession:
    """Records every request and replays a scripted list of responses.

    The last response repeats once the script runs out, so a test can describe
    "429 forever" with a single entry.
    """

    def __init__(self, responses):
        if isinstance(responses, FakeResponse):
            responses = [responses]

        self.responses = list(responses)
        self.calls = []

    @property
    def call_count(self):
        return len(self.calls)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "params": params or {}, "headers": headers or {}, "timeout": timeout}
        )

        index = min(len(self.calls) - 1, len(self.responses) - 1)

        return self.responses[index]


@pytest.fixture
def fake_response():
    return FakeResponse


@pytest.fixture
def fake_session():
    return FakeSession


@pytest.fixture
def recorded_sleep():
    """A sleep replacement that records durations instead of waiting."""

    durations = []

    def sleep(seconds):
        durations.append(seconds)

    sleep.durations = durations

    return sleep


@pytest.fixture
def no_sleep():
    return lambda seconds: None


# --------------------------------------------------------------------------
# Payload and paper builders
# --------------------------------------------------------------------------

def openalex_work(
    index=1,
    title="Portfolio risk measurement",
    year=2023,
    citations=25,
    concepts=("Value at risk", "Portfolio optimization"),
    authors=("Ada Lovelace", "Alan Turing"),
    abstract_words=("Measuring", "portfolio", "risk"),
    doi="10.1234/example",
):
    """One OpenAlex work in the API's own wire format."""

    inverted = {word: [position] for position, word in enumerate(abstract_words)}

    return {
        "id": f"https://openalex.org/W{index}",
        "title": title,
        "abstract_inverted_index": inverted or None,
        "publication_year": year,
        "cited_by_count": citations,
        "authorships": [{"author": {"display_name": name}} for name in authors],
        "concepts": [
            {
                "id": f"https://openalex.org/C{position}",
                "display_name": name,
                "level": 2,
                "score": 0.75,
            }
            for position, name in enumerate(concepts)
        ],
        "doi": f"https://doi.org/{doi}" if doi else None,
    }


def semantic_scholar_paper(
    index=1,
    title="Systemic risk in banking networks",
    year=2022,
    citations=40,
    fields=("Economics", "Computer Science"),
    authors=("Grace Hopper",),
    doi="10.5555/s2example",
):
    """One Semantic Scholar record in the Graph API's own wire format."""

    return {
        "paperId": f"s2paper{index}",
        "title": title,
        "abstract": "A study of systemic risk.",
        "year": year,
        "citationCount": citations,
        "authors": [{"authorId": str(index), "name": name} for name in authors],
        "s2FieldsOfStudy": [{"category": name, "source": "s2-fos-model"} for name in fields],
        "fieldsOfStudy": list(fields),
        "externalIds": {"DOI": doi} if doi else {},
        "url": f"https://www.semanticscholar.org/paper/s2paper{index}",
    }


def canonical_paper(index=1, **overrides):
    """A paper already in canonical form, for testing downstream layers."""

    paper = {
        "paper_id": f"https://openalex.org/W{index}",
        "title": f"Paper {index}",
        "abstract": "An abstract.",
        "year": 2023,
        "citation_count": 10,
        "authors": ["Ada Lovelace"],
        "concepts": [
            {"name": "Value at risk", "key": "value at risk", "id": "C1", "level": 3, "score": 0.8}
        ],
        "doi": f"10.1234/p{index}",
        "url": f"https://openalex.org/W{index}",
        "source": "openalex",
        "research_score": 0,
        "keyword": "portfolio risk",
    }

    paper.update(overrides)

    return paper


def concept(name, level=3, score=0.8, identifier=None):
    """A canonical concept dict."""

    return {
        "name": name,
        "key": name.casefold(),
        "id": identifier,
        "level": level,
        "score": score,
    }


def corpus(spec, start_index=1, **defaults):
    """Build a corpus from ``[(year, count, (topics...)), ...]``.

    Keeps the trend tests readable: the shape of the corpus is visible in one
    line instead of buried in loops.
    """

    papers = []
    index = start_index

    for year, count, topics in spec:
        for _ in range(count):
            papers.append(
                canonical_paper(
                    index,
                    year=year,
                    concepts=[concept(name) for name in topics],
                    **defaults,
                )
            )
            index += 1

    return papers


@pytest.fixture
def make_openalex_work():
    return openalex_work


@pytest.fixture
def make_s2_paper():
    return semantic_scholar_paper


@pytest.fixture
def make_paper():
    return canonical_paper


@pytest.fixture
def make_concept():
    return concept


@pytest.fixture
def make_corpus():
    return corpus
