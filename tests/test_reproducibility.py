"""Fresh-clone guarantees: recorded runs are committed, analyses refuse missing inputs, replay is strict."""

import gzip
import json

import pytest

from analytics_agent.config import RUNS


def test_committed_run_files_have_expected_rows():
    expect = {"bird__a_zeroshot_official__haiku": 500, "bird__d_selfcorrect__haiku": 500,
              "bird__e_sc5__haiku__sub200": 200, "bird__d_selfcorrect__sonnet__sub200": 200,
              "olist__olist_raw__haiku": 50, "olist__olist_raw_n5__haiku": 50}
    for stem, n in expect.items():
        with gzip.open(RUNS / f"{stem}.jsonl.gz", "rt") as f:
            assert sum(1 for _ in f) == n, stem


def test_analysis_refuses_missing_inputs(monkeypatch, tmp_path):
    from analytics_agent import config
    from analytics_agent.eval import report

    monkeypatch.setattr(config, "RUNS", tmp_path)
    with pytest.raises(report.MissingRunError):
        report.load_stem("bird__a_zeroshot_official__haiku")


def test_strict_replay_never_calls_a_model(monkeypatch, tmp_path):
    from analytics_agent.llm.cache import DiskCache, ReplayMiss
    from analytics_agent.llm.claude_cli import ClaudeCLIClient

    monkeypatch.setenv("AA_STRICT_REPLAY", "1")
    client = ClaudeCLIClient(cache=DiskCache(tmp_path))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called")))
    with pytest.raises(ReplayMiss):
        client.complete("system", "a prompt that is not cached")


def test_cache_key_carries_pipeline_version():
    from analytics_agent.llm.cache import _base_key, cache_key, versioned

    parts = {"backend": "claude_cli", "model": "m", "system": "s", "prompt": "p", "sample": 0}
    assert cache_key(**parts) == versioned(_base_key(**parts)) != _base_key(**parts)
    assert versioned("k", "v1") != versioned("k", "v2")


def test_headline_file_is_valid_json():
    from analytics_agent.config import RESULTS

    assert json.loads((RESULTS / "headline.json").read_text())
