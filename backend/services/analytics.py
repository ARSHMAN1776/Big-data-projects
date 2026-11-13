"""Analytics service: wraps engine.Engine to produce Chart.js-ready data."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

BRAND_COLORS: dict[str, str] = {
    "Khaadi":       "#2B6A4D",
    "Sapphire":     "#1B3A6B",
    "Gul Ahmed":    "#8B1A1A",
    "Maria B":      "#6B2D8B",
    "Sana Safinaz": "#1A5276",
    "Nishat Linen": "#1A5276",
    "Junaid Jamshed":"#C0392B",
    "Alkaram Studio":"#E67E22",
    "Outfitters":   "#16A085",
    "Zellbury":     "#8E44AD",
}
_DEFAULT_COLOR = "#6B7280"

_PALETTE = [
    "#2B6A4D", "#1B3A6B", "#8B1A1A", "#6B2D8B", "#1A5276",
    "#C0392B", "#E67E22", "#16A085", "#8E44AD", "#2C3E50",
]


def _color(brand: str, idx: int = 0) -> str:
    return BRAND_COLORS.get(brand, _PALETTE[idx % len(_PALETTE)])


def _safe(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _filter(df: pd.DataFrame, platform: str | None, brand: str | None) -> pd.DataFrame:
    if brand:
        df = df[df["brand"] == brand]
    if platform:
        df = df[df["platform"] == platform]
    return df


def _choose_metric(df: pd.DataFrame) -> tuple[str, str]:
    """Pick the best metric column given data coverage."""
    cov = lambda c: float(df[c].notna().mean()) if len(df) and c in df.columns else 0.0  # noqa: E731
    if cov("eng_rate") >= 0.5:
        return "eng_rate", "Engagement Rate (%)"
    if cov("engagement") >= 0.5:
        return "engagement", "Median Engagement"
    if cov("views") > 0:
        return "views", "Median Views"
    return "views", "Median Views"


def engagement_by_brand(df: pd.DataFrame, platform: str | None = None, brand: str | None = None) -> dict:
    """Median engagement/views per brand — Chart.js dataset."""
    df = _filter(df, platform, None)   # don't filter brand so we always show all brands
    if df.empty:
        return {"labels": [], "datasets": [], "meta": {"n": 0, "note": "No data"}}

    metric_col, metric_label = _choose_metric(df)

    g = df.groupby("brand")[metric_col].median().dropna().sort_values(ascending=False)
    counts = df.groupby("brand")[metric_col].count()
    labels = list(g.index)
    data = [_safe(v) for v in g.values]
    colors = [_color(b, i) for i, b in enumerate(labels)]

    return {
        "labels": labels,
        "datasets": [{
            "label": metric_label,
            "data": data,
            "backgroundColor": colors,
        }],
        "meta": {
            "n": int(len(df)),
            "metric": metric_col,
            "platform": platform or "all",
            "counts": {b: int(counts.get(b, 0)) for b in labels},
            "note": f"{len(df)} posts across {df['platform'].nunique()} platform(s)",
        },
    }


def performance_by_format(df: pd.DataFrame, platform: str | None = None, brand: str | None = None) -> dict:
    """Median metric per media format — Chart.js dataset."""
    df = _filter(df, platform, brand)
    if df.empty:
        return {"labels": [], "datasets": [], "meta": {"n": 0, "note": "No data"}}

    metric_col, metric_label = _choose_metric(df)

    if "media_type" not in df.columns:
        return {"labels": [], "datasets": [], "meta": {"n": 0, "note": "No media_type column"}}

    g = df.groupby("media_type")[metric_col].median().dropna().sort_values(ascending=False)
    counts = df.groupby("media_type")[metric_col].count()
    labels = list(g.index)
    data = [_safe(v) for v in g.values]

    FORMAT_COLORS = {
        "reel": "#E74C3C", "video": "#C0392B", "short": "#E91E63",
        "image": "#3498DB", "carousel": "#2980B9",
        "text": "#95A5A6", "link": "#7F8C8D", "other": "#BDC3C7",
    }
    colors = [FORMAT_COLORS.get(lbl, "#6B7280") for lbl in labels]

    return {
        "labels": labels,
        "datasets": [{
            "label": metric_label,
            "data": data,
            "backgroundColor": colors,
        }],
        "meta": {
            "n": int(len(df)),
            "metric": metric_col,
            "platform": platform or "all",
            "brand": brand or "all",
            "counts": {b: int(counts.get(b, 0)) for b in labels},
            "note": f"{len(df)} posts",
        },
    }


def performance_by_platform(df: pd.DataFrame, brand: str | None = None) -> dict:
    """Median metric per platform — Chart.js dataset."""
    if brand:
        df = df[df["brand"] == brand]
    if df.empty:
        return {"labels": [], "datasets": [], "meta": {"n": 0, "note": "No data"}}

    metric_col, metric_label = _choose_metric(df)

    PLATFORM_COLORS = {
        "instagram": "#E1306C",
        "facebook":  "#1877F2",
        "tiktok":    "#000000",
        "youtube":   "#FF0000",
    }

    g = df.groupby("platform")[metric_col].median().dropna().sort_values(ascending=False)
    counts = df.groupby("platform")[metric_col].count()
    labels = list(g.index)
    data = [_safe(v) for v in g.values]
    colors = [PLATFORM_COLORS.get(lbl, "#6B7280") for lbl in labels]

    return {
        "labels": labels,
        "datasets": [{
            "label": metric_label,
            "data": data,
            "backgroundColor": colors,
        }],
        "meta": {
            "n": int(len(df)),
            "metric": metric_col,
            "brand": brand or "all",
            "counts": {b: int(counts.get(b, 0)) for b in labels},
            "note": f"{len(df)} posts",
        },
    }


WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def performance_by_timing(df: pd.DataFrame, platform: str | None = None, brand: str | None = None) -> dict:
    """Best day + best hour-bucket — returns two datasets."""
    df = _filter(df, platform, brand)
    if df.empty:
        return {"labels": [], "datasets": [], "meta": {"n": 0, "note": "No data"}}

    metric_col, metric_label = _choose_metric(df)

    # By weekday
    day_col = "weekday_pkt"
    days_present = [d for d in WEEKDAYS if d in df[day_col].values] if day_col in df.columns else []
    day_data: list[float | None] = []
    for day in days_present:
        vals = df[df[day_col] == day][metric_col].dropna()
        day_data.append(_safe(vals.median()) if len(vals) else None)

    # By hour bucket
    BUCKETS = [
        ("Late Night\n12–5am", range(0, 6)),
        ("Morning\n6–11am", range(6, 12)),
        ("Afternoon\n12–4pm", range(12, 17)),
        ("Evening\n5–9pm", range(17, 22)),
        ("Night\n10–11pm", range(22, 24)),
    ]
    hour_labels = []
    hour_data: list[float | None] = []
    if "hour_pkt" in df.columns:
        for label, hrs in BUCKETS:
            mask = df["hour_pkt"].isin(list(hrs))
            vals = df[mask][metric_col].dropna()
            if len(vals):
                hour_labels.append(label)
                hour_data.append(_safe(vals.median()))

    return {
        "labels": days_present,
        "datasets": [
            {
                "label": f"{metric_label} by Day",
                "data": day_data,
                "backgroundColor": "#2B6A4D",
                "type": "day",
            },
            {
                "label": f"{metric_label} by Time of Day",
                "data": hour_data,
                "labels_hour": hour_labels,
                "backgroundColor": "#1B3A6B",
                "type": "hour",
            },
        ],
        "meta": {
            "n": int(len(df)),
            "metric": metric_col,
            "platform": platform or "all",
            "brand": brand or "all",
            "note": f"{len(df)} posts",
        },
    }
