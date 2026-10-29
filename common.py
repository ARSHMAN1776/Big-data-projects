"""Helpers shared by every collector and by the manual-entry merger."""
from __future__ import annotations

import os
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKT = timezone(timedelta(hours=5), "PKT")


_sink = threading.local()


def set_log_sink(fn) -> None:
    """Send this thread's log() output to fn(text) instead of stderr. Pass None to restore."""
    _sink.fn = fn


def log(msg: str = "") -> None:
    fn = getattr(_sink, "fn", None)
    if fn is not None:
        fn(str(msg))
    else:
        print(msg, file=sys.stderr, flush=True)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def to_int(value) -> int | None:
    """Missing stays missing (blank), it is never turned into 0."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def time_fields(iso: str) -> dict:
    """Accepts '...Z', '...+00:00' and Meta's '...+0000'. Returns UTC and Pakistan-time fields."""
    iso = iso.strip().replace("Z", "+00:00")
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    dt = dt.astimezone(timezone.utc)
    pkt = dt.astimezone(PKT)
    return {
        "published_at_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date_pkt": pkt.date().isoformat(),
        "weekday_pkt": pkt.strftime("%A"),
        "hour_pkt": pkt.hour,
    }


def extract_hashtags(text: str) -> str:
    seen: dict[str, None] = {}
    for tag in re.findall(r"#(?!\d+\b)\w+", text or ""):
        seen.setdefault(tag.lower(), None)
    return "|".join(seen)


# One schema for Instagram and Facebook posts, whether they came from the API or from hand entry.
POST_COLUMNS = [
    "platform", "brand", "post_id", "url", "caption", "hashtags",
    "published_at_utc", "date_pkt", "weekday_pkt", "hour_pkt",
    "media_type", "likes", "comments", "shares", "views", "followers",
    "source", "collector", "collected_at_utc",
]
MEDIA_TYPES = ["image", "carousel", "reel", "video", "text", "link", "other"]
NO_TIME = {"published_at_utc": "", "date_pkt": "", "weekday_pkt": "", "hour_pkt": None}


def build_post(*, platform, brand, post_id, url, caption, media_type, likes, comments, shares,
               views, followers, source, collector, collected_at, when) -> dict:
    row = {
        "platform": platform, "brand": brand, "post_id": post_id, "url": url,
        "caption": caption, "hashtags": extract_hashtags(caption),
        "media_type": media_type, "likes": likes, "comments": comments, "shares": shares,
        "views": views, "followers": followers, "source": source, "collector": collector,
        "collected_at_utc": collected_at,
    }
    row.update(NO_TIME)
    row.update(when)
    return row
