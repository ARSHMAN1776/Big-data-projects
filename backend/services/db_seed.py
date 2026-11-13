"""Seed the SQLite posts table from data/raw/*.csv on startup.

Upserts by (platform, post_id_ext). Safe to call repeatedly.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "chatbot"))
sys.path.insert(0, str(ROOT))

import pandas as pd
from sqlalchemy.orm import Session

from backend.database import Post, SessionLocal, create_tables
import engine as eng  # chatbot/engine.py


def _to_int(v) -> int | None:
    try:
        f = float(v)
        import math
        if math.isnan(f):
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def _to_float(v) -> float | None:
    try:
        import math
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def seed_from_dataframe(df: pd.DataFrame, db: Session) -> int:
    """Upsert rows from a prepared DataFrame into the posts table."""
    if df.empty:
        return 0

    # Build a set of existing (platform, post_id_ext) for fast lookup
    existing: set[tuple[str, str]] = set()
    for row in db.query(Post.platform, Post.post_id_ext).all():
        existing.add((row.platform, row.post_id_ext))

    added = 0
    for _, row in df.iterrows():
        platform = str(row.get("platform", "") or "")
        post_id = str(row.get("post_id", "") or "")
        if not platform or not post_id:
            continue
        key = (platform, post_id)
        if key in existing:
            continue

        # engagement_rate calculation
        likes = _to_int(row.get("likes"))
        comments = _to_int(row.get("comments"))
        shares = _to_int(row.get("shares"))
        followers = _to_int(row.get("followers"))
        eng_rate: float | None = None
        if followers and followers > 0 and likes is not None and comments is not None:
            raw_eng = (likes or 0) + (comments or 0) + (shares or 0)
            eng_rate = round(raw_eng / followers * 100, 4)

        post = Post(
            brand=str(row.get("brand", "") or ""),
            platform=platform,
            post_id_ext=post_id,
            url=str(row.get("url", "") or "") or None,
            caption=str(row.get("text", "") or row.get("caption", "") or "") or None,
            hashtags=str(row.get("hashtags", "") or "") or None,
            post_date=str(row.get("date_pkt", "") or "") or None,
            weekday_pkt=str(row.get("weekday_pkt", "") or "") or None,
            hour_pkt=_to_int(row.get("hour_pkt")),
            media_type=str(row.get("media_type", "") or "") or None,
            likes=likes,
            comments=comments,
            shares=shares,
            views=_to_int(row.get("views")),
            followers_on_page=followers,
            engagement_rate=eng_rate,
            source=str(row.get("source", "") or "") or None,
            collector=str(row.get("collector", "") or "") or None,
            collected_at=str(row.get("collected_at_utc", "") or "") or None,
        )
        db.add(post)
        existing.add(key)
        added += 1

    if added:
        db.commit()
    return added


def seed_all() -> int:
    """Read all CSVs from data/raw and seed into SQLite. Returns total rows added."""
    create_tables()
    raw_dir = ROOT / "data" / "raw"
    if not raw_dir.exists():
        return 0

    # Also check demo dir
    demo_dir = ROOT / "data" / "demo"

    store = eng.DataStore(raw_dir)
    df = store.frame()

    # Try to also load demo CSV if it exists
    frames = [df] if not df.empty else []
    if demo_dir.exists():
        for csv_path in demo_dir.glob("*.csv"):
            try:
                extra = pd.read_csv(csv_path, encoding="utf-8-sig")
                # Normalize columns to match engine format
                if "caption" in extra.columns and "text" not in extra.columns:
                    extra["text"] = extra["caption"]
                if "post_id" not in extra.columns and "post_id_ext" in extra.columns:
                    extra["post_id"] = extra["post_id_ext"]
                # Coerce numeric
                for c in ("likes", "comments", "shares", "views", "followers", "hour_pkt"):
                    if c in extra.columns:
                        extra[c] = pd.to_numeric(extra[c], errors="coerce")
                frames.append(extra)
            except Exception as exc:
                print(f"  [seed] skipped {csv_path.name}: {exc}", flush=True)

    if not frames:
        print("  [seed] no data to seed", flush=True)
        return 0

    combined = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    db = SessionLocal()
    try:
        added = seed_from_dataframe(combined, db)
        print(f"  [seed] seeded {added} new posts into SQLite", flush=True)
        return added
    finally:
        db.close()
