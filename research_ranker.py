def rank_papers(papers, keyword):

    keyword_words = keyword.lower().split()


    scored = []


    for paper in papers:

        score = 0


        title = (
            paper.get("title") or ""
        ).lower()


        # title matching
        for word in keyword_words:

            if word in title:

                score += 20



        # citation impact

        citations = paper.get(
            "citation_count",
            0
        ) or 0


        if citations > 1000:
            score += 20

        elif citations > 100:
            score += 10



        # recent research

        year = paper.get(
            "year"
        )


        if year and year >= 2020:

            score += 10



        paper["research_score"] = score


        scored.append(
            paper
        )



    return sorted(
        scored,
        key=lambda x: x["research_score"],
        reverse=True
    )
