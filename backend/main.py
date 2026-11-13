"""Main FastAPI application for Content Detective."""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "chatbot"))
sys.path.insert(0, str(ROOT))

from backend.database import create_tables
from backend.routers import analytics, brands, chat, graphs, meta, posts
from backend.services.db_seed import seed_all

STATIC_DIR = ROOT / "chatbot" / "static"
TEMPLATE_FILE = ROOT / "templates" / "post_collection_template.csv"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure tables exist and seed SQLite
    create_tables()
    try:
        count = seed_all()
        print(f"[Content Detective] SQLite database initialized. Seeded {count} records.", flush=True)
    except Exception as exc:
        print(f"[Content Detective] Warning during DB seed: {exc}", flush=True)
    yield
    # Shutdown logic if needed


app = FastAPI(
    title="Content Detective API",
    description="Analytics and AI Agent API for Pakistani Fashion Brands",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware: allow local frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(meta.router)
app.include_router(analytics.router)
app.include_router(chat.router)
app.include_router(posts.router)
app.include_router(brands.router)
app.include_router(graphs.router)

# Mount static folder
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Direct static asset routes for frontend compatibility
@app.get("/", include_in_schema=False)
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file, media_type="text/html")
    return JSONResponse(status_code=404, content={"error": "index.html not found"})


@app.get("/app.js", include_in_schema=False)
def serve_app_js():
    f = STATIC_DIR / "app.js"
    if f.exists():
        return FileResponse(f, media_type="text/javascript")
    return Response(status_code=404)


@app.get("/style.css", include_in_schema=False)
def serve_style_css():
    f = STATIC_DIR / "style.css"
    if f.exists():
        return FileResponse(f, media_type="text/css")
    return Response(status_code=404)


@app.get("/template.csv", include_in_schema=False)
def serve_template():
    if TEMPLATE_FILE.exists():
        return FileResponse(
            TEMPLATE_FILE,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="post_collection_template.csv"'},
        )
    return Response(status_code=404)


@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon():
    return Response(status_code=204)
