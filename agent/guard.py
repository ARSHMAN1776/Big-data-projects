"""Keep text that came from the internet (captions, titles) from being read as instructions.

Post text is written by brands and the public, so it is untrusted. Before anything from the data goes
to the model it is cleaned, shortened and fenced in <post_text> tags. The system prompt tells the model
that fenced text is data and never an instruction. This is one layer of defence, together with the fact
that the model has no tool that can change files or spend money without the user's approval.
"""
from __future__ import annotations

import json
import re
import unicodedata

# zero-width and bidirectional control characters can hide or reorder text
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁩﻿]")
_FENCE = re.compile(r"</?\s*post_text[^>]*>", re.I)
OPEN, CLOSE = "<post_text>", "</post_text>"
MAX_RESULT_CHARS = 14_000


def clean_text(value, limit: int = 200) -> str:
    s = unicodedata.normalize("NFC", str(value or ""))
    s = _INVISIBLE.sub("", s)
    s = "".join(ch if (ch.isprintable() or ch in "\n\t") else " " for ch in s)
    s = _FENCE.sub("‹tag removed›", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def untrusted(value, limit: int = 200) -> str:
    """Fence a piece of post text so the model can tell it is data."""
    return f"{OPEN}{clean_text(value, limit)}{CLOSE}"


def compact_reply(reply: dict, max_rows: int = 15) -> dict:
    """A smaller, safer copy of an engine answer for the model: numbers and cautions, no links, fenced text."""
    out: dict = {"summary": clean_text(reply.get("text", ""), 600), "evidence": clean_text(reply.get("evidence", ""), 300)}
    notes = [n["text"] for n in reply.get("notes", []) if n.get("level") != "method"]
    if notes:
        out["cautions_and_notes"] = notes
    shown = []
    for b in reply.get("blocks", []):
        if b["type"] == "bars":
            shown.append({"chart": b["title"], "bars": [
                {"label": clean_text(i["label"], 60), "value": i["display"], "n": i["note"].replace("n = ", "") if i.get("note") else None,
                 "small_sample": bool(i.get("flag"))} for i in b["items"][:max_rows]]})
        else:
            cols = {c["key"]: c["label"] for c in b["columns"]}
            rows = []
            for r in b["rows"][:max_rows]:
                row = {}
                for k, v in r.items():
                    label = cols.get(k, k)
                    if isinstance(v, dict):                      # a post cell: {"text", "href"}
                        row[label] = untrusted(v.get("text", ""))
                    else:
                        row[label] = v
                rows.append(row)
            shown.append({"table": rows})
    if shown:
        out["results"] = shown
    return out


def to_json(obj, limit: int = MAX_RESULT_CHARS) -> str:
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(s) > limit:
        s = s[:limit].rsplit(",", 1)[0] + '…"truncated":true}'
    return s
