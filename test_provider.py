from research_service import collect_papers


papers = collect_papers(
    "portfolio risk management"
)


print(
    "RESULT:",
    len(papers)
)


for p in papers[:3]:

    print("----------------")

    print(
        p["title"]
    )

    print(
        p["year"]
    )

    print(
        p["citation_count"]
    )

    print(
        "SCORE:",
        p["research_score"]
    )