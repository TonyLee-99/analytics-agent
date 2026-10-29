"""On-disk response cache keyed by a hash of everything that determines the output.

`sample` is part of the key: sample 0 is the default answer, samples 1..N-1 are extra draws used for
self-consistency. Re-running any eval is therefore free, and changing a prompt invalidates only the
affected entries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from analytics_agent.config import CACHE_DIR


def cache_key(**parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


class DiskCache:
    def __init__(self, root: Path = CACHE_DIR):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        p = self._path(key)
        if p.exists():
            return json.loads(p.read_text())
        return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False))
        tmp.replace(p)
