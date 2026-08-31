"""
Central configuration for the Semantic Research Engine.

Every value can be overridden through an environment variable, optionally
supplied through a local ``.env`` file.  Nothing sensitive is ever hard-coded:
API keys are read from the environment only, and their absence simply means the
corresponding provider stays disabled.
"""

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# .env support (optional dependency, never required)
# --------------------------------------------------------------------------

try:  # pragma: no cover - trivial import guard
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:  # pragma: no cover - python-dotenv missing or unreadable .env
    pass


# --------------------------------------------------------------------------
# Environment helpers
# --------------------------------------------------------------------------

def env_str(name, default=""):
    value = os.environ.get(name)

    if value is None:
        return default

    return value.strip()


def env_int(name, default):
    raw = os.environ.get(name)

    if raw is None or not raw.strip():
        return default

    try:
        return int(raw.strip())
    except ValueError:
        return default


def env_float(name, default):
    raw = os.environ.get(name)

    if raw is None or not raw.strip():
        return default

    try:
        return float(raw.strip())
    except ValueError:
        return default


def env_bool(name, default=False):
    raw = os.environ.get(name)

    if raw is None or not raw.strip():
        return default

    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


# --------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------

#: Name of the provider used when a request does not ask for a specific one.
DEFAULT_PROVIDER = env_str("RESEARCH_PROVIDER", "openalex").lower() or "openalex"


# --------------------------------------------------------------------------
# Search volume / politeness
# --------------------------------------------------------------------------

#: Pages requested per search.  Kept small on purpose: OpenAlex is a free,
#: community-funded service and every page is a real HTTP request.
SEARCH_PAGES = max(1, min(env_int("SEARCH_PAGES", 3), 20))

#: Results per page (OpenAlex allows up to 200; 100 is a good balance).
SEARCH_PER_PAGE = max(1, min(env_int("SEARCH_PER_PAGE", 100), 200))


# --------------------------------------------------------------------------
# Relevance analysis
# --------------------------------------------------------------------------

#: Conservative exclusion boundary.  Scores below this have too little
#: lexical/topic evidence to enter the stored corpus.  Kept deliberately low
#: to favour recall: uncertainty keeps a paper rather than discarding it.
RELEVANCE_MIN_SCORE = max(0, min(env_int("RELEVANCE_MIN_SCORE", 20), 100))

#: Human-readable bands shown with the independent numerical score.
RELEVANCE_MEDIUM_SCORE = max(
    RELEVANCE_MIN_SCORE,
    min(env_int("RELEVANCE_MEDIUM_SCORE", 30), 100),
)
RELEVANCE_HIGH_SCORE = max(
    RELEVANCE_MEDIUM_SCORE,
    min(env_int("RELEVANCE_HIGH_SCORE", 50), 100),
)

#: Socket/read timeout for a single provider HTTP request, in seconds.
REQUEST_TIMEOUT = env_float("REQUEST_TIMEOUT", 30.0)

#: How many times a *retryable* failure is retried.  Bounded and finite --
#: never recursive, never unlimited.
MAX_RETRIES = max(0, min(env_int("MAX_RETRIES", 2), 5))

#: OpenAlex 429 responses may be retried once after waiting. This is separate
#: from normal transient-error retries and intentionally capped.
RATE_LIMIT_RETRIES = max(0, min(env_int("RATE_LIMIT_RETRIES", 1), 2))

#: Maximum time spent honoring one Retry-After value.
MAX_RATE_LIMIT_WAIT_SECONDS = max(
    0.0,
    min(env_float("MAX_RATE_LIMIT_WAIT_SECONDS", 60.0), 300.0),
)

#: Base for the exponential backoff between retries, in seconds.
RETRY_BACKOFF_SECONDS = env_float("RETRY_BACKOFF_SECONDS", 2.0)

#: Hard ceiling on any single backoff sleep, so a hostile ``Retry-After``
#: header can never park the application for minutes.
MAX_BACKOFF_SECONDS = env_float("MAX_BACKOFF_SECONDS", 10.0)

#: Courtesy pause between paged requests to the same provider.
INTER_PAGE_DELAY = env_float("INTER_PAGE_DELAY", 0.3)

#: Contact address for the OpenAlex "polite pool".  Optional but recommended;
#: OpenAlex gives identified traffic better throughput.
OPENALEX_API_KEY = env_str("OPENALEX_API_KEY", "")
OPENALEX_MAILTO = env_str("OPENALEX_MAILTO", "")

USER_AGENT = env_str("USER_AGENT", "SemanticResearchEngine/2.0 (academic research)")


# --------------------------------------------------------------------------
# Semantic Scholar
# --------------------------------------------------------------------------

#: Read from the environment only.  Never hard-code a key, never commit one.
SEMANTIC_SCHOLAR_API_KEY = env_str("SEMANTIC_SCHOLAR_API_KEY", "")

#: Unauthenticated Semantic Scholar traffic is rate-limited (HTTP 429), so the
#: adapter refuses to make network calls unless a key is configured.  Setting
#: SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED=1 is an explicit, deliberate opt-in.
SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED = env_bool(
    "SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED",
    False
)


def semantic_scholar_enabled():
    """True when Semantic Scholar may perform live requests."""

    return bool(SEMANTIC_SCHOLAR_API_KEY) or SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def _resolve_database_path():
    """Locate the SQLite file, preferring an explicit setting.

    The original code used ``sqlite:///./papers.db``, which resolves against
    the *process* working directory -- start uvicorn from elsewhere and you
    silently get a different, empty database.  Resolving against this file's
    directory fixes that.

    The one exception exists to protect existing data: if a ``papers.db``
    already sits in the working directory and there is none next to the code,
    that pre-existing file is used rather than orphaned.
    """

    explicit = env_str("RESEARCH_DATABASE_PATH", "")

    if explicit:
        return Path(explicit).expanduser()

    alongside_code = BASE_DIR / "papers.db"
    in_working_dir = Path.cwd() / "papers.db"

    if not alongside_code.exists() and in_working_dir.exists():
        return in_working_dir

    return alongside_code


DATABASE_PATH = _resolve_database_path()

DATABASE_URL = env_str("RESEARCH_DATABASE_URL", "") or f"sqlite:///{DATABASE_PATH}"


# --------------------------------------------------------------------------
# Concept / topic pipeline
# --------------------------------------------------------------------------

#: OpenAlex scores each concept 0..1.  Weakly-associated concepts are mostly
#: noise for trend analysis, so they are dropped during normalization.
CONCEPT_MIN_SCORE = env_float("CONCEPT_MIN_SCORE", 0.30)

#: OpenAlex concept levels: 0 = root discipline ("Economics"), 5 = very narrow.
#: Level 0 terms are too generic to describe a research trend on their own, but
#: they are kept when a paper has nothing more specific (see concepts.py).
CONCEPT_MAX_LEVEL = env_int("CONCEPT_MAX_LEVEL", 5)

#: Maximum concepts kept per paper (highest-scoring first).
CONCEPT_MAX_PER_PAPER = max(1, env_int("CONCEPT_MAX_PER_PAPER", 12))


# --------------------------------------------------------------------------
# Trend analysis thresholds (explicit, so claims stay defensible)
# --------------------------------------------------------------------------

#: A year needs at least this many papers before its topic shares are trusted.
TREND_MIN_PAPERS_PER_YEAR = max(1, env_int("TREND_MIN_PAPERS_PER_YEAR", 5))

#: A topic needs at least this many occurrences across the whole corpus before
#: it can be classified at all.  Stops "1 paper -> 2 papers = hot trend".
TREND_MIN_TOTAL_OCCURRENCES = max(1, env_int("TREND_MIN_TOTAL_OCCURRENCES", 5))

#: A topic needs at least this many occurrences in the recent window.
TREND_MIN_RECENT_OCCURRENCES = max(1, env_int("TREND_MIN_RECENT_OCCURRENCES", 3))

#: Relative change in normalized share required to call something growing or
#: declining (0.5 == +/-50%).
TREND_GROWTH_THRESHOLD = env_float("TREND_GROWTH_THRESHOLD", 0.50)

#: Years per comparison window (recent window vs the window before it).
TREND_WINDOW_YEARS = max(1, env_int("TREND_WINDOW_YEARS", 5))

#: A topic counts as "persistent" when it appears in at least this fraction of
#: the years that have enough data.
TREND_PERSISTENCE_RATIO = env_float("TREND_PERSISTENCE_RATIO", 0.60)

#: How many topics each trend list returns.
TREND_TOP_N = max(1, env_int("TREND_TOP_N", 10))

#: Dominant topics shown per year.
TREND_TOPICS_PER_YEAR = max(1, env_int("TREND_TOPICS_PER_YEAR", 5))
