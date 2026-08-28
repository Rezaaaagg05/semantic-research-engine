from research_service import collect_papers


papers = collect_papers(
    "option portfolio risk management"
)


print(
    "TOTAL:",
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
