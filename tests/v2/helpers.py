"""Small in-memory deployment pieces for unit tests (no model, no network, no Postgres)."""

from __future__ import annotations

import hashlib

import numpy as np

from analytics_agent.copilot.docsource import DocSource
from analytics_agent.copilot.identity import Directory
from analytics_agent.copilot.index import EmbeddingCache, IndexSync


class FakeEmbedder:
    """Deterministic bag-of-words hashing vectors; same interface as index.Embedder."""

    def __init__(self, path):
        self.cache = EmbeddingCache(path)

    def embed(self, texts, kind="passage"):
        out = []
        for t in texts:
            k = EmbeddingCache.key(t, kind)
            v = self.cache.get(k)
            if v is None:
                v = np.zeros(64, dtype=np.float32)
                for w in t.lower().split():
                    v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
                v = v / (np.linalg.norm(v) or 1)
                self.cache.put(k, v)
            out.append(v)
        return np.stack(out)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


DOCS = {
    "pub-1": ("Pothole basics", "Potholes are reported to 311 and patched by CDOT crews within days.", ["group:all-staff"]),
    "cdot-1": ("CDOT internal pothole metric", "CDOT pothole crews are routed in clusters each morning; target 7 days.",
               ["group:dept-cdot", "group:perf-office"]),
    "dwm-1": ("Water quality", "DWM delivers almost one billion gallons of water each day with corrosion control.",
              ["group:dept-dwm", "group:perf-office"]),
    "dss-1": ("Rodent metric", "Rodent baiting requests target 7 days for DSS crews and rat complaints.",
              ["group:dept-dss", "group:perf-office"]),
}


def small_deployment(tmp_path, lag_s=300.0):
    clock = Clock()
    d = Directory.from_config(clock=clock)
    s = DocSource(clock=clock)
    for doc_id, (title, text, acl) in DOCS.items():
        s.upsert(doc_id, title, f"https://example.test/{doc_id}", text, frozenset(acl))
    ix = IndexSync(s, d, FakeEmbedder(tmp_path / "emb.npz"), lag_s=lag_s)
    ix.sync(now=clock() + lag_s)
    return clock, d, s, ix


def mock_llm(route="docs", metric_query=None, answer="Potholes are patched by CDOT crews [S1]."):
    import json as _json

    from analytics_agent.copilot import prompts as P
    from analytics_agent.llm.mock import MockLLM

    def responder(system, prompt, sample):
        if system == P.PLAN_SYSTEM:
            return "```json\n" + _json.dumps({"route": route, "search_query": "pothole", "metric_query": metric_query,
                                               "metric_evidence": {}, "raw_sql": None,
                                               "departments_requested": []}) + "\n```"
        return answer

    return MockLLM(responder)


class FakeWarehouse:
    def __init__(self):
        self.calls = []

    def query(self, user_id, live_depts, sql_text):
        from analytics_agent.copilot.warehouse import QueryResult, role_name

        self.calls.append((user_id, sorted(live_depts), sql_text))
        return QueryResult(True, ["n"], [(42,)], role=role_name(user_id), live_depts=tuple(sorted(live_depts)))


def small_copilot(tmp_path, llm=None, **kw):
    from analytics_agent.copilot.copilot import Copilot, Deployment
    from analytics_agent.copilot.retrieve import Retriever

    clock, d, s, ix = small_deployment(tmp_path, lag_s=kw.pop("lag_s", 300.0))
    dep = Deployment(d, s, ix, Retriever(ix, d, s), FakeWarehouse(), None, clock)
    return Copilot(dep, llm=llm or mock_llm(), trace_path=tmp_path / "trace.jsonl", **kw)
