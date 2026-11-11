"""A2/A3: corpus provenance, polite fetcher, Simulated ACL assignment."""

import hashlib
import json
import sys

import httpx
import yaml

from analytics_agent.copilot.docsource import DocSource, acl_for, load_acl_rules
from analytics_agent.copilot.settings import CONFIG, CORPUS, ROOT

sys.path.insert(0, str(ROOT / "scripts" / "v2"))
import fetch_corpus  # noqa: E402


def manifest():
    return [json.loads(line) for line in (CORPUS / "manifest.jsonl").read_text().splitlines()]


def test_at_least_60_real_documents_with_provenance():
    m = manifest()
    assert len(m) >= 60
    for d in m:
        text = (CORPUS / "docs" / f"{d['doc_id']}.txt").read_text()
        assert d["url"].startswith("https://") and ("cityofchicago.org" in d["url"] or "chicago.gov" in d["url"])
        assert d["fetched_at"] and d["license"]
        assert hashlib.sha256(text.encode()).hexdigest() == d["sha256"]


def test_fetcher_identifies_itself_and_backs_off(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_corpus, "CACHE", tmp_path)
    monkeypatch.setattr(fetch_corpus, "MIN_DELAY_S", 0.0)
    seen, sleeps = [], []
    monkeypatch.setattr(fetch_corpus.time, "sleep", sleeps.append)

    def handler(req):
        seen.append(req)
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 1\n")
        if len([r for r in seen if r.url.path == "/page"]) == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, text="<html><main><p>hello</p></main></html>", headers={"content-type": "text/html"})

    f = fetch_corpus.Fetcher()
    f.client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": fetch_corpus.UA})
    body, _ = f.get("https://example.test/page")
    assert b"hello" in body
    assert 7.0 in sleeps  # honoured Retry-After
    assert all("analytics-agent-corpus" in r.headers["user-agent"] for r in seen)
    n = len(seen)
    assert f.get("https://example.test/page")[0] == body and len(seen) == n  # cached: no new request


def test_acl_config_is_simulated_and_restricts_at_least_20_percent():
    rules = load_acl_rules()
    assert rules.get("simulated") is True
    assert yaml.safe_load((CONFIG / "org.yaml").read_text()).get("simulated") is True
    m = manifest()
    restricted = [d for d in m if acl_for(d, rules) != frozenset(rules["default"])]
    assert len(restricted) / len(m) >= 0.20


def test_docsource_acls_come_only_from_rules():
    s = DocSource.from_corpus()
    rules = load_acl_rules()
    for d in manifest():
        assert s.docs[d["doc_id"]].acl == acl_for(d, rules)
