"""SQL cassette: record warehouse results during live runs, serve them in replay (CI) runs.

Key = (cassette version, role, live departments, SQL). Replay is not security evidence: RLS itself is
tested against a real Postgres in tests/v2/test_rls.py. A miss in replay mode raises.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
from pathlib import Path

from analytics_agent.copilot.settings import V2_CACHE
from analytics_agent.copilot.warehouse import POLICY_VERSION, QueryResult

CASSETTE = V2_CACHE / "v2_sql_cassette.jsonl.gz"
VERSION = f"wh-2024-11-01_2026-11-04|{POLICY_VERSION}|schema-v1"
_lock = threading.Lock()


def key(role: str, depts, sql_text: str) -> str:
    return hashlib.sha256(json.dumps([VERSION, role, sorted(depts), sql_text]).encode()).hexdigest()


def load(path: Path = CASSETTE) -> dict[str, dict]:
    if not path.exists():
        return {}
    with gzip.open(path, "rt") as f:
        return {r["key"]: r for r in map(json.loads, f) if r}


class RecordingWarehouse:
    def __init__(self, inner, path: Path = CASSETTE):
        self.inner, self.path = inner, path
        self.data = load(path)
        self.new = 0

    def query(self, user_id, live_depts, sql_text):
        from analytics_agent.copilot.warehouse import role_name

        res = self.inner.query(user_id, live_depts, sql_text)
        k = key(role_name(user_id), live_depts, sql_text)
        rec = {"key": k, "ok": res.ok, "columns": res.columns, "rows": [[_j(v) for v in r] for r in res.rows],
               "truncated": res.truncated, "error": res.error, "elapsed_ms": res.elapsed_ms}
        with _lock:
            if k not in self.data:
                self.new += 1
            self.data[k] = rec
        return res

    def save(self) -> None:
        with _lock, gzip.open(self.path, "wt") as f:
            for r in sorted(self.data.values(), key=lambda r: r["key"]):
                f.write(json.dumps(r) + "\n")


class ReplayWarehouse:
    def __init__(self, path: Path = CASSETTE):
        self.data = load(path)

    def query(self, user_id, live_depts, sql_text):
        from analytics_agent.copilot.warehouse import role_name

        role = role_name(user_id)
        r = self.data.get(key(role, live_depts, sql_text))
        if r is None:
            raise RuntimeError(f"SQL cassette miss for {role} (replay mode)")
        return QueryResult(r["ok"], r["columns"], [tuple(x) for x in r["rows"]], r["truncated"], r["error"],
                           r["elapsed_ms"], role, tuple(sorted(live_depts)))


def _j(v):
    from decimal import Decimal

    if isinstance(v, Decimal | float):
        return float(v)
    return v if v is None or isinstance(v, bool | int | str) else str(v)
