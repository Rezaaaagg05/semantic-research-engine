from sources.openalex import search as openalex_search


SOURCE = "openalex"


def calculate_research_score(paper):

    citations = paper.get(
        "citation_count",
        0
    ) or 0

    concepts = len(
        paper.get(
            "concepts",
            []
        )
    )

    score = 0

    # Citation component
    if citations >= 1000:
        score += 50

    elif citations >= 500:
        score += 45

    elif citations >= 200:
        score += 35

    elif citations >= 100:
        score += 30

    elif citations >= 50:
        score += 20

    elif citations >= 10:
        score += 10

    else:
        score += 5

    # Concept richness
    if concepts >= 8:
        score += 20

    elif concepts >= 5:
        score += 15

    elif concepts >= 3:
        score += 10

    else:
        score += 5

    # гон score
    return min(
        score,
        100
    )


def rank_papers(
    papers,
    keyword
):

    ranked = []

    for paper in papers:

        paper["keyword"] = keyword

        paper["research_score"] = (
            calculate_research_score(
                paper
            )
        )

        ranked.append(
            paper
        )

    ranked.sort(
        key=lambda paper: (
            paper.get(
                "research_score",
                0
            ),
            paper.get(
                "citation_count",
                0
            )
        ),
        reverse=True
    )

    return ranked


def collect_papers(keyword):

    if SOURCE == "openalex":

        papers = openalex_search(
            keyword
        )

        return rank_papers(
            papers,
            keyword
        )

    return []