"""Meta and operational endpoints wrapping chatbot/server.py logic."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.state import ENV_FILE, state
from agent import config as agent_config
from agent.jobs import JobError

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta")
def get_meta():
    return state.meta()


@router.get("/status")
def get_status():
    return state.status()


@router.get("/jobs")
def get_jobs():
    return state.jobs.status()


@router.post("/jobs/start")
async def start_job(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    source = str(body.get("source", ""))
    try:
        res = state.jobs.start(source)
        return res
    except JobError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/recheck")
def recheck():
    agent_config.reload_env(ENV_FILE)
    state.agent.reload()
    return state.status()
