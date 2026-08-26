import requests
import time


SEARCH_URL = (
    "https://api.semanticscholar.org/"
    "graph/v1/paper/search"
)


def search_paper_ids(keyword, limit=10):

    time.sleep(2)

    params = {

        "query": keyword,

        "limit": limit,

        "fields": "paperId"

    }


    response = requests.get(
        SEARCH_URL,
        params=params
    )


    print(
        "SEARCH STATUS:",
        response.status_code
    )


    if response.status_code != 200:
        print(response.text)
        return []


    data = response.json()


    return [
        paper["paperId"]
        for paper in data.get(
            "data",
            []
        )
    ]