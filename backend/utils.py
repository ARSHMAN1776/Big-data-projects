"""Data sanitization and helper utilities."""
from __future__ import annotations

import math
from typing import Any

import engine


def clean(obj: Any) -> Any:
    """Make an answer JSON-safe: no NaN or Infinity, no numpy types."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if hasattr(obj, "item"):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def pick_filters(platform: str | None = None, brand: str | None = None) -> tuple[str | None, str | None]:
    p = (platform.strip() if platform else None) or None
    b = (brand.strip() if brand else None) or None
    if p is not None and p not in engine.PLATFORM_NAMES:
        p = None
    if b is not None and (not isinstance(b, str) or len(b) > 80):
        b = None
    return p, b
