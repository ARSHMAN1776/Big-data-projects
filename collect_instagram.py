#!/usr/bin/env python3
"""Collect public Instagram posts of Pakistani brands through Instagram's official API.

It uses the "Business Discovery" feature: your own Instagram Business or Creator account
asks Meta for the public posts and counts of another Business/Creator account. This is
Meta's sanctioned way to read other brands' public data. It cannot read personal accounts,
and it only returns what Instagram shows publicly (a hidden like count stays blank).

Setup (about 30 minutes, once): see README, "Instagram API setup". You end up with
IG_USER_ID and IG_ACCESS_TOKEN in .env.

Examples
  python collect_instagram.py --check              # verify usernames, show post totals
  python collect_instagram.py                      # 250 newest posts per brand
  python collect_instagram.py --max-posts 0        # every post the account has
  python collect_instagram.py --brand Khaadi
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from common import POST_COLUMNS, build_post, load_env, log, time_fields, to_int

ROOT = Path(__file__).resolve().parent
GRAPH = "https://graph.facebook.com"
DEFAULT_VERSION = "v23.0"
PAGE_SIZE = 50
# Fields are tried in order. Meta rejects a request that names a field it does not offer for
# other accounts' media, so we fall back to the shorter list instead of failing the run.
FIELD_SETS = [
    "caption,media_type,media_product_type,like_count,comments_count,timestamp,permalink,view_count",
    "caption,media_type,like_count,comments_count,timestamp,permalink",
]
FATAL_CODES = {10, 102, 190, 200}      # bad/expired token, missing permission
RATE_CODES = {4, 17, 32, 613, 80002, 80004, 80006}
USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
MEDIA_MAP = {"IMAGE": "image", "VIDEO": "video", "CAROUSEL_ALBUM": "carousel"}


class GraphError(RuntimeError):
    def __init__(self, status: int, code, message: str):
        super().__init__(f"HTTP {status}, Meta error {code}: {message}")
        self.status, self.code = status, code


class TokenError(GraphError):
    """Token expired/invalid or permission missing: the whole run must stop."""


class RateLimited(GraphError):
    """Meta's hourly limit: stop and keep what we have, then run again later."""


class FieldError(GraphError):
    """Meta rejected a field name or the request shape (error 100)."""


class InstagramAPI:
    def __init__(self, ig_user_id: str, token: str, version: str = DEFAULT_VERSION,
                 session=None, sleep=time.sleep):
        self.ig_user_id, self.token, self.version = ig_user_id, token, version
        self.session = session or requests.Session()
        self.sleep = sleep
        self.calls = 0
        self.field_idx = 0

    def _get(self, path: str, **params) -> dict:
        params["access_token"] = self.token
        url = f"{GRAPH}/{self.version}/{path}"
        status, code, msg = 0, None, "unknown"
        for attempt in range(4):
            self.calls += 1
            try:
                r = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as exc:
                # request errors embed the URL, which contains the token: redact it
                status, code, msg = 0, None, str(exc).replace(self.token, "***")
                self.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json()
            status, code, msg = r.status_code, None, "unknown"
            try:
                err = r.json()["error"]
                code, msg = err.get("code"), err.get("message", "")
            except (ValueError, KeyError, TypeError):
                pass
            if code in FATAL_CODES:
                raise TokenError(status, code, msg)
            if code in RATE_CODES:
                raise RateLimited(status, code, msg)
            if code == 100:
                raise FieldError(status, code, msg)
            if status in (500, 502, 503, 504) or code in (1, 2):
                self.sleep(2 ** attempt)
                continue
            raise GraphError(status, code, msg)
        raise GraphError(status, code, f"{msg} (gave up after retries)")

    def _discovery(self, username: str, inner: str) -> dict:
        if not USERNAME.match(username):
            raise GraphError(0, None, f"'{username}' is not a valid Instagram username")
        query = "business_discovery.username(" + username + "){" + inner + "}"
        return self._get(self.ig_user_id, fields=query)["business_discovery"]

    def account_info(self, username: str) -> dict:
        bd = self._discovery(username, "followers_count,media_count")
        return {"followers": to_int(bd.get("followers_count")), "media_count": to_int(bd.get("media_count"))}

    def fetch_account(self, username: str, max_posts: int) -> tuple[dict, list[dict]]:
        posts: list[dict] = []
        after = None
        info: dict = {}
        while True:
            limit = min(PAGE_SIZE, max_posts - len(posts)) if max_posts else PAGE_SIZE
            media = f"media.limit({limit})" + (f".after({after})" if after else "")
            while True:
                inner = "followers_count,media_count," + media + "{" + FIELD_SETS[self.field_idx] + "}"
                try:
                    bd = self._discovery(username, inner)
                    break
                except FieldError:
                    if self.field_idx + 1 >= len(FIELD_SETS):
                        raise
                    self.field_idx += 1
                    log(f"  note: Meta rejected a field, retrying with: {FIELD_SETS[self.field_idx]}")
            info = {"followers": to_int(bd.get("followers_count")), "media_count": to_int(bd.get("media_count"))}
            media_obj = bd.get("media") or {}
            posts += media_obj.get("data", [])
            paging = media_obj.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            if not after or "next" not in paging or (max_posts and len(posts) >= max_posts):
                break
            self.sleep(0.3)
        return info, (posts[:max_posts] if max_posts else posts)


def map_media_type(item: dict) -> str:
    if (item.get("media_product_type") or "").upper() == "REELS":
        return "reel"
    mt = (item.get("media_type") or "").upper()
    return MEDIA_MAP.get(mt, mt.lower() or "other")


def ig_post_row(item: dict, brand: str, followers, collected_at: str) -> dict:
    ts = item.get("timestamp")
    return build_post(
        platform="instagram", brand=brand, post_id=item["id"], url=item.get("permalink", ""),
        caption=item.get("caption") or "", media_type=map_media_type(item),
        likes=to_int(item.get("like_count")),          # absent when the owner hides likes
        comments=to_int(item.get("comments_count")),
        shares=None,                                    # Instagram does not publish shares
        views=to_int(item.get("view_count")),           # video/reel only
        followers=followers, source="api", collector="", collected_at=collected_at,
        when=time_fields(ts) if ts else {},
    )


def collect(api: InstagramAPI, brands: list[dict], max_posts: int, collected_at: str):
    """Returns (posts, accounts, stop_reason). stop_reason is None when every brand finished."""
    posts: list[dict] = []
    accounts: list[dict] = []
    try:
        for b in brands:
            try:
                info, items = api.fetch_account(b["instagram"], max_posts)
            except (TokenError, RateLimited):
                raise
            except GraphError as exc:
                log(f"  ! {b['brand']} (@{b['instagram']}) skipped: {exc}")
                log("    Check the username in config/social_brands.json. It must be a Business or Creator account.")
                continue
            rows = [ig_post_row(it, b["brand"], info["followers"], collected_at) for it in items]
            posts += rows
            accounts.append({"brand": b["brand"], "instagram": b["instagram"], **info,
                             "posts_collected": len(rows), "collected_at_utc": collected_at})
            log(f"  {b['brand']:<15} {len(rows):>4} posts   (account reports {info['media_count']} in total)")
    except TokenError as exc:
        return posts, accounts, f"token or permission problem ({exc}). Make a fresh token, see README."
    except RateLimited as exc:
        return posts, accounts, f"Meta rate limit reached ({exc}). Wait an hour and run again."
    return posts, accounts, None


def check(api: InstagramAPI, brands: list[dict]) -> int:
    log(f"{'brand':<16}{'username':<22}{'followers':>10}{'posts':>8}")
    ok = total = 0
    try:
        for b in brands:
            try:
                info = api.account_info(b["instagram"])
            except (TokenError, RateLimited):
                raise
            except GraphError as exc:
                log(f"{b['brand']:<16}{'@' + b['instagram']:<22}  NOT FOUND: {exc}")
                continue
            ok += 1
            total += info["media_count"] or 0
            log(f"{b['brand']:<16}{'@' + b['instagram']:<22}{info['followers'] or 0:>10,}{info['media_count'] or 0:>8,}")
    except (TokenError, RateLimited) as exc:
        log(f"\nStopped: {exc}")
        return 1
    log(f"\n{ok} of {len(brands)} usernames work. Together they hold about {total:,} posts.")
    return 0 if ok else 1


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="only verify usernames and show post totals")
    p.add_argument("--max-posts", type=int, default=250, help="newest posts per brand, 0 = all (default 250)")
    p.add_argument("--brand", action="append", help="limit to a brand name (repeatable)")
    p.add_argument("--config", type=Path, default=ROOT / "config" / "social_brands.json")
    p.add_argument("--out-dir", type=Path, default=ROOT / "data" / "raw")
    return p.parse_args(argv)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    load_env(ROOT / ".env")
    brands = json.loads(args.config.read_text(encoding="utf-8"))["brands"]
    if args.brand:
        wanted = {n.lower() for n in args.brand}
        brands = [b for b in brands if b["brand"].lower() in wanted]
        if not brands:
            log(f"No brand matched {args.brand}.")
            return 2

    user_id = os.environ.get("IG_USER_ID", "").strip()
    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    if not user_id or not token:
        log("Missing IG_USER_ID or IG_ACCESS_TOKEN in .env. See README, section 'Instagram API setup'.")
        return 2
    api = InstagramAPI(user_id, token, os.environ.get("GRAPH_API_VERSION", DEFAULT_VERSION))

    if args.check:
        return check(api, brands)

    now = datetime.now(timezone.utc)
    collected_at, stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%d")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    log(f"Instagram official API | brands: {len(brands)} | {collected_at}")
    posts, accounts, stop = collect(api, brands, args.max_posts, collected_at)
    if stop:
        log(f"\n  ! Stopped early: {stop}\n    Saving what was collected so far.")
    if not posts:
        log("No posts collected.")
        return 1

    seen: set[str] = set()
    posts = [p for p in posts if not (p["post_id"] in seen or seen.add(p["post_id"]))]
    files = {"posts": f"posts_instagram_api_{stamp}.csv", "accounts": f"accounts_instagram_api_{stamp}.csv"}
    pd.DataFrame(posts, columns=POST_COLUMNS).to_csv(args.out_dir / files["posts"], index=False, encoding="utf-8-sig")
    pd.DataFrame(accounts).to_csv(args.out_dir / files["accounts"], index=False, encoding="utf-8-sig")
    manifest = {
        "collected_at_utc": collected_at, "source": "Instagram API (Business Discovery), Meta",
        "graph_version": api.version, "complete": stop is None, "stop_reason": stop,
        "fields_used": FIELD_SETS[api.field_idx], "args": {"max_posts": args.max_posts, "brands": [b["brand"] for b in brands]},
        "rows": {"posts": len(posts), "accounts": len(accounts)}, "api_calls": api.calls, "files": files,
        "notes": "Blank likes means the owner hides likes. Blank views means not a video/reel or the field was not offered. "
                 "Shares are not published by Instagram. Without media_product_type, reels appear as 'video'.",
    }
    (args.out_dir / f"manifest_instagram_api_{stamp}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log(f"\nSaved {len(posts)} posts from {len(accounts)} accounts to {args.out_dir} ({api.calls} API calls).")
    return 0 if stop is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
