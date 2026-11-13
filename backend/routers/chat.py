"""Chat endpoints: rule-based ask and streaming AI agent."""
from __future__ import annotations

import json
import queue
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent.agent import AgentError, AgentUnavailable
from backend.state import state
from backend.utils import clean, pick_filters

router = APIRouter(prefix="/api", tags=["chat"])

MAX_QUESTION = 300


@router.post("/ask")
async def ask_question(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Send a JSON object.")

    question = body.get("question")
    if not isinstance(question, str):
        raise HTTPException(status_code=400, detail="Send {\"question\": text}.")

    plat, br = pick_filters(body.get("platform"), body.get("brand"))
    with state.lock:
        ans = state.engine.ask(question[:MAX_QUESTION], plat, br)
    return JSONResponse(content=clean(ans))


@router.post("/agent")
async def chat_agent(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Send a JSON object.")

    mode = body.get("mode") or "chat"
    message = body.get("message")
    history = body.get("history") or []

    if mode not in ("chat", "brief") or not isinstance(message, str) or not isinstance(history, list) or len(history) > 40:
        raise HTTPException(status_code=400, detail="Send {\"message\": text, \"history\": [...], \"mode\": \"chat\" or \"brief\"}.")

    status = state.agent.status()
    if not status["available"]:
        raise HTTPException(status_code=503, detail={"error": status["reason"], "code": "unavailable"})

    if not state.allow_agent_request():
        raise HTTPException(status_code=429, detail="Too many questions in a minute. Wait a moment and try again.")

    plat, br = pick_filters(body.get("platform"), body.get("brand"))
    filters = {"platform": plat, "brand": br}

    def generate_events():
        q: queue.Queue = queue.Queue()
        SENTINEL = object()

        def emit(event: dict) -> None:
            q.put(event)

        def runner():
            try:
                state.agent.run(message, history, filters, mode, emit)
                q.put({"type": "done"})
            except AgentUnavailable as exc:
                q.put({"type": "error", "code": "unavailable", "message": str(exc)})
            except AgentError as exc:
                q.put({"type": "error", "message": str(exc)})
            except Exception as exc:
                q.put({"type": "error", "message": f"Something went wrong inside the agent: {exc}"})
            finally:
                q.put(SENTINEL)

        worker = threading.Thread(target=runner, daemon=True)
        worker.start()

        while True:
            item = q.get()
            if item is SENTINEL:
                break
            line = json.dumps(clean(item), ensure_ascii=False, allow_nan=False) + "\n"
            yield line.encode("utf-8")

    return StreamingResponse(
        generate_events(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Connection": "close", "X-Accel-Buffering": "no"},
    )
