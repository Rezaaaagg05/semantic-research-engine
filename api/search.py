import requests
import time


SEARCH_URL = (
    "https://api.semanticscholar.org/"
    "graph/v1/paper/search"
)


HEADERS = {
    "User-Agent": "SemanticResearchEngine/1.0 Academic Research"
}


last_request_time = 0



def search_paper_ids(keyword, limit=5):

    global last_request_time


    wait = 10 - (time.time() - last_request_time)

    if wait > 0:
        time.sleep(wait)



    params = {

        "query": keyword,

        "limit": limit,

        "fields": "paperId,title,year,citationCount"

    }



    for attempt in range(3):

        response = requests.get(

            SEARCH_URL,

            params=params,

            headers=HEADERS,

            timeout=30

        )


        last_request_time = time.time()



        print(
            "SEARCH STATUS:",
            response.status_code
        )



        if response.status_code == 200:

            data = response.json()


            return [
                paper["paperId"]
                for paper in data.get(
                    "data",
                    []
                )
            ]



        if response.status_code == 429:

            wait_time = 30 * (attempt + 1)

            print(
                f"Rate limit. Waiting {wait_time}s"
            )

            time.sleep(wait_time)



        else:

            print(response.text)

            return []



    return []