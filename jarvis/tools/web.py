"""Free web search (DuckDuckGo) and page reading."""
import requests

from .registry import tool

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}


def search(query, n=6):
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    return list(DDGS().text(query, max_results=n))


@tool("Search the web (DuckDuckGo) for current information: news, prices, facts, docs. Returns titles, URLs, snippets.",
      query="search query")
def web_search(query):
    try:
        res = search(query)
    except Exception as e:
        return f"Search failed: {e}"
    return "\n\n".join(f"{i}. {r.get('title')}\n   {r.get('href')}\n   {r.get('body')}" for i, r in enumerate(res, 1)) or "No results."


def page_text(url, limit=20000):
    from bs4 import BeautifulSoup
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    if "pdf" in r.headers.get("content-type", ""):
        import io
        from pypdf import PdfReader
        pdf = PdfReader(io.BytesIO(r.content))
        return "\n".join((p.extract_text() or "") for p in pdf.pages[:20])[:limit]
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        t.decompose()
    text = "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if len(l.strip()) > 30)
    return text[:limit]


@tool("Read the main text of a web page or online PDF.", url="URL")
def fetch_url(url):
    try:
        return page_text(url, 15000)
    except Exception as e:
        return f"Could not fetch {url}: {e}"
