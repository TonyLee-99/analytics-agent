"""Run the current red-team SQL attacks through the *v1* validator (commit db30f5f) to document which
attacks the original guardrails let through. Run: uv run python scripts/redteam_v1_check.py"""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.catalog import get_catalog  # noqa: E402
from analytics_agent.config import RESULTS  # noqa: E402
from analytics_agent.eval.redteam import ATTACKS  # noqa: E402

src = subprocess.run(["git", "show", "db30f5f:src/analytics_agent/guardrails.py"], cwd=ROOT, capture_output=True,
                     text=True, check=True).stdout
with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
    f.write(src)
spec = importlib.util.spec_from_file_location("guardrails_v1", f.name)
v1 = importlib.util.module_from_spec(spec)
sys.modules["guardrails_v1"] = v1
spec.loader.exec_module(v1)
passed = []
for aid, dialect, sql, cat in ATTACKS:
    tables = get_catalog("olist" if dialect == "duckdb" else "toxicology").table_names
    if v1.validate_sql(sql, dialect, allowed_tables=tables, row_limit=1000).ok:
        passed.append({"id": aid, "category": cat})
out = {"validator_version": "db30f5f", "attacks": len(ATTACKS), "allowed_by_v1": passed}
(RESULTS / "redteam_v1_validator.json").write_text(json.dumps(out, indent=2))
print(f"v1 validator allowed {len(passed)}/{len(ATTACKS)}: {[p['id'] for p in passed]}")
