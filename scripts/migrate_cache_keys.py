"""One-off migration (2026-10-29, R1): cache keys gained the pipeline version. Every entry created
before that is re-filed under versioned(old_key, "v1") -- the prompts they answered were v1 prompts.
Run once: uv run python scripts/migrate_cache_keys.py"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from analytics_agent.config import CACHE_DIR  # noqa: E402
from analytics_agent.llm.cache import versioned  # noqa: E402

marker = CACHE_DIR / "MIGRATED_v1"
if marker.exists():
    sys.exit("already migrated")
files = [p for p in CACHE_DIR.glob("*/*.json")]
for p in files:
    new = versioned(p.stem, "v1")
    dst = CACHE_DIR / new[:2] / f"{new}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    p.replace(dst)
for tape in (ROOT / "tests" / "regression").glob("cassette_*.jsonl"):
    rows = [json.loads(x) for x in tape.read_text().splitlines() if x.strip()]
    for r in rows:
        r["key"] = versioned(r["key"], "v1")
    tape.write_text("".join(json.dumps(r) + "\n" for r in rows))
for d in CACHE_DIR.iterdir():
    if d.is_dir() and not any(d.iterdir()):
        d.rmdir()
marker.write_text(f"{len(files)} entries migrated\n")
print(f"migrated {len(files)} cache entries and the regression cassettes")
