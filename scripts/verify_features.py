"""Run every verification command in feature_list.json and record state + evidence (the only writer)."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "feature_list.json"
if not path.exists():
    sys.exit("feature_list.json is a local file (not committed); nothing to verify")
features = json.loads(path.read_text())
for f in features:
    p = subprocess.run(f["verification"], shell=True, cwd=ROOT, capture_output=True, text=True, timeout=900)
    f["state"] = "passing" if p.returncode == 0 else "failing"
    tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
    f["evidence"] = f"{datetime.now(UTC).isoformat(timespec='seconds')} exit={p.returncode} {tail[0][:160]}"
    print(f"{f['id']:4s} {f['state']:8s} {f['evidence']}")
path.write_text(json.dumps(features, indent=1))
