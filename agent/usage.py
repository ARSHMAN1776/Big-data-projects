"""A small ledger of what the agent has spent today, so a daily limit can stop a runaway bill."""
from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

from .config import estimate_usd


class Ledger:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._mem: dict = {}

    def _load(self) -> dict:
        if self.path and self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        return self._mem

    def _save(self, data: dict) -> None:
        self._mem = data
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
            except OSError:
                pass  # the limit still works from memory

    def record(self, model: str, usage, new_run: bool = False, today: date | None = None) -> float:
        day = (today or date.today()).isoformat()
        usd = estimate_usd(model, usage)
        with self._lock:
            data = self._load()
            row = data.setdefault(day, {"runs": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "usd": 0.0})
            row["calls"] += 1
            row["runs"] += 1 if new_run else 0
            row["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
            row["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)
            row["cache_read_tokens"] += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            row["usd"] = round(row["usd"] + usd, 6)
            for old in sorted(data)[:-30]:       # keep a month
                del data[old]
            self._save(data)
        return usd

    def today(self, today: date | None = None) -> dict:
        day = (today or date.today()).isoformat()
        with self._lock:
            return dict(self._load().get(day, {"runs": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "usd": 0.0}))
