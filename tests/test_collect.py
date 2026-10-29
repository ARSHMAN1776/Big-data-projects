"""Offline tests. The API tests use stubbed responses shaped like the documented
YouTube Data API v3 replies, so they prove our parsing and paging, not live behaviour.

Run:  python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collect  # noqa: E402

KEY = "SECRET-KEY-123"
NOW = "2026-10-21T00:00:00Z"


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload

    def json(self):
        return self._payload


class FakeSession:
    """handler(endpoint, params) -> (status, payload) or raises."""

    def __init__(self, handler):
        self.handler, self.requests = handler, []

    def get(self, url, params=None, timeout=None, headers=None):
        endpoint = url.rsplit("/", 1)[-1]
        self.requests.append((endpoint, dict(params or {})))
        status, payload = self.handler(endpoint, params or {})
        return FakeResponse(status, payload)


def err(status, reason):
    return status, {"error": {"code": status, "message": reason, "errors": [{"reason": reason}]}}


def video_item(vid, duration, views, likes=None, comments=None, live="none"):
    stats = {"viewCount": str(views)}
    if likes is not None:
        stats["likeCount"] = str(likes)
    if comments is not None:
        stats["commentCount"] = str(comments)
    return {
        "id": vid,
        "snippet": {"channelId": "UC_TEST", "title": f"Title {vid} #Lawn", "description": "Shop now #Khaadi",
                    "publishedAt": "2026-10-19T19:30:00Z", "tags": ["lawn", "eid"],
                    "defaultAudioLanguage": "ur", "liveBroadcastContent": live},
        "contentDetails": {"duration": duration},
        "statistics": stats,
    }


class HelperTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(collect.parse_duration("PT1M30S"), 90)
        self.assertEqual(collect.parse_duration("PT45S"), 45)
        self.assertEqual(collect.parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(collect.parse_duration("P0D"), 0)
        self.assertIsNone(collect.parse_duration(None))
        self.assertIsNone(collect.parse_duration("garbage"))

    def test_time_fields_use_pakistan_time(self):
        t = collect.time_fields("2026-10-19T19:30:00Z")  # 19:30 UTC is 00:30 next day in PKT
        self.assertEqual((t["date_pkt"], t["weekday_pkt"], t["hour_pkt"]), ("2026-10-20", "Tuesday", 0))
        self.assertEqual(t["published_at_utc"], "2026-10-19T19:30:00Z")

    def test_hashtags(self):
        self.assertEqual(collect.extract_hashtags("#Khaadi #khaadi #1 #عید text"), "#khaadi|#عید")
        self.assertEqual(collect.extract_hashtags(""), "")

    def test_missing_numbers_stay_blank(self):
        self.assertIsNone(collect.to_int(None))
        self.assertEqual(collect.to_int("0"), 0)


class RssTests(unittest.TestCase):
    XML = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
     <entry><yt:videoId>AAA</yt:videoId><title>Short one</title>
      <link rel="alternate" href="https://www.youtube.com/shorts/AAA"/><published>2026-10-19T04:53:23+00:00</published>
      <media:group><media:description>Hello #Eid</media:description>
       <media:community><media:starRating count="2" average="5.00" min="1" max="5"/><media:statistics views="217"/></media:community>
      </media:group></entry>
     <entry><yt:videoId>BBB</yt:videoId><title>Long one</title>
      <link rel="alternate" href="https://www.youtube.com/watch?v=BBB"/><published>2026-10-18T09:12:45+00:00</published>
      <media:group><media:community><media:statistics views="5"/></media:community></media:group></entry>
    </feed>"""

    def test_parse(self):
        a, b = collect.parse_rss(self.XML, "Khaadi", "UC_TEST", NOW)
        self.assertEqual((a["format"], a["views"], a["rss_rating_count"], a["hashtags"]), ("short", 217, 2, "#eid"))
        self.assertEqual((b["format"], b["views"]), ("video", 5))
        self.assertIsNone(a["likes"])
        self.assertEqual(a["channel_id"], "UC_TEST")
        self.assertEqual(set(a), set(collect.VIDEO_COLUMNS))


class ApiTests(unittest.TestCase):
    def make_handler(self):
        def handler(endpoint, p):
            if endpoint == "channels":
                return 200, {"items": [{"id": "UC_TEST", "snippet": {"title": "Test Brand", "publishedAt": "2016-01-01T00:00:00Z", "country": "PK"},
                                        "statistics": {"subscriberCount": "47300", "viewCount": "1000", "videoCount": "3"},
                                        "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST"}}}]}
            if endpoint == "playlistItems":
                if "pageToken" not in p:
                    return 200, {"items": [{"contentDetails": {"videoId": "v1"}}, {"contentDetails": {"videoId": "v2"}}], "nextPageToken": "T2"}
                return 200, {"items": [{"contentDetails": {"videoId": "v3"}}, {"contentDetails": {"videoId": "v4"}}]}
            if endpoint == "videos":
                return 200, {"items": [
                    video_item("v1", "PT45S", 1000, likes=50, comments=2),
                    video_item("v2", "PT10M", 500, likes=None, comments=None),   # likes hidden, comments off
                    video_item("v3", "PT2M", 300, likes=9, comments=1),          # comments disabled -> 403
                    video_item("v4", "PT30S", 0, live="upcoming"),               # must be skipped
                ]}
            if endpoint == "commentThreads":
                if p["videoId"] == "v1":
                    return 200, {"items": [
                        {"snippet": {"totalReplyCount": 1, "topLevelComment": {"id": "c1", "snippet": {
                            "textOriginal": "Bohat khoobsurat", "likeCount": 4, "publishedAt": "2026-10-20T10:00:00Z",
                            "authorDisplayName": "SHOULD NOT BE KEPT", "authorChannelId": {"value": "x"}}}}},
                        {"snippet": {"totalReplyCount": 0, "topLevelComment": {"id": "c2", "snippet": {
                            "textOriginal": "Beautiful", "likeCount": 0, "publishedAt": "2026-10-20T11:00:00Z"}}}},
                    ]}
                return err(403, "commentsDisabled")
            raise AssertionError(endpoint)
        return handler

    def run_collect(self, handler, **kw):
        api = collect.YouTubeAPI(KEY, session=FakeSession(handler), sleep=lambda s: None)
        brands = [{"brand": "Test Brand", "channel_id": "UC_TEST"}]
        out = collect.collect_api(api, brands, kw.get("max_videos", 0), kw.get("comments", 100), NOW)
        return api, out

    def test_full_collection(self):
        api, (videos, channels, comments, complete) = self.run_collect(self.make_handler())
        self.assertTrue(complete)
        self.assertEqual([v["video_id"] for v in videos], ["v1", "v2", "v3"])  # upcoming v4 skipped, paging followed
        v1, v2, v3 = videos
        self.assertEqual((v1["format"], v1["duration_sec"], v1["likes"], v1["comment_count"]), ("short", 45, 50, 2))
        self.assertEqual(v2["format"], "video")
        self.assertIsNone(v2["likes"])           # hidden, not zero
        self.assertIsNone(v2["comment_count"])
        self.assertEqual(v3["format"], "video")  # 120 s is over the 60 s Shorts rule
        self.assertEqual(v1["tags"], "lawn|eid")
        self.assertEqual(v1["hashtags"], "#lawn|#khaadi")
        self.assertEqual(v1["default_language"], "ur")
        self.assertEqual(channels[0]["subscribers"], 47300)
        self.assertEqual([c["comment_id"] for c in comments], ["c1", "c2"])  # v3's 403 handled, v2 skipped
        self.assertEqual(set(comments[0]), set(collect.COMMENT_COLUMNS))     # no author fields
        self.assertNotIn("SHOULD NOT BE KEPT", str(comments))
        self.assertGreater(api.calls, 4)

    def test_max_videos_limit(self):
        api, _ = self.run_collect(self.make_handler(), max_videos=2, comments=0)
        video_calls = [p for e, p in api.session.requests if e == "videos"]
        self.assertEqual(video_calls[0]["id"], "v1,v2")  # list cut at the limit; page 2 was never needed
        self.assertEqual(len([1 for e, _ in api.session.requests if e == "playlistItems"]), 1)

    def test_no_comment_calls_when_disabled(self):
        api, _ = self.run_collect(self.make_handler(), comments=0)
        self.assertNotIn("commentThreads", [e for e, _ in api.session.requests])

    def test_quota_exhaustion_returns_partial_results(self):
        base = self.make_handler()

        def handler(endpoint, p):
            if endpoint == "commentThreads":
                return err(403, "quotaExceeded")
            return base(endpoint, p)
        _, (videos, _channels, comments, complete) = self.run_collect(handler)
        self.assertFalse(complete)
        self.assertGreaterEqual(len(videos), 1)
        self.assertEqual(comments, [])

    def test_retries_transient_errors(self):
        calls = {"n": 0}

        def handler(endpoint, p):
            calls["n"] += 1
            return err(503, "backendError") if calls["n"] == 1 else (200, {"items": []})
        api = collect.YouTubeAPI(KEY, session=FakeSession(handler), sleep=lambda s: None)
        self.assertEqual(api.channels(["UC_TEST"]), [])
        self.assertEqual(calls["n"], 2)

    def test_other_errors_raise(self):
        api = collect.YouTubeAPI(KEY, session=FakeSession(lambda e, p: err(400, "keyInvalid")), sleep=lambda s: None)
        with self.assertRaises(collect.ApiError) as ctx:
            api.channels(["UC_TEST"])
        self.assertEqual(ctx.exception.reason, "keyInvalid")

    def test_network_errors_never_leak_the_key(self):
        def handler(endpoint, p):
            raise requests.ConnectionError(f"Max retries exceeded with url: /youtube/v3/channels?id=x&key={KEY}")
        api = collect.YouTubeAPI(KEY, session=FakeSession(handler), sleep=lambda s: None)
        with self.assertRaises(collect.ApiError) as ctx:
            api.channels(["UC_TEST"])
        self.assertNotIn(KEY, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
