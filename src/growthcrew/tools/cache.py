"""A small on-disk JSON cache for tool results."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

CACHE_DIR = Path(".cache")
WEEK = 7 * 24 * 3600


class DiskCache:
    def __init__(self, name: str, ttl: float = WEEK, root: Path = CACHE_DIR) -> None:
        self.dir = root / name
        self.ttl = ttl

    def _path(self, key: str) -> Path:
        return self.dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        entry = json.loads(path.read_text())
        if time.time() - entry["saved_at"] > self.ttl:
            return None
        return entry["value"]

    def set(self, key: str, value: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        entry = {"key": key, "saved_at": time.time(), "value": value}
        self._path(key).write_text(json.dumps(entry))
