from analytics_agent.agent import Agent, AgentConfig, extract_clarify, extract_sql
from analytics_agent.llm.mock import MockLLM


def test_extract_sql_variants():
    assert extract_sql("plan\n```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("```sql\nSELECT 1\n```\nfix:\n```sql\nSELECT 2\n```") == "SELECT 2"
    assert extract_sql("Plan: use orders\nSELECT count(*) FROM orders") == "SELECT count(*) FROM orders"
    assert extract_sql("I cannot write this, the table with the selected values is missing.") == ""
    assert extract_clarify("CLARIFY: do you mean GMV with or without freight?").startswith("do you mean")


def test_happy_path(toy_duck):
    llm = MockLLM("```sql\nSELECT count(*) AS n FROM orders WHERE order_status = 'delivered'\n```")
    ans = Agent(llm, AgentConfig(schema_mode="full")).ask("How many delivered orders?", "toy")
    assert ans.status == "answered" and ans.rows == [(2,)]
    assert len(llm.calls) == 1
    names = [s["name"] for s in ans.trace["spans"]]
    assert names[:4] == ["retrieve", "llm.generate", "validate", "execute"]


def test_self_correction_on_error_and_empty(toy_duck):
    replies = iter([
        "```sql\nSELECT count(*) FROM orderz\n```",  # unknown table -> validator rejects
        "```sql\nSELECT order_id FROM orders WHERE order_status = 'DELIVERED'\n```",  # empty result
        "```sql\nSELECT order_id FROM orders WHERE order_status = 'delivered' ORDER BY 1\n```",
    ])
    llm = MockLLM(lambda s, p, i: next(replies))
    ans = Agent(llm, AgentConfig(schema_mode="full", max_corrections=2)).ask("Delivered order ids?", "toy")
    assert ans.status == "answered" and ans.rows == [("o1",), ("o3",)]
    assert ans.signals["corrections"] == 2
    # the empty-result correction prompt shows the real values of the filtered column
    assert "delivered" in llm.calls[2][1] and "Actual values" in llm.calls[2][1]


def test_dml_from_llm_is_blocked_not_executed(toy_duck):
    llm = MockLLM("```sql\nDELETE FROM orders\n```")
    ans = Agent(llm, AgentConfig(schema_mode="full", max_corrections=1)).ask("Clean up orders", "toy")
    assert ans.status == "blocked" and ans.rows == []
    assert not any(s["name"] == "execute" for s in ans.trace["spans"])


def test_self_consistency_votes_on_results(toy_duck):
    def responder(system, prompt, sample):
        # 3 of 4 samples agree on the result even though the SQL text differs
        return {0: "```sql\nSELECT 2 AS n\n```", 1: "```sql\nSELECT count(*) FROM orders WHERE amount > 6\n```",
                2: "```sql\nSELECT 3\n```", 3: "```sql\nSELECT 1 + 1\n```"}[sample]
    ans = Agent(MockLLM(responder), AgentConfig(schema_mode="full", n_samples=4)).ask("q", "toy")
    assert ans.rows == [(2,)] and ans.signals["sc_agreement"] == 0.75 and ans.signals["sc_votes"] == [3, 1]


def test_clarify_short_circuits(toy_duck):
    llm = MockLLM("CLARIFY: Do you want revenue including freight or item price only?")
    ans = Agent(llm, AgentConfig(schema_mode="full", allow_clarify=True)).ask("What was revenue?", "toy")
    assert ans.status == "clarify" and "freight" in ans.clarification and ans.sql == ""
