"""Document source system (Simulated document store holding real public documents).

Like the directory it is event-sourced: `doc_upsert`, `acl_change`, `doc_delete`. The live source is
the authority for "does this document still exist and who may read it"; the search index only has a
lagged copy (see index.IndexSync).

ACLs are lists of principals: `group:<id>` or `user:<id>`. They are assigned by the rules in
config/doc_acl.yaml, which is Simulated configuration.
"""

from __future__ import annotations

import fnmatch
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from analytics_agent.copilot.settings import CONFIG, CORPUS


@dataclass
class Doc:
    doc_id: str
    title: str
    url: str
    text: str
    acl: frozenset[str]
    version: int = 1
    source_type: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def load_acl_rules(path: Path = CONFIG / "doc_acl.yaml") -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def acl_for(meta: dict[str, Any], rules: dict[str, Any]) -> frozenset[str]:
    """First matching rule wins; otherwise the default ACL. Matching is on doc id, title or dept hint."""
    for r in rules["rules"]:
        m = r["match"]
        ok = True
        if "doc_id" in m:
            ok &= any(fnmatch.fnmatch(meta["doc_id"], p) for p in _list(m["doc_id"]))
        if "title" in m:
            ok &= any(fnmatch.fnmatch(meta.get("title", "").lower(), p.lower()) for p in _list(m["title"]))
        if "dept_hint" in m:
            ok &= meta.get("dept_hint") in _list(m["dept_hint"])
        if "source_type" in m:
            ok &= meta.get("source_type") in _list(m["source_type"])
        if ok:
            return frozenset(r["acl"])
    return frozenset(rules["default"])


def _list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


class DocSource:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.events: list[dict[str, Any]] = []
        self.docs: dict[str, Doc] = {}
        self.listeners: list[Callable[[dict[str, Any]], None]] = []

    @classmethod
    def from_corpus(cls, corpus: Path = CORPUS, rules: dict[str, Any] | None = None,
                    clock: Callable[[], float] = time.time) -> DocSource:
        rules = rules or load_acl_rules()
        src = cls(clock=clock)
        for line in (Path(corpus) / "manifest.jsonl").read_text().splitlines():
            meta = json.loads(line)
            text = (Path(corpus) / "docs" / f"{meta['doc_id']}.txt").read_text()
            src.upsert(meta["doc_id"], meta["title"], meta["url"], text, acl_for(meta, rules),
                       source_type=meta.get("source_type", ""), meta=meta)
        return src

    def _emit(self, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
        ev = {"seq": len(self.events) + 1, "ts": self.clock(), "type": type_, "payload": payload}
        self.events.append(ev)
        for fn in self.listeners:
            fn(ev)
        return ev

    def upsert(self, doc_id: str, title: str, url: str, text: str, acl: frozenset[str], source_type: str = "",
               meta: dict[str, Any] | None = None) -> Doc:
        prev = self.docs.get(doc_id)
        doc = Doc(doc_id, title, url, text, frozenset(acl), (prev.version + 1) if prev else 1, source_type,
                  meta or {})
        self.docs[doc_id] = doc
        self._emit("doc_upsert", {"doc_id": doc_id, "version": doc.version})
        return doc

    def set_acl(self, doc_id: str, acl: frozenset[str]) -> None:
        d = self.docs[doc_id]
        d.acl = frozenset(acl)
        d.version += 1
        self._emit("acl_change", {"doc_id": doc_id, "version": d.version, "acl": sorted(acl)})

    def delete(self, doc_id: str) -> None:
        self.docs.pop(doc_id)
        self._emit("doc_delete", {"doc_id": doc_id})

    # --- live authority checks (used by the query-time recheck and delivery-time reauthorisation)
    def live_acl(self, doc_id: str) -> frozenset[str] | None:
        d = self.docs.get(doc_id)
        return d.acl if d else None

    def live_version(self, doc_id: str) -> int | None:
        d = self.docs.get(doc_id)
        return d.version if d else None
