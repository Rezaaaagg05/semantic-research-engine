"""The database layer (database.py).

Two promises are tested here, because breaking either would cost a user data
they cannot get back:

1. Migration is additive.  An old ``papers.db`` opens, gains the new columns, and
   keeps every row.  Nothing is dropped, recreated or deleted -- ever.

2. Saving never clobbers.  A later, sparser response about a paper we already
   know must not erase a citation count or a research score we already have.

Every test runs against a throwaway SQLite file inside a temporary directory
(see conftest), so the real database is untouchable from here.
"""

import json

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

import config
import database
from database import Paper, count_papers, load_papers, migrate_database, save_papers
from models import PAPER_FIELDS


#: The very first release: no score, no concepts, no source, no timestamps.
V1_COLUMNS = (
    "id INTEGER PRIMARY KEY",
    "paper_id VARCHAR",
    "title VARCHAR",
    "abstract TEXT",
    "year INTEGER",
    "citation_count INTEGER",
    "authors TEXT",
    "keyword VARCHAR",
)

#: The second generation: scores and concepts arrived, source did not.
V2_COLUMNS = V1_COLUMNS + (
    "research_score INTEGER DEFAULT 0",
    "concepts TEXT",
)


def legacy_row(index, **overrides):
    row = {
        "paper_id": f"https://openalex.org/W{index}",
        "title": f"Legacy paper {index}",
        "abstract": "An older abstract.",
        "year": 2010 + index,
        "citation_count": 40 + index,
        "authors": "Ada Lovelace, Alan Turing",
        "keyword": "portfolio risk",
    }
    row.update(overrides)

    return row


def column_names(bind):
    return {column["name"] for column in inspect(bind).get_columns("papers")}


def index_names(bind):
    return {index["name"] for index in inspect(bind).get_indexes("papers")}


def all_rows(bind):
    with bind.begin() as connection:
        return connection.execute(text("SELECT * FROM papers")).mappings().all()


class TestSafety:

    def test_the_suite_is_pointed_at_a_temporary_database(self):
        # If this ever fails, stop: the tests below write and delete rows.
        assert "sre_tests_" in str(config.DATABASE_PATH)
        assert str(config.DATABASE_PATH).endswith("test_papers.db")


class TestMigrationFromTheFirstRelease:

    def test_every_missing_column_is_added(self, legacy_database):
        engine = legacy_database(V1_COLUMNS, [legacy_row(1)])

        migrate_database(bind=engine)

        assert column_names(engine) >= {
            "research_score",
            "concepts",
            "source",
            "doi",
            "url",
            "created_at",
            "updated_at",
        }

    def test_the_existing_rows_survive_untouched(self, legacy_database):
        engine = legacy_database(V1_COLUMNS, [legacy_row(1), legacy_row(2), legacy_row(3)])

        migrate_database(bind=engine)

        rows = all_rows(engine)

        assert len(rows) == 3
        assert [row["title"] for row in rows] == [
            "Legacy paper 1",
            "Legacy paper 2",
            "Legacy paper 3",
        ]
        assert [row["citation_count"] for row in rows] == [41, 42, 43]

    def test_legacy_rows_are_attributed_to_openalex(self, legacy_database):
        engine = legacy_database(V1_COLUMNS, [legacy_row(1)])

        applied = migrate_database(bind=engine)

        assert "backfill source=openalex" in applied
        assert all_rows(engine)[0]["source"] == "openalex"

    def test_the_indexes_are_created(self, legacy_database):
        engine = legacy_database(V1_COLUMNS, [legacy_row(1)])

        migrate_database(bind=engine)

        assert index_names(engine) >= {
            "ix_papers_source",
            "ix_papers_year",
            "ix_papers_keyword",
            "ix_papers_research_score",
            "ix_papers_doi",
            "ix_papers_source_paper_id",
        }

    def test_the_orm_can_read_a_migrated_database(self, legacy_database):
        engine = legacy_database(V1_COLUMNS, [legacy_row(1)])
        migrate_database(bind=engine)

        session = sessionmaker(bind=engine)()

        try:
            row = session.query(Paper).one()

            assert row.title == "Legacy paper 1"
            assert row.author_list() == ["Ada Lovelace", "Alan Turing"]
            assert row.concept_list() == []
            assert set(row.to_dict()) == set(PAPER_FIELDS)
        finally:
            session.close()

    def test_new_papers_can_be_saved_into_a_migrated_database(self, legacy_database, make_paper):
        engine = legacy_database(V1_COLUMNS, [legacy_row(1)])
        migrate_database(bind=engine)

        session = sessionmaker(bind=engine)()

        try:
            inserted, updated = save_papers([make_paper(99)], keyword="risk", session=session)

            assert (inserted, updated) == (1, 0)
            assert session.query(Paper).count() == 2
        finally:
            session.close()

    def test_a_legacy_row_is_updated_not_duplicated(self, legacy_database, make_paper):
        engine = legacy_database(V1_COLUMNS, [legacy_row(1)])
        migrate_database(bind=engine)

        session = sessionmaker(bind=engine)()

        try:
            # Same paper_id as the legacy row.
            inserted, updated = save_papers(
                [make_paper(1, title="Refreshed title")], keyword="risk", session=session
            )

            assert (inserted, updated) == (0, 1)
            assert session.query(Paper).count() == 1
            assert session.query(Paper).one().title == "Refreshed title"
        finally:
            session.close()


class TestMigrationFromTheSecondRelease:

    def test_only_the_genuinely_missing_columns_are_added(self, legacy_database):
        engine = legacy_database(
            V2_COLUMNS,
            [legacy_row(1, research_score=63, concepts=json.dumps(["Value at risk"]))],
        )

        applied = migrate_database(bind=engine)

        assert "add column research_score" not in applied
        assert "add column concepts" not in applied
        assert "add column source" in applied

    def test_stored_scores_and_concepts_are_preserved(self, legacy_database):
        engine = legacy_database(
            V2_COLUMNS,
            [legacy_row(1, research_score=63, concepts=json.dumps(["Value at risk"]))],
        )

        migrate_database(bind=engine)

        session = sessionmaker(bind=engine)()

        try:
            row = session.query(Paper).one()

            assert row.research_score == 63
            assert [c["name"] for c in row.concept_list()] == ["Value at risk"]
        finally:
            session.close()


class TestMigrationEdgeCases:

    def test_migration_is_idempotent(self, legacy_database):
        engine = legacy_database(V1_COLUMNS, [legacy_row(1)])

        first = migrate_database(bind=engine)
        second = migrate_database(bind=engine)

        assert first
        assert second == []

    def test_an_empty_legacy_database_migrates_fine(self, legacy_database):
        engine = legacy_database(V1_COLUMNS, [])

        migrate_database(bind=engine)

        assert all_rows(engine) == []
        assert "source" in column_names(engine)

    def test_no_papers_table_is_not_an_error(self, legacy_database):
        engine = legacy_database(("id INTEGER PRIMARY KEY",))

        with engine.begin() as connection:
            connection.execute(text("DROP TABLE papers"))

        assert migrate_database(bind=engine) == []

    def test_duplicate_legacy_rows_keep_their_data_even_if_an_index_cannot_be_built(
        self, legacy_database
    ):
        # An old database can legitimately contain the same paper twice.  The
        # unique index then cannot be created -- and that is the acceptable
        # outcome, because deleting a user's row to make an index fit is not.
        engine = legacy_database(
            V1_COLUMNS,
            [legacy_row(1), legacy_row(1, title="Duplicate of paper 1")],
        )

        applied = migrate_database(bind=engine)

        assert len(all_rows(engine)) == 2
        assert "add column source" in applied
        assert any(entry.startswith("skip index ix_papers_source_paper_id") for entry in applied)

    def test_the_column_additions_survive_a_failed_index(self, legacy_database):
        engine = legacy_database(V1_COLUMNS, [legacy_row(1), legacy_row(1)])

        migrate_database(bind=engine)

        # The failed unique index must not roll back the new columns.
        assert column_names(engine) >= {"source", "doi", "url", "concepts", "research_score"}

    def test_init_database_creates_a_table_from_nothing(self, tmp_path):
        from sqlalchemy import create_engine

        engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")

        try:
            database.init_database(bind=engine)

            assert "papers" in inspect(engine).get_table_names()
            assert column_names(engine) >= set(PAPER_FIELDS)
        finally:
            engine.dispose()


class TestSavePapers:

    def test_new_papers_are_inserted(self, db_session, make_paper):
        inserted, updated = save_papers(
            [make_paper(1), make_paper(2)], keyword="risk", session=db_session
        )

        assert (inserted, updated) == (2, 0)
        assert count_papers(session=db_session) == 2

    def test_saving_the_same_papers_again_updates_them(self, db_session, make_paper):
        save_papers([make_paper(1)], keyword="risk", session=db_session)

        inserted, updated = save_papers([make_paper(1)], keyword="risk", session=db_session)

        assert (inserted, updated) == (0, 1)
        assert count_papers(session=db_session) == 1

    def test_duplicates_inside_one_batch_are_collapsed(self, db_session, make_paper):
        inserted, updated = save_papers(
            [make_paper(1), make_paper(1), make_paper(1)], session=db_session
        )

        assert (inserted, updated) == (1, 0)

    def test_papers_without_an_id_are_skipped(self, db_session, make_paper):
        inserted, updated = save_papers(
            [make_paper(1), make_paper(2, paper_id=None), make_paper(3, paper_id="")],
            session=db_session,
        )

        assert (inserted, updated) == (1, 0)

    def test_non_dict_entries_are_skipped(self, db_session, make_paper):
        inserted, _ = save_papers([make_paper(1), None, "junk", 42], session=db_session)

        assert inserted == 1

    def test_empty_input(self, db_session):
        assert save_papers([], session=db_session) == (0, 0)
        assert save_papers(None, session=db_session) == (0, 0)

    def test_fields_are_stored_in_their_documented_shapes(self, db_session, make_paper):
        save_papers(
            [make_paper(1, authors=["Ada Lovelace", "Alan Turing"])],
            keyword="portfolio risk",
            session=db_session,
        )

        row = db_session.query(Paper).one()

        assert row.authors == "Ada Lovelace, Alan Turing"      # comma-joined text
        assert json.loads(row.concepts)[0]["name"] == "Value at risk"   # JSON array
        assert row.source == "openalex"
        assert row.keyword == "portfolio risk"

    def test_the_keyword_of_the_latest_search_is_recorded(self, db_session, make_paper):
        save_papers([make_paper(1)], keyword="first search", session=db_session)
        save_papers([make_paper(1)], keyword="second search", session=db_session)

        assert db_session.query(Paper).one().keyword == "second search"

    def test_a_paper_with_no_concepts_stores_an_empty_array_not_null(self, db_session, make_paper):
        save_papers([make_paper(1, concepts=[])], session=db_session)

        assert json.loads(db_session.query(Paper).one().concepts) == []


class TestSavingNeverClobbers:

    def test_an_unknown_citation_count_does_not_erase_a_known_one(self, db_session, make_paper):
        # Regression: a sparse re-fetch used to turn 42 citations into 0.
        save_papers([make_paper(1, citation_count=42)], session=db_session)
        save_papers([make_paper(1, citation_count=None)], session=db_session)

        assert db_session.query(Paper).one().citation_count == 42

    def test_a_real_citation_update_is_applied(self, db_session, make_paper):
        save_papers([make_paper(1, citation_count=42)], session=db_session)
        save_papers([make_paper(1, citation_count=57)], session=db_session)

        assert db_session.query(Paper).one().citation_count == 57

    def test_a_citation_count_can_legitimately_drop(self, db_session, make_paper):
        # Providers do revise counts downwards; an explicit number is trusted.
        save_papers([make_paper(1, citation_count=42)], session=db_session)
        save_papers([make_paper(1, citation_count=40)], session=db_session)

        assert db_session.query(Paper).one().citation_count == 40

    def test_a_negative_citation_count_is_floored_at_zero(self, db_session, make_paper):
        save_papers([make_paper(1, citation_count=-5)], session=db_session)

        assert db_session.query(Paper).one().citation_count == 0

    def test_an_unscored_pass_does_not_erase_a_stored_score(self, db_session, make_paper):
        # Regression: provider dicts carry research_score 0, and re-saving one
        # used to wipe the score the pipeline had already computed.
        save_papers([make_paper(1, research_score=71)], session=db_session)
        save_papers([make_paper(1, research_score=0)], session=db_session)

        assert db_session.query(Paper).one().research_score == 71

    def test_a_real_score_update_is_applied(self, db_session, make_paper):
        save_papers([make_paper(1, research_score=71)], session=db_session)
        save_papers([make_paper(1, research_score=64)], session=db_session)

        assert db_session.query(Paper).one().research_score == 64

    def test_a_missing_abstract_does_not_erase_the_one_we_have(self, db_session, make_paper):
        save_papers([make_paper(1, abstract="The full abstract.")], session=db_session)
        save_papers([make_paper(1, abstract=None)], session=db_session)

        assert db_session.query(Paper).one().abstract == "The full abstract."

    def test_missing_concepts_do_not_erase_stored_concepts(self, db_session, make_paper, make_concept):
        save_papers([make_paper(1, concepts=[make_concept("Value at risk")])], session=db_session)
        save_papers([make_paper(1, concepts=[])], session=db_session)

        assert [c["name"] for c in db_session.query(Paper).one().concept_list()] == [
            "Value at risk"
        ]

    def test_a_missing_year_does_not_erase_the_one_we_have(self, db_session, make_paper):
        save_papers([make_paper(1, year=2019)], session=db_session)
        save_papers([make_paper(1, year=None)], session=db_session)

        assert db_session.query(Paper).one().year == 2019

    def test_nothing_is_ever_deleted(self, db_session, make_paper):
        save_papers([make_paper(1), make_paper(2), make_paper(3)], session=db_session)

        # A later search returns only one of them; the other two stay.
        save_papers([make_paper(2)], session=db_session)

        assert count_papers(session=db_session) == 3


class TestLoadPapers:

    def test_returns_canonical_dicts(self, db_session, make_paper):
        save_papers([make_paper(1)], keyword="risk", session=db_session)

        papers = load_papers(session=db_session)

        assert len(papers) == 1
        assert set(papers[0]) == set(PAPER_FIELDS)
        assert papers[0]["authors"] == ["Ada Lovelace"]
        assert papers[0]["concepts"][0]["name"] == "Value at risk"

    def test_ordered_oldest_publication_first(self, db_session, make_paper):
        save_papers(
            [make_paper(1, year=2020), make_paper(2, year=1999), make_paper(3, year=2010)],
            session=db_session,
        )

        assert [paper["year"] for paper in load_papers(session=db_session)] == [1999, 2010, 2020]

    def test_filtering_by_keyword(self, db_session, make_paper):
        save_papers([make_paper(1)], keyword="alpha", session=db_session)
        save_papers([make_paper(2)], keyword="beta", session=db_session)

        papers = load_papers(session=db_session, keyword="beta")

        assert [paper["paper_id"] for paper in papers] == ["https://openalex.org/W2"]

    def test_filtering_by_source(self, db_session, make_paper):
        save_papers(
            [make_paper(1), make_paper(2, source="semanticscholar")], session=db_session
        )

        papers = load_papers(session=db_session, source="semanticscholar")

        assert [paper["source"] for paper in papers] == ["semanticscholar"]

    def test_limit(self, db_session, make_paper):
        save_papers([make_paper(index) for index in range(5)], session=db_session)

        assert len(load_papers(session=db_session, limit=2)) == 2

    def test_an_empty_database_loads_an_empty_list(self, db_session):
        assert load_papers(session=db_session) == []

    def test_a_row_with_nothing_but_an_id_still_converts(self, db_session):
        db_session.add(Paper(paper_id="https://openalex.org/W1"))
        db_session.commit()

        paper = load_papers(session=db_session)[0]

        assert paper["citation_count"] == 0
        assert paper["research_score"] == 0
        assert paper["authors"] == []
        assert paper["concepts"] == []
        assert paper["source"] == config.DEFAULT_PROVIDER

    @pytest.mark.parametrize("stored", [None, "", "not json", "12345", "{}"])
    def test_an_unreadable_concepts_column_does_not_break_loading(self, db_session, stored):
        db_session.add(Paper(paper_id="https://openalex.org/W1", concepts=stored))
        db_session.commit()

        assert load_papers(session=db_session)[0]["concepts"] == []


class TestCountPapers:

    def test_counts_rows(self, db_session, make_paper):
        assert count_papers(session=db_session) == 0

        save_papers([make_paper(1), make_paper(2)], session=db_session)

        assert count_papers(session=db_session) == 2
