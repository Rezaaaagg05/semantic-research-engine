from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text


DATABASE_URL = "sqlite:///./papers.db"


engine = create_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False
    }
)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


Base = declarative_base()


class Paper(Base):

    __tablename__ = "papers"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    paper_id = Column(
        String,
        unique=True,
        index=True
    )

    title = Column(
        String
    )

    abstract = Column(
        Text
    )

    year = Column(
        Integer
    )

    citation_count = Column(
        Integer,
        default=0
    )

    authors = Column(
        Text
    )

    keyword = Column(
        String
    )

    research_score = Column(
        Integer,
        default=0
    )

    concepts = Column(
        Text
    )


# ساخت جدول اگر وجود نداشته باشد
Base.metadata.create_all(
    bind=engine
)


# --------------------------------------------------
# Migration ساده برای دیتابیس قبلی
# --------------------------------------------------

def migrate_database():

    inspector = inspect(engine)

    if "papers" not in inspector.get_table_names():
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("papers")
    }

    with engine.begin() as connection:

        if "research_score" not in columns:

            connection.execute(
                text(
                    """
                    ALTER TABLE papers
                    ADD COLUMN research_score INTEGER DEFAULT 0
                    """
                )
            )

        if "concepts" not in columns:

            connection.execute(
                text(
                    """
                    ALTER TABLE papers
                    ADD COLUMN concepts TEXT
                    """
                )
            )


migrate_database()