"""Posts endpoints: SQLite post queries and data ingestion."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

import entry
from backend.database import Post, get_db
from backend.schemas import PostResponse
from backend.services.db_seed import seed_all
from backend.state import MANUAL_DIR, RAW_DIR, state
from backend.utils import clean, pick_filters

router = APIRouter(prefix="/api", tags=["posts"])


@router.get("/posts", response_model=list[PostResponse])
def get_posts(
    brand: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(Post)
    plat, br = pick_filters(platform, brand)
    if br:
        query = query.filter(Post.brand == br)
    if plat:
        query = query.filter(Post.platform == plat)
    posts = query.order_by(Post.id.desc()).limit(limit).all()
    return posts


@router.post("/add_post")
async def add_post(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Send a JSON object.")

    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise HTTPException(status_code=400, detail="Send {\"fields\": {...}}.")

    now = datetime.now(timezone.utc)
    with state.lock:
        res = entry.add_post(fields, state.brands, MANUAL_DIR, RAW_DIR, now)
        if res.get("ok"):
            try:
                seed_all()
            except Exception:
                pass
    return JSONResponse(content=clean(res))


@router.post("/import")
async def import_posts(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Send a JSON object.")

    text = body.get("text")
    name = body.get("filename")
    if not isinstance(text, str) or not isinstance(name, str):
        raise HTTPException(status_code=400, detail="Send {\"filename\": text, \"text\": text}.")

    now = datetime.now(timezone.utc)
    with state.lock:
        res = entry.import_csv(text, name[:120], state.brands, MANUAL_DIR, RAW_DIR, now)
        if res.get("ok"):
            try:
                seed_all()
            except Exception:
                pass
    return JSONResponse(content=clean(res))
