"""Read-only, time-boxed, row-capped query execution for SQLite (BIRD) and DuckDB (Olist).

Even if the AST validator were bypassed, these connections cannot write:
- SQLite is opened with `mode=ro` URI + `PRAGMA query_only=ON`.
- DuckDB is opened with `read_only=True` and external file access disabled.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb

from analytics_agent.config import DEFAULT_ROW_LIMIT, DEFAULT_TIMEOUT_S


@dataclass
class ExecResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str = ""
    elapsed_s: float = 0.0
    truncated: bool = False
    timed_out: bool = False

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    def preview(self, n: int = 10) -> str:
        if not self.ok:
            return f"ERROR: {self.error}"
        head = " | ".join(self.columns)
        body = "\n".join(" | ".join(_fmt(v) for v in r) for r in self.rows[:n])
        more = f"\n... ({self.n_rows} rows{' +, truncated' if self.truncated else ''})" if self.n_rows > n else ""
        return f"{head}\n{body}{more}"


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "..."


_SQLITE_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
_SQLITE_DENY_FUNCS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


def _sqlite_authorizer(action, arg1, arg2, _db, _trigger):
    """Statement-level allowlist inside SQLite itself: only reads and (non-dangerous) functions.
    Blocks ATTACH (which would otherwise create files even on a read-only connection), PRAGMA, VACUUM INTO..."""
    if action not in _SQLITE_ALLOWED:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _SQLITE_DENY_FUNCS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def connect_readonly(dialect: str, path: str):
    if dialect == "sqlite":
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        con.execute("PRAGMA query_only = ON")
        con.set_authorizer(_sqlite_authorizer)
        return con
    if dialect == "duckdb":
        con = duckdb.connect(path, read_only=True, config={"enable_external_access": False,
                                                           "autoload_known_extensions": False,
                                                           "autoinstall_known_extensions": False})
        try:  # no SET/PRAGMA can loosen the above afterwards
            con.execute("SET lock_configuration = true")
        except duckdb.Error as e:  # DuckDB shares one instance per file inside a process: already locked
            if "locked" not in str(e):
                raise
        return con
    raise ValueError(f"unsupported dialect {dialect}")


def execute(sql: str, dialect: str, path: str, timeout_s: float = DEFAULT_TIMEOUT_S,
            row_limit: int = DEFAULT_ROW_LIMIT) -> ExecResult:
    t0 = time.time()
    try:
        con = connect_readonly(dialect, path)
    except Exception as e:  # noqa: BLE001
        return ExecResult(False, error=f"connect failed: {e}")
    timed_out = threading.Event()
    try:
        if dialect == "sqlite":
            deadline = t0 + timeout_s

            def _progress() -> int:
                if time.time() > deadline:
                    timed_out.set()
                    return 1  # non-zero aborts the statement
                return 0

            con.set_progress_handler(_progress, 10_000)
            cur = con.execute(sql)
        else:
            timer = threading.Timer(timeout_s, lambda: (timed_out.set(), con.interrupt()))
            timer.start()
            try:
                cur = con.execute(sql)
            finally:
                timer.cancel()
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(row_limit + 1)
        truncated = len(rows) > row_limit
        return ExecResult(True, cols, [tuple(r) for r in rows[:row_limit]], elapsed_s=round(time.time() - t0, 4),
                          truncated=truncated)
    except Exception as e:  # noqa: BLE001
        msg = f"timeout after {timeout_s:.0f}s" if timed_out.is_set() else f"{type(e).__name__}: {e}"
        return ExecResult(False, error=msg[:500], elapsed_s=round(time.time() - t0, 4), timed_out=timed_out.is_set())
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def execute_all(sql: str, dialect: str, path: str, timeout_s: float = DEFAULT_TIMEOUT_S) -> tuple[list[tuple] | None, str]:
    """Fetch the full result (used only by the EX evaluator, mirroring BIRD's official script)."""
    r = execute(sql, dialect, path, timeout_s=timeout_s, row_limit=10_000_000)
    return (r.rows if r.ok else None), r.error
