"""Tests for the overview dashboard, saving posts, and the HTTP server.

Run:  python -m unittest discover -s tests -v
"""
import http.client
import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "chatbot"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402
import entry  # noqa: E402
import merge_manual as mm  # noqa: E402
import server  # noqa: E402
from common import POST_COLUMNS  # noqa: E402
from test_engine import BRANDS, synthetic_posts, write  # noqa: E402

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
BRAND_MAP = {b.lower(): b for b in BRANDS}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        write(self.dir, "posts_instagram_api_2026-10-21.csv", synthetic_posts())
        self.eng = engine.Engine(engine.DataStore(self.dir))

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_cards_and_tiles(self):
        d = self.eng.dashboard()
        self.assertFalse(d["empty"])
        self.assertEqual([s["id"] for s in d["sections"]],
                         ["brands", "platforms", "formats", "days", "times", "hashtags", "themes", "top", "next"])
        self.assertEqual([k["label"] for k in d["kpis"]], ["Posts", "Platforms with data", "Posts published", "Main measure", "Typical post"])
        for s in d["sections"]:
            self.assertTrue(s["title"] and s["help"] and s["question"].endswith("?"))
            self.assertTrue(s["answer"]["text"])

    def test_every_platform_is_listed_even_without_data(self):
        status = {p["key"]: p for p in self.eng.dashboard()["platform_status"]}
        self.assertEqual(set(status), {"youtube", "instagram", "facebook", "tiktok"})
        self.assertGreater(status["instagram"]["rows"], 0)
        self.assertEqual(status["tiktok"]["rows"], 0)
        self.assertEqual(status["tiktok"]["sources"], [])

    def test_filters_remove_the_cards_they_make_meaningless(self):
        by_platform = [s["id"] for s in self.eng.dashboard("instagram")["sections"]]
        self.assertNotIn("platforms", by_platform)
        by_brand = [s["id"] for s in self.eng.dashboard(None, "Khaadi")["sections"]]
        self.assertNotIn("brands", by_brand)
        self.assertIn("platforms", by_brand)

    def test_filter_with_no_matches_is_reported_empty(self):
        self.assertTrue(self.eng.dashboard("tiktok")["empty"])
        self.assertTrue(self.eng.dashboard(None, "Nobody")["empty"])

    def test_empty_store(self):
        d = engine.Engine(engine.DataStore(self.dir / "missing")).dashboard()
        self.assertTrue(d["empty"])
        self.assertEqual(len(d["platform_status"]), 4)

    def test_mixed_platforms_are_flagged_and_single_platform_is_not(self):
        both = engine.Engine(engine.DataStore(self._fresh(synthetic_posts()))).dashboard()
        formats = next(s for s in both["sections"] if s["id"] == "formats")["answer"]
        self.assertTrue(any("mixes 2 platforms" in n["text"] for n in formats["notes"]))
        one = engine.Engine(engine.DataStore(self._fresh(synthetic_posts()))).dashboard("instagram")
        formats = next(s for s in one["sections"] if s["id"] == "formats")["answer"]
        self.assertFalse(any("mixes" in n["text"] for n in formats["notes"]))

    def _fresh(self, df):
        d = Path(tempfile.mkdtemp(dir=self.dir))
        write(d, "posts_instagram_api_2026-10-21.csv", df)
        return d

    def test_one_broken_card_does_not_blank_the_page(self):
        self.eng.h_hashtags = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        d = self.eng.dashboard()
        card = next(s for s in d["sections"] if s["id"] == "hashtags")
        self.assertIn("could not be built", card["answer"]["text"])
        self.assertEqual(len(d["sections"]), 9)

    def test_dashboard_is_json_safe(self):
        json.dumps(server.clean(self.eng.dashboard()), allow_nan=False)

    def test_tiktok_is_understood(self):
        write(self.dir, "posts_manual_2026-10-22.csv", synthetic_posts(platforms=("tiktok",)).assign(source="manual"))
        a = self.eng.ask("which tiktok posts do best?")
        self.assertIn("TikTok", a["text"])
        self.assertEqual(self.eng.ask("which platform performs best?")["intent"], "platform")
        self.assertIn("TikTok", self.eng.info()["platform_status"][3]["name"])

    def test_chat_respects_the_brand_filter(self):
        rows = self.eng.ask("show the top 3 posts", None, "Khaadi")["blocks"][0]["rows"]
        self.assertTrue(rows and all(r["brand"] == "Khaadi" for r in rows))
        self.assertEqual(self.eng.ask("which format works best?", None, "Nobody")["text"], "There are no Nobody posts in the data yet.")


class EntryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.manual, self.raw = root / "manual", root / "raw"

    def tearDown(self):
        self._tmp.cleanup()

    fields = {"collector": "Ali", "platform": "tiktok", "brand": "Khaadi", "post_url": "https://www.tiktok.com/@khaadi/video/123",
              "post_date": "2026-10-10", "media_type": "video", "caption": "Hello", "likes": "1.2K", "comments": "5", "collected_on": "2026-10-24"}

    def add(self, **over):
        return entry.add_post({**self.fields, **over}, BRAND_MAP, self.manual, self.raw, NOW)

    def test_a_valid_post_is_saved_and_the_dataset_rebuilt(self):
        out = self.add()
        self.assertEqual((out["ok"], out["saved"], out["total_manual"]), (True, 1, 1))
        df = pd.read_csv(self.raw / "posts_manual_2026-10-24.csv")
        self.assertEqual((df.loc[0, "platform"], df.loc[0, "likes"], df.loc[0, "post_id"]), ("tiktok", 1200, 123))

    def test_second_post_is_added_to_the_same_dataset(self):
        self.add()
        self.add(post_url="https://www.tiktok.com/@khaadi/video/456")
        self.assertEqual(len(pd.read_csv(self.raw / "posts_manual_2026-10-24.csv")), 2)

    def test_duplicate_is_refused_and_not_stored_twice(self):
        self.add()
        out = self.add(post_url="https://www.tiktok.com/@khaadi/video/123?is_from_webapp=1")
        self.assertFalse(out["ok"])
        self.assertIn("already", out["problems"][0])
        self.assertEqual(len(pd.read_csv(self.manual / entry.WEB_FILE)), 1)

    def test_invalid_post_writes_nothing_and_explains_why(self):
        out = self.add(likes="abc", platform="snapchat")
        self.assertFalse(out["ok"])
        self.assertGreaterEqual(len(out["problems"]), 2)
        self.assertFalse((self.manual / entry.WEB_FILE).exists())
        self.assertFalse(self.raw.exists())

    def test_collected_on_defaults_to_today(self):
        out = self.add(collected_on="")
        self.assertTrue(out["ok"])

    def test_oversized_fields_are_refused(self):
        self.assertFalse(self.add(caption="x" * 2001)["ok"])
        self.assertFalse(self.add(notes="y" * 301)["ok"])

    def test_text_cells_cannot_become_spreadsheet_formulas(self):
        self.add(caption="=HYPERLINK(\"http://evil\")", notes="+cmd", collector="@me")
        stored = pd.read_csv(self.manual / entry.WEB_FILE, dtype=str, keep_default_na=False).iloc[0]
        self.assertTrue(stored["caption"].startswith("'="))
        self.assertTrue(stored["notes"].startswith("'+"))
        self.assertTrue(stored["collector"].startswith("'@"))

    def csv(self, *rows, header=None):
        head = header or ",".join(mm.TEMPLATE_COLUMNS)
        return "﻿" + "\n".join([head, *rows])

    def test_import_saves_good_rows_and_explains_the_rest(self):
        good = "Sara,instagram,Khaadi,https://www.instagram.com/p/AAA111/,2026-10-12,,carousel,نیا #khaadi,\"2,310\",41,,,1.2M,2026-10-24,"
        typo = "Sara,instagram,Khadi,https://www.instagram.com/p/BBB222/,2026-10-12,,image,x,10,1,,,,2026-10-24,"
        again = "Sara,instagram,Khaadi,https://www.instagram.com/p/AAA111/?igsh=1,2026-10-12,,carousel,x,1,1,,,,2026-10-24,"
        out = entry.import_csv(self.csv(good, typo, again), "sara.csv", BRAND_MAP, self.manual, self.raw, NOW)
        self.assertEqual((out["ok"], out["saved"], out["rejected"]), (True, 1, 2))
        problems = " | ".join(r["problems"] for r in out["rejects"])
        self.assertIn("did you mean 'Khaadi'", problems)
        self.assertIn("duplicate", problems)
        df = pd.read_csv(self.raw / "posts_manual_2026-10-24.csv")
        self.assertEqual(df.loc[0, "caption"], "نیا #khaadi")      # Urdu survives the round trip
        self.assertEqual(df.loc[0, "likes"], 2310)

    def test_import_twice_does_not_duplicate(self):
        row = "Sara,instagram,Khaadi,https://www.instagram.com/p/AAA111/,2026-10-12,,image,x,10,1,,,,2026-10-24,"
        entry.import_csv(self.csv(row), "a.csv", BRAND_MAP, self.manual, self.raw, NOW)
        out = entry.import_csv(self.csv(row), "b.csv", BRAND_MAP, self.manual, self.raw, NOW)
        self.assertEqual((out["saved"], out["rejected"]), (0, 1))
        self.assertIn("already in your data", out["rejects"][0]["problems"])
        self.assertEqual(len(pd.read_csv(self.raw / "posts_manual_2026-10-24.csv")), 1)

    def test_import_with_missing_columns_or_too_many_rows_is_refused(self):
        out = entry.import_csv("a,b\n1,2", "x.csv", BRAND_MAP, self.manual, self.raw, NOW)
        self.assertFalse(out["ok"])
        self.assertIn("missing these columns", out["problems"][0])
        many = self.csv(*["x"] * (entry.MAX_IMPORT_ROWS + 1))
        self.assertFalse(entry.import_csv(many, "big.csv", BRAND_MAP, self.manual, self.raw, NOW)["ok"])

    def test_import_warns_when_excel_damaged_the_text(self):
        row = "Sara,instagram,Khaadi,https://www.instagram.com/p/AAA111/,2026-10-12,,image,��,10,1,,,,2026-10-24,"
        out = entry.import_csv(self.csv(row), "a.csv", BRAND_MAP, self.manual, self.raw, NOW)
        self.assertIn("CSV UTF-8", out["warning"])

    def test_import_file_name_cannot_escape_the_folder(self):
        row = "Sara,instagram,Khaadi,https://www.instagram.com/p/AAA111/,2026-10-12,,image,x,10,1,,,,2026-10-24,"
        entry.import_csv(self.csv(row), "../../evil name.csv", BRAND_MAP, self.manual, self.raw, NOW)
        files = [p.name for p in self.manual.iterdir()]
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].startswith("import_") and "/" not in files[0] and ".." not in files[0])


class HttpTests(unittest.TestCase):
    """A real server on a free port, pointed at throwaway folders so no real data is touched."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls._saved = (server.RAW_DIR, server.MANUAL_DIR)
        server.RAW_DIR, server.MANUAL_DIR = root / "raw", root / "manual"
        server.RAW_DIR.mkdir(parents=True)
        write(server.RAW_DIR, "posts_instagram_api_2026-10-21.csv", synthetic_posts(per_brand=6))
        server.Handler.app = server.App()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        server.Handler.port = cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.RAW_DIR, server.MANUAL_DIR = cls._saved
        cls._tmp.cleanup()

    def req(self, method, path, body=None, headers=None, host=None, raw_body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        data = raw_body
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        c.close()
        try:
            return r.status, json.loads(payload), r
        except ValueError:
            return r.status, payload, r

    def test_pages_and_meta(self):
        self.assertEqual(self.req("GET", "/")[0], 200)
        self.assertEqual(self.req("GET", "/app.js")[0], 200)
        status, meta, _ = self.req("GET", "/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual([p["key"] for p in meta["manual_platforms"]], ["instagram", "facebook", "tiktok"])
        self.assertIn("Khaadi", meta["brands"])

    def test_template_download(self):
        status, body, r = self.req("GET", "/template.csv")
        self.assertEqual(status, 200)
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        self.assertTrue(body.decode("utf-8").lstrip("﻿").startswith("collector,platform,brand"))

    def test_dashboard(self):
        status, d, _ = self.req("GET", "/api/dashboard")
        self.assertEqual((status, len(d["sections"])), (200, 9))
        status, d, _ = self.req("GET", "/api/dashboard?platform=instagram")
        self.assertEqual(status, 200)

    def test_bad_filters_are_rejected(self):
        self.assertEqual(self.req("GET", "/api/dashboard?platform=myspace")[0], 400)
        self.assertEqual(self.req("POST", "/api/ask", {"question": "hi", "platform": "myspace"})[0], 400)

    def test_ask_with_filters(self):
        status, a, _ = self.req("POST", "/api/ask", {"question": "which brand performs best?", "platform": "instagram"})
        self.assertEqual(status, 200)
        self.assertIn("Instagram", a["text"])

    def test_adding_posts_works(self):
        fields = {"collector": "Ali", "platform": "facebook", "brand": "Khaadi", "post_url": "https://www.facebook.com/khaadi/posts/pfbid0xyz",
                  "post_date": "2026-10-10", "media_type": "image", "likes": "10", "comments": "2"}
        status, out, _ = self.req("POST", "/api/add_post", {"fields": fields})
        self.assertEqual((status, out["ok"]), (200, True))
        status, info, _ = self.req("GET", "/api/info")
        self.assertTrue(any(p["key"] == "facebook" and p["rows"] >= 1 for p in info["platform_status"]))

    def test_import_endpoint(self):
        row = "Sara,instagram,Khaadi,https://www.instagram.com/p/ZZZ999/,2026-10-12,,image,x,10,1,,,,2026-10-24,"
        text = "﻿" + ",".join(mm.TEMPLATE_COLUMNS) + "\n" + row
        status, out, _ = self.req("POST", "/api/import", {"filename": "s.csv", "text": text})
        self.assertEqual((status, out["saved"]), (200, 1))

    def test_requests_from_other_hosts_or_sites_are_refused(self):
        self.assertEqual(self.req("GET", "/api/info", host="evil.example.com")[0], 403)
        self.assertEqual(self.req("POST", "/api/ask", {"question": "hi"}, headers={"Origin": "http://evil.example.com"})[0], 403)

    def test_body_rules(self):
        self.assertEqual(self.req("POST", "/api/ask", raw_body=b"hi", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.req("POST", "/api/ask", raw_body=b"not json", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.req("POST", "/api/ask", raw_body=b"[1,2]", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.req("POST", "/api/ask", {"question": "a" * 3000})[0], 413)
        self.assertEqual(self.req("POST", "/api/add_post", {"fields": "nope"})[0], 400)
        self.assertEqual(self.req("POST", "/api/nothing", {"question": "hi"})[0], 404)

    def test_security_headers(self):
        _, _, r = self.req("GET", "/")
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")

    def test_no_directory_traversal(self):
        self.assertEqual(self.req("GET", "/../../etc/passwd")[0], 404)
        self.assertEqual(self.req("GET", "/static/")[0], 404)


if __name__ == "__main__":
    unittest.main()
