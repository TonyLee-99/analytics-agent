"""Permission-aware hybrid retrieval: filter -> rank (BM25 + dense, RRF) -> optional rerank -> recheck.

Order matters. The allowed set is computed *before* any scoring, and BM25 statistics (IDF, average
document length) are computed over the allowed set only: a corpus-wide IDF would make a user's scores
depend on how often a term appears in documents they cannot read, which is a (small) side channel.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from analytics_agent.copilot.docsource import DocSource
from analytics_agent.copilot.identity import Directory
from analytics_agent.copilot.index import Chunk, IndexSync, tokenize
from analytics_agent.copilot.settings import V2_CACHE

RRF_K = 60
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
RERANK_CACHE_PATH = V2_CACHE / "rerank_scores.json"


@dataclass
class Hit:
    chunk: Chunk
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None


@dataclass
class Retrieval:
    hits: list[Hit]
    n_allowed_chunks: int
    n_total_chunks: int
    dropped_by_recheck: list[dict] = field(default_factory=list)
    scored_chunk_ids: frozenset[str] = frozenset()  # for tests: exactly what reached the scorers


class _BM25Cache:
    """BM25 index per allowed set (keyed by a hash of the chunk ids), small LRU."""

    def __init__(self, size: int = 64):
        self.size = size
        self.data: OrderedDict[str, BM25Okapi] = OrderedDict()

    def get(self, ids: list[str], chunks: dict[str, Chunk]) -> BM25Okapi:
        # key on content, not ids: an update or deletion changes the key, so stale statistics are never reused
        k = hashlib.sha256("\n".join(f"{i}:{chunks[i].text_hash}" for i in ids).encode()).hexdigest()
        if k in self.data:
            self.data.move_to_end(k)
            return self.data[k]
        bm = BM25Okapi([tokenize(chunks[i].title + " " + chunks[i].text) for i in ids])
        self.data[k] = bm
        if len(self.data) > self.size:
            self.data.popitem(last=False)
        return bm


class Reranker:
    """Cross-encoder rerank with a persisted score cache (so CI can replay it)."""

    def __init__(self, path: Path = RERANK_CACHE_PATH):
        self.path = path
        self.scores: dict[str, float] = json.loads(path.read_text()) if path.exists() else {}
        self._model = None
        self.dirty = False

    def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        keys = [hashlib.sha256(f"{RERANK_MODEL}|{query}|{c.text_hash}".encode()).hexdigest() for c in chunks]
        missing = [i for i, k in enumerate(keys) if k not in self.scores]
        if missing:
            if os.environ.get("AA_EMBED_REPLAY", os.environ.get("AA_STRICT_REPLAY", "")) not in ("", "0"):
                raise RuntimeError("rerank scores not cached (replay mode)")
            if self._model is None:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                self._model = TextCrossEncoder(RERANK_MODEL, cache_dir=str(V2_CACHE.parent / ".fastembed_cache"))
            got = list(self._model.rerank(query, [chunks[i].text for i in missing]))
            for i, s in zip(missing, got, strict=True):
                self.scores[keys[i]] = float(s)
            self.dirty = True
        return [self.scores[k] for k in keys]

    def save(self) -> None:
        self.path.write_text(json.dumps(self.scores))
        self.dirty = False


class Retriever:
    def __init__(self, index: IndexSync, directory: Directory, source: DocSource, rerank: bool = False,
                 recheck: bool = True, pool: int = 50):
        self.index = index
        self.directory = directory
        self.source = source
        self.rerank = rerank
        self.recheck = recheck
        self.pool = pool
        self._bm25 = _BM25Cache()
        self.reranker = Reranker() if rerank else None

    def live_allows(self, user_id: str, doc_id: str) -> tuple[bool, str]:
        """Authoritative check against the live source and live directory."""
        acl = self.source.live_acl(doc_id)
        if acl is None:
            return False, "deleted_at_source"
        if not (acl & self.directory.state.principals(user_id)):
            return False, "acl_denies_live"
        return True, "ok"

    def search(self, user_id: str, query: str, k: int = 6) -> Retrieval:
        principals = self.index.principals(user_id)  # the index's (possibly stale) view
        allowed = sorted(self.index.allowed_chunk_ids(principals))
        out = Retrieval([], len(allowed), len(self.index.chunks), scored_chunk_ids=frozenset(allowed))
        if not allowed:
            return out
        q_tokens = tokenize(query)
        bm = self._bm25.get(allowed, self.index.chunks)
        bm_scores = bm.get_scores(q_tokens) if q_tokens else np.zeros(len(allowed))
        qv = self.index.embedder.embed([query], kind="query")[0]
        mat = np.stack([self.index.vectors[i] for i in allowed])
        dense = mat @ qv
        bm_order = np.argsort(-bm_scores)[: self.pool]
        de_order = np.argsort(-dense)[: self.pool]
        fused: dict[int, float] = {}
        bm_rank, de_rank = {}, {}
        for r, i in enumerate(bm_order):
            if bm_scores[i] <= 0:
                break
            fused[i] = fused.get(i, 0) + 1 / (RRF_K + r + 1)
            bm_rank[i] = r
        for r, i in enumerate(de_order):
            fused[i] = fused.get(i, 0) + 1 / (RRF_K + r + 1)
            de_rank[i] = r
        ranked = sorted(fused, key=lambda i: -fused[i])
        if self.reranker is not None:
            top = ranked[:20]
            scores = self.reranker.score(query, [self.index.chunks[allowed[i]] for i in top])
            ranked = [i for _, i in sorted(zip(scores, top, strict=True), key=lambda t: -t[0])] + ranked[20:]
            fused = {i: s for i, s in zip(top, scores, strict=True)} | {i: fused[i] for i in ranked[20:]}
        hits: list[Hit] = []
        for i in ranked:
            c = self.index.chunks[allowed[i]]
            if self.recheck:
                ok, why = self.live_allows(user_id, c.doc_id)
                if not ok:
                    out.dropped_by_recheck.append({"chunk_id": c.chunk_id, "doc_id": c.doc_id, "reason": why})
                    continue
            hits.append(Hit(c, float(fused[i]), bm_rank.get(i), de_rank.get(i)))
            if len(hits) >= k:
                break
        out.hits = hits
        return out
