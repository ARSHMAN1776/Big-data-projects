"""The agent loop: a Claude model that answers by calling tools over the collected posts.

The loop is written by hand (rather than with the SDK's tool runner) because it must:
  * report progress to the page while it works,
  * stop at a step limit, a per-question token cap and a daily spending limit,
  * give the model only tools that read data, and turn any request to collect data into a proposal the
    user approves in the app,
  * pass the model's own content blocks (including its thinking blocks) back unchanged between steps.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import anthropic

from . import config as cfgmod
from .config import EFFORT_MODELS, FALLBACK_BETA, FALLBACK_MODELS, AgentConfig
from .prompts import BRIEF, SYSTEM, context_line
from .tools import LABELS, TOOLS, ToolBox
from .usage import Ledger


class AgentUnavailable(Exception):
    """No usable credentials. The message says how to fix it and is safe to show."""


class AgentError(Exception):
    """Something went wrong that the user can act on. The message is safe to show."""


@dataclass
class AgentResult:
    text: str = ""
    evidence: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    stop_reason: str = ""
    model: str = ""


def _redact(text: str) -> str:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        secret = os.environ.get(name)
        if secret and len(secret) > 8:
            text = text.replace(secret, "***")
    return text


class DataAgent:
    def __init__(self, engine, lock=None, cfg: AgentConfig | None = None, ledger: Ledger | None = None,
                 integrations: Callable[[], dict] = lambda: {}, client_factory=None):
        self.engine = engine
        self.lock = lock or threading.Lock()
        self.cfg = cfg or cfgmod.from_env()
        self.ledger = ledger or Ledger(None)
        self.integrations = integrations
        self._factory = client_factory
        self._client = None
        self.toolbox = ToolBox(engine, self.lock, integrations)
        self._slots = threading.BoundedSemaphore(2)   # at most two questions in flight

    # -- setup

    def reload(self) -> None:
        """Pick up a changed .env: new settings and a fresh client."""
        self.cfg = cfgmod.from_env()
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                self._client = (self._factory or self._default_client)()
            except AgentUnavailable:
                raise
            except Exception as exc:
                raise AgentUnavailable("No Anthropic API key was found. Add ANTHROPIC_API_KEY to the .env file and press Re-check in the Setup tab.") from exc
        return self._client

    def _default_client(self):
        return anthropic.Anthropic(timeout=self.cfg.timeout, max_retries=2)

    def status(self) -> dict:
        try:
            self._get_client()
            ok, reason = True, None
        except AgentUnavailable as exc:
            ok, reason = False, str(exc)
        return {"available": ok, "reason": reason, "model": self.cfg.model, "effort": self.cfg.effort,
                "daily_usd_limit": self.cfg.daily_usd_limit, "usage_today": self.ledger.today()}

    # -- one question

    def _history(self, history) -> list[dict]:
        out: list[dict] = []
        for h in list(history or [])[-self.cfg.history_messages:]:
            if not isinstance(h, dict) or h.get("role") not in ("user", "assistant") or not isinstance(h.get("content"), str):
                continue
            text = h["content"].strip()[: self.cfg.history_chars]
            if not text:
                continue
            if out and out[-1]["role"] == h["role"]:
                out[-1]["content"] += "\n\n" + text
            else:
                out.append({"role": h["role"], "content": text})
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    def _call(self, client, messages):
        kw = dict(model=self.cfg.model, max_tokens=self.cfg.max_tokens, system=SYSTEM, tools=TOOLS, messages=messages,
                  cache_control={"type": "ephemeral"})
        if self.cfg.model in EFFORT_MODELS:
            kw["output_config"] = {"effort": self.cfg.effort}   # thinking is always on for this model; effort is the control
        if self.cfg.model in FALLBACK_MODELS:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"                          # a declined request is re-run on Anthropic's recommended model
        try:
            return client.beta.messages.create(**kw)
        except anthropic.RateLimitError as exc:
            wait = int(exc.response.headers.get("retry-after", "30")) if getattr(exc, "response", None) is not None else 30
            raise AgentError(f"Anthropic's rate limit was reached. Wait about {wait} seconds and ask again.") from exc
        except anthropic.AuthenticationError as exc:
            raise AgentError("Anthropic rejected the API key. Check ANTHROPIC_API_KEY in the .env file.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AgentError("This API key is not allowed to use that model. Check the key's permissions, or set AGENT_MODEL to another model.") from exc
        except anthropic.NotFoundError as exc:
            raise AgentError(f"Anthropic does not know the model '{self.cfg.model}'. Check AGENT_MODEL in the .env file.") from exc
        except anthropic.BadRequestError as exc:
            raise AgentError("Anthropic could not accept the request: " + _redact(str(getattr(exc, "message", exc)))[:300]) from exc
        except anthropic.APITimeoutError as exc:
            raise AgentError("Anthropic took too long to answer. Try again.") from exc
        except anthropic.APIConnectionError as exc:
            raise AgentError("Could not reach Anthropic. Check the internet connection.") from exc
        except anthropic.APIStatusError as exc:
            raise AgentError(f"Anthropic had a problem (HTTP {exc.status_code}). Try again in a moment.") from exc

    def run(self, message: str, history=(), filters: dict | None = None, mode: str = "chat",
            emit: Callable[[dict], None] | None = None, today: date | None = None) -> AgentResult:
        emit = emit or (lambda ev: None)
        message = (message or "").strip()[:1000]
        if mode == "chat" and not message:
            raise AgentError("Type a question first.")
        client = self._get_client()
        limit = self.cfg.daily_usd_limit
        spent = self.ledger.today(today)["usd"]
        if limit > 0 and spent >= limit:
            raise AgentError(f"Today's agent spending limit (${limit:.2f}) has been reached. Raise AGENT_DAILY_USD_LIMIT in the .env file, or try again tomorrow.")
        if not self._slots.acquire(blocking=False):
            raise AgentError("The agent is busy with other questions. Try again in a moment.")
        try:
            return self._loop(client, message, history, filters or {}, mode, emit, today or date.today())
        finally:
            self._slots.release()

    def _loop(self, client, message, history, filters, mode, emit, today) -> AgentResult:
        cfg = self.cfg
        if mode == "brief":
            scope = " ".join(x for x in (filters.get("brand"), filters.get("platform") and f"on {filters['platform']}") if x) or "all brands on all platforms"
            user_text = BRIEF.format(scope=scope)
        else:
            user_text = message
        messages = self._history(history) + [{"role": "user", "content": f"{context_line(filters, today.isoformat())}\n\n{user_text}"}]
        res = AgentResult(model=cfg.model)
        used = {"calls": 0, "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0, "usd": 0.0}
        emit({"type": "start", "model": cfg.model})
        last_text = ""
        first = True
        for _ in range(cfg.max_steps):
            resp = self._call(client, messages)
            used["usd"] += self.ledger.record(cfg.model, resp.usage, new_run=first, today=today)
            first = False
            used["calls"] += 1
            used["input_tokens"] += int(getattr(resp.usage, "input_tokens", 0) or 0)
            used["cache_read_tokens"] += int(getattr(resp.usage, "cache_read_input_tokens", 0) or 0)
            used["output_tokens"] += int(getattr(resp.usage, "output_tokens", 0) or 0)
            res.stop_reason = resp.stop_reason or ""
            texts = [b.text for b in resp.content if b.type == "text"]
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            last_text = "\n".join(texts).strip() or last_text
            if resp.stop_reason == "refusal":
                res.text = "The model declined to answer that. Try asking in a different way."
                break
            if resp.stop_reason == "max_tokens":
                res.text = (last_text + "\n\n(The answer was cut off. Ask a narrower question.)").strip()
                break
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            if not tool_uses:
                res.text = last_text
                break
            messages.append({"role": "assistant", "content": resp.content})   # unchanged, so thinking blocks stay valid
            results = []
            for tu in tool_uses:
                t0 = time.time()
                emit({"type": "step", "id": tu.id, "tool": tu.name, "status": "running", "label": _label(tu)})
                out = self.toolbox.run(tu.name, tu.input)
                ms = int((time.time() - t0) * 1000)
                res.steps.append({"tool": tu.name, "label": out.label, "ok": not out.is_error, "ms": ms})
                emit({"type": "step", "id": tu.id, "tool": tu.name, "status": "error" if out.is_error else "ok", "label": out.label, "ms": ms})
                if out.ui:
                    res.evidence.append(out.ui)
                    emit({"type": "evidence", "title": out.ui["title"], "reply": out.ui["reply"], "kind": out.ui.get("kind")})
                if out.action:
                    res.actions.append(out.action)
                    emit({"type": "action", "action": out.action})
                item = {"type": "tool_result", "tool_use_id": tu.id, "content": out.content}
                if out.is_error:
                    item["is_error"] = True
                results.append(item)
            messages.append({"role": "user", "content": results})        # every result in one message
            if used["output_tokens"] > cfg.max_output_tokens:
                res.text = last_text or "I stopped because this question used more than the per-question limit. Ask a narrower question."
                break
        else:
            res.text = last_text or "I ran out of steps before I could finish. Try a narrower question."
        res.usage = {**used, "usd": round(used["usd"], 4)}
        emit({"type": "answer", "text": res.text, "usage": res.usage, "stop_reason": res.stop_reason})
        return res


def _label(tool_use) -> str:
    args = tool_use.input if isinstance(tool_use.input, dict) else {}
    return LABELS.get(args.get("analysis"), LABELS.get(tool_use.name, tool_use.name))
