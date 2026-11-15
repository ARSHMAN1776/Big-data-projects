"""Chat endpoints: AI-powered (Gemini) + rule-based fallback."""
from __future__ import annotations

import json
import queue
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from agent.agent import AgentError, AgentUnavailable
from backend.services.gemini_chat import ask_gemini
from backend.state import state
from backend.utils import clean, pick_filters

router = APIRouter(prefix="/api", tags=["chat"])

MAX_QUESTION = 300


@router.post("/ask")
async def ask_question(request: Request):
    """Rule-based analytics answer (always works, no API key needed)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Send a JSON object.")

    question = body.get("question")
    if not isinstance(question, str):
        raise HTTPException(status_code=400, detail='Send {"question": text}.')

    plat, br = pick_filters(body.get("platform"), body.get("brand"))
    with state.lock:
        ans = state.engine.ask(question[:MAX_QUESTION], plat, br)
    return JSONResponse(content=clean(ans))


@router.post("/chat")
async def ai_chat(request: Request):
    """AI-powered chat using Gemini with rule-based data context.
    Falls back to rule-based if no GEMINI_API_KEY is set.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Send a JSON object.")

    question = body.get("question") or body.get("message", "")
    history  = body.get("history") or []
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(status_code=400, detail='Send {"question": text}.')

    plat, br = pick_filters(body.get("platform"), body.get("brand"))

    with state.lock:
        engine_ans = state.engine.ask(question[:MAX_QUESTION], plat, br)
        info       = state.engine.info()

    total_posts = int(info.get("rows", 0))
    result = ask_gemini(question, history, engine_ans, total_posts, plat, br)
    return JSONResponse(content=clean(result))


@router.post("/agent")
async def chat_agent(request: Request):
    """Streaming Claude agent (requires ANTHROPIC_API_KEY in .env)."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Send a JSON object.")

    mode    = body.get("mode") or "chat"
    message = body.get("message")
    history = body.get("history") or []

    if (mode not in ("chat", "brief") or not isinstance(message, str)
            or not isinstance(history, list) or len(history) > 40):
        raise HTTPException(status_code=400, detail="Invalid request.")

    status = state.agent.status()
    if not status["available"]:
        raise HTTPException(status_code=503, detail={"error": status["reason"], "code": "unavailable"})

    if not state.allow_agent_request():
        raise HTTPException(status_code=429, detail="Too many questions in a minute. Wait a moment.")

    plat, br = pick_filters(body.get("platform"), body.get("brand"))
    filters  = {"platform": plat, "brand": br}

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
                q.put({"type": "error", "message": f"Agent error: {exc}"})
            finally:
                q.put(SENTINEL)

        threading.Thread(target=runner, daemon=True).start()
        while True:
            item = q.get()
            if item is SENTINEL:
                break
            yield (json.dumps(clean(item), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")

    return StreamingResponse(
        generate_events(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Connection": "close", "X-Accel-Buffering": "no"},
    )
