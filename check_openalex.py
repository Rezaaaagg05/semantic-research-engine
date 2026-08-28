import requests


url = "https://api.openalex.org/works"


params = {

    "search": "portfolio risk management",

    "per-page": 5

}


response = requests.get(
    url,
    params=params
)


print(response.url)

print(
    response.status_code
)


for item in response.json()["results"]:

    print("----------------")
    print(
        item["title"]
    )

    print(
        item.get("publication_year")
    )

    print(
        item.get("concepts", [])[:3]
    )