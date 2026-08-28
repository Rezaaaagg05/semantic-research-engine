from openalex_api import search_papers


papers = search_papers(
    "option portfolio risk management"
)


print(
    "COUNT:",
    len(papers)
)


for p in papers:

    print("----------------")

    print(
        p["title"]
    )

    print(
        p["year"]
    )

    print(
        p["citationCount"]
    )