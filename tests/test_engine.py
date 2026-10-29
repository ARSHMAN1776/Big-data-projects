"""Tests for the chat engine and server helpers.

All data here is generated inside the tests (seeded, clearly synthetic) so the checks do not depend on
whatever is in data/raw.

Run:  python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "chatbot"))
import engine  # noqa: E402
import server  # noqa: E402
from common import POST_COLUMNS  # noqa: E402

BRANDS = ["Khaadi", "Gul Ahmed", "Maria B", "Nishat Linen"]


def synthetic_posts(seed=3, per_brand=30, hours=True, followers=True, platforms=("instagram", "facebook")) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    base = pd.Timestamp("2026-10-01")
    scale = {"Khaadi": 1.0, "Gul Ahmed": 3.0, "Maria B": 0.6, "Nishat Linen": 1.5}
    for b in BRANDS:
        for plat in platforms:
            for i in range(per_brand):
                d = base - timedelta(days=int(rng.integers(0, 60)))
                mt = ["image", "carousel", "reel"][i % 3]
                likes = int(rng.lognormal(5, 0.6) * scale[b])
                rows.append({
                    "platform": plat, "brand": b, "post_id": f"{b[:2]}{plat[:2]}{i}", "url": f"https://www.instagram.com/p/{b[:2]}{plat[:2]}{i}/",
                    "caption": f"{'Winter sale' if i % 4 == 0 else 'New lawn'} #{b.split()[0].lower()}",
                    "hashtags": f"#{b.split()[0].lower()}|#lawn" if i % 2 else f"#{b.split()[0].lower()}",
                    "published_at_utc": "", "date_pkt": d.date().isoformat(), "weekday_pkt": d.strftime("%A"),
                    "hour_pkt": int(rng.integers(0, 24)) if hours else np.nan,
                    "media_type": mt, "likes": likes, "comments": int(likes * 0.05), "shares": np.nan,
                    "views": int(likes * 9) if mt == "reel" else np.nan,
                    "followers": 100_000 * scale[b] if followers else np.nan,
                    "source": "api", "collector": "", "collected_at_utc": "2026-10-21T00:00:00Z",
                })
    return pd.DataFrame(rows, columns=POST_COLUMNS)


def write(dirpath: Path, name: str, df: pd.DataFrame):
    df.to_csv(dirpath / name, index=False, encoding="utf-8-sig")


class Env(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def engine_with(self, df=None, name="posts_instagram_api_2026-10-21.csv"):
        if df is not None:
            write(self.dir, name, df)
        return engine.Engine(engine.DataStore(self.dir))


class LoadingTests(Env):
    def test_empty_folder(self):
        eng = self.engine_with()
        self.assertTrue(eng.info()["empty"])
        self.assertEqual(eng.ask("which brand performs best?")["intent"], "empty")

    def test_only_newest_file_of_each_kind_is_used(self):
        write(self.dir, "posts_manual_2026-10-01.csv", synthetic_posts(per_brand=5).assign(source="manual"))
        write(self.dir, "posts_manual_2026-10-20.csv", synthetic_posts(per_brand=10).assign(source="manual"))
        eng = engine.Engine(engine.DataStore(self.dir))
        self.assertEqual(eng.info()["rows"], 10 * len(BRANDS) * 2)

    def test_api_wins_over_manual_for_the_same_post(self):
        a = synthetic_posts(per_brand=4, platforms=("instagram",))
        m = a.copy()
        m["source"], m["likes"] = "manual", 1
        write(self.dir, "posts_instagram_api_2026-10-21.csv", a)
        write(self.dir, "posts_manual_2026-10-21.csv", m)
        df = engine.DataStore(self.dir).frame()
        self.assertEqual(len(df), len(a))
        self.assertTrue((df["source"] == "api").all())

    def test_new_files_are_picked_up_without_restart(self):
        eng = self.engine_with(synthetic_posts(per_brand=5, platforms=("instagram",)))
        before = eng.info()["rows"]
        write(self.dir, "posts_manual_2026-10-22.csv", synthetic_posts(per_brand=5, platforms=("facebook",)).assign(source="manual"))
        self.assertGreater(eng.info()["rows"], before)

    def test_youtube_feed_file_is_adapted(self):
        yt = pd.DataFrame({"brand": ["Khaadi", "Khaadi"], "video_id": ["a", "b"], "url": ["https://youtu.be/a", "https://youtu.be/b"],
                           "title": ["One", "Two"], "hashtags": ["", ""], "date_pkt": ["2026-10-01", "2026-10-02"],
                           "weekday_pkt": ["Thursday", "Friday"], "hour_pkt": [10, 11], "format": ["short", "video"],
                           "views": [100, 900], "likes": [np.nan, np.nan], "comment_count": [np.nan, np.nan],
                           "source": ["rss", "rss"], "collected_at_utc": ["2026-10-21T00:00:00Z"] * 2})
        eng = self.engine_with(yt, "videos_rss_2026-10-21.csv")
        a = eng.ask("which brand performs best?")
        self.assertIn("views", a["text"])
        self.assertTrue(any("no likes or comments" in n["text"].lower() for n in a["notes"]))
        self.assertEqual(eng.info()["platforms"][0]["key"], "youtube")


class MetricTests(Env):
    def test_rate_per_follower_is_used_when_possible(self):
        a = self.engine_with(synthetic_posts()).ask("which brand performs best?")
        self.assertIn("engagement per 100 followers", a["evidence"])
        self.assertEqual(a["intent"], "brands_rank")

    def test_raw_counts_are_flagged_when_followers_are_missing(self):
        a = self.engine_with(synthetic_posts(followers=False)).ask("which brand performs best?")
        self.assertIn("likes + comments", a["evidence"])
        self.assertTrue(any("account size" in n["text"] for n in a["notes"]))

    def test_user_can_pick_the_metric(self):
        a = self.engine_with(synthetic_posts()).ask("which brand gets the most comments?")
        self.assertIn("comments", a["evidence"])


class ChanceTestTests(unittest.TestCase):
    def test_a_big_brand_cannot_fake_a_pattern(self):
        # Brand A has big numbers and always says "sale"; brand B has small numbers and never does.
        rng = np.random.default_rng(1)
        vals = np.concatenate([rng.normal(1000, 50, 30), rng.normal(100, 10, 30)])
        brand = np.array(["A"] * 30 + ["B"] * 30)
        sale = {"sale": np.array([True] * 30 + [False] * 30)}
        self.assertLess(engine.perm_p(vals, sale), 0.01)                  # naive test is fooled
        self.assertGreater(engine.perm_p(vals, sale, brand), 0.5)         # brand-aware test is not

    def test_a_real_effect_inside_every_brand_is_found(self):
        rng = np.random.default_rng(2)
        brand, sale, vals = [], [], []
        for b, level in (("A", 1000), ("B", 100)):
            for i in range(30):
                is_sale = i % 2 == 0
                brand.append(b)
                sale.append(is_sale)
                vals.append(rng.normal(level * (3 if is_sale else 1), level * 0.1))
        p = engine.perm_p(np.array(vals), {"sale": np.array(sale)}, np.array(brand))
        self.assertLess(p, 0.01)

    def test_groups_too_small_are_not_tested(self):
        self.assertIsNone(engine.perm_p(np.arange(20.0), {"x": np.array([True] * 3 + [False] * 17)}))

    def test_small_leading_group_gets_no_verdict(self):
        self.assertIn("too few", engine.chance_phrase(0.001, 4))
        self.assertIn("very unlikely", engine.chance_phrase(0.001, 40))
        self.assertIn("could easily be chance", engine.chance_phrase(0.4, 40))

    def test_result_is_repeatable(self):
        vals = np.random.default_rng(5).normal(size=60)
        m = {"x": np.arange(60) % 2 == 0}
        self.assertEqual(engine.perm_p(vals, m), engine.perm_p(vals, m))


ROUTES = [
    ("Which brand performs best?", "brands_rank"), ("who is the top brand", "brands_rank"),
    ("Shorts vs long videos", "media"), ("which content type gets more engagement", "media"), ("do reels work", "media"),
    ("What is the best day to post?", "when"), ("best time of day", "when"), ("is friday better than monday", "when"),
    ("Most popular hashtags", "hashtags"), ("which #tags work", "hashtags"),
    ("Show the top 5 posts", "top_posts"), ("most viewed videos", "top_posts"), ("top 3 posts from Khaadi", "top_posts"),
    ("Which themes work best?", "themes"), ("does winter content work", "themes"),
    ("What should a brand post next?", "recommend"), ("any advice", "recommend"),
    ("What are the limits of this data?", "limits"), ("can I trust this", "limits"),
    ("What data do you have?", "overview"), ("how many posts", "overview"),
    ("Tell me about Khaadi", "brand_profile"), ("how is khadi doing", "brand_profile"),
    ("Compare Khaadi and Gul Ahmed", "compare_brands"), ("khaadi vs maria b", "compare_brands"),
    ("which platform is better", "platform"), ("instagram vs facebook", "platform"),
    ("who responds to the posts", "cannot"), ("audience demographics", "cannot"), ("sentiment of comments", "cannot"),
    ("hello", "help"), ("what can you do", "help"), ("blah blah", "unknown"),
]


class RoutingTests(Env):
    def test_phrasings_reach_the_right_analysis(self):
        eng = self.engine_with(synthetic_posts())
        wrong = [(q, want, eng.ask(q)["intent"]) for q, want in ROUTES if eng.ask(q)["intent"] != want]
        self.assertEqual(wrong, [])

    def test_every_suggestion_and_follow_up_is_answerable(self):
        eng = self.engine_with(synthetic_posts())
        todo, seen = list(engine.SUGGESTIONS), set()
        while todo:
            q = todo.pop()
            if q in seen:
                continue
            seen.add(q)
            a = eng.ask(q)
            self.assertNotIn(a["intent"], ("unknown", "empty"), q)
            todo += a["follow_ups"]

    def test_brand_names_typos_and_short_forms(self):
        eng = self.engine_with(synthetic_posts())
        for q in ("tell me about khadi", "tell me about gulahmed", "tell me about nishat", "tell me about maria"):
            self.assertEqual(eng.ask(q)["intent"], "brand_profile", q)
        self.assertIn("Nishat Linen", eng.ask("tell me about nishat")["text"])

    def test_time_of_day_is_not_answered_as_day(self):
        a = self.engine_with(synthetic_posts()).ask("best time of day")
        self.assertTrue(any("time of day" in b["title"] for b in a["blocks"]))
        self.assertFalse(any("weekday" in b["title"] for b in a["blocks"]))


class AnswerTests(Env):
    def setUp(self):
        super().setUp()
        self.eng = self.engine_with(synthetic_posts())

    def test_top_posts_respects_count_and_brand(self):
        a = self.eng.ask("top 3 posts from Khaadi")
        rows = a["blocks"][0]["rows"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["brand"] == "Khaadi" for r in rows))

    def test_platform_filter_from_the_dropdown(self):
        a = self.eng.ask("which brand performs best?", "facebook")
        self.assertIn("Facebook", a["text"])
        self.assertEqual(self.eng.ask("which brand performs best?", "youtube")["text"], "There are no YouTube posts in the data yet.")

    def test_platform_in_the_question_wins(self):
        a = self.eng.ask("which brand performs best on instagram?", "facebook")
        self.assertIn("Instagram", a["text"])

    def test_platform_comparison_needs_two_platforms(self):
        one = engine.Engine(engine.DataStore(self._fresh(synthetic_posts(platforms=("instagram",)))))
        self.assertIn("cannot compare platforms", one.ask("which platform is better")["text"])
        self.assertIn("leads", self.eng.ask("which platform is better")["text"])

    def test_unknown_posting_hour_is_reported_not_guessed(self):
        eng = engine.Engine(engine.DataStore(self._fresh(synthetic_posts(hours=False))))
        a = eng.ask("best time of day")
        self.assertTrue(any("posting hour is known for only" in n["text"] for n in a["notes"]))

    def _fresh(self, df):
        d = Path(tempfile.mkdtemp(dir=self.dir))
        write(d, "posts_instagram_api_2026-10-21.csv", df)
        return d

    def test_every_answer_carries_evidence_and_is_json_safe(self):
        for q in engine.SUGGESTIONS + ["tell me about Khaadi", "compare khaadi and maria b", "limits"]:
            a = self.eng.ask(q)
            self.assertTrue(a["evidence"] or a["intent"] in ("cannot", "help", "unknown"), q)
            json.dumps(server.clean(a), allow_nan=False)

    def test_recommendation_only_reports_strong_patterns(self):
        # random synthetic data has no real pattern, so nothing should pass the strict test
        a = self.eng.ask("what should a brand post next?")
        self.assertIn("No pattern", a["text"])

    def test_recommendation_finds_a_planted_pattern(self):
        df = synthetic_posts(per_brand=60)
        df.loc[df["media_type"] == "reel", "likes"] *= 8
        df.loc[df["media_type"] == "reel", "comments"] = (df["likes"] * 0.05).astype(int)
        a = self.engine_with(df, "posts_instagram_api_2026-10-22.csv").ask("what should a brand post next?")
        rows = [r for b in a["blocks"] for r in b.get("rows", [])]
        self.assertTrue(any("Reels" in r["f"] for r in rows), a["text"])


class ServerHelperTests(unittest.TestCase):
    def test_clean_removes_nan_and_numpy_types(self):
        out = server.clean({"a": float("nan"), "b": np.float64(2.5), "c": [np.int64(3), float("inf")], "d": "x"})
        self.assertEqual(out, {"a": None, "b": 2.5, "c": [3, None], "d": "x"})
        json.dumps(out, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
