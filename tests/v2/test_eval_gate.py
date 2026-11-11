"""D4: the eval gate. Offline: recorded runs re-score to the committed numbers, the gate passes on them
and fails on a degraded recording, and part of the pipeline replays byte-for-byte from the caches."""

import copy
import json
import os

import pytest

from analytics_agent.copilot.settings import V2_RESULTS
from analytics_agent.eval import copilot_eval as E
from analytics_agent.eval.gate_v2 import check, final_redteam

pytestmark = pytest.mark.skipif(not (V2_RESULTS / "eval.json").exists(), reason="no recorded eval yet")


def committed():
    return json.loads((V2_RESULTS / "eval.json").read_text())


def test_recorded_runs_rescore_to_committed_numbers():
    now, then = E.score(write=False), committed()
    for s, v in then["systems"].items():
        assert now["systems"][s]["all"] == v["all"], s
        assert now["systems"][s]["route_accuracy"] == v["route_accuracy"]


def test_gate_passes_on_recording():
    assert check(committed(), final_redteam()) == []


def test_gate_fails_on_degraded_recording():
    bad = copy.deepcopy(committed())
    bad["systems"]["copilot"]["all"]["accuracy"] = 0.3
    bad["systems"]["copilot"]["citations_valid_rate"] = 0.9
    leaky = {"summary": {"leaked": 1, "leaked_ids": ["x"], "complete": True, "scenarios": 40}}
    assert len(check(bad, leaky)) == 3
    assert check(committed(), None) == ["no final red-team round recorded"]
    partial = {"summary": {"leaked": 0, "complete": False, "scenarios": 5, "expected": 40}}
    assert any("incomplete" in f for f in check(committed(), partial))


REPLAY_IDS = ["au-q01", "au-q21", "au-q53", "cx-q23"]


def test_pipeline_replays_from_caches(monkeypatch):
    monkeypatch.setenv("AA_LLM_BACKEND", "replay")
    monkeypatch.setenv("AA_EMBED_REPLAY", "1")
    recorded = E.load_run("copilot")
    ask, _ = E.make_system("copilot")
    items = {q["id"]: q for q in E.load_items(n_per_set=0)}
    for i in REPLAY_IDS:
        got = ask(items[i])
        assert got["status"] == recorded[i]["status"], i
        assert got["text"] == recorded[i]["text"], i
        assert got["rows"] == recorded[i]["rows"], i
    assert os.environ["AA_LLM_BACKEND"] == "replay"
