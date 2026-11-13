"""Analytics endpoints wrapping engine.dashboard() and engine.info()."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

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
