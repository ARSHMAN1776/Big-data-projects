"""Analytics endpoints wrapping engine.dashboard() and engine.info()."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.services.analytics import (
    engagement_by_brand,
    performance_by_format,
    performance_by_platform,
    performance_by_timing,
)
from backend.state import state
from backend.utils import clean, pick_filters

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/info")
def get_info():
    with state.lock:
        data = state.engine.info()
    return JSONResponse(content=clean(data))


@router.get("/dashboard")
def get_dashboard(
    platform: str | None = Query(default=None),
    brand: str | None = Query(default=None),
):
    plat, br = pick_filters(platform, brand)
    with state.lock:
        data = state.engine.dashboard(plat, br)
    return JSONResponse(content=clean(data))


@router.get("/summary")
def get_summary():
    """High-level project summary for the report and research page."""
    with state.lock:
        df = state.engine.store.frame()
        info = state.engine.info()

    eng  = engagement_by_brand(df)
    fmt  = performance_by_format(df)
    plat = performance_by_platform(df)
    timing = performance_by_timing(df)

    # Top brand
    top_brand, top_brand_val = None, None
    if eng["labels"]:
        vals = eng["datasets"][0]["data"]
        idx  = vals.index(max(v for v in vals if v is not None))
        top_brand     = eng["labels"][idx]
        top_brand_val = vals[idx]

    # Best format
    top_format, top_format_val = None, None
    if fmt["labels"]:
        vals = fmt["datasets"][0]["data"]
        idx  = vals.index(max(v for v in vals if v is not None))
        top_format     = fmt["labels"][idx]
        top_format_val = vals[idx]

    # Best platform
    top_platform = None
    if plat["labels"]:
        top_platform = plat["labels"][0]

    # Best day
    best_day = None
    if timing["labels"] and timing["datasets"]:
        day_data = timing["datasets"][0]["data"]
        if day_data:
            valid = [(v, l) for v, l in zip(day_data, timing["labels"]) if v is not None]
            if valid:
                best_day = max(valid, key=lambda x: x[0])[1]

    return JSONResponse(content=clean({
        "total_posts":    int(info.get("rows", 0)),
        "brands_count":   len(eng["labels"]),
        "platforms_count": int(df["platform"].nunique()) if not df.empty else 0,
        "metric_used":    eng["meta"].get("metric", "views"),
        "top_brand":      top_brand,
        "top_brand_val":  top_brand_val,
        "best_format":    top_format,
        "best_format_val": top_format_val,
        "best_platform":  top_platform,
        "best_day":       best_day,
        "github_url":     "https://github.com/ARSHMAN1776/Big-data-projects",
        "version":        "2.0.0",
    }))
