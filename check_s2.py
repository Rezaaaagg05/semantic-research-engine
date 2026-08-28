import requests

url = "https://api.semanticscholar.org/graph/v1/paper/search"

headers = {
    "User-Agent": "AcademicResearchProject/1.0"
}

params = {
    "query": "finance",
    "limit": 1,
    "fields": "title"
}

r = requests.get(
    url,
    headers=headers,
    params=params
)

print("STATUS:", r.status_code)
print("HEADERS:")
print(r.headers)
print("BODY:")
print(r.text)