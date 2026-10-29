"""Save posts typed into the page (or imported from a CSV) using the same rules as merge_manual.py.

Nothing is fixed silently: a row either passes every check and is saved exactly as entered, or it is
returned with the reasons in plain words. Saved rows go to data/manual, and the combined dataset in
data/raw is rebuilt so the dashboard sees them at once.
"""
from __future__ import annotations

import csv
import io
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import merge_manual as mm  # noqa: E402

MAX_IMPORT_ROWS = 5000
MAX_CAPTION = 2000
MAX_FIELD = 300
WEB_FILE = "web_entries.csv"
TEXT_FIELDS = ("collector", "caption", "notes")


def _safe_cell(value: str) -> str:
    """Stop a text cell from being run as a formula when the CSV is opened in Excel."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _append_csv(path: Path, rows: list[dict]) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=mm.TEMPLATE_COLUMNS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({c: (_safe_cell(r.get(c, "")) if c in TEXT_FIELDS else r.get(c, "")) for c in mm.TEMPLATE_COLUMNS})


def _existing_keys(manual_dir: Path, brands: dict, today) -> set:
    unique, _ = mm.load_manual(manual_dir, brands, today)
    return {(r["platform"], r["post_id"]) for r in unique}


def _refresh(manual_dir: Path, raw_dir: Path, brands: dict, now: datetime) -> int:
    unique, rejects = mm.load_manual(manual_dir, brands, now.astimezone(mm.PKT).date())
    mm.write_outputs(unique, rejects, raw_dir, now)
    return len(unique)


def add_post(fields: dict, brands: dict, manual_dir: Path, raw_dir: Path, now: datetime) -> dict:
    today = now.astimezone(mm.PKT).date()
    raw = {c: str(fields.get(c, "") or "").strip() for c in mm.TEMPLATE_COLUMNS}
    if not raw["collected_on"]:
        raw["collected_on"] = today.isoformat()
    too_long = [c for c, v in raw.items() if len(v) > (MAX_CAPTION if c == "caption" else MAX_FIELD)]
    if too_long:
        return {"ok": False, "problems": [f"{c} is too long" for c in too_long]}
    try:
        row = mm.clean_row(raw, brands, today)
    except mm.RowError as exc:
        return {"ok": False, "problems": exc.problems}
    manual_dir.mkdir(parents=True, exist_ok=True)
    if (row["platform"], row["post_id"]) in _existing_keys(manual_dir, brands, today):
        return {"ok": False, "problems": ["This post is already in your data."]}
    _append_csv(manual_dir / WEB_FILE, [raw])
    return {"ok": True, "saved": 1, "total_manual": _refresh(manual_dir, raw_dir, brands, now)}


def import_csv(text: str, filename: str, brands: dict, manual_dir: Path, raw_dir: Path, now: datetime) -> dict:
    today = now.astimezone(mm.PKT).date()
    try:
        df = pd.read_csv(io.StringIO(text.lstrip("﻿")), dtype=str, keep_default_na=False)
    except Exception:
        return {"ok": False, "problems": ["That file could not be read as a CSV. Start from the blank template."]}
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in mm.TEMPLATE_COLUMNS if c not in df.columns]
    if missing:
        return {"ok": False, "problems": ["The file is missing these columns: " + ", ".join(missing) + ". Start from the blank template."]}
    if len(df) > MAX_IMPORT_ROWS:
        return {"ok": False, "problems": [f"The file has {len(df):,} rows. The limit is {MAX_IMPORT_ROWS:,}. Split it into smaller files."]}
    manual_dir.mkdir(parents=True, exist_ok=True)
    good, rejects, kept = mm.validate_rows_ex(df, brands, today, filename)
    existing = _existing_keys(manual_dir, brands, today)
    to_save = []
    for row, raw in zip(good, kept):
        if (row["platform"], row["post_id"]) in existing:
            rejects.append({"file": filename, "row": "", "collector": row["collector"], "brand": row["brand"],
                            "post_url": row["url"], "problems": "already in your data"})
        else:
            to_save.append(raw)
    total = None
    if to_save:
        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(filename).stem)[:40] or "file"
        _append_csv(manual_dir / f"import_{now.strftime('%Y%m%d_%H%M%S')}_{safe}.csv", to_save)
        total = _refresh(manual_dir, raw_dir, brands, now)
    warning = "Some characters could not be read. In Excel use Save As, CSV UTF-8, so Urdu text is kept." if "�" in text else None
    return {"ok": True, "saved": len(to_save), "rejected": len(rejects), "total_manual": total, "warning": warning,
            "rejects": [{"row": r.get("row", ""), "post_url": r.get("post_url", ""), "problems": r["problems"]} for r in rejects[:20]]}
