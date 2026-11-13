"""Shared application state and singletons for Content Detective backend."""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "chatbot"))
sys.path.insert(0, str(ROOT))

import engine
import merge_manual
from agent import config as agent_config
from agent.agent import DataAgent
from agent.jobs import JobRunner, integration_status
from agent.usage import Ledger
from common import PKT, load_env

RAW_DIR = ROOT / "data" / "raw"
MANUAL_DIR = ROOT / "data" / "manual"
CONFIG = ROOT / "config" / "social_brands.json"
ENV_FILE = ROOT / ".env"

MEDIA_LABELS = [
    ("image", "Photo"),
    ("carousel", "Carousel (several photos)"),
    ("reel", "Reel"),
    ("video", "Video"),
    ("text", "Text only"),
    ("link", "Link post"),
    ("other", "Other"),
]
AGENT_PER_MINUTE = 20


class AppState:
    def __init__(self):
        load_env(ENV_FILE)
        self.engine = engine.Engine(engine.DataStore(RAW_DIR))
        self.lock = threading.Lock()
        self.brands = merge_manual.load_brands(CONFIG)
        self.ledger = Ledger(RAW_DIR.parent / "agent_usage.json")
        self.agent = DataAgent(self.engine, self.lock, ledger=self.ledger, integrations=integration_status)
        self.jobs = JobRunner(RAW_DIR)
        self._recent: deque = deque()
        self._rate_lock = threading.Lock()

    def meta(self) -> dict:
        return {
            "brands": sorted(self.brands.values()),
            "manual_platforms": [{"key": k, "name": engine.PLATFORM_NAMES[k]} for k in ("instagram", "facebook", "tiktok")],
            "media_types": [{"key": k, "label": v} for k, v in MEDIA_LABELS],
            "today": datetime.now(timezone.utc).astimezone(PKT).date().isoformat(),
        }

    def status(self) -> dict:
        return {"agent": self.agent.status(), "integrations": integration_status(), "job": self.jobs.status()}

    def allow_agent_request(self) -> bool:
        now = time.time()
        with self._rate_lock:
            while self._recent and now - self._recent[0] > 60:
                self._recent.popleft()
            if len(self._recent) >= AGENT_PER_MINUTE:
                return False
            self._recent.append(now)
            return True


state = AppState()
