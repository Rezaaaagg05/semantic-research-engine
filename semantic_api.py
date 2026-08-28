import requests
import time


BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


last_request_time = 0


HEADERS = {
    "User-Agent": "SemanticResearchEngine/1.0 Academic Research"
}



def search_papers(keyword):

    global last_request_time


    wait_time = 8 - (time.time() - last_request_time)

    if wait_time > 0:
        time.sleep(wait_time)


    params = {

        "query": keyword,

        "limit": 5,

        "fields":
        "title,abstract,year,authors,citationCount,venue,publicationDate"

    }


    for attempt in range(3):

        response = requests.get(

            BASE_URL,

            params=params,

            headers=HEADERS,

            timeout=30

        )


        last_request_time = time.time()



        if response.status_code == 200:

            return response.json().get(
                "data",
                []
            )



        elif response.status_code == 429:


            wait = 30 * (attempt + 1)

            print(
                f"Rate limited. Waiting {wait} seconds..."
            )

            time.sleep(wait)



        else:

            print(
                response.text
            )

            return []



    return []