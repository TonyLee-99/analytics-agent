"""Central paths and defaults. Everything is relative to the repo root so `make` targets are reproducible."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
BIRD_DB_ROOT = DATA / "raw" / "bird" / "dev" / "dev_databases"
BIRD_QUESTIONS = DATA / "raw" / "bird_hf" / "data" / "mini_dev_sqlite.jsonl"  # 2026 "Mini-Dev 1024" release
BIRD_BASELINE_PROMPTS = DATA / "raw" / "bird_hf" / "baseline_inference" / "mini_dev_sqlite.jsonl"
BIRD_DEV_FULL = DATA / "raw" / "bird" / "bird_dev_full" / "dev.json"  # source of the few-shot pool
BIRD_ARCHIVE_MINIDEV = (
    DATA / "raw" / "bird_hf" / "archive" / "bird_mini_dev_2025-07-04" / "data" / "mini_dev_sqlite-00000-of-00001.json"
)
OLIST_RAW = DATA / "raw" / "olist"
OLIST_DB = DATA / "olist.duckdb"
REDTEAM_DB = DATA / "redteam.duckdb"
EVAL_SETS = ROOT / "eval_sets"
RESULTS = ROOT / "results"
RUNS = RESULTS / "runs"
TRACES = ROOT / "traces"
CACHE_DIR = Path(os.environ.get("AA_LLM_CACHE", ROOT / ".llm_cache"))
SEMANTIC_SPEC = ROOT / "src" / "analytics_agent" / "semantic" / "metrics.yaml"
CATALOG_DOCS = ROOT / "src" / "analytics_agent" / "catalog_docs"

# Execution guardrail defaults
DEFAULT_ROW_LIMIT = 1000
DEFAULT_TIMEOUT_S = 30.0
