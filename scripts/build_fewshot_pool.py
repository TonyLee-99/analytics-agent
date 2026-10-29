"""Few-shot pool for BIRD = the full BIRD dev set (1,534 verified question/SQL pairs on the SAME 11
databases) minus anything that overlaps the Mini-Dev questions we evaluate on.

This mirrors the enterprise setting: the warehouse has a log of certified queries, and new questions
are answered with the most similar certified examples. Decontamination rules (all must pass):
1. the dev question_id is not one of the 500 ids of the original 2024/2025 Mini-Dev (archive release);
2. its question is not near-duplicate of any 2026 Mini-Dev question on the same DB
   (token Jaccard < 0.5);
3. its gold SQL is not near-duplicate of any 2026 Mini-Dev gold SQL on the same DB (token Jaccard < 0.7).
Run: uv run python scripts/build_fewshot_pool.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.config import BIRD_ARCHIVE_MINIDEV, BIRD_DEV_FULL, BIRD_QUESTIONS, EVAL_SETS  # noqa: E402


def toks(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", s.lower()))


def jac(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


def main() -> None:
    dev = json.load(open(BIRD_DEV_FULL))
    raw = open(BIRD_ARCHIVE_MINIDEV).read()
    archive = json.loads(raw) if raw.lstrip().startswith("[") else [json.loads(x) for x in raw.splitlines() if x]
    archive_ids = {r["question_id"] for r in archive}
    new = [json.loads(x) for x in open(BIRD_QUESTIONS)]
    by_db: dict[str, list[tuple[set, set]]] = {}
    for r in new:
        by_db.setdefault(r["db_id"], []).append((toks(r["question"]), toks(r["SQL"])))
    kept, drop = [], {"archive_id": 0, "question_dup": 0, "sql_dup": 0}
    for r in dev:
        if r["question_id"] in archive_ids:
            drop["archive_id"] += 1
            continue
        q, s = toks(r["question"]), toks(r["SQL"])
        mq = max(jac(q, a) for a, _ in by_db[r["db_id"]])
        ms = max(jac(s, b) for _, b in by_db[r["db_id"]])
        if mq >= 0.5:
            drop["question_dup"] += 1
            continue
        if ms >= 0.7:
            drop["sql_dup"] += 1
            continue
        kept.append({"question_id": r["question_id"], "db_id": r["db_id"], "question": r["question"],
                     "evidence": r.get("evidence", ""), "SQL": r["SQL"], "max_q_jaccard": round(mq, 3),
                     "max_sql_jaccard": round(ms, 3)})
    out = EVAL_SETS / "bird_fewshot_pool.jsonl"
    with open(out, "w") as f:
        for r in kept:
            f.write(json.dumps(r) + "\n")
    print(f"dev={len(dev)} kept={len(kept)} dropped={drop} -> {out}")


if __name__ == "__main__":
    main()
