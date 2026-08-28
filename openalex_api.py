import requests
import time


BASE_URL = "https://api.openalex.org/works"


def search_papers(keyword, limit=10):

    params = {
        "search": keyword,
        "per-page": limit
    }


    response = requests.get(
        BASE_URL,
        params=params,
        timeout=30
    )


    print(
        "OPENALEX STATUS:",
        response.status_code
    )


    if response.status_code != 200:
        print(response.text)
        return []


    data = response.json()


    papers = []


    for item in data.get("results", []):

        authors = []

        for author in item.get("authorships", []):
            name = author.get("author", {}).get("display_name")

            if name:
                authors.append(name)



        papers.append({

            "paperId": item.get("id"),

            "title": item.get("title"),

            "abstract": item.get("abstract_inverted_index"),

            "year": item.get("publication_year"),

            "citationCount": item.get("cited_by_count"),

            "authors": [
                {
                    "name": a
                }
                for a in authors
            ]

        })


    return papers