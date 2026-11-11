"""Search index: chunks, embeddings, and a *lagged* copy of document ACLs and directory memberships.

`IndexSync.sync(now)` applies document-source and directory events older than `lag_s`, the way a
connector polling a change feed would. Everything the index knows about permissions can therefore be
stale by up to `lag_s`; retrieve.py's query-time recheck against the live systems closes that gap.

Deletion is a purge, not a tombstone: chunk text, postings (rebuilt per query from live chunks),
vectors and embedding-cache entries of the document are dropped, and listeners (answer cache, LLM
response dependencies) are told so they can purge too.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from analytics_agent.copilot.docsource import DocSource
from analytics_agent.copilot.identity import Directory, DirectoryReplica
from analytics_agent.copilot.settings import V2_CACHE

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # bge retrieval instruction
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
EMBED_CACHE_PATH = V2_CACHE / "embeddings.npz"


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_version: int
    title: str
    url: str
    start: int
    end: int
    text: str

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[tuple[int, int]]:
    """Character spans of overlapping chunks, cut at paragraph or sentence boundaries when possible."""
    spans, start, n = [], 0, len(text)
    while start < n:
        end = min(n, start + size)
        if end < n:
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(". "))
            if cut > size // 2:
                end = start + cut + 1
        spans.append((start, end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
        # do not start mid-word
        while start < n and start > 0 and text[start - 1].isalnum() and text[start].isalnum():
            start += 1
    return spans


class ReplayEmbedMiss(RuntimeError):
    pass


class EmbeddingCache:
    """content hash -> vector. Persisted to one .npz so CI can replay without the model."""

    def __init__(self, path: Path = EMBED_CACHE_PATH):
        self.path = Path(path)
        self.vecs: dict[str, np.ndarray] = {}
        self.dirty = False
        if self.path.exists():
            z = np.load(self.path, allow_pickle=False)
            for k, v in zip(z["keys"], z["vecs"], strict=True):
                self.vecs[str(k)] = v.astype(np.float32)

    @staticmethod
    def key(text: str, kind: str) -> str:
        return hashlib.sha256(f"{EMBED_MODEL}|{kind}|{text}".encode()).hexdigest()

    def get(self, k: str) -> np.ndarray | None:
        return self.vecs.get(k)

    def put(self, k: str, v: np.ndarray) -> None:
        self.vecs[k] = v.astype(np.float32)
        self.dirty = True

    def purge(self, keys: set[str]) -> int:
        n = 0
        for k in keys:
            if self.vecs.pop(k, None) is not None:
                n += 1
        self.dirty = self.dirty or n > 0
        return n

    def save(self, path: Path | None = None) -> None:
        p = Path(path or self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        keys = np.array(sorted(self.vecs))
        vecs = np.stack([self.vecs[k] for k in keys]).astype(np.float16) if len(keys) else np.zeros((0, 384))
        np.savez_compressed(p, keys=keys, vecs=vecs)
        self.dirty = False


class Embedder:
    """bge-small via fastembed (ONNX, local, no API). In replay mode only cached vectors are served."""

    def __init__(self, cache: EmbeddingCache | None = None):
        self.cache = cache or EmbeddingCache()
        self._model = None

    @property
    def replay(self) -> bool:
        return os.environ.get("AA_EMBED_REPLAY", os.environ.get("AA_STRICT_REPLAY", "")) not in ("", "0")

    def _embed(self, texts: list[str]) -> list[np.ndarray]:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(EMBED_MODEL, cache_dir=str(V2_CACHE.parent / ".fastembed_cache"))
        return [np.asarray(v, dtype=np.float32) for v in self._model.embed(texts, batch_size=32)]

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        prepared = [(QUERY_PREFIX + t) if kind == "query" else t for t in texts]
        keys = [EmbeddingCache.key(t, kind) for t in texts]
        missing = [i for i, k in enumerate(keys) if self.cache.get(k) is None]
        if missing:
            if self.replay:
                raise ReplayEmbedMiss(f"{len(missing)} {kind} embeddings not cached (replay mode)")
            for i, v in zip(missing, self._embed([prepared[i] for i in missing]), strict=True):
                self.cache.put(keys[i], v / (np.linalg.norm(v) or 1.0))
        return np.stack([self.cache.get(k) for k in keys])


class IndexSync:
    """The search index plus its lagged view of document ACLs and directory memberships."""

    def __init__(self, source: DocSource, directory: Directory, embedder: Embedder | None = None,
                 lag_s: float = 0.0):
        self.source = source
        self.embedder = embedder or Embedder()
        self.lag_s = lag_s
        self.dir_replica = DirectoryReplica(directory, lag_s)
        self.chunks: dict[str, Chunk] = {}
        self.doc_chunks: dict[str, list[str]] = {}
        self.doc_acl: dict[str, frozenset[str]] = {}
        self.doc_version: dict[str, int] = {}
        self.vectors: dict[str, np.ndarray] = {}
        self.applied = 0
        self.on_purge: list[Callable[[str], None]] = []
        self.stats = {"upserts": 0, "acl_changes": 0, "deletes": 0}

    # --- sync
    def sync(self, now: float | None = None) -> int:
        now = self.source.clock() if now is None else now
        n = self.dir_replica.sync(now)
        while self.applied < len(self.source.events):
            ev = self.source.events[self.applied]
            if ev["ts"] > now - self.lag_s:
                break
            self._apply(ev)
            self.applied += 1
            n += 1
        return n

    def _apply(self, ev: dict[str, Any]) -> None:
        p = ev["payload"]
        if ev["type"] == "doc_upsert":
            doc = self.source.docs.get(p["doc_id"])
            if doc is None:  # deleted since; its delete event follows
                return
            self._drop_doc(p["doc_id"], purge_embeddings=False)
            ids = []
            for i, (s, e) in enumerate(chunk_text(doc.text)):
                c = Chunk(f"{doc.doc_id}#{i}", doc.doc_id, doc.version, doc.title, doc.url, s, e, doc.text[s:e])
                self.chunks[c.chunk_id] = c
                ids.append(c.chunk_id)
            self.doc_chunks[doc.doc_id] = ids
            self.doc_acl[doc.doc_id] = doc.acl
            self.doc_version[doc.doc_id] = doc.version
            vecs = self.embedder.embed([self.chunks[i].text for i in ids]) if ids else []
            for i, v in zip(ids, vecs, strict=True):
                self.vectors[i] = v
            self.stats["upserts"] += 1
        elif ev["type"] == "acl_change":
            if p["doc_id"] in self.doc_acl:
                self.doc_acl[p["doc_id"]] = frozenset(p["acl"])
                self.doc_version[p["doc_id"]] = p["version"]
            self.stats["acl_changes"] += 1
        elif ev["type"] == "doc_delete":
            self._drop_doc(p["doc_id"], purge_embeddings=True)
            for fn in self.on_purge:
                fn(p["doc_id"])
            self.stats["deletes"] += 1

    def _drop_doc(self, doc_id: str, purge_embeddings: bool) -> None:
        ids = self.doc_chunks.pop(doc_id, [])
        hashes = {self.chunks[i].text_hash: self.chunks[i].text for i in ids if i in self.chunks}
        for i in ids:
            self.chunks.pop(i, None)
            self.vectors.pop(i, None)
        self.doc_acl.pop(doc_id, None)
        self.doc_version.pop(doc_id, None)
        if purge_embeddings and hashes:
            live = {c.text_hash for c in self.chunks.values()}
            # identical text still live in another document keeps its vector (shared content)
            dead = {EmbeddingCache.key(t, "passage") for h, t in hashes.items() if h not in live}
            self.embedder.cache.purge(dead)

    # --- what retrieval may use
    def principals(self, user_id: str) -> frozenset[str]:
        return self.dir_replica.state.principals(user_id)

    def allowed_chunk_ids(self, principals: frozenset[str]) -> list[str]:
        ok_docs = {d for d, acl in self.doc_acl.items() if acl & principals}
        return [cid for cid, c in self.chunks.items() if c.doc_id in ok_docs]


TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP = frozenset("a an and are as at be by for from has have in is it its of on or that the this to was were "
                 "will with what which who how when where does do did".split())


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP]
