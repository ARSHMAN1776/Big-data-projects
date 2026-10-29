"""Run the data collectors in the background, one at a time, with their output captured.

A job starts only from a user action (a button, or Approve on an agent proposal). The agent itself cannot
start one. Which keys are present is reported, never their values.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from common import set_log_sink  # noqa: E402


class JobError(Exception):
    """The job could not start. The message is safe to show."""


def integration_status(env=None) -> dict:
    env = os.environ if env is None else env
    has = lambda k: bool((env.get(k) or "").strip())  # noqa: E731
    yt = has("YOUTUBE_API_KEY")
    return {
        "youtube": {"configured": yt, "mode": "official API, full history" if yt else "public feed, newest 15 videos per brand"},
        "instagram": {"configured": has("IG_USER_ID") and has("IG_ACCESS_TOKEN")},
        "facebook": {"configured": False, "note": "no API for other brands' pages; add posts by hand"},
        "tiktok": {"configured": False, "note": "no API for other brands' videos; add posts by hand"},
    }


def _run_youtube(raw_dir: Path) -> int:
    import collect
    return collect.main(["--mode", "auto", "--out-dir", str(raw_dir)])


def _run_instagram(raw_dir: Path) -> int:
    import collect_instagram
    return collect_instagram.main(["--out-dir", str(raw_dir)])


class JobRunner:
    def __init__(self, raw_dir: Path, runners: dict | None = None, env=None):
        self.raw_dir = Path(raw_dir)
        self.runners = runners or {"youtube": _run_youtube, "instagram": _run_instagram}
        self.env = env
        self._lock = threading.Lock()
        self._log: deque = deque(maxlen=60)
        self._state = {"state": "idle", "source": None, "started": None, "finished": None, "exit_code": None, "message": ""}
        self._thread: threading.Thread | None = None

    def start(self, source: str) -> dict:
        if source not in self.runners:
            raise JobError("That source cannot be collected automatically. Facebook and TikTok posts are added by hand.")
        status = integration_status(self.env)
        if source == "instagram" and not status["instagram"]["configured"]:
            raise JobError("Instagram collection is not set up yet. Add IG_USER_ID and IG_ACCESS_TOKEN to the .env file (see the Setup tab).")
        with self._lock:
            if self._state["state"] == "running":
                raise JobError(f"A {self._state['source']} update is already running. Wait for it to finish.")
            self._log.clear()
            self._state = {"state": "running", "source": source, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "finished": None, "exit_code": None, "message": "Running…"}
            self._thread = threading.Thread(target=self._work, args=(source,), daemon=True, name=f"collect-{source}")
            self._thread.start()
        return self.status()

    def _append(self, text: str) -> None:
        for line in str(text).splitlines():
            if line.strip():
                self._log.append(line.rstrip()[:300])

    def _work(self, source: str) -> None:
        set_log_sink(self._append)
        code, message = 1, ""
        try:
            self._append(f"Starting {source} collection")
            code = int(self.runners[source](self.raw_dir) or 0)
            message = "Finished. The overview now shows the new data." if code == 0 else "Stopped with a problem. See the log below."
        except SystemExit as exc:                      # a collector that exits instead of returning
            code = int(exc.code or 0) if isinstance(exc.code, int) else 1
            message = "Finished." if code == 0 else "Stopped with a problem. See the log below."
        except Exception as exc:
            self._append(f"Failed: {type(exc).__name__}: {exc}")
            message = "Failed. See the log below."
        finally:
            set_log_sink(None)
            with self._lock:
                self._state.update({"state": "done" if code == 0 else "failed", "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "exit_code": code, "message": message})

    def status(self) -> dict:
        with self._lock:
            return {**self._state, "log": list(self._log)}

    def wait(self, timeout: float = 10.0) -> None:
        """For tests: block until the running job ends."""
        t = self._thread
        if t:
            t.join(timeout)
        end = time.time() + timeout
        while self.status()["state"] == "running" and time.time() < end:
            time.sleep(0.01)
