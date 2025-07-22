import feedparser
import requests


# === arXiv ===
def search_arxiv(query: str, lang: str = "en", limit: int = 5) -> list[str]:
    query_encoded = requests.utils.quote(query)
    url = f"http://export.arxiv.org/api/query?search_query=all:{query_encoded}&start=0&max_results={limit}"
    try:
        feed = feedparser.parse(url)
        return [f"{entry.title.strip()}\n{entry.summary.strip()}" for entry in feed.entries]
    except Exception as e:
        raise RuntimeError(f"Ошибка поиска в arXiv: {e}")


# === CrossRef ===
def search_crossref(query: str, lang: str = "en", limit: int = 5) -> list[str]:
    url = "https://api.crossref.org/works"
    params = {"query": query, "rows": limit}

    # if lang:
    #     params["filter"] = f"language:{lang}"

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
        return [
            f"{item.get('title', [''])[0]}\n{item.get('abstract', '')}"
            for item in items
            if item.get("title")
        ]
    except Exception as e:
        raise RuntimeError(f"Ошибка поиска в CrossRef: {e}")


# === OpenAlex ===
def search_openalex(query: str, limit: int = 5) -> list[str]:
    url = "https://api.openalex.org/works"
    params = {"search": query, "per-page": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("results", [])
        result = []
        for item in items:
            title = item.get("display_name", "")
            abstract_dict = item.get("abstract_inverted_index")
            abstract = ""
            if abstract_dict:
                # Распаковываем inverted_index
                tokens = sorted(
                    ((pos, word) for word, positions in abstract_dict.items() for pos in positions),
                    key=lambda x: x[0],
                )
                abstract = " ".join(word for _, word in tokens)
            result.append(f"{title}\n{abstract}")
        return result
    except Exception as e:
        raise RuntimeError(f"Ошибка поиска в OpenAlex: {e}")


# === Aggregator ===
def aggregated_search(query: str, source: str = "all", lang: str = "en") -> list[str]:
    """
    Выполняет агрегированный поиск по внешним источникам.
    Аргументы:
        - query: строка запроса
        - source: arxiv | crossref | all
        - lang: язык (по умолчанию 'en')
    """
    results = []

    if source in ("arxiv", "all"):
        try:
            results += search_arxiv(query, lang=lang)
        except Exception as e:
            print(f"⚠️ Ошибка поиска в arXiv: {str(e)}")

    if source in ("crossref", "all"):
        try:
            results += search_crossref(query, lang=lang)
        except Exception as e:
            print(f"⚠️ Ошибка поиска в Crossref: {str(e)}")

    return results
