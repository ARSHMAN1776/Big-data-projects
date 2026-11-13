#!/usr/bin/env python3
"""Start Content Detective. Same as before but with FastAPI. Run: python run.py"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "chatbot") not in sys.path:
    sys.path.insert(0, str(ROOT / "chatbot"))

import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8765, reload=False, log_level="info")
