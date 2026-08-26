from api.search import search_paper_ids
from api.papers import get_papers_details



keyword = "option portfolio risk management"



ids = search_paper_ids(
    keyword,
    limit=5
)


print(
    "IDS:",
    ids
)



papers = get_papers_details(
    ids
)


for paper in papers:

    print("----------------")

    print(
        paper.get("title")
    )

    print(
        paper.get("year")
    )