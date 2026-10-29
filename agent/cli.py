"""Use the agent from the terminal, or from a scheduler.

  python -m agent.cli status
  python -m agent.cli ask "Which format works best on Instagram?"
  python -m agent.cli brief --brand Khaadi            # writes reports/<date>_<brand>.md
  python -m agent.cli brief --out reports

To run a brief every Monday, point Windows Task Scheduler or cron at the last command.
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "chatbot"))
import engine  # noqa: E402
from common import load_env  # noqa: E402

from . import config as cfgmod  # noqa: E402
from .agent import AgentError, AgentUnavailable, DataAgent  # noqa: E402
from .jobs import integration_status  # noqa: E402
from .usage import Ledger  # noqa: E402


def build_agent(raw_dir: Path | None = None) -> DataAgent:
    load_env(ROOT / ".env")
    store = engine.DataStore(raw_dir or ROOT / "data" / "raw")
    return DataAgent(engine.Engine(store), threading.Lock(), ledger=Ledger(ROOT / "data" / "agent_usage.json"), integrations=integration_status)


def _print_events(ev: dict) -> None:
    if ev["type"] == "step" and ev["status"] != "running":
        print(f"  [{'ok' if ev['status'] == 'ok' else 'problem'}] {ev['label']} ({ev['ms']} ms)", file=sys.stderr)
    elif ev["type"] == "action":
        print(f"  [proposal] collect {ev['action']['source']}: {ev['action']['reason']} (approve it in the app)", file=sys.stderr)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="agent.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show whether the agent is ready")
    a = sub.add_parser("ask", help="ask one question")
    a.add_argument("question")
    b = sub.add_parser("brief", help="write a marketing brief")
    b.add_argument("--brand")
    b.add_argument("--platform", choices=list(engine.PLATFORM_NAMES))
    b.add_argument("--out", type=Path, help="folder to save the brief in (default: print it)")
    args = p.parse_args(argv)

    agent = build_agent()
    if args.cmd == "status":
        s = agent.status()
        print(f"agent: {'ready' if s['available'] else 'not ready: ' + str(s['reason'])}")
        print(f"model: {s['model']} (effort {s['effort']}), daily limit ${s['daily_usd_limit']:.2f}, spent today ${s['usage_today']['usd']:.4f}")
        print("collection:", {k: v["configured"] for k, v in integration_status().items()})
        return 0 if s["available"] else 1
    filters = {"brand": getattr(args, "brand", None), "platform": getattr(args, "platform", None)}
    try:
        res = agent.run(getattr(args, "question", ""), filters=filters, mode="brief" if args.cmd == "brief" else "chat", emit=_print_events)
    except (AgentUnavailable, AgentError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.cmd == "brief" and args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", (args.brand or "all-brands")).strip("-").lower()
        path = args.out / f"{date.today().isoformat()}_{slug}.md"
        path.write_text(res.text + "\n", encoding="utf-8")
        print(f"saved {path}", file=sys.stderr)
    print(res.text)
    print(f"\n({res.usage['calls']} model calls, about ${res.usage['usd']:.3f})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
