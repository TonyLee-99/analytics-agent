"""CI regression gate for prompt / pipeline changes (no network: recorded responses only).

Rule (tests/regression/gate.json): a candidate fails when it loses at least k more of the 30 frozen
questions than it gains, versus the baseline recording. k=5 was sized from real redraw data so that an
unchanged prompt re-recorded with fresh sampling fails only ~2% of the time. The price of n=30 is low
power: a true -5 pp regression is caught ~10% of the time, -16 pp ~53% (see gate.json).

- test_current_pipeline_clears_the_gate: shipped prompts, replayed exactly, pass (net loss 0).
- test_gate_flags_the_recorded_degraded_prompt: a recorded "token-saving" prompt change (no column
  docs, no hints) loses 8 and gains 3 -> net 5 -> flagged.
- test_prompt_change_without_rerecording_fails_loudly: any prompt edit invalidates the recording.
"""

import json

import pytest

from analytics_agent.eval.regression import REG, gate_decision, run_gate

GATE = json.loads((REG / "gate.json").read_text())


def test_gate_is_sized_with_a_known_false_alarm_rate():
    assert GATE["false_alarm_rate"] <= 0.05 and GATE["k"] >= 1


@pytest.mark.skipif(not (REG / "cassette_current.jsonl").exists(), reason="cassette not recorded")
def test_current_pipeline_clears_the_gate():
    res = run_gate("current")
    dec = gate_decision(res["outcomes"], GATE)
    assert res["n"] == GATE["n"] and dec["net"] == 0 and not dec["fail"]


@pytest.mark.skipif(not (REG / "cassette_degraded.jsonl").exists(), reason="cassette not recorded")
def test_gate_flags_the_recorded_degraded_prompt():
    dec = gate_decision(run_gate("degraded")["outcomes"], GATE)
    assert dec["fail"] and dec["net"] == GATE["observed_degraded"]["net"]


def test_prompt_change_without_rerecording_fails_loudly(monkeypatch):
    from analytics_agent import prompts
    from analytics_agent.llm.cassette import CassetteMiss

    monkeypatch.setattr(prompts, "SYSTEM_AGENT", prompts.SYSTEM_AGENT + "\n- Be concise.")
    with pytest.raises(CassetteMiss):
        run_gate("current")
