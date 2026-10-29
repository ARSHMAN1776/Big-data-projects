"""Agent settings, read from the environment (.env). Nothing here ever contains a secret value."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"
VALID_EFFORT = ("low", "medium", "high", "xhigh", "max")
# $ per million tokens (input, output). Cache reads cost 0.1x input, cache writes 1.25x input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00), "claude-opus-5": (5.00, 25.00), "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50), "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
}
# Models that accept output_config.effort, and models that accept the server-side refusal fallback.
EFFORT_MODELS = set(PRICES)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Keys the app uses. reload_env() re-reads only these from .env.
ENV_KEYS = ("ANTHROPIC_API_KEY", "YOUTUBE_API_KEY", "IG_USER_ID", "IG_ACCESS_TOKEN", "GRAPH_API_VERSION",
            "AGENT_MODEL", "AGENT_EFFORT", "AGENT_DAILY_USD_LIMIT", "AGENT_MAX_STEPS")


@dataclass(frozen=True)
class AgentConfig:
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_tokens: int = 16000          # thinking counts toward this, so keep it generous
    max_steps: int = 8               # model calls per question
    max_output_tokens: int = 60_000  # total generated tokens per question, across steps
    daily_usd_limit: float = 5.0
    history_messages: int = 8
    history_chars: int = 3000
    timeout: float = 180.0


def _num(value: str | None, cast, default, low=None, high=None):
    try:
        v = cast(value)
    except (TypeError, ValueError):
        return default
    if (low is not None and v < low) or (high is not None and v > high):
        return default
    return v


def from_env(env=None) -> AgentConfig:
    env = os.environ if env is None else env
    effort = (env.get("AGENT_EFFORT") or "medium").strip().lower()
    return AgentConfig(
        model=(env.get("AGENT_MODEL") or DEFAULT_MODEL).strip(),
        effort=effort if effort in VALID_EFFORT else "medium",
        max_steps=_num(env.get("AGENT_MAX_STEPS"), int, 8, 1, 20),
        daily_usd_limit=_num(env.get("AGENT_DAILY_USD_LIMIT"), float, 5.0, 0.0, 10_000.0),
    )


def reload_env(path: Path, env=None) -> None:
    """Re-read .env so a key pasted while the app is running takes effect. Only known keys are touched."""
    env = os.environ if env is None else env
    if not path.exists():
        return
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k in ENV_KEYS:
            seen.add(k)
            env[k] = v.strip().strip('"').strip("'")
    for k in ENV_KEYS:
        if k not in seen and env is os.environ and k in os.environ and not os.environ[k]:
            del os.environ[k]


def estimate_usd(model: str, usage) -> float:
    """Cost of one API response from its usage block. Unknown models are priced like the default."""
    inp, out = PRICES.get(model, PRICES[DEFAULT_MODEL])
    g = lambda name: float(getattr(usage, name, 0) or 0)  # noqa: E731
    cost = g("input_tokens") * inp + g("cache_read_input_tokens") * inp * 0.1
    cost += g("cache_creation_input_tokens") * inp * 1.25 + g("output_tokens") * out
    return cost / 1_000_000
