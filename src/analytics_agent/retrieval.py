"""Schema + example retrieval (the "RAG" in text-to-SQL).

Tables are indexed by name, human description, column names/descriptions and sample values; the
question (+ evidence hint) is the query. Methods: BM25 (lexical), dense embeddings (bge-small,
local), or hybrid via reciprocal-rank fusion. After top-k we add FK "bridge" tables so the selected
set stays joinable -- the same idea as LinkedIn's knowledge-graph expansion of retrieved tables.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache

from rank_bm25 import BM25Okapi

from analytics_agent.catalog import DatabaseCatalog

_TOKEN = re.compile(r"[A-Za-z]+|\d+")
STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are", "what", "which", "how",
        "many", "with", "by", "be", "as", "at", "that", "this", "it", "its", "from", "please", "list", "give",
        "show", "all", "each", "their", "who", "whose", "does", "do", "was", "were", "has", "have", "name",
        "id", "among", "s", "than", "more", "most", "number"}


def tokenize(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")  # camelCase -> camel Case
    toks = [t.lower() for t in _TOKEN.findall(text)]
    out = []
    for t in toks:
        if t in STOP or len(t) == 1:
            continue
        out.append(t)
        if len(t) > 4 and t.endswith("s"):
            out.append(t[:-1])  # crude singularisation: "schools" also matches "school"
    return out


@lru_cache(maxsize=1)
def _embedder():
    from sentence_transformers import SentenceTransformer  # optional dependency

    return SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")


BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@dataclass
class RetrievalResult:
    tables: list[str]
    ranked: list[tuple[str, float]]
    added_by_fk: list[str] = field(default_factory=list)

    @property
    def top_score(self) -> float:
        return self.ranked[0][1] if self.ranked else 0.0

    @property
    def margin(self) -> float:
        """Gap between the best and the k-th ranked score (a weak confidence signal)."""
        if len(self.ranked) < 2:
            return 0.0
        return self.ranked[0][1] - self.ranked[min(len(self.ranked) - 1, len(self.tables) - 1)][1]


class SchemaRetriever:
    def __init__(self, catalog: DatabaseCatalog, method: str = "bm25"):
        self.catalog = catalog
        self.method = method
        self.names = catalog.table_names
        self.docs = [catalog.doc_for_table(t) for t in catalog.tables]
        self._bm25 = BM25Okapi([tokenize(d) for d in self.docs])
        self._emb = None
        self.graph = self._fk_graph()

    def _fk_graph(self) -> dict[str, set[str]]:
        g: dict[str, set[str]] = {n.lower(): set() for n in self.names}
        for t in self.catalog.tables:
            for _c, rt, _rc in t.fks:
                if rt.lower() in g and rt.lower() != t.name.lower():
                    g[t.name.lower()].add(rt.lower())
                    g[rt.lower()].add(t.name.lower())
        return g

    def _bm25_rank(self, query: str) -> list[tuple[str, float]]:
        scores = self._bm25.get_scores(tokenize(query))
        return sorted(zip(self.names, map(float, scores), strict=True), key=lambda x: -x[1])

    def _embed_rank(self, query: str) -> list[tuple[str, float]]:
        model = _embedder()
        if self._emb is None:
            self._emb = model.encode([d[:2000] for d in self.docs], normalize_embeddings=True)
        q = model.encode([BGE_QUERY_PREFIX + query], normalize_embeddings=True)[0]
        sims = self._emb @ q
        return sorted(zip(self.names, map(float, sims), strict=True), key=lambda x: -x[1])

    def rank(self, query: str) -> list[tuple[str, float]]:
        if self.method == "bm25":
            return self._bm25_rank(query)
        if self.method == "embed":
            return self._embed_rank(query)
        if self.method == "hybrid":  # reciprocal-rank fusion, k=60 (Cormack et al. 2009)
            fused: dict[str, float] = {}
            for ranking in (self._bm25_rank(query), self._embed_rank(query)):
                for r, (name, _s) in enumerate(ranking):
                    fused[name] = fused.get(name, 0.0) + 1.0 / (60 + r + 1)
            return sorted(fused.items(), key=lambda x: -x[1])
        raise ValueError(self.method)

    def _bridge(self, chosen: list[str]) -> list[str]:
        """Add intermediate tables on shortest FK paths between chosen tables."""
        chosen_l = [c.lower() for c in chosen]
        added: list[str] = []
        for i, a in enumerate(chosen_l):
            for b in chosen_l[i + 1:]:
                path = self._shortest(a, b)
                for node in path[1:-1] if path else []:
                    if node not in chosen_l and node not in added:
                        added.append(node)
        lookup = {n.lower(): n for n in self.names}
        return [lookup[a] for a in added]

    def _shortest(self, a: str, b: str) -> list[str] | None:
        prev = {a: None}
        dq = deque([a])
        while dq:
            x = dq.popleft()
            if x == b:
                path = [b]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return path[::-1]
            for y in self.graph.get(x, ()):
                if y not in prev:
                    prev[y] = x
                    dq.append(y)
        return None

    def retrieve(self, query: str, k: int = 5, expand_fk: bool = True) -> RetrievalResult:
        ranked = self.rank(query)
        top = [n for n, _ in ranked[:k]]
        added = self._bridge(top) if expand_fk else []
        return RetrievalResult(top + added, ranked, added)


@dataclass
class Example:
    question: str
    sql: str
    db_id: str
    evidence: str = ""
    source: str = ""


class ExampleStore:
    """Verified question->SQL pairs (in a company: certified queries from the query log / notebooks)."""

    def __init__(self, examples: list[Example]):
        self.examples = examples
        self._by_db: dict[str, tuple[list[Example], BM25Okapi]] = {}
        for db in {e.db_id for e in examples}:
            exs = [e for e in examples if e.db_id == db]
            self._by_db[db] = (exs, BM25Okapi([tokenize(e.question + " " + e.evidence) for e in exs]))

    def search(self, query: str, db_id: str, n: int = 3) -> list[Example]:
        if db_id not in self._by_db:
            return []
        exs, bm = self._by_db[db_id]
        scores = bm.get_scores(tokenize(query))
        order = sorted(range(len(exs)), key=lambda i: -scores[i])
        return [exs[i] for i in order[:n]]
