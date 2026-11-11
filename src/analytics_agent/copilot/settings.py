"""Paths and defaults for v2. Everything is relative to the repo root; data/ is gitignored."""

from __future__ import annotations

import os
from pathlib import Path

from analytics_agent.config import DATA, RESULTS, ROOT

CONFIG = ROOT / "config"
CORPUS = ROOT / "corpus"  # fetched public documents (text + metadata), committed
V2_RESULTS = RESULTS / "v2"
V2_EVAL_SETS = ROOT / "eval_sets" / "v2"
V2_CACHE = ROOT / "cache"
V2_STATE = DATA / "v2"  # directory / doc source / index state for local runs

SOURCE_WAREHOUSE = Path(os.environ.get(
    "AA_311_SOURCE", Path.home() / "projects" / "chicago-311-warehouse" / "data" / "warehouse.duckdb"))
# Bounded window copied into Postgres: two recent years of real requests.
WINDOW_START = "2024-11-01"
WINDOW_END = "2026-11-04"  # exclusive; the source build's latest observation is 2026-11-03

PG_ADMIN_DSN = os.environ.get("AA_PG_ADMIN_DSN", "host=localhost port=5433 user=postgres dbname=copilot")
PG_GATEWAY_DSN = os.environ.get("AA_PG_DSN", "host=localhost port=5433 user=copilot_gateway dbname=copilot")
PG_BIN = Path(os.environ.get("AA_PG_BIN", "/opt/homebrew/opt/postgresql@16/bin"))
