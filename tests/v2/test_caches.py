"""P4/P5: cache re-authorisation and invalidation; deletion purges every index structure."""

from analytics_agent.copilot.caches import AnswerCache, CacheEntry
from analytics_agent.copilot.index import EmbeddingCache

from .helpers import small_deployment


def entry(d, user="maria", docs=None, depts=frozenset({"CDOT"})):
    return CacheEntry(user, "q", {"text": "a"}, d.state.fingerprint(user), docs or {"cdot-1": 1}, depts, "rls-v1")


def test_hit_requires_live_entitlements(tmp_path):
    _, d, s, _ = small_deployment(tmp_path)
    c = AnswerCache(d, s, "rls-v1")
    c.put(entry(d))
    assert c.get("maria", "Q") is not None
    d.remove_member("dept-cdot", "maria")
    assert c.get("maria", "q") is None


def test_read_path_check_catches_what_events_miss(tmp_path):
    _, d, s, _ = small_deployment(tmp_path)
    c = AnswerCache(d, s, "rls-v1")
    d.listeners.clear()  # simulate a lost directory event
    s.listeners.clear()
    c.put(entry(d))
    d.remove_member("dept-cdot", "maria")
    assert c.get("maria", "q") is None and c.stats["evicted_on_read"] == 1


def test_acl_change_and_delete_evict(tmp_path):
    _, d, s, _ = small_deployment(tmp_path)
    c = AnswerCache(d, s, "rls-v1")
    c.put(entry(d))
    s.set_acl("cdot-1", frozenset({"group:perf-office"}))
    assert c.get("maria", "q") is None
    c.put(entry(d, docs={"pub-1": 1}, depts=frozenset()))
    s.delete("pub-1")
    assert c.get("maria", "q") is None


def test_policy_version_change_evicts(tmp_path):
    _, d, s, _ = small_deployment(tmp_path)
    c = AnswerCache(d, s, "rls-v2")
    c.put(entry(d))
    assert c.get("maria", "q") is None


def test_deletion_purges_chunks_vectors_and_embeddings(tmp_path):
    clock, d, s, ix = small_deployment(tmp_path, lag_s=0)
    text = s.docs["dwm-1"].text
    key = EmbeddingCache.key(ix.chunks["dwm-1#0"].text, "passage")
    purged = []
    ix.on_purge.append(purged.append)
    assert ix.embedder.cache.get(key) is not None
    clock.t += 1
    s.delete("dwm-1")
    ix.sync()
    assert purged == ["dwm-1"]
    assert not [c for c in ix.chunks.values() if c.doc_id == "dwm-1"]
    assert not [k for k in ix.vectors if k.startswith("dwm-1")]
    assert "dwm-1" not in ix.doc_acl and ix.embedder.cache.get(key) is None
    blob = " ".join(c.text for c in ix.chunks.values())
    assert "billion gallons" not in blob and text not in blob
    ix.embedder.cache.save()
    assert key not in set(map(str, __import__("numpy").load(tmp_path / "emb.npz")["keys"]))


def test_shared_text_keeps_its_vector(tmp_path):
    clock, d, s, ix = small_deployment(tmp_path, lag_s=0)
    s.upsert("pub-copy", "copy", "u", s.docs["pub-1"].text, frozenset({"group:all-staff"}))
    ix.sync(now=clock.t + 1)
    key = EmbeddingCache.key(ix.chunks["pub-1#0"].text, "passage")
    s.delete("pub-1")
    ix.sync(now=clock.t + 2)
    assert ix.embedder.cache.get(key) is not None  # still used by pub-copy
