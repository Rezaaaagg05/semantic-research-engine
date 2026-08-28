from sources.openalex import search
from trend_analyzer import analyze_trends



papers = search(
    "portfolio risk management",
    pages=5,
    per_page=100
)



result = analyze_trends(
    papers
)



print(
    "YEAR COUNTS"
)


for year, count in sorted(
    result["yearly_count"].items()
):

    print(
        year,
        ":",
        count
    )



print("\nTOPICS")


for year, topics in sorted(
    result["topics_by_year"].items()
):

    print("----------------")

    print(
        year
    )


    for topic, count in topics:

        print(
            topic,
            count
        )