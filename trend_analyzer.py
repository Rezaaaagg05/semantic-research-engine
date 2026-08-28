from collections import defaultdict, Counter



def analyze_trends(papers):

    yearly_count = defaultdict(int)

    topics_by_year = defaultdict(list)



    for paper in papers:

        year = paper.get("year")


        if not year:
            continue


        year = str(year)


        yearly_count[year] += 1


        for concept in paper.get(
            "concepts",
            []
        ):

            topics_by_year[year].append(
                concept
            )



    result_topics = {}


    for year, topics in topics_by_year.items():

        result_topics[year] = Counter(
            topics
        ).most_common(5)



    return {

        "yearly_count":
            dict(yearly_count),

        "topics_by_year":
            result_topics
    }