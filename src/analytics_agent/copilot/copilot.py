"""The copilot: identity -> cache -> plan -> (documents | warehouse | both) -> answer -> re-authorise.

Ordering rule (docs/DESIGN.md section 6): an answer is delivered only if, at delivery time, the live
directory and the live document source still grant every dependency it used. The same check runs on
cache hits. Anything that fails it is discarded, never "patched".
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from analytics_agent.copilot import prompts as P
from analytics_agent.copilot.caches import AnswerCache, CacheEntry
from analytics_agent.copilot.departments import DEPT_CODES
from analytics_agent.copilot.docsource import DocSource
from analytics_agent.copilot.identity import ALL_DEPTS, Directory
from analytics_agent.copilot.index import Embedder, IndexSync
from analytics_agent.copilot.llm import make_llm, v2_key
from analytics_agent.copilot.retrieve import Retrieval, Retriever
from analytics_agent.copilot.settings import ROOT, WINDOW_START
from analytics_agent.copilot.sqlguard import check_sql
from analytics_agent.semantic.compiler import SemanticError, compile_query, load_layer
from analytics_agent.tracing import Trace

LAYER_PATH = ROOT / "src" / "analytics_agent" / "semantic" / "chicago311.yaml"
TRACE_PATH = ROOT / "traces" / "copilot.jsonl"
CODE_TO_NAME = {v: k for k, v in DEPT_CODES.items()}
TAG_RE = re.compile(r"\[S(\d+)\]")
DATA_END = "2026-11-03"
# planners write operators as words surprisingly often ("eq"); found on the development half of the eval
OP_ALIASES = {"eq": "=", "equals": "=", "==": "=", "ne": "!=", "neq": "!=", "not_eq": "!=", "gt": ">", "gte": ">=",
              "ge": ">=", "lt": "<", "lte": "<=", "le": "<=", "not_in": "not in", "nin": "not in", "contains": "like"}


def definition_signals(question_lower: str, variants: dict[str, dict]) -> list[str]:
    """Variants of a metric family the question explicitly asks for.

    A variant is named when one of its literal selector phrases or one of its `signals` (regexes for how
    people state that definition: "only ... completed", "excluding canceled", "status Completed",
    "including canceled", "of all requests", "open ... as misses") occurs in the question. If both or
    neither variant is named, the caller asks. Deterministic on purpose: the model never chooses."""
    named = []
    for name, meta in variants.items():
        if any(sel in question_lower for sel in meta.get("selectors", [])) or any(
                re.search(rx, question_lower) for rx in meta.get("signals", [])):
            named.append(name)
    return named


_SENT_RE = re.compile(r".*?(?:[.!?]+(?=\s|$)|$)")  # a sentence ends at . ! ? followed by space/end (keeps 3.5)
_BRACKET_META = re.compile(r"\[[^\]]*\b(?:SOURCES|DATA|NOTICES)\b[^\]]*\]|\[DATA\]")
_CAPS_INTERNAL = re.compile(r"\b(?:SOURCES|DATA|NOTICES)\b|warehouse_results|</?documents>")
NOT_FOUND = "I could not find that in the documents or data you can access."


def scrub_internal(text: str) -> tuple[str, list[str]]:
    """Output contract: drop bracketed meta notes and any sentence that names a prompt section.

    Deterministic last line of defence behind the prompt rule ("never mention input sections"). Only the
    upper-case section names count, so ordinary words ("the data shows") are untouched. Returns the
    cleaned text and the removed fragments (recorded in the trace)."""
    removed = [m.group(0) for m in _BRACKET_META.finditer(text or "")]
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", _BRACKET_META.sub("", text or ""))
    out_lines = []
    for line in text.split("\n"):
        keep = []
        for sent in _SENT_RE.findall(line):
            if not sent:
                continue
            if _CAPS_INTERNAL.search(sent):
                removed.append(sent.strip())
            else:
                keep.append(sent)
        joined = "".join(keep).strip()
        if joined or not line.strip():
            out_lines.append(joined)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    return (cleaned or NOT_FOUND), removed


@dataclass
class Answer:
    status: str  # answered | clarify | denied | error
    text: str
    route: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    metric_query: dict | None = None
    metrics_used: list[str] = field(default_factory=list)
    doc_deps: dict[str, int] = field(default_factory=dict)
    dept_deps: list[str] = field(default_factory=list)
    trace_id: str = ""
    cached: bool = False
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("trace", None)
        return d


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return round(float(v), 6)
    if isinstance(v, float):
        return round(v, 6)
    if v is None or isinstance(v, bool | int | str):
        return v
    return str(v)


def parse_json(text: str) -> dict | None:
    m = re.findall(r"```(?:json)?\s*\n(.*?)```", text or "", re.S)
    blob = m[-1] if m else text
    try:
        start, end = blob.index("{"), blob.rindex("}")
        return json.loads(blob[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        return None


@dataclass
class Deployment:
    directory: Directory
    source: DocSource
    index: IndexSync
    retriever: Retriever
    warehouse: Any  # Warehouse | replay executor
    wh_sync: Any = None
    clock: Any = time.time


def build_deployment(*, lag_s: float = 0.0, recheck: bool = True, rerank: bool = False, warehouse: Any = None,
                     wh_sync: bool = True, clock=time.time) -> Deployment:
    from analytics_agent.copilot.identity import DirectoryReplica

    directory = Directory.from_config(clock=clock)
    source = DocSource.from_corpus(clock=clock)
    index = IndexSync(source, directory, Embedder(), lag_s=lag_s)
    index.sync(now=clock() + lag_s)  # bootstrap: the initial load counts as fully synced
    sync = None
    if warehouse is None:
        from analytics_agent.copilot.warehouse import Warehouse

        warehouse = Warehouse()
    if wh_sync:
        from analytics_agent.copilot.warehouse import WarehouseSync

        sync = WarehouseSync(DirectoryReplica(directory, lag_s))
        sync.sync(now=clock() + lag_s)
    return Deployment(directory, source, index, Retriever(index, directory, source, rerank=rerank, recheck=recheck),
                      warehouse, sync, clock)


class Copilot:
    def __init__(self, dep: Deployment, llm=None, mode: str = "full", use_cache: bool = True, k: int = 6,
                 delivery_check: bool = True, trace_path: Path | None = TRACE_PATH, purge_llm_on_delete: bool = False):
        self.dep = dep
        self.llm = llm or make_llm("haiku")
        self.mode = mode  # full | docs_only | sql_only
        self.layer = load_layer(LAYER_PATH)
        self.k = k
        self.delivery_check = delivery_check
        self.trace_path = trace_path
        from analytics_agent.copilot.warehouse import POLICY_VERSION

        self.cache = AnswerCache(dep.directory, dep.source, POLICY_VERSION) if use_cache else None
        self.llm_deps: dict[str, set[str]] = {}  # doc_id -> LLM cache keys whose prompt contained its text
        self.purge_llm_on_delete = purge_llm_on_delete
        self.purged_llm_keys: list[str] = []
        self.live_depts_fn = None  # ablation hook only (red team): replaces the live entitlement lookup
        dep.index.on_purge.append(self._purge_llm_for_doc)

    # ------------------------------------------------------------ helpers
    def _catalog(self) -> str:
        lines = [self.layer.catalog_text().replace("(purchase timestamp)", "(created_at)")]
        types = json.loads((ROOT / "config" / "sr_types.json").read_text())
        lines.append("sr_type VALUES (exact spelling, owner dept): " + "; ".join(
            f"{t['sr_type']} ({t['dept_code']})" for t in types))
        fams: dict[str, list[str]] = {}
        for name, m in self.layer.metrics.items():
            if m.get("family"):
                fams.setdefault(m["family"], []).append(name)
        for fam, names in fams.items():
            lines.append(f"NOTE: '{fam}' has {len(names)} governed definitions: {', '.join(names)}.")
        return "\n".join(lines)

    def _purge_llm_for_doc(self, doc_id: str) -> None:
        keys = self.llm_deps.pop(doc_id, set())
        if not keys:
            return
        self.purged_llm_keys.extend(sorted(keys))
        cache = getattr(self.llm, "cache", None)
        if self.purge_llm_on_delete and cache is not None:
            for k in keys:
                p = cache._path(k)
                if p.exists():
                    p.unlink()

    def _llm(self, trace: Trace, step: str, system: str, prompt: str):
        with trace.span(step) as s:
            r = self.llm.complete(system, prompt, tag=f"copilot:{step}")
            s.attrs["llm"] = {k: v for k, v in r.to_dict().items() if k != "text"}  # usage only, no text
        return r

    def resolve_conflicts(self, question: str, metrics: list[str]) -> tuple[list[str], str | None]:
        """Pick the governed variant the question names; ask when a family is ambiguous."""
        q = question.lower()
        out = []
        for m in metrics:
            meta = self.layer.metrics.get(m, {})
            fam = meta.get("family")
            if not fam:
                out.append(m)
                continue
            variants = [n for n, x in self.layer.metrics.items() if x.get("family") == fam]
            named = definition_signals(q, {n: self.layer.metrics[n] for n in variants})
            if len(named) == 1:
                out.append(named[0])
                continue
            opts = "; ".join(f"*{self.layer.metrics[n]['label']}* (owner: {self.layer.metrics[n].get('owner', '?')}): "
                             f"{self.layer.metrics[n]['description']}" for n in variants)
            return [], (f"Two governed definitions of '{fam.replace('_', ' ')}' exist and they give different "
                        f"numbers. Which one do you want? {opts}")
        return list(dict.fromkeys(out)), None

    # ------------------------------------------------------------ main entry
    def ask(self, user_id: str, question: str, *, slack_user: str | None = None) -> Answer:
        t0 = time.time()
        trace = Trace(question)
        st = self.dep.directory.state
        trace.attrs.update({"user": user_id, "slack_user": slack_user, "mode": self.mode,
                            "directory_version": st.version})
        if os.environ.get("AA_COPILOT_DISABLED"):  # kill switch (docs/ROLLOUT.md)
            return self._finish(Answer("denied", "The assistant is temporarily disabled."), trace, t0, store=False)
        user = st.users.get(user_id)
        if not user or not user["active"]:
            return self._finish(Answer("denied", "You are not authorised to use this assistant."), trace, t0)

        if self.cache is not None:
            hit = self.cache.get(user_id, question)
            if hit is not None:
                a = Answer(**{k: v for k, v in hit.answer.items() if k in Answer.__dataclass_fields__})
                a.cached = True
                trace.attrs["cache"] = "hit"
                return self._finish(a, trace, t0, store=False)
            trace.attrs["cache"] = "miss"

        live_depts = self.live_depts_fn(user_id) if self.live_depts_fn else st.data_departments(user_id)
        plan = self._plan(trace, user, question, live_depts)
        route = plan.get("route", "both")
        if os.environ.get("AA_DISABLE_SQL") and route in ("sql", "both"):
            route = "docs"
        elif os.environ.get("AA_DISABLE_DOCS") and route in ("docs", "both"):
            route = "sql"
        if self.mode == "docs_only":
            route = "docs"
        elif self.mode == "sql_only":
            route = "sql"
        if route not in ("docs", "sql", "both"):
            route = "both"
        trace.attrs["route"] = route
        notices: list[str] = []
        ans = Answer("answered", "", route=route)

        # ---------------- warehouse path
        data_block = "(none)"
        if route in ("sql", "both"):
            res = self._sql_path(trace, user_id, question, plan, live_depts, notices, ans)
            if ans.status == "clarify":
                return self._finish(ans, trace, t0)
            if res is not None:
                data_block = res

        # ---------------- document path
        retrieval: Retrieval | None = None
        sources_block = "(none)"
        tag_map: dict[str, dict[str, Any]] = {}
        if route in ("docs", "both"):
            with trace.span("retrieve") as s:
                retrieval = self.dep.retriever.search(user_id, plan.get("search_query") or question, k=self.k)
                s.attrs.update({"n_allowed_chunks": retrieval.n_allowed_chunks,
                                "n_total_chunks": retrieval.n_total_chunks,
                                "dropped_by_recheck": retrieval.dropped_by_recheck,
                                "hits": [{"chunk_id": h.chunk.chunk_id, "doc_id": h.chunk.doc_id,
                                          "score": round(h.score, 5), "bm25_rank": h.bm25_rank,
                                          "dense_rank": h.dense_rank} for h in retrieval.hits]})
            parts = []
            for i, h in enumerate(retrieval.hits, 1):
                c = h.chunk
                tag_map[str(i)] = {"tag": f"S{i}", "doc_id": c.doc_id, "chunk_id": c.chunk_id, "title": c.title,
                                   "url": c.url, "start": c.start, "end": c.end, "doc_version": c.doc_version}
                parts.append(f"[S{i}] {c.title} ({c.url})\n{c.text}")
                ans.doc_deps[c.doc_id] = c.doc_version
            sources_block = "\n\n".join(parts) if parts else "(none)"

        # ---------------- answer
        notice_text = ("Tell the user:\n" + "\n".join(f"- {n}" for n in notices) + "\n\n") if notices else ""
        prompt = P.ANSWER_PROMPT.format(question=question, notices=notice_text, sources=sources_block, data=data_block)
        r = self._llm(trace, "answer", P.ANSWER_SYSTEM, prompt)
        self._track_llm_deps(ans.doc_deps, P.ANSWER_SYSTEM, prompt)
        text, bad = r.text.strip(), self._bad_tags(r.text, tag_map)
        if bad:
            trace.attrs["invalid_citations_first_draft"] = sorted(bad)
            prompt2 = prompt + "\n\n" + P.REGEN_NOTE.format(bad=", ".join(f"[S{b}]" for b in sorted(bad)))
            r = self._llm(trace, "answer_regen", P.ANSWER_SYSTEM, prompt2)
            self._track_llm_deps(ans.doc_deps, P.ANSWER_SYSTEM, prompt2)
            text, bad = r.text.strip(), self._bad_tags(r.text, tag_map)
            if bad:
                text = "I could not produce an answer grounded in the documents you can access."
                ans.status = "error"
        text, removed = scrub_internal(text)
        if removed:
            trace.attrs["internal_text_removed"] = removed
        used = sorted({m for m in TAG_RE.findall(text) if m in tag_map}, key=int)
        ans.citations = [tag_map[m] for m in used]
        ans.text = text
        ans.notices = notices

        # ---------------- delivery-time re-authorisation
        if self.delivery_check:
            ok, why = self.authorised(user_id, ans.doc_deps, set(ans.dept_deps))
            trace.attrs["delivery_check"] = why
            if not ok:
                ans = Answer("denied", "Your access changed while this answer was being prepared, so it was "
                                       "discarded. Please ask again.", route=route)
        return self._finish(ans, trace, t0)

    # ------------------------------------------------------------ steps
    def _plan(self, trace: Trace, user: dict, question: str, live_depts: frozenset[str]) -> dict:
        prompt = P.PLAN_PROMPT.format(window_start=WINDOW_START, window_end=DATA_END, catalog=self._catalog(),
                                      user_name=user["name"], user_title=user["title"],
                                      user_depts=", ".join(sorted(live_depts)) or "none", question=question)
        r = self._llm(trace, "plan", P.PLAN_SYSTEM, prompt)
        plan = parse_json(r.text) or {}
        trace.attrs["plan"] = plan
        if not plan:
            trace.attrs["plan_parse_error"] = True
        return plan

    def _sql_path(self, trace: Trace, user_id: str, question: str, plan: dict, live_depts: frozenset[str],
                  notices: list[str], ans: Answer) -> str | None:
        if not live_depts:
            notices.append("You have no access to warehouse data, so no numbers are included.")
            return None
        requested = {d for d in (plan.get("departments_requested") or []) if d in ALL_DEPTS}
        denied = requested - live_depts
        if denied:
            names = ", ".join(sorted(CODE_TO_NAME.get(d, d) for d in denied))
            notices.append(f"You do not have access to warehouse data for: {names}. Those departments are not "
                           f"included in any number below.")
            if requested and not (requested & live_depts):
                return None
        mq = plan.get("metric_query")
        sql_text = None
        if isinstance(mq, dict) and mq.get("metrics"):
            metrics, clar = self.resolve_conflicts(question, list(mq.get("metrics") or []))
            if clar:
                ans.status, ans.text = "clarify", clar
                trace.attrs["clarify_reason"] = "metric_definition_conflict"
                return None
            mq = {**mq, "metrics": metrics}
            # department filters may only name departments the user can see
            fl = []
            for f in mq.get("filters") or []:
                f = {**f, "op": OP_ALIASES.get(str(f.get("op", "=")).lower(), f.get("op", "="))}
                if f.get("field") == "dept_code":
                    vals = f.get("value") if isinstance(f.get("value"), list) else [f.get("value")]
                    keep = [v for v in vals if v in live_depts]
                    if not keep:
                        continue
                    f = {**f, "value": keep if isinstance(f.get("value"), list) else keep[0]}
                fl.append(f)
            mq["filters"] = fl
            try:
                sql_text = compile_query(mq, self.layer)
                ans.metric_query = mq
                ans.metrics_used = metrics
            except SemanticError as e:
                trace.attrs["semantic_error"] = str(e)
                sql_text = None
        if sql_text is None and plan.get("raw_sql"):
            sql_text = plan["raw_sql"]
            trace.attrs["raw_sql"] = True
        if not sql_text:
            notices.append("The question could not be expressed with the governed metrics.")
            return None
        g = check_sql(sql_text)
        with trace.span("sql") as s:
            s.attrs.update({"sql": g.sql, "guard_ok": g.ok, "guard_errors": g.errors})
            if not g.ok:
                notices.append("The generated query was rejected by the SQL guard.")
                return None
            res = self.dep.warehouse.query(user_id, live_depts, g.sql)
            s.attrs.update({"role": res.role, "live_depts": list(res.live_depts), "ok": res.ok, "error": res.error,
                            "row_count": len(res.rows), "truncated": res.truncated, "elapsed_ms": res.elapsed_ms})
        trace.attrs["db_role"] = res.role
        trace.attrs["live_depts"] = sorted(live_depts)
        ans.sql = g.sql
        if not res.ok:
            notices.append("The warehouse query failed.")
            return None
        ans.columns = res.columns
        ans.rows = [[_jsonable(v) for v in r] for r in res.rows]
        ans.dept_deps = sorted(live_depts)
        defs = "\n".join(f"- {m}: {self.layer.metrics[m]['label']}. {self.layer.metrics[m]['description']}"
                         for m in ans.metrics_used)
        shown = ans.rows[:40]
        table = " | ".join(ans.columns) + "\n" + "\n".join(" | ".join(str(v) for v in r) for r in shown)
        more = f"\n({len(ans.rows) - len(shown)} more rows not shown)" if len(ans.rows) > len(shown) else ""
        scope = ", ".join(sorted(live_depts)) if live_depts != ALL_DEPTS else "all departments"
        qdesc = json.dumps({k: ans.metric_query.get(k) for k in ("dimensions", "time_grain", "time_range", "filters")
                            if ans.metric_query.get(k)}) if ans.metric_query else "raw SQL: " + g.sql[:600]
        return (f"Governed metrics used:\n{defs or '(raw SQL, no governed metric)'}\n"
                f"Query applied (dimensions, time range end-exclusive, filters): {qdesc}\n"
                f"Rows visible to this user cover departments: {scope}. Data runs {WINDOW_START} to {DATA_END}.\n"
                f"Result:\n{table}{more}")

    def _bad_tags(self, text: str, tag_map: dict) -> set[str]:
        return {m for m in TAG_RE.findall(text or "") if m not in tag_map}

    def _track_llm_deps(self, doc_deps: dict[str, int], system: str, prompt: str) -> None:
        key = v2_key(getattr(self.llm, "backend", ""), self.llm.model, system, prompt, 0)
        for d in doc_deps:
            self.llm_deps.setdefault(d, set()).add(key)

    def authorised(self, user_id: str, doc_deps: dict[str, int], depts: set[str]) -> tuple[bool, str]:
        st = self.dep.directory.state
        if not st.is_active(user_id):
            return False, "user_inactive"
        principals = st.principals(user_id)
        for d in doc_deps:
            acl = self.dep.source.live_acl(d)
            if acl is None:
                return False, f"doc_deleted:{d}"
            if not (acl & principals):
                return False, f"doc_acl_revoked:{d}"
        if not depts <= st.data_departments(user_id):
            return False, "dept_revoked"
        return True, "ok"

    def audience_ok(self, ans: Answer, member_user_ids: list[str | None]) -> bool:
        """True if every channel member may see every source and department the answer used."""
        return all(m is not None and self.authorised(m, ans.doc_deps, set(ans.dept_deps))[0]
                   for m in member_user_ids)

    def _finish(self, ans: Answer, trace: Trace, t0: float, store: bool = True) -> Answer:
        trace.attrs["status"] = ans.status
        for k, v in (("route", ans.route or "n/a"), ("db_role", "n/a"), ("live_depts", []), ("cache", "n/a"),
                     ("delivery_check", "n/a")):  # same schema on every return path
            trace.attrs.setdefault(k, v)
        trace.attrs["sql"] = ans.sql
        trace.attrs["cached"] = ans.cached
        trace.attrs["citations"] = [{k: c[k] for k in ("doc_id", "chunk_id", "start", "end")} for c in ans.citations]
        trace.attrs["doc_deps"] = ans.doc_deps
        trace.attrs["dept_deps"] = ans.dept_deps
        trace.attrs["latency_s"] = round(time.time() - t0, 3)
        ans.trace_id = trace.trace_id
        ans.trace = trace.to_dict()
        if self.trace_path is not None:
            trace.write(self.trace_path)
        st = self.dep.directory.state
        user = trace.attrs.get("user")
        if store and self.cache is not None and ans.status == "answered" and user in st.users:
            self.cache.put(CacheEntry(user, trace.question, ans.to_dict(), st.fingerprint(user), dict(ans.doc_deps),
                                      frozenset(ans.dept_deps), self.cache.policy_version))
        return ans
