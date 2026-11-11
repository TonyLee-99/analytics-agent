"""P2: filter before ranking; BM25 statistics over the allowed set; query-time recheck closes the lag."""

import rank_bm25

from analytics_agent.copilot.retrieve import Retriever

from .helpers import small_deployment


def test_scorer_only_sees_allowed_chunks(tmp_path, monkeypatch):
    clock, d, s, ix = small_deployment(tmp_path)
    seen = []
    orig = rank_bm25.BM25Okapi.__init__

    def spy(self, corpus, *a, **k):
        seen.append([" ".join(doc) for doc in corpus])
        orig(self, corpus, *a, **k)

    monkeypatch.setattr(rank_bm25.BM25Okapi, "__init__", spy)
    r = Retriever(ix, d, s)
    res = r.search("maria", "pothole crews water gallons rodent")
    allowed_docs = {ix.chunks[c].doc_id for c in res.scored_chunk_ids}
    assert allowed_docs == {"pub-1", "cdot-1"}
    assert seen and all("gallons" not in doc and "rodent" not in doc for doc in seen[0])  # IDF corpus = allowed only
    assert {h.chunk.doc_id for h in res.hits} <= {"pub-1", "cdot-1"}


def test_bm25_scores_do_not_depend_on_restricted_docs(tmp_path):
    _, d, s, ix = small_deployment(tmp_path)
    r = Retriever(ix, d, s)
    before = [(h.chunk.chunk_id, h.bm25_rank) for h in r.search("maria", "pothole crews").hits]
    s.upsert("dwm-2", "x", "u", "pothole pothole pothole crews crews", frozenset({"group:dept-dwm"}))
    ix.sync(now=10**9)
    after = [(h.chunk.chunk_id, h.bm25_rank) for h in r.search("maria", "pothole crews").hits]
    assert before == after


def test_revocation_window_recheck_on_vs_off(tmp_path):
    clock, d, s, ix = small_deployment(tmp_path, lag_s=300)
    clock.t += 5
    d.remove_member("dept-cdot", "maria")
    ix.sync()  # inside the lag: the index still thinks maria is in dept-cdot
    assert "group:dept-cdot" in ix.principals("maria")
    on = Retriever(ix, d, s, recheck=True).search("maria", "pothole crews target")
    off = Retriever(ix, d, s, recheck=False).search("maria", "pothole crews target")
    assert "cdot-1" not in {h.chunk.doc_id for h in on.hits}
    assert any(x["doc_id"] == "cdot-1" and x["reason"] == "acl_denies_live" for x in on.dropped_by_recheck)
    assert "cdot-1" in {h.chunk.doc_id for h in off.hits}  # the window is real without the recheck


def test_deleted_at_source_is_dropped_before_sync(tmp_path):
    clock, d, s, ix = small_deployment(tmp_path)
    clock.t += 5
    s.delete("pub-1")
    ix.sync()
    res = Retriever(ix, d, s).search("maria", "potholes reported 311")
    assert "pub-1" not in {h.chunk.doc_id for h in res.hits}
    assert any(x["reason"] == "deleted_at_source" for x in res.dropped_by_recheck)


def test_unknown_or_inactive_user_gets_nothing(tmp_path):
    _, d, s, ix = small_deployment(tmp_path)
    assert Retriever(ix, d, s).search("tom", "pothole").hits == []
    assert Retriever(ix, d, s).search("nobody", "pothole").hits == []
