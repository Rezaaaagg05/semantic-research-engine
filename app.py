from database import SessionLocal, Paper
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from semantic_api import search_papers

from database import (
    SessionLocal,
    Paper
)


app = FastAPI()


templates = Jinja2Templates(
    directory="templates"
)



@app.get("/")
def home(request: Request):

    return templates.TemplateResponse(
        name="index.html",
        request=request,
        context={
            "papers": []
        }
    )




@app.get("/search")
def search(
    request: Request,
    keyword: str
):

    papers = search_papers(keyword)


    db = SessionLocal()


    for p in papers:


        exists = db.query(Paper).filter(
            Paper.paper_id == p["paperId"]
        ).first()



        if not exists:


            authors = ", ".join(
                [
                    a["name"]
                    for a in p.get(
                        "authors",
                        []
                    )
                ]
            )


            new_paper = Paper(

                paper_id=p["paperId"],

                title=p.get(
                    "title"
                ),

                abstract=p.get(
                    "abstract"
                ),

                year=p.get(
                    "year"
                ),

                citation_count=p.get(
                    "citationCount"
                ),

                authors=authors,

                keyword=keyword

            )


            db.add(new_paper)



    db.commit()

    db.close()


@app.get("/dashboard")
def dashboard(request: Request):

    db = SessionLocal()


    papers = db.query(Paper).all()


    total = len(papers)


    db.close()


    return templates.TemplateResponse(

        name="dashboard.html",

        request=request,

        context={

            "papers": papers,

            "total": total

        }

    )

    return templates.TemplateResponse(

        name="index.html",

        request=request,

        context={
            "papers": papers
        }
    )