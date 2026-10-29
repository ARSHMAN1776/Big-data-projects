#!/usr/bin/env python3
"""Collect public YouTube data for Pakistani fashion brands into CSV files.

Modes
  api   Official YouTube Data API v3. Needs a free key in .env (YOUTUBE_API_KEY).
        Full upload history, likes, comment counts, duration, tags, top comments.
  rss   Public channel feeds. No key. Only the ~15 newest videos per channel,
        views only. Good for a first look and as a fallback.
  auto  api if a key is present, otherwise rss (default).

Examples
  python collect.py                          # auto mode, 300 newest videos per brand
  python collect.py --mode rss               # no key needed
  python collect.py --max-videos 0           # api: every upload
  python collect.py --brand Khaadi --brand "Maria B" --comments-per-video 0
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from common import extract_hashtags, load_env, log, time_fields, to_int

ROOT = Path(__file__).resolve().parent
API = "https://www.googleapis.com/youtube/v3"
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
USER_AGENT = "PakistaniContentDetective/1.0 (university data-analytics project)"
SHORT_MAX_SEC = 60  # api mode only; rss mode reads the real Shorts flag from the feed link

VIDEO_COLUMNS = [
    "brand", "channel_id", "video_id", "url", "title", "description",
    "published_at_utc", "date_pkt", "weekday_pkt", "hour_pkt",
    "format", "duration_sec", "views", "likes", "comment_count", "rss_rating_count",
    "hashtags", "tags", "default_language", "source", "collected_at_utc",
]
CHANNEL_COLUMNS = [
    "brand", "channel_id", "channel_title", "subscribers", "total_views",
    "video_count", "channel_created_utc", "country", "collected_at_utc",
]
COMMENT_COLUMNS = [
    "brand", "video_id", "comment_id", "comment_text", "like_count",
    "reply_count", "published_at_utc",
]

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}
FATAL_QUOTA = {"quotaExceeded", "dailyLimitExceeded"}
RETRY_STATUS = {500, 502, 503, 504}
RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}


# ---------------------------------------------------------------- helpers

_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration(iso: str | None) -> int | None:
    m = _DURATION.match(iso or "")
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def build_row(*, brand, channel_id, video_id, title, description, published, fmt,
              duration_sec, views, likes, comment_count, rss_rating_count, tags,
              default_language, source, collected_at) -> dict:
    row = {
        "brand": brand,
        "channel_id": channel_id,
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "title": title,
        "description": description,
        "format": fmt,
        "duration_sec": duration_sec,
        "views": views,
        "likes": likes,
        "comment_count": comment_count,
        "rss_rating_count": rss_rating_count,
        "hashtags": extract_hashtags(f"{title}\n{description}"),
        "tags": tags,
        "default_language": default_language,
        "source": source,
        "collected_at_utc": collected_at,
    }
    row.update(time_fields(published))
    return row


# ---------------------------------------------------------------- rss mode

def parse_rss(xml_text: str, brand: str, channel_id: str, collected_at: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    rows = []
    for e in root.findall("a:entry", NS):
        video_id = e.findtext("yt:videoId", namespaces=NS)
        link = e.find("a:link", NS)
        href = link.get("href", "") if link is not None else ""
        group = e.find("media:group", NS)
        star = group.find("media:community/media:starRating", NS)
        stats = group.find("media:community/media:statistics", NS)
        rows.append(build_row(
            brand=brand,
            channel_id=channel_id,
            video_id=video_id,
            title=e.findtext("a:title", default="", namespaces=NS),
            description=group.findtext("media:description", default="", namespaces=NS),
            published=e.findtext("a:published", namespaces=NS),
            fmt="short" if "/shorts/" in href else "video",
            duration_sec=None,
            views=to_int(stats.get("views")) if stats is not None else None,
            likes=None,
            comment_count=None,
            rss_rating_count=to_int(star.get("count")) if star is not None else None,
            tags="",
            default_language="",
            source="rss",
            collected_at=collected_at,
        ))
    return rows


def collect_rss(session, brands: list[dict], collected_at: str) -> list[dict]:
    rows: list[dict] = []
    for b in brands:
        try:
            r = session.get(FEED.format(b["channel_id"]), timeout=30, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            got = parse_rss(r.text, b["brand"], b["channel_id"], collected_at)
        except (requests.RequestException, ET.ParseError) as exc:
            log(f"  ! {b['brand']}: feed failed ({type(exc).__name__}), skipped")
            continue
        log(f"  {b['brand']:<15} {len(got)} videos")
        rows += got
        time.sleep(0.5)
    return rows


# ---------------------------------------------------------------- api mode

class ApiError(RuntimeError):
    def __init__(self, status: int, reason: str, message: str = ""):
        super().__init__(f"HTTP {status} {reason} {message}".strip())
        self.status, self.reason = status, reason


class QuotaExceeded(ApiError):
    pass


class YouTubeAPI:
    def __init__(self, key: str, session=None, sleep=time.sleep):
        self.key = key
        self.session = session or requests.Session()
        self.sleep = sleep
        self.calls = 0  # every endpoint used here costs 1 quota unit

    def _get(self, endpoint: str, **params) -> dict:
        params["key"] = self.key
        status, reason, msg = 0, "unknown", ""
        for attempt in range(5):
            self.calls += 1
            try:
                r = self.session.get(f"{API}/{endpoint}", params=params, timeout=30)
            except requests.RequestException as exc:
                # request errors embed the URL, which contains the key: redact it
                status, reason, msg = 0, "network", str(exc).replace(self.key, "***")
                self.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json()
            status, reason, msg = r.status_code, "unknown", ""
            try:
                err = r.json()["error"]
                reason = err["errors"][0]["reason"]
                msg = err.get("message", "")
            except (ValueError, KeyError, IndexError, TypeError):
                pass
            if reason in FATAL_QUOTA:
                raise QuotaExceeded(status, reason, msg)
            if status in RETRY_STATUS or reason in RETRY_REASONS:
                self.sleep(2 ** attempt)
                continue
            raise ApiError(status, reason, msg)
        raise ApiError(status, reason, f"{msg} (gave up after retries)")

    def channels(self, ids: list[str]) -> list[dict]:
        return self._get("channels", part="snippet,statistics,contentDetails", id=",".join(ids)).get("items", [])

    def upload_video_ids(self, playlist_id: str, limit: int) -> list[str]:
        ids: list[str] = []
        token = None
        while True:
            params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50}
            if token:
                params["pageToken"] = token
            data = self._get("playlistItems", **params)
            ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])]
            token = data.get("nextPageToken")
            if not token or (limit and len(ids) >= limit):
                break
        return ids[:limit] if limit else ids

    def videos(self, ids: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(ids), 50):
            out += self._get("videos", part="snippet,contentDetails,statistics", id=",".join(ids[i:i + 50])).get("items", [])
        return out

    def top_comments(self, video_id: str, n: int) -> list[dict]:
        items: list[dict] = []
        token = None
        while len(items) < n:
            params = {"part": "snippet", "videoId": video_id, "maxResults": min(100, n - len(items)),
                      "order": "relevance", "textFormat": "plainText"}
            if token:
                params["pageToken"] = token
            try:
                data = self._get("commentThreads", **params)
            except QuotaExceeded:
                raise
            except ApiError as exc:
                if exc.status in (403, 404):  # comments disabled, or video removed
                    return items
                raise
            items += data.get("items", [])
            token = data.get("nextPageToken")
            if not token:
                break
        return items


def api_video_row(item: dict, brand: str, collected_at: str) -> dict:
    sn, st, cd = item["snippet"], item.get("statistics", {}), item.get("contentDetails", {})
    duration = parse_duration(cd.get("duration"))
    return build_row(
        brand=brand,
        channel_id=sn.get("channelId", ""),
        video_id=item["id"],
        title=sn.get("title", ""),
        description=sn.get("description", ""),
        published=sn["publishedAt"],
        fmt="short" if duration is not None and duration <= SHORT_MAX_SEC else "video",
        duration_sec=duration,
        views=to_int(st.get("viewCount")),
        likes=to_int(st.get("likeCount")),        # absent when the owner hides likes
        comment_count=to_int(st.get("commentCount")),  # absent when comments are off
        rss_rating_count=None,
        tags="|".join(sn.get("tags", [])),
        default_language=sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "",
        source="api",
        collected_at=collected_at,
    )


def channel_row(ch: dict, brand: str, collected_at: str) -> dict:
    st, sn = ch.get("statistics", {}), ch.get("snippet", {})
    return {
        "brand": brand,
        "channel_id": ch["id"],
        "channel_title": sn.get("title", ""),
        "subscribers": None if st.get("hiddenSubscriberCount") else to_int(st.get("subscriberCount")),
        "total_views": to_int(st.get("viewCount")),
        "video_count": to_int(st.get("videoCount")),
        "channel_created_utc": sn.get("publishedAt", ""),
        "country": sn.get("country", ""),
        "collected_at_utc": collected_at,
    }


def comment_row(thread: dict, brand: str, video_id: str) -> dict:
    top = thread["snippet"]["topLevelComment"]
    sn = top["snippet"]
    return {
        "brand": brand,
        "video_id": video_id,
        "comment_id": top["id"],
        # author names/ids are deliberately not collected
        "comment_text": sn.get("textOriginal") or sn.get("textDisplay", ""),
        "like_count": to_int(sn.get("likeCount")),
        "reply_count": to_int(thread["snippet"].get("totalReplyCount")),
        "published_at_utc": time_fields(sn["publishedAt"])["published_at_utc"],
    }


def collect_api(api: YouTubeAPI, brands: list[dict], max_videos: int,
                comments_per_video: int, collected_at: str):
    """Returns (videos, channels, comments, complete). Partial results survive a quota stop."""
    videos: list[dict] = []
    channels: list[dict] = []
    comments: list[dict] = []
    try:
        found = {c["id"]: c for c in api.channels([b["channel_id"] for b in brands])}
        for b in brands:
            ch = found.get(b["channel_id"])
            if ch is None:
                log(f"  ! {b['brand']}: channel id not found, skipped")
                continue
            channels.append(channel_row(ch, b["brand"], collected_at))
            ids = api.upload_video_ids(ch["contentDetails"]["relatedPlaylists"]["uploads"], max_videos)
            items = [it for it in api.videos(ids) if it["snippet"].get("liveBroadcastContent") != "upcoming"]
            rows = [api_video_row(it, b["brand"], collected_at) for it in items]
            videos += rows
            log(f"  {b['brand']:<15} {len(rows)} videos")
            if comments_per_video:
                for row in rows:
                    if not row["comment_count"]:  # none, or comments switched off
                        continue
                    for thread in api.top_comments(row["video_id"], comments_per_video):
                        comments.append(comment_row(thread, b["brand"], row["video_id"]))
    except QuotaExceeded as exc:
        log(f"\n  ! Daily API quota used up ({exc.reason}). Saving what was collected so far.")
        return videos, channels, comments, False
    return videos, channels, comments, True


# ---------------------------------------------------------------- main

def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["auto", "api", "rss"], default="auto")
    p.add_argument("--max-videos", type=int, default=300, help="api: newest videos per brand, 0 = all (default 300)")
    p.add_argument("--comments-per-video", type=int, default=100, help="api: top comments per video, 0 = skip (default 100)")
    p.add_argument("--brand", action="append", help="limit to a brand name from config/brands.json (repeatable)")
    p.add_argument("--config", type=Path, default=ROOT / "config" / "brands.json")
    p.add_argument("--out-dir", type=Path, default=ROOT / "data" / "raw")
    return p.parse_args(argv)


def write_csv(rows: list[dict], columns: list[str], path: Path) -> None:
    # utf-8-sig so Excel shows Urdu text correctly; pandas reads it transparently
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False, encoding="utf-8-sig")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    load_env(ROOT / ".env")
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    brands = cfg["brands"]
    if args.brand:
        wanted = {n.lower() for n in args.brand}
        brands = [b for b in brands if b["brand"].lower() in wanted]
        if not brands:
            log(f"No brand matched {args.brand}. Available: {[b['brand'] for b in cfg['brands']]}")
            return 2

    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    mode = ("api" if key else "rss") if args.mode == "auto" else args.mode
    if mode == "api" and not key:
        log("api mode needs YOUTUBE_API_KEY. Copy .env.example to .env and paste your key (see README).")
        return 2

    now = datetime.now(timezone.utc)
    collected_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = now.strftime("%Y-%m-%d")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Mode: {mode} | brands: {len(brands)} | {collected_at}")
    if args.mode == "auto" and mode == "rss":
        log("No YOUTUBE_API_KEY found, using public feeds (latest ~15 videos per brand).")

    channels: list[dict] = []
    comments: list[dict] = []
    complete = True
    units = None
    if mode == "rss":
        with requests.Session() as s:
            videos = collect_rss(s, brands, collected_at)
    else:
        api = YouTubeAPI(key)
        videos, channels, comments, complete = collect_api(api, brands, args.max_videos, args.comments_per_video, collected_at)
        units = api.calls

    if not videos:
        log("No videos collected.")
        return 1

    seen: set[str] = set()
    videos = [v for v in videos if not (v["video_id"] in seen or seen.add(v["video_id"]))]

    files = {"videos": f"videos_{mode}_{stamp}.csv"}
    write_csv(videos, VIDEO_COLUMNS, args.out_dir / files["videos"])
    if channels:
        files["channels"] = f"channels_{mode}_{stamp}.csv"
        write_csv(channels, CHANNEL_COLUMNS, args.out_dir / files["channels"])
    if comments:
        files["comments"] = f"comments_{mode}_{stamp}.csv"
        write_csv(comments, COMMENT_COLUMNS, args.out_dir / files["comments"])

    df = pd.DataFrame(videos)
    per_brand = df.groupby("brand").agg(videos=("video_id", "count"), first=("date_pkt", "min"),
                                        last=("date_pkt", "max"), median_views=("views", "median"))
    manifest = {
        "collected_at_utc": collected_at,
        "mode": mode,
        "complete": complete,
        "source": "YouTube Data API v3" if mode == "api" else "YouTube public channel RSS feeds",
        "args": {"max_videos": args.max_videos, "comments_per_video": args.comments_per_video, "brands": [b["brand"] for b in brands]},
        "rows": {"videos": len(videos), "channels": len(channels), "comments": len(comments)},
        "api_quota_units_used": units,
        "files": files,
        "per_brand": json.loads(per_brand.reset_index().to_json(orient="records")),
        "notes": ("RSS mode returns only the newest ~15 videos per channel, with views only "
                  "(likes, comment counts, duration and tags are blank). Blank means not collected, not zero."
                  if mode == "rss" else
                  "Blank likes means the owner hides likes; blank comment_count means comments are off. "
                  "format uses duration <= 60s as the Shorts rule, which is approximate."),
    }
    (args.out_dir / f"manifest_{mode}_{stamp}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    log("")
    log(per_brand.to_string())
    log(f"\nSaved {len(videos)} videos, {len(comments)} comments to {args.out_dir}")
    if units is not None:
        log(f"API quota used: about {units} of 10,000 free units per day.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
