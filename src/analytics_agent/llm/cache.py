"""On-disk response cache keyed by a hash of everything that determines the output.

`sample` is part of the key: sample 0 is the default answer, samples 1..N-1 are extra draws used for
self-consistency. Re-running any eval is therefore free, and changing a prompt invalidates only the
affected entries.

Keys also carry `PIPELINE_VERSION` (see `prompts.PROMPT_VERSION`): bump it whenever anything that is
not prompt text but changes what a response means (answer extraction, sampling setup, backend
flags) changes, and every old entry stops matching. Configs that send byte-identical prompts share
entries on purpose -- that is what makes "same first draft, with vs without self-correction" a clean
paired comparison.

Strict replay (`AA_STRICT_REPLAY=1`): any cache miss raises ReplayMiss instead of calling a model.
Use it for every "reproduce from the recorded runs" command."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from analytics_agent.config import CACHE_DIR
from analytics_agent.prompts import PROMPT_VERSION

PIPELINE_VERSION = PROMPT_VERSION


class ReplayMiss(RuntimeError):
    """Raised in strict-replay mode when a response is not in the cache."""


def strict_replay() -> bool:
    return os.environ.get("AA_STRICT_REPLAY", "") not in ("", "0", "false")


def _base_key(**parts: Any) -> str:
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def versioned(base: str, version: str = PIPELINE_VERSION) -> str:
    return hashlib.sha256(f"{base}|{version}".encode()).hexdigest()


def cache_key(**parts: Any) -> str:
    return versioned(_base_key(**parts))


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
