import requests
import time


BATCH_URL = (
    "https://api.semanticscholar.org/"
    "graph/v1/paper/batch"
)



def get_papers_details(
    paper_ids
):


    if not paper_ids:
        return []


    time.sleep(2)


    params = {

        "fields":
        "title,abstract,authors,year,citationCount,venue"

    }



    payload = {

        "ids": paper_ids

    }



    response = requests.post(

        BATCH_URL,

        params=params,

        json=payload

    )


    print(
        "BATCH STATUS:",
        response.status_code
    )


    if response.status_code != 200:

        print(response.text)

        return []



    return response.json()