import json
from collections import Counter, defaultdict

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from database import (
    SessionLocal,
    Paper
)

from provider import collect_papers


app = FastAPI()


templates = Jinja2Templates(
    directory="templates"
)


# --------------------------------------------------
# HOME
# --------------------------------------------------

@app.get("/")
def home(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "papers": []
        }
    )


# --------------------------------------------------
# SEARCH
# --------------------------------------------------

@app.get("/search")
def search(
    request: Request,
    keyword: str
):

    papers = collect_papers(
        keyword
    )

    db = SessionLocal()

    try:

        for paper in papers:

            paper_id = paper.get(
                "paper_id"
            )

            if not paper_id:
                continue

            existing = (
                db.query(Paper)
                .filter(
                    Paper.paper_id == paper_id
                )
                .first()
            )

            concepts_json = json.dumps(
                paper.get(
                    "concepts",
                    []
                ),
                ensure_ascii=False
            )

            authors_text = ", ".join(
                paper.get(
                    "authors",
                    []
                )
            )

            if existing:

                existing.title = paper.get(
                    "title"
                )

                existing.abstract = paper.get(
                    "abstract"
                )

                existing.year = paper.get(
                    "year"
                )

                existing.citation_count = (
                    paper.get(
                        "citation_count",
                        0
                    )
                    or 0
                )

                existing.authors = (
                    authors_text
                )

                existing.keyword = keyword

                existing.research_score = (
                    paper.get(
                        "research_score",
                        0
                    )
                )

                existing.concepts = (
                    concepts_json
                )

            else:

                new_paper = Paper(

                    paper_id=paper_id,

                    title=paper.get(
                        "title"
                    ),

                    abstract=paper.get(
                        "abstract"
                    ),

                    year=paper.get(
                        "year"
                    ),

                    citation_count=(
                        paper.get(
                            "citation_count",
                            0
                        )
                        or 0
                    ),

                    authors=authors_text,

                    keyword=keyword,

                    research_score=(
                        paper.get(
                            "research_score",
                            0
                        )
                    ),

                    concepts=concepts_json
                )

                db.add(
                    new_paper
                )

        db.commit()

    finally:

        db.close()

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "papers": papers,
            "keyword": keyword
        }
    )


# --------------------------------------------------
# DASHBOARD
# --------------------------------------------------

@app.get("/dashboard")
def dashboard(
    request: Request
):

    db = SessionLocal()

    try:

        papers = (
            db.query(Paper)
            .order_by(
                Paper.year.asc()
            )
            .all()
        )

    finally:

        db.close()


    # ----------------------------------------------
    # تعداد کل
    # ----------------------------------------------

    total = len(
        papers
    )


    # ----------------------------------------------
    # تعداد مقالات بر اساس سال
    # ----------------------------------------------

    yearly_counter = Counter()

    for paper in papers:

        if paper.year:

            yearly_counter[
                paper.year
            ] += 1


    yearly_count = [
        {
            "year": year,
            "count": yearly_counter[year]
        }

        for year in sorted(
            yearly_counter.keys()
        )
    ]


    # ----------------------------------------------
    # Concept های هر سال
    # ----------------------------------------------

    concepts_by_year = defaultdict(
        Counter
    )

    for paper in papers:

        if not paper.year:
            continue

        concepts = []

        if paper.concepts:

            try:

                concepts = json.loads(
                    paper.concepts
                )

            except (
                json.JSONDecodeError,
                TypeError
            ):

                concepts = []

        for concept in concepts:

            if concept:

                concepts_by_year[
                    paper.year
                ][concept] += 1


    # ----------------------------------------------
    # Topic های غالب هر سال
    # ----------------------------------------------

    topics_by_year = []

    for year in sorted(
        concepts_by_year.keys()
    ):

        counter = concepts_by_year[
            year
        ]

        top_topics = [
            {
                "name": name,
                "count": count
            }

            for name, count in counter.most_common(
                5
            )
        ]

        topics_by_year.append(
            {
                "year": year,
                "topics": top_topics
            }
        )


    # ----------------------------------------------
    # Trend کلی
    # ----------------------------------------------

    global_concepts = Counter()

    for counter in concepts_by_year.values():

        global_concepts.update(
            counter
        )


    top_global_topics = [
        {
            "name": name,
            "count": count
        }

        for name, count in global_concepts.most_common(
            10
        )
    ]


    # ----------------------------------------------
    # Score جداگانه
    # ----------------------------------------------

    score_sorted = sorted(
        papers,
        key=lambda paper: (
            paper.research_score or 0
        ),
        reverse=True
    )


    top_scored_papers = score_sorted[:10]


    # ----------------------------------------------
    # Citation جداگانه
    # ----------------------------------------------

    citation_sorted = sorted(
        papers,
        key=lambda paper: (
            paper.citation_count or 0
        ),
        reverse=True
    )


    top_cited_papers = citation_sorted[:10]


    # ----------------------------------------------
    # Template
    # ----------------------------------------------

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "request": request,
            "papers": papers,
            "total": total,
            "yearly_count": yearly_count,
            "topics_by_year": topics_by_year,
            "top_global_topics": top_global_topics,
            "top_scored_papers": top_scored_papers,
            "top_cited_papers": top_cited_papers
        }
    )