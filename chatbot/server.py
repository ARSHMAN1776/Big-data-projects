#!/usr/bin/env python3
"""Local app for exploring, adding and asking about the collected posts.

  python chatbot/server.py            then open http://127.0.0.1:8765

Uses only the standard library plus pandas/numpy (and the anthropic package for the agent). It listens
on this computer only, and it rejects requests that come from other websites or other host names.
No endpoint ever returns an API key, or whether a key's value is right or wrong, only whether one is set.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import engine  # noqa: E402
import entry  # noqa: E402
import merge_manual  # noqa: E402
from agent import config as agent_config  # noqa: E402
from agent.agent import AgentError, AgentUnavailable, DataAgent  # noqa: E402
from agent.jobs import JobError, JobRunner, integration_status  # noqa: E402
from agent.usage import Ledger  # noqa: E402
from common import PKT, load_env  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
MANUAL_DIR = ROOT / "data" / "manual"
CONFIG = ROOT / "config" / "social_brands.json"
TEMPLATE = ROOT / "templates" / "post_collection_template.csv"
ENV_FILE = ROOT / ".env"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
CSP = ("default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
BODY_LIMIT = {"/api/ask": 2_000, "/api/agent": 40_000, "/api/add_post": 16_000, "/api/import": 1_500_000,
              "/api/jobs/start": 500, "/api/recheck": 500}
MAX_QUESTION = 300
AGENT_PER_MINUTE = 20
MEDIA_LABELS = [("image", "Photo"), ("carousel", "Carousel (several photos)"), ("reel", "Reel"), ("video", "Video"),
                ("text", "Text only"), ("link", "Link post"), ("other", "Other")]


def clean(obj):
    """Make an answer JSON-safe: no NaN or Infinity, no numpy types."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if hasattr(obj, "item"):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


class App:
    def __init__(self):
        load_env(ENV_FILE)
        self.engine = engine.Engine(engine.DataStore(RAW_DIR))
        self.lock = threading.Lock()  # the data reloads itself, so engine calls take turns
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


class Bad(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


def pick(values: dict) -> tuple[str | None, str | None]:
    """Validate the platform / brand filters that a call may carry."""
    platform = values.get("platform") or None
    brand = values.get("brand") or None
    if platform is not None and platform not in engine.PLATFORM_NAMES:
        raise Bad(400, "Unknown platform.")
    if brand is not None and (not isinstance(brand, str) or len(brand) > 80):
        raise Bad(400, "Unknown brand.")
    return platform, brand


class Handler(BaseHTTPRequestHandler):
    app: App
    port: int

    server_version = "ContentDetective"
    sys_version = ""

    # -- plumbing

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    def _headers(self, status: int, ctype: str, length: int | None, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self._headers(status, ctype, len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict):
        self._send(status, json.dumps(clean(payload), ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def _guard(self) -> bool:
        if not self._host_ok():
            self._json(403, {"error": "Unknown host."})
            return False
        return True

    def _stream(self, run) -> None:
        """Send newline-delimited JSON events while run(emit) works. The connection closes at the end."""
        self.close_connection = True
        self._headers(200, "application/x-ndjson; charset=utf-8", None, {"Connection": "close", "X-Accel-Buffering": "no"})

        def emit(event: dict) -> None:
            self.wfile.write((json.dumps(clean(event), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
            self.wfile.flush()

        try:
            run(emit)
            emit({"type": "done"})
        except AgentUnavailable as exc:
            emit({"type": "error", "code": "unavailable", "message": str(exc)})
        except AgentError as exc:
            emit({"type": "error", "message": str(exc)})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                              # the page was closed; nothing to tell
        except Exception as exc:
            sys.stderr.write(f"  ! agent error: {type(exc).__name__}: {exc}\n")
            try:
                emit({"type": "error", "message": "Something went wrong inside the agent. The terminal running the server shows details."})
            except OSError:
                pass

    # -- routes

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._guard():
            return
        url = urlparse(self.path)
        path = url.path
        try:
            if path in STATIC:
                name, ctype = STATIC[path]
                f = HERE / "static" / name
                return self._send(200, f.read_bytes(), ctype) if f.exists() else self._json(404, {"error": "Not found."})
            if path == "/template.csv" and TEMPLATE.exists():
                return self._send(200, TEMPLATE.read_bytes(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="post_collection_template.csv"'})
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if path == "/api/meta":
                return self._json(200, self.app.meta())
            if path == "/api/status":
                return self._json(200, self.app.status())
            if path == "/api/jobs":
                return self._json(200, self.app.jobs.status())
            if path in ("/api/info", "/api/dashboard"):
                platform, brand = pick(q)
                with self.app.lock:
                    out = self.app.engine.info() if path == "/api/info" else self.app.engine.dashboard(platform, brand)
                return self._json(200, out)
            return self._json(404, {"error": "Not found."})
        except Bad as exc:
            return self._json(exc.status, {"error": exc.message, **exc.extra})
        except Exception as exc:
            sys.stderr.write(f"  ! error on {path}: {type(exc).__name__}: {exc}\n")
            return self._json(500, {"error": "Something went wrong while reading the data. The terminal running the server shows details."})

    def do_POST(self):
        if not self._guard():
            return
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            return self._json(403, {"error": "Requests from other sites are not allowed."})
        path = self.path.split("?", 1)[0]
        if path not in BODY_LIMIT:
            return self._json(404, {"error": "Not found."})
        if "application/json" not in self.headers.get("Content-Type", ""):
            return self._json(415, {"error": "Send JSON."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= BODY_LIMIT[path]:
            return self._json(413, {"error": "That is too large to send."})
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "Send a JSON object."})
        try:
            return self._post(path, body)
        except Bad as exc:
            return self._json(exc.status, {"error": exc.message, **exc.extra})
        except Exception as exc:  # keep the page alive, show the cause in the terminal
            sys.stderr.write(f"  ! error on {path}: {type(exc).__name__}: {exc}\n")
            return self._json(500, {"error": "Something went wrong while reading the data. The terminal running the server shows details."})

    def _post(self, path: str, body: dict):
        app = self.app
        if path == "/api/recheck":
            agent_config.reload_env(ENV_FILE)
            app.agent.reload()
            return self._json(200, app.status())
        if path == "/api/jobs/start":
            try:
                return self._json(200, app.jobs.start(str(body.get("source", ""))))
            except JobError as exc:
                raise Bad(409, str(exc)) from exc
        platform, brand = pick(body)
        if path == "/api/ask":
            question = body.get("question")
            if not isinstance(question, str):
                raise Bad(400, "Send {\"question\": text}.")
            with app.lock:
                return self._json(200, app.engine.ask(question[:MAX_QUESTION], platform, brand))
        if path == "/api/agent":
            return self._agent(body, platform, brand)
        now = datetime.now(timezone.utc)
        with app.lock:
            if path == "/api/add_post":
                fields = body.get("fields")
                if not isinstance(fields, dict):
                    raise Bad(400, "Send {\"fields\": {...}}.")
                return self._json(200, entry.add_post(fields, app.brands, MANUAL_DIR, RAW_DIR, now))
            text, name = body.get("text"), body.get("filename")
            if not isinstance(text, str) or not isinstance(name, str):
                raise Bad(400, "Send {\"filename\": text, \"text\": text}.")
            return self._json(200, entry.import_csv(text, name[:120], app.brands, MANUAL_DIR, RAW_DIR, now))

    def _agent(self, body: dict, platform, brand):
        app = self.app
        mode = body.get("mode") or "chat"
        message = body.get("message")
        history = body.get("history") or []
        if mode not in ("chat", "brief") or not isinstance(message, str) or not isinstance(history, list) or len(history) > 40:
            raise Bad(400, "Send {\"message\": text, \"history\": [...], \"mode\": \"chat\" or \"brief\"}.")
        status = app.agent.status()
        if not status["available"]:
            raise Bad(503, status["reason"], code="unavailable")
        if not app.allow_agent_request():
            raise Bad(429, "Too many questions in a minute. Wait a moment and try again.")
        filters = {"platform": platform, "brand": brand}
        self._stream(lambda emit: app.agent.run(message, history, filters, mode, emit))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--data-root", type=Path, help="use another folder holding raw/ and manual/ (for testing; your real data is untouched)")
    args = p.parse_args(argv)
    if args.data_root:
        global RAW_DIR, MANUAL_DIR
        RAW_DIR, MANUAL_DIR = args.data_root / "raw", args.data_root / "manual"
    Handler.app = App()
    Handler.port = args.port
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        print(f"Cannot start on port {args.port}: {exc}. Try --port {args.port + 1}.", file=sys.stderr)
        return 1
    info = Handler.app.engine.info()
    agent = Handler.app.agent.status()
    print(f"Content Detective: http://127.0.0.1:{args.port}", file=sys.stderr)
    print(f"  data folder: {RAW_DIR}", file=sys.stderr)
    print(f"  loaded: {info['rows']} posts" if not info["empty"] else "  no data yet (use the Add data or Setup tab)", file=sys.stderr)
    print(f"  agent: {agent['model']} ready" if agent["available"] else "  agent: not set up (simple mode). Add ANTHROPIC_API_KEY to .env", file=sys.stderr)
    print("  press Ctrl+C to stop", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
