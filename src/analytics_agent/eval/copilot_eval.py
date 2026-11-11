"""120-question evaluation of the v2 copilot and its baselines.

    uv run python -m analytics_agent.eval.copilot_eval run --system copilot      # live, cached
    uv run python -m analytics_agent.eval.copilot_eval run --system docs_only
    uv run python -m analytics_agent.eval.copilot_eval run --system v1_sql
    uv run python -m analytics_agent.eval.copilot_eval judge --system copilot     # LLM judge (Haiku)
    uv run python -m analytics_agent.eval.copilot_eval score                      # results/v2/eval.json

Scoring (per item type, fixed before the first scored run):
- sql (strict, the headline): deterministic. The rows the system computed must match the frozen gold rows (gold SQL run as the
  owner restricted to the asker's departments): same number of rows; every gold row has a system row
  containing all its numbers (1% relative tolerance; a fraction may be reported as a percentage) and
  its text labels. A clarification counts as wrong.
- sql (key value, secondary): same, but only the last numeric value of each gold row must match.
- docs: LLM judge (Claude Haiku 5.5) against the reference answer and key facts; "correct" only.
- mixed: SQL match AND judge "correct".
- clarify: correct iff the system asked a clarification question.
Route accuracy: docs->docs, sql->sql, mixed->both, clarify->clarification status.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from analytics_agent.copilot.settings import V2_EVAL_SETS, V2_RESULTS

SETS = ["questions_author", "questions_codex", "questions_holdout"]
RUNS = V2_RESULTS / "runs"
EXPECTED_ROUTE = {"docs": "docs", "sql": "sql", "mixed": "both", "clarify": "clarify"}

JUDGE_SYSTEM = """You grade answers from an assistant for City of Chicago 311 analysts against a reference.
Reply with one JSON object in a ```json fence: {"verdict": "correct" | "partially_correct" | "incorrect", "reason": "..."}.
- correct: the answer states the key facts of the reference (wording may differ) and states nothing that contradicts it.
- partially_correct: some key facts present, others missing or wrong.
- incorrect: key facts missing or wrong, or the answer says it cannot answer.
Extra correct detail is fine. For numbers from a database, judge only whether the document facts are right;
numbers are checked separately."""

JUDGE_PROMPT = """Question: {question}

Reference answer: {reference}
Key facts: {facts}

Answer to grade:
{answer}"""


def stratified(items: list[dict], n: int) -> list[dict]:
    """First n items, round-robin over item types in file order (deterministic, keeps every type)."""
    by: dict[str, list[dict]] = {}
    for q in items:
        by.setdefault(q["type"], []).append(q)
    out, i = [], 0
    while len(out) < min(n, len(items)):
        for t in sorted(by):
            if i < len(by[t]) and len(out) < n:
                out.append(by[t][i])
        i += 1
    return sorted(out, key=lambda q: q["id"])


def load_items(names: list[str] = SETS, n_per_set: int | None = None) -> list[dict]:
    """n_per_set: stratified sample per question file (env AA_EVAL_N; default 15). 0 or 'all' = everything."""
    import os

    if n_per_set is None:
        v = os.environ.get("AA_EVAL_N", "15")
        n_per_set = 0 if v in ("all", "0") else int(v)
    out = []
    for n in names:
        sel = []
        gold = json.loads((V2_EVAL_SETS / f"gold_{n}.json").read_text())["items"]
        for line in (V2_EVAL_SETS / f"{n}.jsonl").read_text().splitlines():
            if line.strip():
                q = json.loads(line)
                q["set"] = n
                q["gold"] = gold.get(q["id"])
                sel.append(q)
        out += stratified(sel, n_per_set) if n_per_set else sel
    return out


# ------------------------------------------------------------ deterministic SQL scoring
def _nums(row: list) -> list[float]:
    out = []
    for v in row:
        if isinstance(v, bool):
            continue
        if isinstance(v, int | float):
            out.append(float(v))
        elif isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
            out.append(float(v))
    return out


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _labels(row: list) -> list[str]:
    out = []
    for v in row:
        if isinstance(v, str) and not re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
            t = v.strip().lower()
            out.append(t[:10] if DATE_RE.match(t) else t)  # dates: compare the YYYY-MM-DD part only
    return out


def _close(a: float, b: float) -> bool:
    if math.isclose(a, b, rel_tol=0.01, abs_tol=1e-6):
        return True
    # a fraction may be reported as a percentage, only when the gold value is a fraction
    return 0 <= b <= 1 and math.isclose(a, b * 100, rel_tol=0.01, abs_tol=1e-4)


def _assign(gold_nums: list[float], sys_nums: list[float]) -> bool:
    """One-to-one: every gold number matched to a distinct system number (rows are small: backtracking)."""
    def go(i: int, used: frozenset) -> bool:
        if i == len(gold_nums):
            return True
        return any(j not in used and _close(sys_nums[j], gold_nums[i]) and go(i + 1, used | {j})
                   for j in range(len(sys_nums)))
    return go(0, frozenset())


def rows_match(got: list[list], gold: list[list], key_only: bool = False) -> bool:
    """Same number of rows; each gold row matched to a distinct system row whose numbers cover the gold
    numbers one-to-one (1% tolerance) and whose text labels include the gold labels.
    key_only: compare only the last numeric value of each gold row (the asked-for figure)."""
    if gold is None or len(got) != len(gold):
        return False
    if key_only:
        gold = [[v for v in g if not _nums([v])] + _nums(g)[-1:] for g in gold]
    used: set[int] = set()
    for g in gold:
        gn, gl = _nums(g), _labels(g)
        hit = next((i for i, r in enumerate(got) if i not in used and _assign(gn, _nums(r))
                    and all(lab in _labels(r) for lab in gl)), None)
        if hit is None:
            return False
        used.add(hit)
    return True


# ------------------------------------------------------------ systems
_DEPS: list = []  # deployments built by make_system, so run() can persist new query embeddings


def make_system(name: str):
    if name in ("copilot", "docs_only", "copilot_rerank", "copilot_norecheck"):
        from analytics_agent.copilot.copilot import Copilot, build_deployment
        from analytics_agent.copilot.llm import make_llm, replay_mode
        from analytics_agent.copilot.replay import RecordingWarehouse, ReplayWarehouse

        if replay_mode():
            wh, sync = ReplayWarehouse(), False
        else:
            from analytics_agent.copilot.warehouse import Warehouse

            wh, sync = RecordingWarehouse(Warehouse()), True
        dep = build_deployment(warehouse=wh, wh_sync=sync, rerank=name == "copilot_rerank")
        _DEPS.append(dep)
        cp = Copilot(dep, llm=make_llm("haiku"), mode="docs_only" if name == "docs_only" else "full",
                     use_cache=False, trace_path=None)

        def ask(q: dict) -> dict:
            a = cp.ask(q["user"], q["question"])
            hits = [h["doc_id"] for s in a.trace.get("spans", []) if s["name"] == "retrieve" for h in s.get("hits", [])]
            return {"status": a.status, "route": a.route, "text": a.text, "citations": a.citations,
                    "retrieved_docs": hits, "sql": a.sql, "columns": a.columns, "rows": a.rows[:50],
                    "metrics_used": a.metrics_used, "notices": a.notices, "trace": a.trace}
        return ask, wh
    if name == "v1_sql":
        return make_v1(), None
    raise ValueError(name)


def make_v1():
    """v1's text-to-SQL agent (raw schema mode, self-correction) on a DuckDB copy of the same window."""
    from analytics_agent.agent import Agent, AgentConfig
    from analytics_agent.catalog import build_duckdb_catalog, register_catalog
    from analytics_agent.copilot.identity import Directory
    from analytics_agent.copilot.llm import make_llm
    from analytics_agent.copilot.settings import V2_STATE

    path = V2_STATE / "c311.duckdb"
    if not path.exists():
        raise SystemExit("run scripts/v2/build_v1_duckdb.py first")
    register_catalog(build_duckdb_catalog(str(path), "c311"))
    agent = Agent(make_llm("haiku"), AgentConfig(name="v1_baseline_c311", schema_mode="full", max_corrections=2,
                                                 summarize=True, allow_clarify=True))
    st = Directory.from_config().state

    def ask(q: dict) -> dict:
        u = st.users[q["user"]]
        depts = ", ".join(sorted(st.data_departments(q["user"]))) or "none"
        question = f"{q['question']}\n(Asked by {u['name']}, {u['title']}; their departments: {depts}.)"
        a = agent.ask(question, "c311")
        status = {"answered": "answered", "clarify": "clarify"}.get(a.status, a.status)
        return {"status": status, "route": "sql", "text": a.summary or a.clarification or a.error, "citations": [],
                "retrieved_docs": [], "sql": a.sql, "columns": a.columns,
                "rows": [[_j(v) for v in r] for r in a.rows[:50]], "metrics_used": [], "notices": [],
                "trace": a.trace}
    return ask


def _j(v):
    from decimal import Decimal

    if isinstance(v, Decimal | float):
        return round(float(v), 6)
    return v if v is None or isinstance(v, bool | int | str) else str(v)


def run(system: str, workers: int = 3, only: list[str] | None = None, sets: list[str] | None = None,
        fresh: bool = False) -> Path:
    items = [q for q in load_items(sets or SETS) if not only or q["id"] in only]
    ask, wh = make_system(system)
    RUNS.mkdir(parents=True, exist_ok=True)
    out_path = RUNS / f"eval__{system}.jsonl"
    done = {}
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            r = json.loads(line)
            if r.get("status") != "exception":
                done[r["id"]] = r

    def one(q):
        if q["id"] in done and not fresh:
            return done[q["id"]]
        t0 = time.time()
        try:
            r = ask(q)
        except Exception as e:  # noqa: BLE001 - an item failure is recorded, not fatal
            r = {"status": "exception", "text": f"{type(e).__name__}: {e}"[:500]}
        r.update({"id": q["id"], "set": q["set"], "system": system, "wall_s": round(time.time() - t0, 2)})
        print(f"{system} {q['id']} {r.get('status')} {r.get('route')}", flush=True)
        return r

    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(one, items))
    merged = {**done, **{r["id"]: r for r in results}}
    with open(out_path, "w") as f:
        for r in sorted(merged.values(), key=lambda r: r["id"]):
            f.write(json.dumps(r, default=str) + "\n")
    if wh is not None and hasattr(wh, "save"):
        wh.save()
    for dep in _DEPS:
        if dep.index.embedder.cache.dirty:
            dep.index.embedder.cache.save(additive=True)
        rr = dep.retriever.reranker
        if rr is not None and rr.dirty:
            rr.save()
    return out_path


def load_run(system: str) -> dict[str, dict]:
    p = RUNS / f"eval__{system}.jsonl"
    return {r["id"]: r for r in map(json.loads, p.read_text().splitlines())} if p.exists() else {}


# ------------------------------------------------------------ judge
def judge(system: str, workers: int = 3, model: str = "haiku") -> Path:
    from analytics_agent.copilot.copilot import parse_json
    from analytics_agent.copilot.llm import make_llm

    llm = make_llm(model)
    items = {q["id"]: q for q in load_items()}
    run_ = load_run(system)
    out_path = RUNS / f"judge__{system}.jsonl"

    def one(item_id):
        q, r = items[item_id], run_[item_id]
        if q["type"] not in ("docs", "mixed"):
            return None
        if r.get("status") != "answered":
            return {"id": item_id, "verdict": "incorrect", "reason": f"status {r.get('status')}", "auto": True}
        text = re.sub(r"\[S\d+\]", "", r.get("text") or "")
        resp = llm.complete(JUDGE_SYSTEM, JUDGE_PROMPT.format(question=q["question"], reference=q["reference_answer"],
                                                               facts="; ".join(q.get("key_facts") or []),
                                                               answer=text), tag="judge")
        v = parse_json(resp.text) or {}
        return {"id": item_id, "verdict": v.get("verdict", "unparsed"), "reason": v.get("reason", ""),
                "auto": False, "model": llm.model}

    with ThreadPoolExecutor(workers) as ex:
        res = [r for r in ex.map(one, sorted(i for i in run_ if i in items)) if r]
    with open(out_path, "w") as f:
        for r in res:
            f.write(json.dumps(r) + "\n")
    return out_path


def load_judge(system: str) -> dict[str, dict]:
    p = RUNS / f"judge__{system}.jsonl"
    return {r["id"]: r for r in map(json.loads, p.read_text().splitlines())} if p.exists() else {}


# ------------------------------------------------------------ scoring
def score_item(q: dict, r: dict, j: dict | None) -> dict[str, Any]:
    t = q["type"]
    status = r.get("status")
    route_ok = (status == "clarify") if t == "clarify" else (status != "clarify" and r.get("route") == EXPECTED_ROUTE[t])
    sql_ok = None
    if t in ("sql", "mixed"):
        sql_ok = status == "answered" and rows_match(r.get("rows") or [], (q.get("gold") or {}).get("rows"))
    sql_key_ok = None
    if t in ("sql", "mixed"):
        sql_key_ok = status == "answered" and rows_match(r.get("rows") or [], (q.get("gold") or {}).get("rows"), True)
    judge_ok = None
    if t in ("docs", "mixed"):
        judge_ok = bool(j and j.get("verdict") == "correct")
    if t == "clarify":
        correct = status == "clarify"
    elif t == "sql":
        correct = bool(sql_ok)
    elif t == "docs":
        correct = bool(judge_ok)
    else:
        correct = bool(sql_ok) and bool(judge_ok)
    correct_key = {"sql": bool(sql_key_ok), "mixed": bool(sql_key_ok) and bool(judge_ok)}.get(t, correct)
    gold_docs = q.get("gold_doc_ids") or []
    recall5 = None
    if t in ("docs", "mixed") and gold_docs:
        top = []
        for d in r.get("retrieved_docs") or []:
            if d not in top:
                top.append(d)
        recall5 = sum(1 for d in gold_docs if d in top[:5]) / len(gold_docs)
    cites = r.get("citations") or []
    ctx = set(r.get("retrieved_docs") or [])
    cite_valid = all(c["doc_id"] in ctx for c in cites)
    return {"id": q["id"], "set": q["set"], "type": t, "correct": correct, "correct_key_value": correct_key, "route_ok": route_ok, "sql_ok": sql_ok,
            "judge_ok": judge_ok, "recall5": recall5, "n_citations": len(cites), "citations_valid": cite_valid,
            "status": status, "false_clarify": t != "clarify" and status == "clarify"}


def mcnemar(a: list[bool], b: list[bool]) -> dict:
    from math import comb

    b01 = sum(1 for x, y in zip(a, b, strict=True) if not x and y)
    b10 = sum(1 for x, y in zip(a, b, strict=True) if x and not y)
    n = b01 + b10
    p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(0, min(b01, b10) + 1)) / 2 ** n)
    return {"a_only": b10, "b_only": b01, "p_exact": round(p, 4)}


def wilson(k: int, n: int) -> list[float]:
    if n == 0:
        return [0.0, 0.0]
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def score(systems: list[str] = ("copilot", "docs_only", "v1_sql"), write: bool = True) -> dict:
    items = load_items()
    out: dict[str, Any] = {"n_items": len(items), "systems": {}}
    per_sys_correct: dict[str, list[bool]] = {}
    for s in systems:
        run_, jud = load_run(s), load_judge(s)
        if not run_:
            continue
        rows = [score_item(q, run_.get(q["id"], {"status": "missing"}), jud.get(q["id"])) for q in items]
        per_sys_correct[s] = [r["correct"] for r in rows]
        summ: dict[str, Any] = {}
        for label, sel in [("all", rows), ("author", [r for r in rows if r["set"] == "questions_author"]),
                           ("codex_blind", [r for r in rows if r["set"] == "questions_codex"]),
                           ("codex_blind_holdout", [r for r in rows if r["set"] == "questions_holdout"]),
                           ("original_120", [r for r in rows if r["set"] != "questions_holdout"])]:
            if not sel:
                continue
            k = sum(r["correct"] for r in sel)
            kk_ = sum(r["correct_key_value"] for r in sel)
            block = {"n": len(sel), "correct": k, "accuracy": round(k / max(1, len(sel)), 4), "ci95": wilson(k, len(sel)),
                     "accuracy_key_value": round(kk_ / max(1, len(sel)), 4)}
            for t in ("docs", "sql", "mixed", "clarify"):
                st_ = [r for r in sel if r["type"] == t]
                if st_:
                    kk = sum(r["correct"] for r in st_)
                    block[f"{t}_accuracy"] = round(kk / len(st_), 4)
                    block[f"{t}_n"] = len(st_)
            summ[label] = block
        nonclar = [r for r in rows if r["type"] != "clarify"]
        clar = [r for r in rows if r["type"] == "clarify"]
        rec = [r["recall5"] for r in rows if r["recall5"] is not None]
        answered_docs = [r for r in rows if r["type"] in ("docs", "mixed") and r["status"] == "answered"]
        summ["route_accuracy"] = round(sum(r["route_ok"] for r in rows) / len(rows), 4)
        summ["recall_at_5"] = round(sum(rec) / len(rec), 4) if rec else None
        summ["citations_valid_rate"] = round(sum(r["citations_valid"] for r in rows) / len(rows), 4)
        summ["docs_answers_with_citation"] = round(
            sum(r["n_citations"] > 0 for r in answered_docs) / max(1, len(answered_docs)), 4)
        summ["clarify_rate_on_conflicts"] = round(sum(r["status"] == "clarify" for r in clar) / max(1, len(clar)), 4)
        all_items = {q["id"]: q for q in load_items(n_per_set=0)}  # controls run outside the sample
        explicit = [score_item(all_items[i], run_[i], jud.get(i)) for i in sorted(EXPLICIT_DEFINITION_IDS) if i in run_]
        summ["explicit_definition_controls"] = {"n": len(explicit), "correct": sum(r["correct"] for r in explicit)}
        summ["false_clarify_on_explicit_definitions"] = round(
            sum(r["false_clarify"] for r in explicit) / max(1, len(explicit)), 4) if explicit else None
        summ["false_clarify_rate_all"] = round(sum(r["false_clarify"] for r in nonclar) / max(1, len(nonclar)), 4)
        summ["statuses"] = _count(r["status"] for r in rows)
        summ["items"] = rows
        out["systems"][s] = summ
    if "copilot" in per_sys_correct:
        out["paired"] = {f"copilot_vs_{s}": mcnemar(per_sys_correct["copilot"], per_sys_correct[s])
                         for s in per_sys_correct if s != "copilot"}
    if write:
        (V2_RESULTS / "eval.json").write_text(json.dumps(out, indent=1))
    return out


# items whose question names one governed definition explicitly (controls for the conflict check)
EXPLICIT_DEFINITION_IDS = {"au-q57", "au-q58", "au-q59", "au-q60", "cx-q57", "cx-q58", "cx-q59", "cx-q60", "hx-q15"}


def _count(it) -> dict:
    d: dict = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "judge", "score"])
    ap.add_argument("--system", default="copilot")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--sets", nargs="*")
    ap.add_argument("--fresh", action="store_true", help="re-run items already recorded (LLM cache still applies)")
    a = ap.parse_args()
    if a.cmd == "run":
        print(run(a.system, a.workers, a.only, a.sets, a.fresh))
    elif a.cmd == "judge":
        print(judge(a.system, a.workers))
    else:
        s = score()
        print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "items"} for k, v in s["systems"].items()}, indent=1))
        print(json.dumps(s.get("paired"), indent=1))


if __name__ == "__main__":
    main()
