from sources.openalex import search as openalex_search
from research_ranker import rank_papers


SOURCE = "openalex"



def collect_papers(keyword):


    if SOURCE == "openalex":

        papers = openalex_search(
            keyword
        )


        return rank_papers(
            papers,
            keyword
        )



    # بعداً Semantic Scholar را اضافه می‌کنیم
    #
    # elif SOURCE == "semantic":
    #
    #     papers = semantic_search(keyword)
    #
    #     return rank_papers(
    #         papers,
    #         keyword
    #     )



    return []