import requests
import time


BASE_URL = "https://api.openalex.org/works"


HEADERS = {
    "User-Agent": "SemanticResearchEngine/1.0"
}


def reconstruct_abstract(inverted_index):

    if not inverted_index:
        return None

    words = []

    for word, positions in inverted_index.items():

        for position in positions:

            words.append(
                (position, word)
            )

    words.sort(
        key=lambda x: x[0]
    )

    return " ".join(
        word
        for _, word in words
    )


def extract_authors(item):

    authors = []

    for authorship in item.get(
        "authorships",
        []
    ):

        author = authorship.get(
            "author",
            {}
        )

        name = author.get(
            "display_name"
        )

        if name:
            authors.append(name)

    return authors


def extract_concepts(item):

    concepts = []

    for concept in item.get(
        "concepts",
        []
    ):

        name = concept.get(
            "display_name"
        )

        if name:
            concepts.append(name)

    return concepts


def search(
    keyword,
    pages=5,
    per_page=100
):

    all_papers = []

    for page in range(
        1,
        pages + 1
    ):

        params = {

            "search": keyword,

            "page": page,

            "per-page": per_page,

            "sort": "relevance_score:desc",

            "select": (
                "id,"
                "title,"
                "abstract_inverted_index,"
                "publication_year,"
                "cited_by_count,"
                "authorships,"
                "concepts,"
                "doi"
            )
        }

        try:

            response = requests.get(
                BASE_URL,
                params=params,
                headers=HEADERS,
                timeout=30
            )

        except requests.RequestException as error:

            print(
                "OpenAlex request error:",
                error
            )

            break

        print(
            "OPENALEX:",
            response.status_code,
            "PAGE:",
            page
        )

        if response.status_code != 200:

            print(
                response.text
            )

            break

        data = response.json()

        results = data.get(
            "results",
            []
        )

        if not results:
            break

        for item in results:

            paper = {

                "paper_id": item.get(
                    "id"
                ),

                "title": item.get(
                    "title"
                ),

                "abstract": reconstruct_abstract(
                    item.get(
                        "abstract_inverted_index"
                    )
                ),

                "year": item.get(
                    "publication_year"
                ),

                "citation_count": item.get(
                    "cited_by_count",
                    0
                ),

                "authors": extract_authors(
                    item
                ),

                "concepts": extract_concepts(
                    item
                ),

                "doi": item.get(
                    "doi"
                )
            }

            all_papers.append(
                paper
            )

        # کمی فاصله برای رفتار محترمانه‌تر با API
        if page < pages:

            time.sleep(0.3)

    return all_papers