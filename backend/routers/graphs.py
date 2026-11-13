"""Graph endpoints providing Chart.js-ready analytics data."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.schemas import GraphData
from backend.services.analytics import (
    engagement_by_brand,
    performance_by_format,
    performance_by_platform,
    performance_by_timing,
)
from backend.state import state
from backend.utils import clean, pick_filters

router = APIRouter(prefix="/api/graphs", tags=["graphs"])


@router.get("/engagement")
def get_engagement_graph(
    brand: str | None = Query(default=None),
    platform: str | None = Query(default=None),
):
    plat, br = pick_filters(platform, brand)
    with state.lock:
        df = state.engine.store.frame()
    result = engagement_by_brand(df, platform=plat, brand=br)
    return JSONResponse(content=clean(result))


@router.get("/formats")
def get_formats_graph(
    brand: str | None = Query(default=None),
    platform: str | None = Query(default=None),
):
    plat, br = pick_filters(platform, brand)
    with state.lock:
        df = state.engine.store.frame()
    result = performance_by_format(df, platform=plat, brand=br)
    return JSONResponse(content=clean(result))


@router.get("/platforms")
def get_platforms_graph(
    brand: str | None = Query(default=None),
):
    _, br = pick_filters(None, brand)
    with state.lock:
        df = state.engine.store.frame()
    result = performance_by_platform(df, brand=br)
    return JSONResponse(content=clean(result))


@router.get("/timing")
def get_timing_graph(
    brand: str | None = Query(default=None),
    platform: str | None = Query(default=None),
):
    plat, br = pick_filters(platform, brand)
    with state.lock:
        df = state.engine.store.frame()
    result = performance_by_timing(df, platform=plat, brand=br)
    return JSONResponse(content=clean(result))
