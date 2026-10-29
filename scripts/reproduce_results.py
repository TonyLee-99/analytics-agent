"""Rebuild every results/*.json, results/*.md and docs/img/*.png from the recorded runs, with zero model
calls. Called by `make reproduce-results` (which sets AA_STRICT_REPLAY=1 and AA_MAX_CLAUDE_CALLS=0, so
any attempt to call a model raises instead of spending).

Steps that re-execute SQL need the databases (`make data`); without them they are skipped, and the
committed outputs of those steps are kept.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.config import BIRD_DB_ROOT, OLIST_DB  # noqa: E402

HAS_DB = OLIST_DB.exists() and (BIRD_DB_ROOT / "superhero" / "superhero.sqlite").exists()
PY = [sys.executable]
STEPS = [  # (needs databases, command)
    (False, ["-m", "analytics_agent.eval.analysis", "all"]),
    (True, ["scripts/noise_floor.py"]),
    (False, ["-m", "analytics_agent.eval.stats"]),
    (False, ["-m", "analytics_agent.eval.learned_router"]),
    (True, ["-m", "analytics_agent.eval.robustness"]),
    (False, ["scripts/error_analysis.py"]),
    (True, ["-m", "analytics_agent.eval.redteam", "--no-llm"]),
    (True, ["scripts/redteam_v1_check.py"]),
    (True, ["scripts/validator_recorded_fp.py"]),
    (False, ["-m", "analytics_agent.eval.regression", "size"]),
    (False, ["scripts/build_headline.py"]),
    (False, ["-m", "analytics_agent.eval.charts"]),
]
for needs_db, cmd in STEPS:
    if needs_db and not HAS_DB:
        print(f"skip (no databases; run `make data`): {' '.join(cmd)}")
        continue
    print(f"== {' '.join(cmd)}", flush=True)
    subprocess.run(PY + cmd, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
print("done; compare with `git diff --stat -- results docs/img`")
