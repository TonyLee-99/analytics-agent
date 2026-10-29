"""The text-to-SQL agent: an explicit state machine (no framework).

    RETRIEVE -> GENERATE -> [CLARIFY -> END]
                         -> VALIDATE -> EXECUTE -> CHECK --ok--> (SELF-CONSISTENCY) -> ANSWER -> END
                                ^                    |
                                +---- CORRECT <------+  (error / empty / all-NULL / suspicious size), <= N rounds

Why no LangGraph: the graph is small and linear with one loop; a 40-line loop is easier to test,
trace and reason about than a framework graph, and has zero dependency risk. LinkedIn's SQL Bot uses
LangGraph because it has many cooperating agents and human-in-the-loop UI states; we document that
trade-off in the README.
"""

from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from analytics_agent import prompts as P
from analytics_agent.catalog import DatabaseCatalog, get_catalog
from analytics_agent.executor import ExecResult, execute
from analytics_agent.guardrails import validate_sql
from analytics_agent.llm.base import LLMClient, LLMResponse
from analytics_agent.retrieval import ExampleStore, SchemaRetriever
from analytics_agent.semantic.compiler import SemanticError, compile_query, load_layer
from analytics_agent.tracing import Trace
from analytics_agent.value_hints import value_hints

ENGINE = {"sqlite": "SQLite", "duckdb": "DuckDB"}


@dataclass
class AgentConfig:
    name: str = "agent"
    schema_mode: str = "retrieved"  # "official" (BIRD fixed prompt) | "full" | "retrieved"
    retrieval_method: str = "bm25"
    top_k: int = 6
    few_shot: int = 0  # number of retrieved verified examples
    max_corrections: int = 0  # self-correction rounds
    n_samples: int = 1  # >1 enables self-consistency voting
    allow_clarify: bool = False
    semantic_mode: str = "raw"  # "raw" | "glossary" | "semantic"
    summarize: bool = False
    row_limit: int = 1000
    timeout_s: float = 30.0
    huge_result_rows: int = 1000  # result this large (truncated) is "suspicious"
    sample_workers: int = 4
    describe_columns: bool = True  # column descriptions + sample values in the schema text
    use_hints: bool = True  # pass the benchmark's evidence/hint text


@dataclass
class Candidate:
    sample: int
    sql: str = ""
    raw_text: str = ""
    result: ExecResult | None = None
    corrections: int = 0
    problems: list[str] = field(default_factory=list)
    metric_query: dict | None = None
    used_semantic: bool = False

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok and self.result.n_rows > 0


@dataclass
class AgentAnswer:
    question: str
    status: str  # "answered" | "clarify" | "failed" | "blocked"
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    summary: str = ""
    clarification: str = ""
    confidence: float | None = None
    signals: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ---------------------------------------------------------------- parsing helpers
_SQL_BLOCK = re.compile(r"```(?:sql|sqlite|duckdb)?\s*\n(.*?)```", re.S | re.I)
_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)```", re.S | re.I)


def extract_sql(text: str) -> str:
    blocks = _SQL_BLOCK.findall(text or "")
    if blocks:
        sql = blocks[-1].strip()
    else:  # no fence: take from the last line that starts with SELECT/WITH
        m = list(re.finditer(r"(?ims)^\s*(with|select)\b.*", text or ""))
        sql = m[-1].group(0).strip() if m else ""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return "\n".join(lines).strip().rstrip(";").strip()


def extract_clarify(text: str) -> str | None:
    m = re.search(r"^\s*CLARIFY:\s*(.+)$", text or "", re.M)
    return m.group(1).strip() if m else None


def extract_json(text: str) -> dict | None:
    import json

    blocks = _JSON_BLOCK.findall(text or "")
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except json.JSONDecodeError:
        return None


def result_signature(rows: list[tuple]) -> frozenset:
    """Order-insensitive result identity (same notion as BIRD EX), floats rounded for voting."""
    def norm(v):
        return round(v, 6) if isinstance(v, float) else v
    return frozenset(tuple(norm(v) for v in r) for r in rows)


# ---------------------------------------------------------------- the agent
class Agent:
    def __init__(self, llm: LLMClient, config: AgentConfig | None = None, examples: ExampleStore | None = None,
                 trace_path=None):
        self.llm = llm
        self.cfg = config or AgentConfig()
        self.examples = examples
        self.trace_path = trace_path
        self._retrievers: dict[str, SchemaRetriever] = {}

    # ---- helpers
    def _llm(self, trace: Trace, step: str, system: str, prompt: str, sample: int = 0) -> LLMResponse:
        with trace.span(f"llm.{step}", sample=sample) as s:
            r = self.llm.complete(system, prompt, sample=sample, tag=f"{self.cfg.name}:{step}")
            s.attrs["llm"] = r.to_dict() | {"text": None}
        return r

    def _retriever(self, cat: DatabaseCatalog) -> SchemaRetriever:
        key = f"{cat.db_id}:{self.cfg.retrieval_method}"
        if key not in self._retrievers:
            self._retrievers[key] = SchemaRetriever(cat, self.cfg.retrieval_method)
        return self._retrievers[key]

    def _system(self, dialect: str) -> str:
        s = P.SYSTEM_AGENT.format(engine=ENGINE[dialect])
        return s + (P.CLARIFY_RULE if self.cfg.allow_clarify else "")

    def _build_prompt(self, question: str, evidence: str, cat: DatabaseCatalog, tables: list[str] | None,
                      official_prompt: str | None) -> tuple[str, str]:
        if self.cfg.schema_mode == "official" and official_prompt:
            return P.SYSTEM_BASELINE, official_prompt
        schema = cat.render(tables, descriptions=self.cfg.describe_columns, samples=self.cfg.describe_columns)
        note = "" if tables is None else " (tables retrieved as relevant to the question)"
        if not self.cfg.use_hints:
            evidence = ""
        ex_txt = ""
        if self.cfg.few_shot and self.examples is not None:
            exs = self.examples.search(question + " " + evidence, cat.db_id, self.cfg.few_shot)
            if exs:
                ex_txt = P.EXAMPLES_HEADER + "\n".join(
                    P.EXAMPLE_ITEM.format(question=e.question, evidence=f" (hint: {e.evidence})" if e.evidence else "",
                                          sql=e.sql) for e in exs)
        gloss = ""
        if self.cfg.semantic_mode == "glossary":
            gloss = P.GLOSSARY_HEADER.format(catalog=load_layer().catalog_text())
        if self.cfg.semantic_mode == "semantic":
            system = P.SEMANTIC_SYSTEM.format(engine=ENGINE[cat.dialect])
            if self.cfg.allow_clarify:
                system += P.CLARIFY_RULE
            return system, P.SEMANTIC_GENERATE.format(catalog=load_layer().catalog_text(), schema=schema,
                                                      question=question)
        prompt = P.GENERATE.format(engine=ENGINE[cat.dialect], schema_note=note, schema=schema, examples=ex_txt,
                                   glossary=gloss, question=question,
                                   evidence=f"Hint: {evidence}\n" if evidence else "")
        return self._system(cat.dialect), prompt

    def _problem(self, res: ExecResult) -> str | None:
        if not res.ok:
            return f"execution error: {res.error}"
        if res.n_rows == 0:
            return "the query returned 0 rows; check filters, join keys and literal values (case, spelling, format)"
        if all(v is None for r in res.rows for v in r):
            return "the query returned only NULL values; check the aggregation inputs and join conditions"
        if res.truncated and res.n_rows >= self.cfg.huge_result_rows:
            return (f"the query returned more than {self.cfg.huge_result_rows} rows, which looks like a missing "
                    "filter/aggregation or a cartesian join")
        return None

    def _run_candidate(self, trace: Trace, sample: int, system: str, prompt: str, question: str, evidence: str,
                       cat: DatabaseCatalog, allowed: list[str], max_corr: int) -> Candidate:
        cand = Candidate(sample)
        r = self._llm(trace, "generate", system, prompt, sample)
        cand.raw_text = r.text
        if self.cfg.allow_clarify and sample == 0:
            clar = extract_clarify(r.text)
            if clar and not _SQL_BLOCK.search(r.text) and not _JSON_BLOCK.search(r.text):
                cand.problems.append("clarify")
                cand.raw_text = r.text
                cand.sql = ""
                cand.metric_query = {"clarify": clar}
                return cand
        sql = ""
        if self.cfg.semantic_mode == "semantic":
            mq = extract_json(r.text)
            if mq is not None:
                cand.metric_query, cand.used_semantic = mq, True
                try:
                    sql = compile_query(mq)
                except SemanticError as e:
                    cand.problems.append(f"semantic: {e}")
                    sql = ""
        if not sql and not cand.used_semantic:
            sql = extract_sql(r.text)
        for round_ in range(max_corr + 1):
            with trace.span("validate", sample=sample, round=round_) as s:
                v = validate_sql(sql, cat.dialect, allowed_tables=cat.table_names, row_limit=self.cfg.row_limit)
                s.attrs.update(ok=v.ok, errors=v.errors, warnings=v.warnings)
            if v.ok:
                with trace.span("execute", sample=sample, round=round_) as s:
                    res = execute(v.sql, cat.dialect, cat.path, self.cfg.timeout_s, self.cfg.row_limit)
                    s.attrs.update(ok=res.ok, n_rows=res.n_rows, error=res.error, db_s=res.elapsed_s)
                problem = self._problem(res)
            else:
                res = ExecResult(False, error="validation: " + v.reason)
                problem = (f"the SQL was rejected by the validator: {v.reason}" if sql else
                           "no SQL was produced" + (f" ({cand.problems[-1]})" if cand.problems else ""))
            cand.sql, cand.result = sql, res
            if problem is None or round_ == max_corr:
                if problem:
                    cand.problems.append(problem)
                break
            cand.problems.append(problem)
            cand.corrections += 1
            # the corrector always sees the FULL schema (the retrieved subset may have missed a table,
            # as LinkedIn's correction agent can fetch more tables) plus real column values on empty results
            extra = "Full schema:\n" + cat.render() + "\n"
            if sql and "0 rows" in problem:
                extra += value_hints(sql, cat)
            cprompt = P.CORRECT.format(question=question, evidence=f"Hint: {evidence}\n" if evidence else "",
                                       sql=sql or "(none)", problem=problem, extra_schema=extra)
            rr = self._llm(trace, f"correct{round_ + 1}", self._system(cat.dialect), cprompt, sample)
            sql = extract_sql(rr.text)
            cand.used_semantic = False
        return cand

    # ---- main entry point
    def ask(self, question: str, db_id: str, evidence: str = "", official_prompt: str | None = None,
            task_id: str | None = None) -> AgentAnswer:
        cfg = self.cfg
        trace = Trace(question, attrs={"db_id": db_id, "config": cfg.name, "task_id": task_id})
        cat = get_catalog(db_id)
        tables = None
        retr_score = None
        with trace.span("retrieve", method=cfg.retrieval_method, k=cfg.top_k) as s:
            if cfg.schema_mode == "retrieved":
                rr = self._retriever(cat).retrieve(question + " " + evidence, cfg.top_k)
                tables, retr_score = rr.tables, rr.top_score
                s.attrs.update(tables=tables, top_score=round(rr.top_score, 3), fk_added=rr.added_by_fk)
        system, prompt = self._build_prompt(question, evidence, cat, tables, official_prompt)
        trace.attrs["prompt_chars"] = len(prompt)

        if cfg.n_samples > 1:  # self-consistency: all samples run concurrently (sample 0 = the default answer)
            with ThreadPoolExecutor(max_workers=cfg.sample_workers) as pool:
                futs = [pool.submit(self._run_candidate, trace, i, system, prompt, question, evidence, cat,
                                    cat.table_names, cfg.max_corrections if i == 0 else 0)
                        for i in range(cfg.n_samples)]
                cands = [f.result() for f in futs]
        else:
            cands = [self._run_candidate(trace, 0, system, prompt, question, evidence, cat, cat.table_names,
                                         cfg.max_corrections)]
        main = cands[0]
        if main.metric_query and "clarify" in main.metric_query:
            ans = AgentAnswer(question, "clarify", clarification=main.metric_query["clarify"],
                              signals={"retrieval_top_score": retr_score, "main_problems": main.problems})
            return self._finish(ans, trace)

        chosen, agreement, votes = self._vote(cands)
        signals = {
            "sc_agreement": agreement, "sc_votes": votes, "n_samples": len(cands),
            "main_in_majority": chosen is main or (main.ok and chosen.ok and
                                                   result_signature(main.result.rows) == result_signature(chosen.result.rows)),
            "corrections": main.corrections, "main_problems": main.problems,
            "final_ok": chosen.ok, "n_rows": chosen.result.n_rows if chosen.result else 0,
            "retrieval_top_score": retr_score, "used_semantic": chosen.used_semantic,
            "metric_query": chosen.metric_query,
        }
        res = chosen.result
        status = "answered" if (res is not None and res.ok) else "failed"
        if res is not None and not res.ok and res.error.startswith("validation"):
            status = "blocked" if any(w in res.error for w in ("only read", "forbidden", "not allowed", "schema not")) \
                else "failed"
        ans = AgentAnswer(question, status, sql=chosen.sql, columns=res.columns if res else [],
                          rows=res.rows if res else [], signals=signals, error=(res.error if res and not res.ok else ""))
        ans.sql_main = main.sql  # type: ignore[attr-defined]
        if cfg.summarize and status == "answered":
            sp = P.SUMMARIZE.format(question=question, sql=chosen.sql, n_rows=res.n_rows,
                                    truncated=", truncated" if res.truncated else "", preview=res.preview(15))
            ans.summary = self._llm(trace, "summarize", P.SUMMARIZE_SYSTEM, sp).text.strip()
        return self._finish(ans, trace)

    @staticmethod
    def _vote(cands: list[Candidate]) -> tuple[Candidate, float, list[int]]:
        """Self-consistency: group candidates by execution result, pick the biggest group."""
        n = len(cands)
        if n == 1:
            c = cands[0]
            return c, (1.0 if c.ok else 0.0), [1 if c.ok else 0]
        groups: dict[frozenset, list[Candidate]] = {}
        for c in cands:
            if c.ok:
                groups.setdefault(result_signature(c.result.rows), []).append(c)
        if not groups:
            return cands[0], 0.0, []
        ranked = sorted(groups.values(), key=lambda g: (-len(g), min(x.sample for x in g)))
        best = ranked[0]
        chosen = min(best, key=lambda x: x.sample)
        return chosen, len(best) / n, sorted((len(g) for g in groups.values()), reverse=True)

    def _finish(self, ans: AgentAnswer, trace: Trace) -> AgentAnswer:
        tot = trace.llm_totals()
        # modeled end-to-end latency: samples run in parallel, steps within a sample run in sequence
        per_sample: dict[int, float] = {}
        for sp in trace.spans:
            if "llm" in sp.attrs:
                k = sp.attrs.get("sample", 0)
                per_sample[k] = per_sample.get(k, 0.0) + sp.attrs["llm"]["latency_s"]
        db = sum(sp.attrs.get("db_s", 0.0) for sp in trace.spans if sp.name == "execute" and sp.attrs.get("sample", 0) == 0)
        seq, par = max(per_sample.values(), default=0.0), []
        tot["latency_s"] = round(max(seq, max(par, default=0.0)) + db, 3)
        trace.attrs.update(status=ans.status, latency_s=tot["latency_s"])
        ans.trace = trace.to_dict()
        ans.signals["usage"] = tot
        if self.trace_path:
            trace.write(self.trace_path)
        return ans


def answer_confidence(signals: dict[str, Any]) -> float:
    """Heuristic confidence used by the API when no calibrated model is loaded (see confidence.py)."""
    c = signals.get("sc_agreement", 0.0) if signals.get("n_samples", 1) > 1 else (0.7 if signals.get("final_ok") else 0.0)
    c -= 0.1 * signals.get("corrections", 0)
    return max(0.0, min(1.0, c))


def vote_counter(rows_list: list[list[tuple]]) -> Counter:
    return Counter(result_signature(r) for r in rows_list)


class HybridAgent:
    """Metrics-first routing: try the governed semantic layer; if the model decides the question is
    not expressible as governed metrics (it falls back to raw SQL), hand the question to the documented
    raw-SQL agent (schema + metric glossary) instead of using the semantic prompt's own fallback SQL.

    Motivation (measured, see docs/semantic_layer_experiment.md): the semantic arm was best on metric
    questions but its fallback SQL over-applied metric filters to non-metric questions."""

    def __init__(self, semantic: Agent, raw: Agent):
        self.semantic, self.raw = semantic, raw
        self.cfg = semantic.cfg

    def ask(self, question: str, db_id: str, evidence: str = "", official_prompt: str | None = None,
            task_id: str | None = None) -> AgentAnswer:
        a = self.semantic.ask(question, db_id, evidence, task_id=task_id)
        if a.status == "clarify" or a.signals.get("used_semantic"):
            a.signals["route"] = "semantic"
            return a
        b = self.raw.ask(question, db_id, evidence, task_id=task_id)
        ua, ub = a.signals.get("usage", {}), b.signals.get("usage", {})
        b.signals["usage"] = {k: round(ua.get(k, 0) + ub.get(k, 0), 6) for k in set(ua) | set(ub)}
        b.signals["route"] = "raw_fallback"
        return b
