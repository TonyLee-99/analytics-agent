"""D3: every answer writes a trace with the fields an auditor needs; traces hold ids and spans, not text."""

import json

from .helpers import DOCS, mock_llm, small_copilot


def test_trace_fields_for_a_mixed_answer(tmp_path):
    mq = {"metrics": ["requests"], "dimensions": [], "filters": [{"field": "dept_code", "op": "=", "value": "CDOT"}]}
    cp = small_copilot(tmp_path, llm=mock_llm(route="both", metric_query=mq, answer="42 requests; see [S1]."))
    a = cp.ask("maria", "how many pothole requests and how are they fixed?")
    assert a.status == "answered"
    t = json.loads((tmp_path / "trace.jsonl").read_text().splitlines()[-1])
    for k in ("trace_id", "user", "route", "db_role", "live_depts", "citations", "doc_deps", "dept_deps", "latency_s",
              "totals", "delivery_check", "directory_version", "cache"):
        assert k in t, k
    assert t["db_role"] == "cu_maria" and t["live_depts"] == ["CDOT"] and t["route"] == "both"
    spans = {s["name"]: s for s in t["spans"]}
    assert {"plan", "retrieve", "sql", "answer"} <= set(spans)
    assert {"n_allowed_chunks", "n_total_chunks", "dropped_by_recheck", "hits"} <= set(spans["retrieve"])
    assert spans["sql"]["role"] == "cu_maria" and spans["sql"]["row_count"] == 1 and "sql" in spans["sql"]
    assert {"input_tokens", "output_tokens", "cost_usd", "llm_latency_s"} <= set(t["totals"])
    raw = (tmp_path / "trace.jsonl").read_text()
    assert not any(text in raw for _, text, _ in DOCS.values())  # no document text in traces


def test_department_filter_outside_entitlement_is_dropped_and_noticed(tmp_path):
    mq = {"metrics": ["requests"], "dimensions": [], "filters": [{"field": "dept_code", "op": "=", "value": "DWM"}]}
    llm = mock_llm(route="sql", metric_query=mq, answer="none")
    cp = small_copilot(tmp_path, llm=llm)
    import json as _j

    from analytics_agent.copilot import prompts as P

    def responder(system, prompt, sample):
        if system == P.PLAN_SYSTEM:
            return "```json\n" + _j.dumps({"route": "sql", "metric_query": mq, "departments_requested": ["DWM"]}) + "\n```"
        return "no data"
    cp.llm._responder = responder
    a = cp.ask("maria", "how many DWM requests?")
    assert cp.dep.warehouse.calls == []  # nothing executed for a department she cannot see
    assert any("do not have access" in n for n in a.notices)


def test_conflicting_definitions_ask(tmp_path):
    mq = {"metrics": ["median_days_to_close_completed"], "dimensions": []}
    cp = small_copilot(tmp_path, llm=mock_llm(route="sql", metric_query=mq, answer="Median is 42 days."))
    assert cp.ask("maria", "What is the median time to close?").status == "clarify"
    a = cp.ask("maria", "Median time to close, including canceled requests?")
    assert a.status == "answered" and a.metrics_used == ["median_days_to_close_all_closed"]


def test_invalid_citation_is_regenerated_then_refused(tmp_path):
    cp = small_copilot(tmp_path, llm=mock_llm(answer="See [S9]."))
    a = cp.ask("maria", "potholes?")
    assert a.status == "error" and "[S9]" not in a.text and a.citations == []


def test_access_change_during_answer_discards_it(tmp_path):
    cp = small_copilot(tmp_path)
    orig = cp.llm._responder

    def revoking(system, prompt, sample):
        if "ANSWER" not in system and "careful assistant" in system:
            cp.dep.directory.remove_member("dept-cdot", "maria")
        return orig(system, prompt, sample)
    cp.llm._responder = revoking
    a = cp.ask("maria", "pothole crews target")
    assert a.status == "denied" and "discarded" in a.text
