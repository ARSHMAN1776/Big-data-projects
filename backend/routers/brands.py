"""Brands router: exposes configured brands and metadata."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

ROOT = Path(__file__).resolve().parent.parent.parent
SOCIAL_BRANDS_FILE = ROOT / "config" / "social_brands.json"
BRANDS_FILE = ROOT / "config" / "brands.json"

router = APIRouter(prefix="/api", tags=["brands"])


@router.get("/brands")
def get_brands() -> list[dict[str, Any]]:
    social_data: dict[str, Any] = {}
    if SOCIAL_BRANDS_FILE.exists():
        try:
            social_data = json.loads(SOCIAL_BRANDS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    yt_map: dict[str, dict[str, Any]] = {}
    if BRANDS_FILE.exists():
        try:
            b_data = json.loads(BRANDS_FILE.read_text(encoding="utf-8"))
            for b in b_data.get("brands", []):
                yt_map[b.get("brand")] = b
            for b in b_data.get("top5", []):
                yt_map[b.get("brand")] = b
        except Exception:
            pass

    results = []
    for item in social_data.get("brands", []):
        bname = item.get("brand")
        yt = yt_map.get(bname, {})
        results.append({
            "name": bname,
            "instagram_handle": item.get("instagram"),
            "youtube_handle": yt.get("handle"),
            "youtube_channel_id": yt.get("channel_id"),
            "approx_subscribers": yt.get("approx_subscribers"),
        })

    return results
