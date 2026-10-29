"""Execution accuracy (EX) exactly as BIRD's official evaluation_ex.py computes it:
    EX = 1  iff  set(rows(predicted SQL)) == set(rows(gold SQL))
executed on the original SQLite DB with a 30 s timeout; any error/timeout counts as 0.
Plus a business-set variant for Olist that compares floats at 4 significant digits.
"""

from __future__ import annotations

import math
import pickle

from analytics_agent.config import DATA
from analytics_agent.executor import execute_all

GOLD_CACHE = DATA / "gold_cache"


def bird_ex(pred_rows: list[tuple] | None, gold_rows: list[tuple]) -> int:
    if pred_rows is None:
        return 0
    return int(set(pred_rows) == set(gold_rows))


def _round_sig(v, sig: int = 4):
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if v == 0:
            return 0.0
        return round(v, sig - int(math.floor(math.log10(abs(v)))) - 1)
    if isinstance(v, int) and not isinstance(v, bool):
        return float(v) if abs(v) < 1e15 else v
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10] if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 else v.isoformat()
    return v


def business_ex(pred_rows: list[tuple] | None, gold_rows: list[tuple]) -> int:
    """Set-of-rows equality, floats/ints compared at 4 significant digits, dates by ISO string."""
    if pred_rows is None:
        return 0
    norm = lambda rows: {tuple(_round_sig(v) for v in r) for r in rows}  # noqa: E731
    return int(norm(pred_rows) == norm(gold_rows))


def gold_rows(task_id: str, gold_sql: str, dialect: str, path: str) -> list[tuple]:
    GOLD_CACHE.mkdir(parents=True, exist_ok=True)
    p = GOLD_CACHE / f"{task_id}.pkl"
    if p.exists():
        return pickle.loads(p.read_bytes())
    rows, err = execute_all(gold_sql, dialect, path, timeout_s=120)
    if rows is None:
        raise RuntimeError(f"gold SQL failed for {task_id}: {err}")
    p.write_bytes(pickle.dumps(rows))
    return rows


def score(task, pred_sql: str, timeout_s: float = 30.0) -> tuple[int, str]:
    """Return (EX, prediction error) for one task, re-executing the final SQL from scratch."""
    gold = gold_rows(task.id, task.gold_sql, task.dialect, task.db_path)
    if not pred_sql:
        return 0, "no SQL"
    rows, err = execute_all(pred_sql, task.dialect, task.db_path, timeout_s=timeout_s)
    fn = bird_ex if task.dialect == "sqlite" else business_ex
    return fn(rows, gold), err


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def mcnemar_exact(a: list[int], b: list[int]) -> dict:
    """Paired exact McNemar test on per-question 0/1 outcomes of two systems on the same questions."""
    b01 = sum(1 for x, y in zip(a, b, strict=True) if x == 0 and y == 1)
    b10 = sum(1 for x, y in zip(a, b, strict=True) if x == 1 and y == 0)
    n = b01 + b10
    if n == 0:
        return {"only_a": b10, "only_b": b01, "p_value": 1.0}
    k = min(b01, b10)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n * 2
    return {"only_a": b10, "only_b": b01, "p_value": round(min(1.0, p), 5)}


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = (len(s) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


