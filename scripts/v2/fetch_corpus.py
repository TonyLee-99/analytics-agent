"""Fetch the public City of Chicago document corpus (polite, cached, resumable).

Sources (all public):
  portal  - dataset metadata from data.cityofchicago.org (Socrata views API): the 311 dataset, the
            per-type 311 datasets and the department performance-metric datasets
  web     - chicago.gov department landing pages plus a bounded crawl of their service / info / news
            pages (same department path only, depth 1, capped per department)
  pdf     - budget documents linked from the Office of Budget and Management page

Politeness: descriptive User-Agent, robots.txt honoured (crawl-delay, >= 1.5 s per host regardless),
exponential backoff on 429/5xx with Retry-After, every response cached under data/raw/corpus_cache/
so re-runs make no requests.

    uv run python scripts/v2/fetch_corpus.py           # fetch + write corpus/
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import time
import urllib.robotparser
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "raw" / "corpus_cache"
OUT = ROOT / "corpus"
UA = "analytics-agent-corpus/0.2 (public-records research for an open-source portfolio project; " \
     "https://github.com/TonyLee-99/analytics-agent)"
MIN_DELAY_S = 1.5
PORTAL_TERMS = "City of Chicago Data Portal Terms of Use (https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html)"
WEB_TERMS = "Public information published on chicago.gov by the City of Chicago; reproduced for research with attribution"

DEPT_SEEDS = {
    "311": "https://www.chicago.gov/city/en/depts/311.html",
    "CDOT": "https://www.chicago.gov/city/en/depts/cdot.html",
    "DSS": "https://www.chicago.gov/city/en/depts/streets.html",
    "DWM": "https://www.chicago.gov/city/en/depts/water.html",
    "DOB": "https://www.chicago.gov/city/en/depts/bldgs.html",
    "CDPH": "https://www.chicago.gov/city/en/depts/cdph.html",
    "CACC": "https://www.chicago.gov/city/en/depts/cacc.html",
    "BACP": "https://www.chicago.gov/city/en/depts/bacp.html",
    "OBM": "https://www.chicago.gov/city/en/depts/obm.html",
}
DEPT_PATH = {"311": "/depts/311", "CDOT": "/depts/cdot", "DSS": "/depts/streets", "DWM": "/depts/water",
             "DOB": "/depts/bldgs", "CDPH": "/depts/cdph", "CACC": "/depts/cacc", "BACP": "/depts/bacp",
             "OBM": "/depts/obm"}
PAGES_PER_DEPT = 9
PORTAL_QUERIES = ["311", "Performance Metrics"]
MAX_PDFS = 3


class Fetcher:
    def __init__(self) -> None:
        self.client = httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=60)
        self.last: dict[str, float] = {}
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests = 0
        self.cache_hits = 0

    def _robots(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        host = urlparse(url).netloc
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(f"https://{host}/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self.robots[host] = rp
            except httpx.HTTPError:
                self.robots[host] = None
        return self.robots[host]

    def get(self, url: str) -> tuple[bytes, str] | None:
        key = hashlib.sha256(url.encode()).hexdigest()
        body_p, meta_p = CACHE / f"{key}.bin", CACHE / f"{key}.json"
        if body_p.exists():
            self.cache_hits += 1
            return body_p.read_bytes(), json.loads(meta_p.read_text())["content_type"]
        rp = self._robots(url)
        if rp is not None and not rp.can_fetch(UA, url):
            print(f"  robots.txt disallows {url}")
            return None
        host = urlparse(url).netloc
        delay = max(MIN_DELAY_S, (rp.crawl_delay(UA) or 0) if rp else 0)
        for attempt in range(5):
            wait = self.last.get(host, 0) + delay - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last[host] = time.time()
            try:
                r = self.client.get(url)
                self.requests += 1
            except httpx.HTTPError as e:
                print(f"  {type(e).__name__} on {url}; retry {attempt + 1}")
                time.sleep(2 ** attempt * 3)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("retry-after", "")
                time.sleep(float(ra) if ra.isdigit() else 2 ** attempt * 5)
                continue
            if r.status_code != 200:
                print(f"  HTTP {r.status_code} {url}")
                return None
            CACHE.mkdir(parents=True, exist_ok=True)
            body_p.write_bytes(r.content)
            meta_p.write_text(json.dumps({"url": url, "final_url": str(r.url), "status": r.status_code,
                                          "content_type": r.headers.get("content-type", ""),
                                          "fetched_at": datetime.now(UTC).isoformat()}))
            return r.content, r.headers.get("content-type", "")
        return None

    def fetched_at(self, url: str) -> str:
        key = hashlib.sha256(url.encode()).hexdigest()
        return json.loads((CACHE / f"{key}.json").read_text())["fetched_at"]


def clean(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def slug(s: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n]


def portal_doc(view: dict) -> str:
    lines = [f"Dataset: {view.get('name', '')}", f"Portal ID: {view['id']}",
             f"Category: {view.get('category', '')}", f"Attribution: {view.get('attribution', '')}"]
    if view.get("rowsUpdatedAt"):
        lines.append("Rows last updated: " + datetime.fromtimestamp(view["rowsUpdatedAt"], UTC).strftime("%Y-%m-%d"))
    lines += ["", "Description:", view.get("description", "") or "(none)", "", "Columns:"]
    for c in view.get("columns", []):
        if c.get("fieldName", "").startswith(":"):
            continue
        desc = (c.get("description") or "").strip()
        lines.append(f"- {c.get('name')} ({c.get('dataTypeName', '')})" + (f": {desc}" if desc else ""))
    tags = view.get("tags") or []
    if tags:
        lines += ["", "Tags: " + ", ".join(tags)]
    return clean("\n".join(lines))


def html_text(body: bytes) -> tuple[str, str, BeautifulSoup]:
    soup = BeautifulSoup(body, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "").replace(" - City of Chicago", "").replace("City of Chicago :: ", "").strip()
    main = soup.find("main") or soup.body or soup
    for t in main.find_all(["script", "style", "noscript", "nav", "footer", "header", "form"]):
        t.decompose()
    parts = []
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th"]):
        txt = el.get_text(" ", strip=True)
        if txt and (not parts or txt != parts[-1]):
            parts.append(("## " if el.name in ("h1", "h2", "h3", "h4") else "") + txt)
    return title, clean("\n".join(parts)), soup


def pdf_text(body: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    pages = []
    for i, p in enumerate(reader.pages):
        t = p.extract_text() or ""
        pages.append(f"[page {i + 1}]\n{t}")
    return clean("\n\n".join(pages))


def main() -> int:
    f = Fetcher()
    docs: list[dict] = []
    OUT.mkdir(exist_ok=True)
    (OUT / "docs").mkdir(exist_ok=True)

    # --- portal dataset metadata
    ids: dict[str, str] = {}
    for q in PORTAL_QUERIES:
        got = f.get(f"https://api.us.socrata.com/api/catalog/v1?domains=data.cityofchicago.org&q={q.replace(' ', '%20')}"
                    "&limit=100")
        if not got:
            continue
        for r in json.loads(got[0])["results"]:
            res = r["resource"]
            name = res["name"]
            if q == "Performance Metrics" and not name.startswith("Performance Metrics"):
                continue
            if res.get("type") not in ("dataset", None) or len(res.get("description") or "") < 80:
                continue
            ids.setdefault(res["id"], name)
    ids.setdefault("u6fz-87ei", "311 service request benchmark")
    for vid in sorted(ids):
        got = f.get(f"https://data.cityofchicago.org/api/views/{vid}.json")
        if not got:
            continue
        view = json.loads(got[0])
        text = portal_doc(view)
        docs.append({"doc_id": f"portal-{vid}", "title": view.get("name", vid), "source_type": "portal_metadata",
                     "url": f"https://data.cityofchicago.org/d/{vid}",
                     "fetch_url": f"https://data.cityofchicago.org/api/views/{vid}.json",
                     "fetched_at": f.fetched_at(f"https://data.cityofchicago.org/api/views/{vid}.json"),
                     "license": PORTAL_TERMS + (f"; dataset license: {view['license'].get('name')}"
                                                if view.get("license") else ""),
                     "attribution": view.get("attribution", ""), "text": text})

    # --- chicago.gov department pages + bounded crawl
    pdf_links: list[str] = []
    seen: set[str] = set()
    for dept, seed in DEPT_SEEDS.items():
        queue, taken = [seed], 0
        got = f.get(seed)
        if not got:
            continue
        _, _, soup = html_text(got[0])
        for a in soup.find_all("a", href=True):
            u = urljoin(seed, a["href"]).split("#")[0].split("?")[0]
            u = u.replace("/content/city/", "/city/")
            if urlparse(u).netloc != "www.chicago.gov":
                continue
            if u.lower().endswith(".pdf") and dept == "OBM" and "budget" in u.lower():
                pdf_links.append(u)
            if DEPT_PATH[dept] + "/" in u and u.endswith(".html") and u not in queue:
                queue.append(u)
        for u in queue:
            if u in seen or taken >= PAGES_PER_DEPT + 1:
                continue
            seen.add(u)
            got = f.get(u)
            if not got or "html" not in got[1]:
                continue
            title, text, _ = html_text(got[0])
            if len(text) < 300:
                continue
            taken += 1
            path = urlparse(u).path.removeprefix("/city/en/").removesuffix(".html")
            docs.append({"doc_id": "web-" + slug(path, 80), "title": title or path, "source_type": "web_page",
                         "url": u, "fetch_url": u, "fetched_at": f.fetched_at(u), "license": WEB_TERMS,
                         "attribution": "City of Chicago", "dept_hint": dept, "text": text})

    # --- budget PDFs linked from OBM pages (bounded)
    budget_page = f.get("https://www.chicago.gov/city/en/depts/obm/provdrs/budget.html")
    if budget_page:
        soup = BeautifulSoup(budget_page[0], "html.parser")
        for a in soup.find_all("a", href=True):
            u = urljoin("https://www.chicago.gov/city/en/depts/obm/provdrs/budget.html", a["href"])
            if u.lower().endswith(".pdf"):
                pdf_links.append(u.replace("/content/dam/", "/content/dam/"))
    picked = []
    for u in dict.fromkeys(pdf_links):
        name = u.rsplit("/", 1)[-1].lower()
        if ("2026" in name or "2026" in u) and ("overview" in name or "recommendation" in name or "budget" in name):
            picked.append(u)
    for u in picked[:MAX_PDFS]:
        got = f.get(u)
        if not got:
            continue
        try:
            text = pdf_text(got[0])
        except Exception as e:  # noqa: BLE001 - a broken PDF must not stop the corpus build
            print(f"  pdf parse failed {u}: {e}")
            continue
        name = u.rsplit("/", 1)[-1].removesuffix(".pdf")
        docs.append({"doc_id": "pdf-" + slug(name), "title": name.replace("_", " "), "source_type": "pdf",
                     "url": u, "fetch_url": u, "fetched_at": f.fetched_at(u), "license": WEB_TERMS,
                     "attribution": "City of Chicago Office of Budget and Management", "dept_hint": "OBM",
                     "text": text})

    # --- write corpus/
    uniq: dict[str, dict] = {}
    for d in docs:
        if d["doc_id"] not in uniq and len(d["text"]) >= 200:
            uniq[d["doc_id"]] = d
    for p in (OUT / "docs").glob("*.txt"):
        p.unlink()
    with open(OUT / "manifest.jsonl", "w") as mf:
        for d in uniq.values():
            text = d.pop("text")
            (OUT / "docs" / f"{d['doc_id']}.txt").write_text(text)
            d["sha256"] = hashlib.sha256(text.encode()).hexdigest()
            d["chars"] = len(text)
            mf.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"{len(uniq)} documents; {f.requests} HTTP requests, {f.cache_hits} cache hits; pdf candidates {len(picked)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
