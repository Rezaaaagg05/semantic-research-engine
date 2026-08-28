from sources.openalex import search



papers = search(
    "portfolio risk management",
    pages=1,
    per_page=5
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
        "YEAR:",
        p["year"]
    )

    print(
        "CITATIONS:",
        p["citation_count"]
    )

    print(
        "CONCEPTS:"
    )

    print(
        p["concepts"][:10]
    )