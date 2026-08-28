import requests


url = "https://api.semanticscholar.org/graph/v1/paper/search"


params = {
    "query": "portfolio risk",
    "limit": 1,
    "fields": "title"
}


headers = {
    "User-Agent": "Mozilla/5.0"
}


r = requests.get(
    url,
    params=params,
    headers=headers
)


print(r.status_code)
print(r.text)