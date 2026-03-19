import feedparser
import requests


def search_arxiv(query: str, lang: str = "en", limit: int = 5) -> list[str]:
    query_encoded = requests.utils.quote(query)
    url = f"http://export.arxiv.org/api/query?search_query=all:{query_encoded}&start=0&max_results={limit}"
    try:
        feed = feedparser.parse(url)
        return [f"{entry.title.strip()}\n{entry.summary.strip()}" for entry in feed.entries]
    except Exception as exc:
        raise RuntimeError(f"Error searching arXiv: {exc}") from exc


def arxiv_search(*args, **kwargs):
    return search_arxiv(*args, **kwargs)


def search_crossref(query: str, lang: str = "en", limit: int = 5) -> list[str]:
    url = "https://api.crossref.org/works"
    params = {"query": query, "rows": limit}

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
        return [
            f"{item.get('title', [''])[0]}\n{item.get('abstract', '')}"
            for item in items
            if item.get("title")
        ]
    except Exception as exc:
        raise RuntimeError(f"Error searching CrossRef: {exc}") from exc


def crossref_search(*args, **kwargs):
    return search_crossref(*args, **kwargs)


def search_openalex(query: str, limit: int = 5) -> list[str]:
    url = "https://api.openalex.org/works"
    params = {"search": query, "per-page": limit}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        items = response.json().get("results", [])
        result = []
        for item in items:
            title = item.get("display_name", "")
            abstract_dict = item.get("abstract_inverted_index")
            abstract = ""
            if abstract_dict:
                tokens = sorted(
                    ((position, word) for word, positions in abstract_dict.items() for position in positions),
                    key=lambda token: token[0],
                )
                abstract = " ".join(word for _, word in tokens)
            result.append(f"{title}\n{abstract}")
        return result
    except Exception as exc:
        raise RuntimeError(f"Error searching OpenAlex: {exc}") from exc


def openalex_search(*args, **kwargs):
    return search_openalex(*args, **kwargs)


def _call_optional_lang(search_fn, query: str, lang: str) -> list[str]:
    try:
        return search_fn(query, lang=lang)
    except TypeError as exc:
        if "unexpected keyword argument 'lang'" not in str(exc):
            raise
        return search_fn(query)


def aggregated_search(query: str, source: str = "all", lang: str = "en") -> list[str]:
    results: list[str] = []

    if source in ("arxiv", "all"):
        try:
            results += _call_optional_lang(arxiv_search, query, lang)
        except Exception as exc:
            print(f"Search error in arXiv: {exc}")

    if source in ("crossref", "all"):
        try:
            results += _call_optional_lang(crossref_search, query, lang)
        except Exception as exc:
            print(f"Search error in CrossRef: {exc}")

    if source in ("openalex", "all"):
        try:
            results += openalex_search(query)
        except Exception as exc:
            print(f"Search error in OpenAlex: {exc}")

    return results
