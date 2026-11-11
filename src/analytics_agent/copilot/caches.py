"""Answer cache with re-authorisation on every hit, plus event-driven eviction.

An entry remembers what it depended on: the user's entitlement fingerprint, the version of every
document whose text was in the model's context, the departments whose rows were read, and the
warehouse policy version. A hit is served only if all of those are unchanged in the *live* systems.
Events (directory change, ACL change, deletion) also evict eagerly; the read-path check is the one
that cannot lag.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

from analytics_agent.copilot.docsource import DocSource
from analytics_agent.copilot.identity import Directory


def normalise(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())


@dataclass
class CacheEntry:
    user_id: str
    question: str
    answer: dict[str, Any]
    fingerprint: str
    doc_versions: dict[str, int]
    depts: frozenset[str]
    policy_version: str
    llm_keys: list[str] = field(default_factory=list)


class AnswerCache:
    def __init__(self, directory: Directory, source: DocSource, policy_version: str):
        self.directory = directory
        self.source = source
        self.policy_version = policy_version
        self.entries: dict[tuple[str, str], CacheEntry] = {}
        self.stats = {"hits": 0, "misses": 0, "evicted_on_read": 0, "evicted_on_event": 0}
        self._lock = threading.Lock()
        directory.listeners.append(self._on_directory_event)
        source.listeners.append(self._on_doc_event)

    def get(self, user_id: str, question: str) -> CacheEntry | None:
        k = (user_id, normalise(question))
        with self._lock:
            e = self.entries.get(k)
            if e is None:
                self.stats["misses"] += 1
                return None
            if not self._still_valid(e):
                del self.entries[k]
                self.stats["evicted_on_read"] += 1
                self.stats["misses"] += 1
                return None
            self.stats["hits"] += 1
            return e

    def _still_valid(self, e: CacheEntry) -> bool:
        st = self.directory.state
        if st.fingerprint(e.user_id) != e.fingerprint or e.policy_version != self.policy_version:
            return False
        principals = st.principals(e.user_id)
        for doc_id, ver in e.doc_versions.items():
            acl = self.source.live_acl(doc_id)
            if acl is None or self.source.live_version(doc_id) != ver or not (acl & principals):
                return False
        return e.depts <= st.data_departments(e.user_id)

    def put(self, e: CacheEntry) -> None:
        with self._lock:
            self.entries[(e.user_id, normalise(e.question))] = e

    def _evict(self, pred) -> int:
        with self._lock:
            dead = [k for k, e in self.entries.items() if pred(e)]
            for k in dead:
                del self.entries[k]
            self.stats["evicted_on_event"] += len(dead)
            return len(dead)

    def _on_directory_event(self, ev: dict[str, Any]) -> None:
        st = self.directory.state
        self._evict(lambda e: st.fingerprint(e.user_id) != e.fingerprint)

    def _on_doc_event(self, ev: dict[str, Any]) -> None:
        doc_id = ev["payload"]["doc_id"]
        self._evict(lambda e: doc_id in e.doc_versions)

    def entries_for_doc(self, doc_id: str) -> list[CacheEntry]:
        return [e for e in self.entries.values() if doc_id in e.doc_versions]
