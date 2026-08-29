"""
Database layer: the canonical Paper table, a safe additive migration, and the
persistence helpers the service layer uses.

Migration policy
----------------
Migrations here are strictly additive: ``ALTER TABLE ... ADD COLUMN`` and
``CREATE INDEX IF NOT EXISTS``.  No column is ever dropped, no table is ever
recreated, and no row is ever deleted.  An existing ``papers.db`` from any
earlier version of this application opens, migrates and keeps every row.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

import config
from concepts import concepts_from_json, concepts_to_json
from models import PAPER_FIELDS, coerce_int, coerce_year


DATABASE_URL = config.DATABASE_URL


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


Base = declarative_base()


def _utcnow():
    return datetime.now(timezone.utc)


class Paper(Base):
    """One scholarly work, as stored.

    Column names match the canonical paper structure in models.py wherever
    possible so the mapping between the two stays obvious.  Two columns are
    denormalized text: ``authors`` (comma-joined names, kept for template and
    backward compatibility) and ``concepts`` (a JSON array).
    """

    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, index=True)

    #: Provider-scoped stable identifier (an OpenAlex or S2 URI).
    paper_id = Column(String, unique=True, index=True)

    #: Which provider this record came from.  Defaults to openalex because
    #: every pre-existing row was collected from OpenAlex.
    source = Column(String, index=True, default=config.DEFAULT_PROVIDER)

    title = Column(String)
    abstract = Column(Text)
    year = Column(Integer, index=True)
    citation_count = Column(Integer, default=0)

    #: Comma-joined author display names.
    authors = Column(Text)

    #: Most recent search term that returned this paper.
    keyword = Column(String, index=True)

    research_score = Column(Integer, default=0, index=True)

    #: JSON array of canonical concept dicts.
    concepts = Column(Text)

    #: Bare DOI, no URL prefix.
    doi = Column(String, index=True)

    #: Landing page.
    url = Column(String)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    #: A provider's id is unique within that provider.  paper_id already
    #: carries a provider-specific URI prefix so it is globally unique too,
    #: but the composite index states the real constraint explicitly.
    __table_args__ = (
        Index("ix_papers_source_paper_id", "source", "paper_id", unique=True),
    )

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<Paper {self.paper_id!r} year={self.year!r} score={self.research_score!r}>"

    # -- conversion ---------------------------------------------------------

    def author_list(self):
        """Author names as a list, tolerating the comma-joined storage format."""

        if not self.authors:
            return []

        return [part.strip() for part in self.authors.split(",") if part.strip()]

    def concept_list(self):
        """Canonical concepts, parsed from whichever historical JSON format."""

        return concepts_from_json(self.concepts)

    def to_dict(self):
        """Return this row in the canonical paper structure."""

        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "abstract": self.abstract,
            "year": self.year,
            "citation_count": self.citation_count or 0,
            "authors": self.author_list(),
            "concepts": self.concept_list(),
            "doi": self.doi,
            "url": self.url,
            "source": self.source or config.DEFAULT_PROVIDER,
            "research_score": self.research_score or 0,
            "keyword": self.keyword,
        }


# --------------------------------------------------------------------------
# Schema creation + additive migration
# --------------------------------------------------------------------------

#: Columns added after the first release, with the DDL to add them.  Every
#: entry is nullable or defaulted so existing rows stay valid.
_ADDED_COLUMNS = (
    ("research_score", "ALTER TABLE papers ADD COLUMN research_score INTEGER DEFAULT 0"),
    ("concepts", "ALTER TABLE papers ADD COLUMN concepts TEXT"),
    ("source", "ALTER TABLE papers ADD COLUMN source VARCHAR"),
    ("doi", "ALTER TABLE papers ADD COLUMN doi VARCHAR"),
    ("url", "ALTER TABLE papers ADD COLUMN url VARCHAR"),
    ("created_at", "ALTER TABLE papers ADD COLUMN created_at DATETIME"),
    ("updated_at", "ALTER TABLE papers ADD COLUMN updated_at DATETIME"),
)


#: Indexes the ORM would create on a fresh database, restated so an existing
#: database gets them too.  Never unique except where the model says so.
_ADDED_INDEXES = (
    ("ix_papers_source", "CREATE INDEX IF NOT EXISTS ix_papers_source ON papers (source)"),
    ("ix_papers_year", "CREATE INDEX IF NOT EXISTS ix_papers_year ON papers (year)"),
    ("ix_papers_keyword", "CREATE INDEX IF NOT EXISTS ix_papers_keyword ON papers (keyword)"),
    (
        "ix_papers_research_score",
        "CREATE INDEX IF NOT EXISTS ix_papers_research_score ON papers (research_score)",
    ),
    ("ix_papers_doi", "CREATE INDEX IF NOT EXISTS ix_papers_doi ON papers (doi)"),
    (
        "ix_papers_source_paper_id",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_papers_source_paper_id "
        "ON papers (source, paper_id)",
    ),
)


def migrate_database(bind=None):
    """Bring an existing ``papers`` table up to the current schema.

    Purely additive and idempotent: safe to call on every import, safe to call
    on a database that is already current, and safe to call on the very first
    release's schema.  Returns the list of changes applied, for logging and
    for tests.
    """

    bind = bind or engine
    applied = []

    inspector = inspect(bind)

    if "papers" not in inspector.get_table_names():
        return applied

    existing = {column["name"] for column in inspector.get_columns("papers")}

    with bind.begin() as connection:

        for name, ddl in _ADDED_COLUMNS:

            if name in existing:
                continue

            connection.execute(text(ddl))
            applied.append(f"add column {name}")

        # Backfill `source` so the composite unique index can be created and
        # so old rows are attributable.  Every legacy row came from OpenAlex.
        if "source" not in existing:
            connection.execute(
                text("UPDATE papers SET source = :source WHERE source IS NULL"),
                {"source": "openalex"},
            )
            applied.append("backfill source=openalex")

    # Indexes are created in their own transaction: a pre-existing duplicate
    # would make the unique index fail, and that must not roll back the
    # column additions above.
    existing_indexes = {index["name"] for index in inspect(bind).get_indexes("papers")}

    for name, ddl in _ADDED_INDEXES:

        if name in existing_indexes:
            continue

        try:
            with bind.begin() as connection:
                connection.execute(text(ddl))

            applied.append(f"create index {name}")

        except Exception as error:  # pragma: no cover - legacy duplicate rows
            # A unique index can legitimately fail on an old database that
            # already contains duplicates.  Losing the index is acceptable;
            # losing the user's rows is not, so we never delete to make it fit.
            applied.append(f"skip index {name}: {type(error).__name__}")

    return applied


def init_database(bind=None):
    """Create missing tables, then migrate.  Idempotent."""

    bind = bind or engine

    Base.metadata.create_all(bind=bind)

    return migrate_database(bind=bind)


# Kept at import time so `import database` continues to be all the setup any
# caller needs -- the behaviour every existing script already relies on.
init_database()


# --------------------------------------------------------------------------
# Persistence helpers
# --------------------------------------------------------------------------

def _authors_text(authors):
    if not authors:
        return None

    if isinstance(authors, str):
        return authors.strip() or None

    return ", ".join(str(name).strip() for name in authors if str(name).strip()) or None


def _apply(row, paper, keyword):
    """Copy canonical paper fields onto an ORM row.

    Never overwrites a populated column with an empty value: a later provider
    response missing an abstract must not erase the abstract we already have.
    """

    def better(new, old):
        if new in (None, "", [], {}):
            return old
        return new

    row.title = better(paper.get("title"), row.title)
    row.abstract = better(paper.get("abstract"), row.abstract)
    row.year = better(coerce_year(paper.get("year")), row.year)

    # Citation count is only updated when the incoming value is a real number:
    # None means "unknown", not "zero", and must not erase a known count.
    incoming_citations = paper.get("citation_count")

    if incoming_citations is not None:
        row.citation_count = max(coerce_int(incoming_citations, 0), 0)

    row.authors = better(_authors_text(paper.get("authors")), row.authors)
    row.doi = better(paper.get("doi"), row.doi)
    row.url = better(paper.get("url"), row.url)
    row.source = paper.get("source") or row.source or config.DEFAULT_PROVIDER

    # Score 0 means "not scored yet" (provider results carry 0).  A real score
    # is written; an unscored pass never wipes the score already stored.
    incoming_score = coerce_int(paper.get("research_score"), 0)

    if incoming_score > 0:
        row.research_score = incoming_score
    elif row.research_score is None:
        row.research_score = 0

    concepts = paper.get("concepts")

    if concepts:
        row.concepts = concepts_to_json(concepts)
    elif not row.concepts:
        row.concepts = concepts_to_json([])

    if keyword:
        row.keyword = keyword

    return row


def save_papers(papers, keyword=None, session=None):
    """Upsert canonical papers, returning ``(inserted, updated)`` counts.

    De-duplicates within the incoming batch as well as against the database,
    so one call can never insert the same paper twice.  Nothing is ever
    deleted.
    """

    own_session = session is None
    db = session or SessionLocal()

    inserted = 0
    updated = 0

    try:
        seen = set()

        for paper in papers or []:

            if not isinstance(paper, dict):
                continue

            paper_id = paper.get("paper_id")

            if not paper_id or paper_id in seen:
                continue

            seen.add(paper_id)

            row = db.query(Paper).filter(Paper.paper_id == paper_id).first()

            if row is None:
                row = Paper(paper_id=paper_id)
                _apply(row, paper, keyword)
                db.add(row)
                inserted += 1
            else:
                _apply(row, paper, keyword)
                updated += 1

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        if own_session:
            db.close()

    return inserted, updated


def load_papers(session=None, keyword=None, source=None, limit=None):
    """Load stored papers as canonical dicts, oldest publication year first."""

    own_session = session is None
    db = session or SessionLocal()

    try:
        query = db.query(Paper)

        if keyword:
            query = query.filter(Paper.keyword == keyword)

        if source:
            query = query.filter(Paper.source == source)

        query = query.order_by(Paper.year.asc(), Paper.id.asc())

        if limit:
            query = query.limit(int(limit))

        return [row.to_dict() for row in query.all()]

    finally:
        if own_session:
            db.close()


def count_papers(session=None):
    own_session = session is None
    db = session or SessionLocal()

    try:
        return db.query(Paper).count()
    finally:
        if own_session:
            db.close()


__all__ = [
    "DATABASE_URL",
    "engine",
    "SessionLocal",
    "Base",
    "Paper",
    "PAPER_FIELDS",
    "init_database",
    "migrate_database",
    "save_papers",
    "load_papers",
    "count_papers",
]
