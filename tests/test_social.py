"""Offline tests for the Instagram collector and the manual-entry merger.

The Instagram tests use stubbed replies shaped like Meta's documented Graph API responses, so they
prove our paging, field fallback and error handling, not live behaviour.

Run:  python -m unittest discover -s tests -v
"""
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collect_instagram as ig  # noqa: E402
import common  # noqa: E402
import merge_manual as mm  # noqa: E402

TOKEN = "EAAB-SECRET-TOKEN"
NOW = "2026-10-21T00:00:00Z"


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, handler):
        self.handler, self.requests = handler, []

    def get(self, url, params=None, timeout=None):
        self.requests.append(dict(params or {}))
        status, payload = self.handler(params or {})
        return FakeResponse(status, payload)


def gerr(code, message="boom", status=400):
    return status, {"error": {"message": message, "code": code}}


def post(pid, **extra):
    base = {"id": pid, "caption": f"Post {pid} #Lawn", "media_type": "IMAGE", "like_count": 100,
            "comments_count": 5, "timestamp": "2026-10-19T19:30:00+0000", "permalink": f"https://www.instagram.com/p/{pid}/"}
    base.update(extra)
    return base


def discovery(items, nxt=None, media_count=500, followers=47300):
    media = {"data": items}
    if nxt:
        media["paging"] = {"cursors": {"after": nxt}, "next": "https://graph.facebook.com/..."}
    return 200, {"business_discovery": {"followers_count": followers, "media_count": media_count, "media": media}}


def make_api(handler):
    return ig.InstagramAPI("17841400000000000", TOKEN, session=FakeSession(handler), sleep=lambda s: None)


class CommonTests(unittest.TestCase):
    def test_meta_timestamp_without_colon(self):
        t = common.time_fields("2026-10-19T19:30:00+0000")
        self.assertEqual((t["date_pkt"], t["weekday_pkt"], t["hour_pkt"]), ("2026-10-20", "Tuesday", 0))

    def test_other_timestamp_forms(self):
        self.assertEqual(common.time_fields("2026-10-19T19:30:00Z")["hour_pkt"], 0)
        self.assertEqual(common.time_fields("2026-10-19T19:30:00+00:00")["hour_pkt"], 0)
        with self.assertRaises(ValueError):
            common.time_fields("2026-10-19T19:30:00")  # no timezone: refuse to guess


class InstagramTests(unittest.TestCase):
    def test_paging_follows_cursor(self):
        def handler(p):
            if ".after(C1)" in p["fields"]:
                return discovery([post("c")])
            return discovery([post("a"), post("b")], nxt="C1")
        api = make_api(handler)
        info, items = api.fetch_account("khaadi", 0)
        self.assertEqual([i["id"] for i in items], ["a", "b", "c"])
        self.assertEqual(info, {"followers": 47300, "media_count": 500})
        self.assertEqual(len(api.session.requests), 2)

    def test_max_posts_limits_page_size_and_result(self):
        api = make_api(lambda p: discovery([post("a"), post("b")], nxt="C1"))
        _, items = api.fetch_account("khaadi", 2)
        self.assertEqual(len(items), 2)
        self.assertIn("media.limit(2)", api.session.requests[0]["fields"])
        self.assertEqual(len(api.session.requests), 1)  # enough after one page, no second call

    def test_field_fallback_when_meta_rejects_a_field(self):
        def handler(p):
            if "view_count" in p["fields"]:
                return gerr(100, "(#100) Tried accessing nonexisting field (view_count)")
            return discovery([post("a")])
        api = make_api(handler)
        _, items = api.fetch_account("khaadi", 0)
        self.assertEqual(len(items), 1)
        self.assertEqual(api.field_idx, 1)

    def test_error_100_on_every_field_set_raises(self):
        api = make_api(lambda p: gerr(100, "Invalid user id"))
        with self.assertRaises(ig.FieldError):
            api.fetch_account("nobody", 0)

    def test_row_mapping(self):
        r = ig.ig_post_row(post("a", media_type="VIDEO", media_product_type="REELS", view_count=9000),
                           "Khaadi", 47300, NOW)
        self.assertEqual((r["platform"], r["media_type"], r["views"], r["likes"], r["comments"]), ("instagram", "reel", 9000, 100, 5))
        self.assertEqual((r["date_pkt"], r["hour_pkt"], r["hashtags"], r["source"]), ("2026-10-20", 0, "#lawn", "api"))
        self.assertIsNone(r["shares"])
        self.assertEqual(set(r), set(common.POST_COLUMNS))
        self.assertEqual(ig.map_media_type({"media_type": "CAROUSEL_ALBUM"}), "carousel")
        self.assertEqual(ig.map_media_type({"media_type": "VIDEO"}), "video")

    def test_hidden_likes_stay_blank_not_zero(self):
        item = post("a")
        del item["like_count"]
        r = ig.ig_post_row(item, "Khaadi", 1, NOW)
        self.assertIsNone(r["likes"])

    def test_missing_timestamp_does_not_crash(self):
        item = post("a")
        del item["timestamp"]
        r = ig.ig_post_row(item, "Khaadi", 1, NOW)
        self.assertEqual(r["date_pkt"], "")

    def test_bad_username_never_reaches_the_network(self):
        api = make_api(lambda p: discovery([]))
        with self.assertRaises(ig.GraphError):
            api.account_info("bad){name")
        self.assertEqual(api.session.requests, [])

    def test_wrong_account_is_skipped_and_the_rest_continue(self):
        def handler(p):
            if "personal" in p["fields"]:
                return gerr(110, "Invalid user id")
            return discovery([post("a")])
        api = make_api(handler)
        brands = [{"brand": "Bad", "instagram": "personal"}, {"brand": "Good", "instagram": "khaadi"}]
        posts, accounts, stop = ig.collect(api, brands, 0, NOW)
        self.assertIsNone(stop)
        self.assertEqual([p["brand"] for p in posts], ["Good"])
        self.assertEqual(len(accounts), 1)

    def test_expired_token_stops_run_but_keeps_earlier_posts(self):
        def handler(p):
            if "second" in p["fields"]:
                return gerr(190, "Error validating access token: Session has expired")
            return discovery([post("a")])
        api = make_api(handler)
        brands = [{"brand": "One", "instagram": "first"}, {"brand": "Two", "instagram": "second"}]
        posts, _accounts, stop = ig.collect(api, brands, 0, NOW)
        self.assertEqual(len(posts), 1)
        self.assertIn("token", stop)

    def test_rate_limit_stops_with_partial_results(self):
        def handler(p):
            return gerr(4, "Application request limit reached") if "second" in p["fields"] else discovery([post("a")])
        api = make_api(handler)
        brands = [{"brand": "One", "instagram": "first"}, {"brand": "Two", "instagram": "second"}]
        posts, _accounts, stop = ig.collect(api, brands, 0, NOW)
        self.assertEqual(len(posts), 1)
        self.assertIn("rate limit", stop)

    def test_server_error_is_retried(self):
        calls = {"n": 0}

        def handler(p):
            calls["n"] += 1
            return gerr(2, "Service temporarily unavailable", status=503) if calls["n"] == 1 else discovery([post("a")])
        api = make_api(handler)
        self.assertEqual(len(api.fetch_account("khaadi", 0)[1]), 1)
        self.assertEqual(calls["n"], 2)

    def test_network_error_never_leaks_the_token(self):
        def handler(p):
            raise requests.ConnectionError(f"Max retries exceeded with url: /v23.0/1?access_token={TOKEN}")
        api = make_api(handler)
        with self.assertRaises(ig.GraphError) as ctx:
            api.account_info("khaadi")
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_check_counts_working_usernames(self):
        api = make_api(lambda p: discovery([], media_count=300))
        self.assertEqual(ig.check(api, [{"brand": "A", "instagram": "a"}, {"brand": "B", "instagram": "b"}]), 0)


BRANDS = {"khaadi": "Khaadi", "gul ahmed": "Gul Ahmed", "maria b": "Maria B"}
TODAY = date(2026, 10, 21)


def good_raw(**over):
    raw = {"collector": "Ali", "platform": "instagram", "brand": "Khaadi",
           "post_url": "https://www.instagram.com/p/ABC123/?igsh=xyz", "post_date": "2026-10-19",
           "post_time_pkt": "", "media_type": "image", "caption": "Winter lawn #khaadi", "likes": "1,234",
           "comments": "56", "shares": "", "views": "", "followers_on_page": "1.2M", "collected_on": "2026-10-21", "notes": ""}
    raw.update(over)
    return raw


class ManualTests(unittest.TestCase):
    def test_parse_count(self):
        self.assertEqual(mm.parse_count("1,234"), 1234)
        self.assertEqual(mm.parse_count("1.2K"), 1200)
        self.assertEqual(mm.parse_count("3m"), 3_000_000)
        self.assertEqual(mm.parse_count("1234.0"), 1234)
        self.assertEqual(mm.parse_count("0"), 0)
        self.assertIsNone(mm.parse_count(""))
        self.assertIsNone(mm.parse_count(None))
        for bad in ("-5", "abc", "12 likes", "1.2.3"):
            with self.assertRaises(ValueError):
                mm.parse_count(bad)

    def test_instagram_url_is_canonicalised(self):
        url, pid = mm.normalize_url("https://instagram.com/reel/XyZ_9/?igsh=abc&utm_source=qr", "instagram")
        self.assertEqual((url, pid), ("https://www.instagram.com/p/XyZ_9/", "XyZ_9"))
        with self.assertRaises(ValueError):
            mm.normalize_url("https://www.instagram.com/khaadi/", "instagram")  # a profile, not a post
        with self.assertRaises(ValueError):
            mm.normalize_url("https://www.facebook.com/p/ABC/", "instagram")

    def test_facebook_url_keeps_only_the_id_parameters(self):
        url, _ = mm.normalize_url("https://m.facebook.com/photo.php?fbid=123&set=a.1&__cft__[0]=zzz", "facebook")
        self.assertEqual(url, "https://www.facebook.com/photo.php?fbid=123")
        with self.assertRaises(ValueError):
            mm.normalize_url("https://www.facebook.com/", "facebook")  # a page, not a post
        with self.assertRaises(ValueError):
            mm.normalize_url("https://evilfacebook.com/posts/1", "facebook")

    def test_good_row_without_time(self):
        r = mm.clean_row(good_raw(), BRANDS, TODAY)
        self.assertEqual((r["platform"], r["brand"], r["post_id"], r["likes"], r["comments"], r["followers"]),
                         ("instagram", "Khaadi", "ABC123", 1234, 56, 1_200_000))
        self.assertEqual((r["date_pkt"], r["weekday_pkt"], r["source"], r["collector"]), ("2026-10-19", "Monday", "manual", "Ali"))
        self.assertIsNone(r["hour_pkt"])
        self.assertEqual(r["published_at_utc"], "")
        self.assertIsNone(r["shares"])
        self.assertEqual(r["hashtags"], "#khaadi")
        self.assertEqual(set(r), set(common.POST_COLUMNS))

    def test_time_is_read_as_pakistan_time(self):
        r = mm.clean_row(good_raw(post_time_pkt="01:30"), BRANDS, TODAY)
        self.assertEqual((r["hour_pkt"], r["published_at_utc"]), (1, "2026-10-18T20:30:00Z"))

    def test_facebook_row_with_shares(self):
        r = mm.clean_row(good_raw(platform="FB", post_url="https://www.facebook.com/khaadi/posts/pfbid0abc", shares="12"), BRANDS, TODAY)
        self.assertEqual((r["platform"], r["shares"]), ("facebook", 12))

    def test_tiktok_urls(self):
        url, pid = mm.normalize_url("https://www.tiktok.com/@khaadi/video/7301234567890123456?is_from_webapp=1&sender_device=pc", "tiktok")
        self.assertEqual((url, pid), ("https://www.tiktok.com/@khaadi/video/7301234567890123456", "7301234567890123456"))
        short, sid = mm.normalize_url("https://vm.tiktok.com/ZMabc123/", "tiktok")
        self.assertEqual((short, sid), ("https://vm.tiktok.com/ZMabc123", "https://vm.tiktok.com/ZMabc123"))
        with self.assertRaises(ValueError):
            mm.normalize_url("https://www.tiktok.com/@khaadi", "tiktok")      # a profile, not a video
        with self.assertRaises(ValueError):
            mm.normalize_url("https://www.instagram.com/p/ABC123/", "tiktok")

    def test_tiktok_row(self):
        r = mm.clean_row(good_raw(platform="TikTok", post_url="https://www.tiktok.com/@khaadi/video/730123", media_type="video",
                                  likes="12.4K", comments="310", shares="95", views="1.2M"), BRANDS, TODAY)
        self.assertEqual((r["platform"], r["post_id"], r["likes"], r["shares"], r["views"]), ("tiktok", "730123", 12400, 95, 1_200_000))

    def test_row_error_lists_each_problem(self):
        with self.assertRaises(mm.RowError) as ctx:
            mm.clean_row(good_raw(platform="snapchat", likes="-1"), BRANDS, TODAY)
        self.assertGreaterEqual(len(ctx.exception.problems), 2)
        self.assertIsInstance(ctx.exception, ValueError)

    def test_all_problems_are_reported_together(self):
        with self.assertRaises(ValueError) as ctx:
            mm.clean_row(good_raw(platform="snapchat", brand="Khadi", post_date="19/10/2026", likes="-3", media_type="gif"), BRANDS, TODAY)
        msg = str(ctx.exception)
        for part in ("platform", "did you mean 'Khaadi'", "YYYY-MM-DD", "likes", "media_type"):
            self.assertIn(part, msg)

    def test_row_with_no_metrics_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            mm.clean_row(good_raw(likes="", comments="", shares="", views=""), BRANDS, TODAY)
        self.assertIn("no metrics", str(ctx.exception))

    def test_hidden_likes_with_comments_is_accepted_and_blank(self):
        r = mm.clean_row(good_raw(likes=""), BRANDS, TODAY)
        self.assertIsNone(r["likes"])

    def test_dates_cannot_be_in_the_future_or_after_collection(self):
        with self.assertRaises(ValueError):
            mm.clean_row(good_raw(collected_on="2026-12-01"), BRANDS, TODAY)
        with self.assertRaises(ValueError):
            mm.clean_row(good_raw(post_date="2026-10-22"), BRANDS, TODAY)

    def test_excel_style_datetime_is_accepted(self):
        r = mm.clean_row(good_raw(post_date="2026-10-19 00:00:00"), BRANDS, TODAY)
        self.assertEqual(r["date_pkt"], "2026-10-19")

    def test_synonyms(self):
        self.assertEqual(mm.clean_row(good_raw(media_type="Photo"), BRANDS, TODAY)["media_type"], "image")
        self.assertEqual(mm.clean_row(good_raw(media_type="Reels"), BRANDS, TODAY)["media_type"], "reel")

    def test_validate_rows_numbers_rows_like_a_spreadsheet_and_flags_duplicates(self):
        df = pd.DataFrame([
            good_raw(),
            {k: "" for k in good_raw()},                                   # blank row: ignored
            good_raw(post_url="https://www.instagram.com/p/ABC123/"),     # same post again
            good_raw(brand="Nope"),                                        # bad brand
        ])
        good, rejects = mm.validate_rows(df, BRANDS, TODAY, "ali.csv")
        self.assertEqual(len(good), 1)
        self.assertEqual([(r["row"], "duplicate" in r["problems"]) for r in rejects], [(4, True), (5, False)])
        self.assertEqual(rejects[0]["file"], "ali.csv")


if __name__ == "__main__":
    unittest.main()
