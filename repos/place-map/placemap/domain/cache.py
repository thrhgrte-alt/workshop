"""Summary cache keyed by script content hash: an unchanged script is never analysed or summarised again.

The cache holds only what is derived from the source text (the analysis in domain/scan.py), never a path or place, so ONE cache may be shared by every place and project of this
workspace. Anything about what exists in a place stays in that place's snapshots.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

HASH_RE = re.compile(r"[0-9a-f]{64}")


class SummaryCache:
    def __init__(self, project):
        self.root = Path(project.workspace) / "summary_cache"

    def _file(self, h: str) -> Path:
        if not HASH_RE.fullmatch(h or ""):
            raise ValueError("a content hash is 64 lowercase hex characters")
        return self.root / h[:2] / f"{h}.json"

    def get(self, h: str) -> dict | None:
        try:
            f = self._file(h)
            return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def put(self, h: str, analysis: dict) -> Path:
        f = self._file(h)
        f.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=".c-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(analysis, fh, separators=(",", ":"), sort_keys=True)
            os.replace(tmp, f)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return f

    def count(self) -> int:
        return sum(1 for _ in self.root.glob("*/*.json")) if self.root.is_dir() else 0
