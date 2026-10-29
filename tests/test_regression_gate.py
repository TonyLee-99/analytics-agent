"""CI regression gate for prompt / pipeline changes (no network: recorded responses only).

- test_current_pipeline_clears_the_gate: the shipped prompts must reach the threshold in gate.json.
  If any prompt text changed, the recorded responses no longer match (CassetteMiss) and the test
  fails until someone re-records with the real model (`make record-regression`) -- and the new
  recording must clear the same threshold.
- test_gate_catches_a_degraded_prompt: a recorded, plausible "token-saving" prompt change (no column
  docs, no evidence hints) must FAIL the gate. This proves the gate has teeth.
"""

import json

import pytest

from analytics_agent.eval.regression import REG, run_gate

GATE = json.loads((REG / "gate.json").read_text())


@pytest.mark.skipif(not (REG / "cassette_current.jsonl").exists(), reason="cassette not recorded")
def test_current_pipeline_clears_the_gate():
    res = run_gate("current")
    assert res["n"] == GATE["n"]
    assert res["ex"] >= GATE["min_ex"], f"EX {res['ex']} below gate {GATE['min_ex']}"


@pytest.mark.skipif(not (REG / "cassette_degraded.jsonl").exists(), reason="cassette not recorded")
def test_gate_catches_a_degraded_prompt():
    res = run_gate("degraded")
    assert res["ex"] < GATE["min_ex"], "the gate failed to catch a known-worse prompt"


def test_prompt_change_without_rerecording_fails_loudly(monkeypatch):
    from analytics_agent import prompts
    from analytics_agent.llm.cassette import CassetteMiss

    monkeypatch.setattr(prompts, "SYSTEM_AGENT", prompts.SYSTEM_AGENT + "\n- Be concise.")
    with pytest.raises(CassetteMiss):
        run_gate("current")
