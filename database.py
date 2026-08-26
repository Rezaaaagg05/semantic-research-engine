from sqlalchemy import create_engine
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
        unique=True
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
        Integer
    )


    authors = Column(
        Text
    )


    keyword = Column(
        String
    )



Base.metadata.create_all(
    bind=engine
)