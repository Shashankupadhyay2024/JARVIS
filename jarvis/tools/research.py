"""Academic research: Google Scholar (via your Chrome) + OpenAlex enrichment, credibility scoring,
per-paper summaries, a critical synthesis, and a cited Word report.

Ported from Atlas Research Engine (local.py) and upgraded:
- persistent Chrome profile so CAPTCHAs are rare; waits for you to solve one instead of failing
- real citation counts, DOIs, full abstracts and retraction status from OpenAlex
- credibility 0-100 from citations/year, venue, peer-review signals, recency and content depth
- keeps paging until it has EXACTLY the number of papers you asked for at/above the threshold
"""
import datetime
import difflib
import io
import json
import math
import re
import time
from pathlib import Path

import requests

from .. import config, ui
from ..llm import router
from .registry import tool

OPENALEX = "https://api.openalex.org/works"
OA_SELECT = ("id,doi,display_name,publication_year,cited_by_count,primary_location,best_oa_location,"
             "authorships,abstract_inverted_index,is_retracted,type,open_access")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
QUALITY_VENUES = ["nature", "science", "lancet", "jama", "bmj", "new england", "cell", "pnas", "ieee", "acm",
                  "springer", "elsevier", "wiley", "plos", "frontiers", "bmc", "jmir", "mdpi", "sage", "taylor",
                  "oxford", "cambridge", "journal", "proceedings", "conference", "annals", "review", "american"]
NOW_YEAR = datetime.date.today().year


# ====================================================================== helpers
def _norm(t):
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def _similar(a, b):
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _abstract(inv):
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))


def _oa_params(extra):
    p = dict(extra, select=OA_SELECT)
    if config.CONTACT_EMAIL:
        p["mailto"] = config.CONTACT_EMAIL
    return p


def _from_openalex(w):
    src = ((w.get("primary_location") or {}).get("source") or {})
    best = w.get("best_oa_location") or {}
    names = [a["author"]["display_name"] for a in (w.get("authorships") or []) if a.get("author")]
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    return {
        "title": w.get("display_name") or "",
        "authors_list": names,
        "authors": ", ".join(names[:6]) + (" et al." if len(names) > 6 else ""),
        "year": w.get("publication_year"),
        "venue": src.get("display_name") or "",
        "venue_type": src.get("type") or "",
        "publisher": src.get("host_organization_name") or "",
        "in_doaj": bool(src.get("is_in_doaj")),
        "abstract": _abstract(w.get("abstract_inverted_index")),
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else (w.get("id") or ""),
        "pdf_url": best.get("pdf_url") or "",
        "citations": w.get("cited_by_count") or 0,
        "is_retracted": bool(w.get("is_retracted")),
        "work_type": w.get("type") or "",
        "openalex_id": w.get("id"),
    }


def openalex_lookup(title):
    try:
        r = requests.get(OPENALEX, params=_oa_params({"search": title[:250], "per-page": 3}), timeout=20)
        r.raise_for_status()
        for w in r.json().get("results", []):
            if _similar(w.get("display_name"), title) >= 0.85:
                return _from_openalex(w)
    except Exception:
        pass
    return None


def openalex_search(query, page=1, per_page=25):
    r = requests.get(OPENALEX, params=_oa_params({
        "search": query, "per-page": per_page, "page": page,
        "filter": "has_abstract:true,is_retracted:false,type:article|review|preprint|book-chapter"}), timeout=30)
    r.raise_for_status()
    return [_from_openalex(w) for w in r.json().get("results", [])]


# ====================================================================== Google Scholar
class Scholar:
    """Drives your real Chrome (visible) so Google treats it like a person. Solve a CAPTCHA if one appears."""

    def __init__(self):
        from selenium import webdriver
        opts = webdriver.ChromeOptions()
        config.CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
        opts.add_argument(f"--user-data-dir={config.CHROME_PROFILE}")
        opts.add_argument("--window-size=1100,900")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        self.driver = webdriver.Chrome(options=opts)
        self.warned = False

    def _blocked(self):
        src = self.driver.page_source.lower()
        return "gs_captcha" in src or "unusual traffic" in src or "recaptcha" in src or "not a robot" in src

    def page(self, query, start=0):
        from selenium.webdriver.common.by import By
        from urllib.parse import quote
        self.driver.get(f"https://scholar.google.com/scholar?hl=en&q={quote(query)}&start={start}")
        deadline = time.time() + 240
        while time.time() < deadline:
            items = self.driver.find_elements(By.CSS_SELECTOR, "div.gs_r.gs_or.gs_scl, div.gs_ri")
            if items:
                break
            if self._blocked() and not self.warned:
                ui.say("Google Scholar wants a CAPTCHA. Please solve it in the Chrome window; I'll continue automatically.")
                self.warned = True
            elif not self._blocked() and "did not match any articles" in self.driver.page_source:
                return []
            time.sleep(2)
        else:
            raise RuntimeError("Google Scholar blocked the search (CAPTCHA not solved in time)")
        time.sleep(1.0)
        out = []
        for res in self.driver.find_elements(By.CSS_SELECTOR, "div.gs_r.gs_or.gs_scl"):
            try:
                out.append(self._parse(res, By))
            except Exception:
                continue
        return [p for p in out if p and p["title"]]

    @staticmethod
    def _parse(res, By):
        def first(sel, attr=None):
            els = res.find_elements(By.CSS_SELECTOR, sel)
            if not els:
                return ""
            return els[0].get_attribute(attr) if attr else els[0].text

        title = re.sub(r"^\[(PDF|HTML|BOOK|B|CITATION|C)\]\s*", "", first("h3.gs_rt")).strip()
        link = first("h3.gs_rt a", "href")
        pdf = first("div.gs_or_ggsm a", "href")
        meta = first("div.gs_a")
        parts = [p.strip() for p in meta.split(" - ")]
        authors = parts[0] if parts else ""
        mid = parts[1] if len(parts) > 1 else ""
        ym = re.search(r"\b(19|20)\d{2}\b", mid)
        venue = re.sub(r",?\s*\b(19|20)\d{2}\b", "", mid).strip(" ,…")
        cited = 0
        for a in res.find_elements(By.CSS_SELECTOR, "div.gs_fl a"):
            m = re.match(r"Cited by (\d+)", a.text)
            if m:
                cited = int(m.group(1))
        return {"title": title, "authors": authors.replace("…", "").strip(" ,"), "authors_list": [],
                "year": int(ym.group(0)) if ym else None, "venue": venue, "venue_type": "", "publisher": parts[2] if len(parts) > 2 else "",
                "abstract": first("div.gs_rs"), "url": link, "pdf_url": pdf, "doi": "", "citations": cited,
                "is_retracted": False, "in_doaj": False, "scholar_link": link, "source": "Google Scholar"}

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass


def enrich(p):
    """Merge OpenAlex metadata (DOI, full abstract, citation count, venue type, retraction)."""
    oa = openalex_lookup(p["title"])
    if not oa:
        return p
    merged = dict(p)
    for k, v in oa.items():
        if k == "citations":
            merged[k] = max(p.get("citations") or 0, v or 0)
        elif k == "abstract":
            merged[k] = v if len(v or "") > len(p.get("abstract") or "") else p.get("abstract", "")
        elif k in ("url",) and p.get("url"):
            merged["doi_url"] = v
        elif v and not p.get(k):
            merged[k] = v
        elif k in ("authors_list", "is_retracted", "venue_type", "in_doaj", "openalex_id"):
            merged[k] = v
    if oa.get("authors_list"):
        merged["authors"] = oa["authors"]
    return merged


# ====================================================================== credibility
def credibility(p):
    """0-100. Citations 35 · venue 25 · verification 15 · recency 15 · content 10. Retracted = 0."""
    if p.get("is_retracted"):
        return 0, {"retracted": True}
    b = {}
    cites = p.get("citations") or 0
    age = max(1, NOW_YEAR - (p.get("year") or NOW_YEAR) + 1)
    cpy = cites / age
    b["citations"] = round(min(35.0, 11 * math.log10(1 + cites) + 9 * math.log10(1 + cpy)), 1)

    venue = f"{p.get('venue', '')} {p.get('publisher', '')}".lower()
    vt = (p.get("venue_type") or "").lower()
    v = {"journal": 17, "conference": 15, "book series": 12, "ebook platform": 10, "repository": 6}.get(vt, 0)
    if any(q in venue for q in QUALITY_VENUES):
        v += 8 if vt else 16
    if p.get("in_doaj"):
        v += 3
    if "arxiv" in venue or "preprint" in (p.get("work_type") or ""):
        v = min(v, 8)  # not peer reviewed
    b["venue"] = min(25, v)

    ver = 0
    ver += 8 if p.get("doi") else 0
    ver += 3 if (len(p.get("authors_list") or []) > 1 or "," in (p.get("authors") or "")) else 0
    ver += 4 if p.get("pdf_url") else 0
    b["verification"] = ver

    y = p.get("year")
    b["recency"] = 2 if not y else (15 if NOW_YEAR - y <= 3 else 12 if NOW_YEAR - y <= 6 else 8 if NOW_YEAR - y <= 10 else 5 if NOW_YEAR - y <= 15 else 2)

    alen = len(p.get("abstract") or "")
    b["content"] = 10 if alen > 600 else 6 if alen > 200 else 2
    return int(round(min(100, sum(b.values())))), b


# ====================================================================== relevance (critical filter)
def judge_relevance(topic, papers):
    if not papers:
        return {}
    listing = "\n".join(f"{i}. {p['title']} — {(p.get('abstract') or '')[:300]}" for i, p in enumerate(papers))
    try:
        data = router().ask_json(
            f"Research topic: {topic}\n\nRate how directly each paper helps answer the topic (0-10). Be strict: "
            f"tangential papers score <=4.\n\n{listing}\n\nReturn JSON: {{\"scores\": {{\"0\": 8, \"1\": 3, ...}}}}",
            task="relevance", temperature=0.1)
        return {int(k): float(v) for k, v in data.get("scores", {}).items()}
    except Exception:
        return {}


def academic_queries(topic):
    try:
        d = router().ask_json(
            f"Turn this research request into 3 Google Scholar search queries, most precise first, using the "
            f"academic terminology researchers would use.\nRequest: {topic}\nReturn JSON: {{\"queries\": [\"...\"]}}",
            task="queries", temperature=0.2)
        qs = [q for q in d.get("queries", []) if isinstance(q, str) and q.strip()]
        return qs[:3] or [topic]
    except Exception:
        return [topic]


# ====================================================================== search pipeline
def find_papers(topic, count=8, min_cred=config.MIN_CREDIBILITY, use_scholar=True):
    queries = academic_queries(topic)
    ui.status(f"Search queries: {queries}")
    kept, seen, examined = [], set(), 0

    def consider(batch, q):
        nonlocal examined
        fresh = [p for p in batch if _norm(p["title"]) not in seen]
        for p in fresh:
            seen.add(_norm(p["title"]))
        enriched = []
        for p in fresh:
            if not p.get("doi") and p.get("source") == "Google Scholar":
                p = enrich(p)
            p["credibility"], p["credibility_breakdown"] = credibility(p)
            enriched.append(p)
        examined += len(enriched)
        passing = [p for p in enriched if p["credibility"] >= min_cred]
        rel = judge_relevance(topic, passing)
        for i, p in enumerate(passing):
            p["relevance"] = rel.get(i, 7)
            if p["relevance"] >= 6 and len(kept) < count:
                kept.append(p)
        ui.status(f"Examined {examined} papers, {len(kept)}/{count} meet credibility ≥{min_cred} and are relevant")

    scholar_error = None
    if use_scholar:
        s = None
        try:
            s = Scholar()
            for q in queries:
                for page in range(10):
                    if len(kept) >= count:
                        break
                    batch = s.page(q, start=page * 10)
                    if not batch:
                        break
                    for p in batch:
                        p["source"] = "Google Scholar"
                    consider(batch, q)
                    time.sleep(2.5)
                if len(kept) >= count:
                    break
        except Exception as e:
            scholar_error = str(e)
            ui.say("Google Scholar isn't cooperating, so I'm switching to OpenAlex, which indexes the same journals.")
        finally:
            if s:
                s.close()

    if len(kept) < count:
        for q in queries:
            for page in range(1, 9):
                if len(kept) >= count:
                    break
                try:
                    batch = openalex_search(q, page=page)
                except Exception as e:
                    scholar_error = (scholar_error or "") + f"; OpenAlex: {e}"
                    break
                if not batch:
                    break
                for p in batch:
                    p["source"] = "OpenAlex"
                consider(batch, q)
            if len(kept) >= count:
                break

    kept.sort(key=lambda p: (p["credibility"], p.get("relevance", 0)), reverse=True)
    return kept[:count], {"examined": examined, "queries": queries, "error": scholar_error}


# ====================================================================== reading & summarising
def paper_text(p, limit=24000):
    for url in (p.get("pdf_url"),):
        if not url:
            continue
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.ok and (b"%PDF" in r.content[:1024]):
                from pypdf import PdfReader
                pdf = PdfReader(io.BytesIO(r.content))
                txt = "\n".join((pg.extract_text() or "") for pg in pdf.pages[:10])
                if len(txt) > 1500:
                    return txt[:limit], "full text"
        except Exception:
            pass
    return (p.get("abstract") or ""), "abstract"


def summarize_paper(topic, p):
    text, basis = paper_text(p)
    p["summary_basis"] = basis
    try:
        d = router().ask_json(
            f"Topic: {topic}\nPaper: {p['title']} ({p.get('year')}), {p.get('venue')}\nSource text ({basis}):\n{text}\n\n"
            "Summarise critically for a literature review. JSON keys: summary (4-6 sentences), key_findings (list of 3-5 "
            "specific findings with numbers where given), methods (study design, sample), limitations (honest weaknesses), "
            "relevance (one sentence on how it answers the topic).",
            task="paper_summary", temperature=0.2)
    except Exception as e:
        d = {"summary": (p.get("abstract") or "")[:1200], "key_findings": [], "methods": "", "limitations": f"(summary failed: {e})", "relevance": ""}
    p.update({k: d.get(k, "") for k in ("summary", "key_findings", "methods", "limitations", "relevance_note")})
    p["relevance_note"] = d.get("relevance", "")
    return p


def synthesize(topic, papers):
    digest = "\n\n".join(
        f"[{i}] {p['authors']} ({p.get('year')}). {p['title']}. Credibility {p['credibility']}.\n"
        f"Summary: {p.get('summary')}\nFindings: {json.dumps(p.get('key_findings'))}\nMethods: {p.get('methods')}\n"
        f"Limitations: {p.get('limitations')}" for i, p in enumerate(papers, 1))
    d = router().ask_json(
        f"You are writing an evidence-based research brief on: {topic}\n\nSources:\n{digest}\n\n"
        "Think critically: weigh stronger studies more, note where findings conflict, flag weak evidence, and don't claim "
        "anything the sources don't support. Cite every claim with bracketed source numbers like [1] or [2][4].\n"
        "Return JSON: {\"title\": str, \"executive_summary\": str (1 paragraph), \"sections\": [{\"heading\": str, \"body\": str "
        "(2-4 paragraphs separated by \\n\\n)}] (3-5 thematic sections), \"consensus\": str, \"disagreements\": str, "
        "\"gaps\": str, \"implications\": str, \"conclusion\": str}",
        task="synthesis", temperature=0.3)
    n = len(papers)

    def fix(s):  # drop citations to sources that don't exist
        s = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= n else "", s or "")
        return re.sub(r"[ \t]+([.,;:])", r"\1", s)
    for k in ("executive_summary", "consensus", "disagreements", "gaps", "implications", "conclusion"):
        d[k] = fix(d.get(k, ""))
    d["sections"] = [{"heading": s.get("heading", ""), "body": fix(s.get("body", ""))} for s in d.get("sections", [])]
    return d


# ====================================================================== citations
def _apa_name(full):
    parts = full.replace(".", "").split()
    if len(parts) < 2:
        return full
    return f"{parts[-1]}, " + " ".join(f"{x[0]}." for x in parts[:-1])


def cite(p, style="APA"):
    names = p.get("authors_list") or [a.strip() for a in (p.get("authors") or "Unknown").split(",") if a.strip()]
    year = p.get("year") or "n.d."
    title, venue = p.get("title", "Untitled"), p.get("venue") or "Unpublished"
    link = f"https://doi.org/{p['doi']}" if p.get("doi") else p.get("url", "")
    if style.upper() == "APA":
        if p.get("authors_list"):
            a = [_apa_name(n) for n in names[:20]]
            au = a[0] if len(a) == 1 else ", ".join(a[:-1]) + ", & " + a[-1]
        else:
            au = ", ".join(names)
        return f"{au} ({year}). {title}. {venue}. {link}".strip()
    if style.upper() == "MLA":
        first = names[0].split()
        lead = f"{first[-1]}, {' '.join(first[:-1])}" if len(first) > 1 else names[0]
        au = lead + (", et al" if len(names) > 2 else (f", and {names[1]}" if len(names) == 2 else ""))
        return f"{au}. \"{title}.\" {venue}, {year}. {link}"
    if style.upper() == "CHICAGO":
        return f"{', '.join(names[:3])}{' et al.' if len(names) > 3 else ''}. {year}. \"{title}.\" {venue}. {link}"
    if style.upper() == "BIBTEX":
        key = (_norm(names[0]).split() or ["ref"])[-1] + str(year)
        return (f"@article{{{key},\n  title={{{title}}},\n  author={{{' and '.join(names)}}},\n  journal={{{venue}}},\n"
                f"  year={{{year}}},\n  doi={{{p.get('doi', '')}}},\n  url={{{link}}}\n}}")
    return cite(p, "APA")


# ====================================================================== the tool
@tool("Research a topic in academic literature (Google Scholar first, OpenAlex backup), keep ONLY papers with credibility "
      "≥ min_credibility, read and summarise each, write a critical synthesis, and save a cited Word report. "
      "Returns the report path. Can also build a slide deck from it.",
      topic="what to research", count="exact number of papers to include",
      min_credibility="0-100 threshold", citation_style="APA, MLA, Chicago or BibTeX",
      make_slides="also create a PowerPoint from the findings")
def research(topic, count=8, min_credibility=config.MIN_CREDIBILITY, citation_style="APA", make_slides=False):
    from . import docs
    count, min_credibility = int(count), int(min_credibility)
    ui.say(f"Starting research on {topic}. I'll gather {count} papers with credibility of at least {min_credibility}.")
    papers, stats = find_papers(topic, count, min_credibility)
    if not papers:
        return (f"I couldn't find any papers on '{topic}' scoring ≥{min_credibility} after examining {stats['examined']} "
                f"results ({stats.get('error') or 'no errors'}). Try a lower threshold, e.g. 70, or a broader topic.")
    ui.say(f"Found {len(papers)} qualifying papers. Reading and summarising them now.")
    for i, p in enumerate(papers, 1):
        ui.status(f"Summarising {i}/{len(papers)}: {p['title'][:70]}")
        summarize_paper(topic, p)
    ui.status("Writing the synthesis")
    syn = synthesize(topic, papers)
    for p in papers:
        p["citation"] = cite(p, citation_style)
        p["bibtex"] = cite(p, "BibTeX")
    path = docs.research_report(topic, papers, syn, stats, citation_style, min_credibility)
    json_path = Path(path).with_suffix(".json")
    json_path.write_text(json.dumps({"topic": topic, "synthesis": syn, "papers": papers, "stats": stats}, indent=2, default=str))
    msg = f"Report saved: {path}"
    if len(papers) < count:
        msg += (f"\nNote: only {len(papers)} of the {count} requested papers met credibility ≥{min_credibility} "
                f"after examining {stats['examined']} results. Ask me to rerun with a lower threshold if you need more.")
    if make_slides:
        from .slides import slides_from_research
        msg += "\nSlides saved: " + slides_from_research(json_path)
    return msg
