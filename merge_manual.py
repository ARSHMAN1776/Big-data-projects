#!/usr/bin/env python3
"""Merge hand-collected Instagram and Facebook posts into one clean dataset.

Put every collector's filled-in template (CSV or XLSX) into data/manual/, then run:

  python merge_manual.py

Good rows go to data/raw/posts_manual_<date>.csv in the same columns as the API collector.
Bad rows are never fixed silently: they go to rejects_manual_<date>.csv with the reason, so
the collector can correct them and you can run the merge again.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

import pandas as pd

from common import MEDIA_TYPES, POST_COLUMNS, PKT, build_post, log, time_fields

ROOT = Path(__file__).resolve().parent

TEMPLATE_COLUMNS = [
    "collector", "platform", "brand", "post_url", "post_date", "post_time_pkt",
    "media_type", "caption", "likes", "comments", "shares", "views",
    "followers_on_page", "collected_on", "notes",
]
PLATFORMS = {"instagram": "instagram", "ig": "instagram", "insta": "instagram",
             "facebook": "facebook", "fb": "facebook",
             "tiktok": "tiktok", "tik tok": "tiktok", "tt": "tiktok"}
MEDIA_SYNONYMS = {"photo": "image", "picture": "image", "pic": "image", "album": "carousel",
                  "reels": "reel", "status": "text"}
HOSTS = {"instagram": ("instagram.com",), "facebook": ("facebook.com", "fb.com", "fb.watch"), "tiktok": ("tiktok.com",)}
FB_KEEP_QUERY = {"fbid", "story_fbid", "id", "v"}
_COUNT = re.compile(r"^([\d,]*\.?\d+)([kKmM]?)$")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T]00:00(?::00)?)?$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")
_IG_CODE = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
_TT_VIDEO = re.compile(r"^/@([A-Za-z0-9_.]+)/video/(\d+)")


def parse_count(value) -> int | None:
    """'1,234' -> 1234, '1.2K' -> 1200, '3M' -> 3000000, blank -> None. Garbage raises ValueError."""
    s = "" if value is None else str(value).strip().replace(" ", "")
    if s == "" or s.lower() in {"nan", "none"}:
        return None
    if s.startswith("-"):
        raise ValueError(f"'{s}' is negative")
    m = _COUNT.match(s)
    if not m:
        raise ValueError(f"'{s}' is not a number (use digits, or 1.2K / 3M)")
    mult = {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()]
    return round(float(m.group(1).replace(",", "")) * mult)


def normalize_url(url: str, platform: str) -> tuple[str, str]:
    """Returns (clean_url, post_id). Raises ValueError for a URL that is not on the right site."""
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not any(host == h or host.endswith("." + h) for h in HOSTS[platform]):
        raise ValueError(f"post_url must be a {platform} link ({', '.join(HOSTS[platform])})")
    path = u.path.rstrip("/")
    if platform == "instagram":
        m = _IG_CODE.search(path)
        if not m:
            raise ValueError("Instagram post_url must look like instagram.com/p/CODE/ or /reel/CODE/")
        return f"https://www.instagram.com/p/{m.group(1)}/", m.group(1)
    if platform == "tiktok":
        m = _TT_VIDEO.match(path)
        if m:
            return f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}", m.group(2)
        if host in ("vm.tiktok.com", "vt.tiktok.com") or path.startswith("/t/"):
            clean = f"https://{host}{path}"
            return clean, clean
        raise ValueError("TikTok post_url must look like tiktok.com/@brand/video/1234567890 (or a vm.tiktok.com short link)")
    kept = sorted((k, v) for k, v in parse_qsl(u.query) if k in FB_KEEP_QUERY)
    clean = f"https://www.facebook.com{path}" if host != "fb.watch" else f"https://fb.watch{path}"
    if kept:
        clean += "?" + urlencode(kept)
    if not path and not kept:
        raise ValueError("Facebook post_url points to a page, not a single post")
    return clean, clean


class RowError(ValueError):
    """A row failed validation. .problems lists every reason, in plain words."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def clean_row(raw: dict, brands: dict[str, str], today: date) -> dict:
    """Validate one row. Returns the unified post row, or raises ValueError listing every problem."""
    problems: list[str] = []
    g = lambda k: str(raw.get(k, "") or "").strip()  # noqa: E731

    collector = g("collector")
    if not collector:
        problems.append("collector name is empty")

    platform = PLATFORMS.get(g("platform").lower())
    if not platform:
        problems.append(f"platform '{g('platform')}' must be instagram, facebook or tiktok")

    brand_in = g("brand")
    brand = brands.get(brand_in.lower())
    if not brand:
        hint = difflib.get_close_matches(brand_in, list(brands.values()), n=1, cutoff=0.5)
        problems.append(f"brand '{brand_in}' is not in config/social_brands.json" + (f" (did you mean '{hint[0]}'?)" if hint else ""))

    url = post_id = ""
    if platform:
        try:
            url, post_id = normalize_url(g("post_url"), platform)
        except ValueError as exc:
            problems.append(str(exc))

    post_date = None
    m = _DATE.match(g("post_date"))
    if m:
        try:
            post_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    if post_date is None:
        problems.append(f"post_date '{g('post_date')}' must be written as YYYY-MM-DD, for example 2026-10-19")

    collected_on = None
    m = _DATE.match(g("collected_on"))
    if m:
        try:
            collected_on = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    if collected_on is None:
        problems.append(f"collected_on '{g('collected_on')}' must be written as YYYY-MM-DD (the day you read the numbers)")
    elif collected_on > today:
        problems.append("collected_on is in the future")
    if post_date and collected_on and post_date > collected_on:
        problems.append("post_date is after collected_on")

    hh = mm = None
    t = g("post_time_pkt")
    if t:
        tm = _TIME.match(t)
        if tm and int(tm.group(1)) < 24 and int(tm.group(2)) < 60:
            hh, mm = int(tm.group(1)), int(tm.group(2))
        else:
            problems.append(f"post_time_pkt '{t}' must be 24-hour HH:MM in Pakistan time, or left blank")

    mt = g("media_type").lower()
    mt = MEDIA_SYNONYMS.get(mt, mt)
    if mt not in MEDIA_TYPES:
        problems.append(f"media_type '{g('media_type')}' must be one of: {', '.join(MEDIA_TYPES)}")

    counts = {}
    for col in ("likes", "comments", "shares", "views", "followers_on_page"):
        try:
            counts[col] = parse_count(raw.get(col))
        except ValueError as exc:
            problems.append(f"{col}: {exc}")
            counts[col] = None
    if all(counts[c] is None for c in ("likes", "comments", "shares", "views")):
        problems.append("no metrics: fill at least one of likes, comments, shares, views (leave a number blank only if the page hides it)")

    if problems:
        raise RowError(problems)

    if hh is not None:
        when = time_fields(f"{post_date.isoformat()}T{hh:02d}:{mm:02d}:00+05:00")
    else:
        when = {"published_at_utc": "", "date_pkt": post_date.isoformat(),
                "weekday_pkt": post_date.strftime("%A"), "hour_pkt": None}
    return build_post(
        platform=platform, brand=brand, post_id=post_id, url=url, caption=g("caption"),
        media_type=mt, likes=counts["likes"], comments=counts["comments"], shares=counts["shares"],
        views=counts["views"], followers=counts["followers_on_page"], source="manual",
        collector=collector, collected_at=collected_on.isoformat(), when=when,
    )


def validate_rows(df: pd.DataFrame, brands: dict[str, str], today: date, source_name: str = ""):
    """Returns (good_rows, rejects). Duplicate posts keep the first row seen."""
    good, rejects, _ = validate_rows_ex(df, brands, today, source_name)
    return good, rejects


def validate_rows_ex(df: pd.DataFrame, brands: dict[str, str], today: date, source_name: str = ""):
    """Like validate_rows, plus the original rows that passed (for saving them back unchanged)."""
    good: list[dict] = []
    kept: list[dict] = []
    rejects: list[dict] = []
    seen: dict[tuple, int] = {}
    for i, raw in enumerate(df.to_dict("records")):
        line = i + 2  # row number as seen in the spreadsheet (row 1 is the header)
        if not any(str(v).strip() for v in raw.values()):
            continue  # fully blank row
        try:
            row = clean_row(raw, brands, today)
        except ValueError as exc:
            rejects.append({"file": source_name, "row": line, "collector": str(raw.get("collector", "")).strip(),
                            "brand": str(raw.get("brand", "")).strip(), "post_url": str(raw.get("post_url", "")).strip(),
                            "problems": str(exc)})
            continue
        key = (row["platform"], row["post_id"])
        if key in seen:
            rejects.append({"file": source_name, "row": line, "collector": row["collector"], "brand": row["brand"],
                            "post_url": row["url"], "problems": f"duplicate of row {seen[key]}: the same post was entered twice"})
            continue
        seen[key] = line
        good.append(row)
        kept.append({c: str(raw.get(c, "") or "") for c in TEMPLATE_COLUMNS})
    return good, rejects, kept


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".xlsx":
        df = pd.read_excel(path, dtype=str).fillna("")
    else:
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        except UnicodeDecodeError:
            log(f"  ! {path.name} is not UTF-8 (Excel's plain CSV). Urdu text may be damaged. Save as 'CSV UTF-8'.")
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="cp1252")
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--in-dir", type=Path, default=ROOT / "data" / "manual")
    p.add_argument("--config", type=Path, default=ROOT / "config" / "social_brands.json")
    p.add_argument("--out-dir", type=Path, default=ROOT / "data" / "raw")
    return p.parse_args(argv)


def load_brands(config: Path) -> dict[str, str]:
    return {b["brand"].lower(): b["brand"] for b in json.loads(config.read_text(encoding="utf-8"))["brands"]}


def load_manual(in_dir: Path, brands: dict[str, str], today: date, report=None):
    """Read every CSV/XLSX in in_dir. Returns (unique_rows, rejects). Nothing is written."""
    files = sorted(p for p in in_dir.glob("*") if p.suffix.lower() in (".csv", ".xlsx") and not p.name.startswith(("_", "~")))
    good: list[dict] = []
    rejects: list[dict] = []
    for path in files:
        df = read_table(path)
        missing = [c for c in TEMPLATE_COLUMNS if c not in df.columns]
        if missing:
            if report:
                report(f"  ! {path.name}: missing columns {missing}. Start again from templates/post_collection_template.csv.")
            continue
        g, r = validate_rows(df, brands, today, path.name)
        if report:
            report(f"  {path.name:<40} {len(g):>4} good, {len(r):>3} rejected")
        good += g
        rejects += r
    # a post entered by two different files is also a duplicate
    seen: set[tuple] = set()
    unique = []
    for row in good:
        key = (row["platform"], row["post_id"])
        if key in seen:
            rejects.append({"file": "(across files)", "row": "", "collector": row["collector"], "brand": row["brand"],
                            "post_url": row["url"], "problems": "duplicate: this post is also in an earlier file"})
        else:
            seen.add(key)
            unique.append(row)
    return unique, rejects


def write_outputs(unique: list[dict], rejects: list[dict], out_dir: Path, now: datetime) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d")
    files = {}
    if unique:
        files["posts"] = f"posts_manual_{stamp}.csv"
        pd.DataFrame(unique, columns=POST_COLUMNS).to_csv(out_dir / files["posts"], index=False, encoding="utf-8-sig")
    if rejects:
        files["rejects"] = f"rejects_manual_{stamp}.csv"
        pd.DataFrame(rejects).to_csv(out_dir / files["rejects"], index=False, encoding="utf-8-sig")
    return files


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    brands = load_brands(args.config)
    if not any(p.suffix.lower() in (".csv", ".xlsx") for p in args.in_dir.glob("*")):
        log(f"No CSV or XLSX files in {args.in_dir}. Put the filled-in templates there.")
        return 2
    now = datetime.now(timezone.utc)
    unique, rejects = load_manual(args.in_dir, brands, now.astimezone(PKT).date(), log)
    files = write_outputs(unique, rejects, args.out_dir, now)
    if unique:
        summary = pd.DataFrame(unique).groupby(["platform", "brand"]).size().unstack(0, fill_value=0)
        log("\nGood posts per brand:\n" + summary.to_string())
    log(f"\n{len(unique)} posts kept, {len(rejects)} rejected." + (f" Fix the rejected rows listed in {files['rejects']} and run again." if rejects else ""))
    return 0 if unique else 1


if __name__ == "__main__":
    raise SystemExit(main())
