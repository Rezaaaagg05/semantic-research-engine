import requests
import time


BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def search_papers(keyword):

    time.sleep(3)

    params = {
        "query": keyword,
        "limit": 5,
        "fields": "title,abstract,authors,year,citationCount"
    }


    response = requests.get(
        BASE_URL,
        params=params
    )


    print("====================")
    print("STATUS CODE:", response.status_code)
    print("RESPONSE:")
    print(response.text[:1000])
    print("====================")



    if response.status_code != 200:
        return []


    data = response.json()


    return data.get("data", [])