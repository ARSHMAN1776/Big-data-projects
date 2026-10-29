"""HTTP tests for the agent endpoints: streaming answers, status, setup re-check and background jobs.

A real server runs on a free port with throwaway data folders and a scripted fake Claude client, so no
network is used and no real data or key is touched.

Run:  python -m unittest discover -s tests -v
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "chatbot"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402
from agent.jobs import JobRunner  # noqa: E402
from test_agent import SECRET, call, fake_client, reply, text  # noqa: E402
from test_engine import synthetic_posts, write  # noqa: E402


class AgentHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls._saved = (server.RAW_DIR, server.MANUAL_DIR, server.ENV_FILE, server.AGENT_PER_MINUTE)
        cls._env = {k: os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "YOUTUBE_API_KEY", "IG_USER_ID", "IG_ACCESS_TOKEN")}
        for k in cls._env:
            os.environ.pop(k, None)
        server.RAW_DIR, server.MANUAL_DIR, server.ENV_FILE = root / "raw", root / "manual", root / ".env"
        server.RAW_DIR.mkdir(parents=True)
        write(server.RAW_DIR, "posts_instagram_api_2026-10-21.csv", synthetic_posts(per_brand=6))
        server.Handler.app = server.App()
        cls.app = server.Handler.app
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        server.Handler.port = cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.RAW_DIR, server.MANUAL_DIR, server.ENV_FILE, server.AGENT_PER_MINUTE = cls._saved
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls._tmp.cleanup()

    def setUp(self):
        self.seen = []
        self.app._recent.clear()
        server.AGENT_PER_MINUTE = 20
        self.app.agent._client = None
        self.app.agent._factory = None

    def use_client(self, *script):
        self.app.agent._client = fake_client(*script)
        return self.app.agent._client

    def req(self, method, path, body=None, raw=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        c.request(method, path, body=data, headers={"Host": f"127.0.0.1:{self.port}", **({"Content-Type": "application/json"} if data is not None else {})})
        r = c.getresponse()
        payload = r.read()
        c.close()
        self.seen.append(payload)
        if raw:
            return r.status, payload, r
        try:
            return r.status, json.loads(payload), r
        except ValueError:
            return r.status, payload, r

    def events(self, body):
        status, payload, r = self.req("POST", "/api/agent", body, raw=True)
        return status, [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()], r

    # -- agent stream

    def test_a_question_streams_progress_then_the_answer(self):
        client = self.use_client(reply([call("t1", "analyze", analysis="brands"), call("t2", "describe_data")], "tool_use"),
                                 reply([text("Gul Ahmed leads (n = 12).")]))
        status, ev, r = self.events({"message": "which brand is best?", "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
                                     "platform": "instagram", "brand": ""})
        self.assertEqual(status, 200)
        self.assertIn("ndjson", r.getheader("Content-Type"))
        self.assertIsNone(r.getheader("Content-Length"))            # streamed, so the page can show progress
        kinds = [e["type"] for e in ev]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(kinds[-2], "answer")
        self.assertEqual([e["status"] for e in ev if e["type"] == "step"], ["running", "running", "ok", "ok"])
        evidence = next(e for e in ev if e["type"] == "evidence")
        self.assertEqual(evidence["kind"], "brands")
        self.assertTrue(evidence["reply"]["blocks"])
        answer = next(e for e in ev if e["type"] == "answer")
        self.assertEqual(answer["text"], "Gul Ahmed leads (n = 12).")
        self.assertGreater(answer["usage"]["usd"], 0)
        first_user = client.beta.messages.calls[0]["messages"][-1]["content"]
        self.assertIn("platform=instagram", first_user)             # the page's filter reached the model

    def test_a_proposed_update_arrives_as_an_action(self):
        self.use_client(reply([call("t1", "request_data_update", source="youtube", reason="Newer videos would help.")], "tool_use"),
                        reply([text("Approve the update to refresh.")]))
        _, ev, _ = self.events({"message": "is this up to date?"})
        action = next(e for e in ev if e["type"] == "action")["action"]
        self.assertEqual((action["kind"], action["source"]), ("collect", "youtube"))
        self.assertEqual(self.app.jobs.status()["state"], "idle")    # proposing did not start anything

    def test_without_a_key_the_page_is_told_to_fall_back(self):
        self.app.agent._factory = lambda: (_ for _ in ()).throw(RuntimeError("no key"))
        status, body, _ = self.req("POST", "/api/agent", {"message": "hi"})
        self.assertEqual((status, body["code"]), (503, "unavailable"))
        self.assertIn("ANTHROPIC_API_KEY", body["error"])

    def test_the_daily_limit_arrives_as_an_error_event(self):
        self.use_client(reply([text("never")]))
        self.app.ledger.record("claude-opus-5-5", type("U", (), {"input_tokens": 3_000_000, "output_tokens": 0,
                                                                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})())
        try:
            status, ev, _ = self.events({"message": "hi"})
        finally:
            self.app.ledger.path.unlink(missing_ok=True)
            self.app.ledger._mem = {}
        self.assertEqual(status, 200)
        errors = [e for e in ev if e["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("AGENT_DAILY_USD_LIMIT", errors[0]["message"])

    def test_bad_requests(self):
        self.use_client(reply([text("x")]))
        for body in ({"message": 5}, {"message": "hi", "mode": "hack"}, {"message": "hi", "history": "nope"},
                     {"message": "hi", "history": [{}] * 41}, {"message": "hi", "platform": "myspace"}):
            self.assertEqual(self.req("POST", "/api/agent", body)[0], 400, body)

    def test_rate_limit(self):
        server.AGENT_PER_MINUTE = 2
        for _ in range(2):
            self.use_client(reply([text("ok")]))
            self.assertEqual(self.req("POST", "/api/agent", {"message": "hi"}, raw=True)[0], 200)
        self.use_client(reply([text("ok")]))
        self.assertEqual(self.req("POST", "/api/agent", {"message": "hi"})[0], 429)

    def test_the_agent_endpoint_refuses_other_sites(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/api/agent", body=b'{"message":"hi"}', headers={"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json", "Origin": "http://evil.example"})
        self.assertEqual(c.getresponse().status, 403)

    # -- status, setup and jobs

    def test_status_reports_presence_never_values(self):
        os.environ["ANTHROPIC_API_KEY"] = SECRET
        os.environ["YOUTUBE_API_KEY"] = SECRET
        os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"] = "17841", SECRET
        try:
            self.use_client(reply([text("x")]))
            status, body, _ = self.req("GET", "/api/status")
        finally:
            for k in ("ANTHROPIC_API_KEY", "YOUTUBE_API_KEY", "IG_USER_ID", "IG_ACCESS_TOKEN"):
                os.environ.pop(k, None)
        self.assertEqual(status, 200)
        self.assertTrue(body["agent"]["available"])
        self.assertEqual(body["agent"]["model"], "claude-opus-5-5")
        self.assertTrue(body["integrations"]["youtube"]["configured"] and body["integrations"]["instagram"]["configured"])
        self.assertFalse(body["integrations"]["facebook"]["configured"])
        self.assertNotIn(SECRET.encode(), b"".join(self.seen))
        self.assertNotIn(b"17841", b"".join(self.seen))

    def test_recheck_picks_up_a_key_added_while_running(self):
        server.ENV_FILE.write_text(f"ANTHROPIC_API_KEY={SECRET}\nAGENT_MODEL=claude-sonnet-5-5\n", encoding="utf-8")
        try:
            self.app.agent._factory = lambda: fake_client(reply([text("x")]))
            status, body, _ = self.req("POST", "/api/recheck", {})
            self.assertEqual(status, 200)
            self.assertEqual(body["agent"]["model"], "claude-sonnet-5-5")
            self.assertTrue(body["agent"]["available"])
            self.assertNotIn(SECRET.encode(), b"".join(self.seen))
        finally:
            server.ENV_FILE.unlink(missing_ok=True)
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("AGENT_MODEL", None)
            self.app.agent.reload()

    def test_a_job_needs_the_user_to_start_it_and_runs_one_at_a_time(self):
        gate = threading.Event()
        self.app.jobs = JobRunner(server.RAW_DIR, {"youtube": lambda raw: (gate.wait(5), 0)[1]}, {})
        status, body, _ = self.req("POST", "/api/jobs/start", {"source": "youtube"})
        self.assertEqual((status, body["state"]), (200, "running"))
        status, body, _ = self.req("POST", "/api/jobs/start", {"source": "youtube"})
        self.assertEqual(status, 409)
        self.assertIn("already running", body["error"])
        gate.set()
        self.app.jobs.wait()
        status, body, _ = self.req("GET", "/api/jobs")
        self.assertEqual((status, body["state"], body["exit_code"]), (200, "done", 0))

    def test_jobs_refuse_unknown_and_unconfigured_sources(self):
        self.app.jobs = JobRunner(server.RAW_DIR, {"youtube": lambda raw: 0, "instagram": lambda raw: 0}, {})
        status, body, _ = self.req("POST", "/api/jobs/start", {"source": "tiktok"})
        self.assertEqual(status, 409)
        status, body, _ = self.req("POST", "/api/jobs/start", {"source": "instagram"})
        self.assertEqual(status, 409)
        self.assertIn("IG_ACCESS_TOKEN", body["error"])

    def test_dropping_the_connection_mid_stream_does_not_hurt_the_server(self):
        self.use_client(reply([call("t1", "describe_data")], "tool_use"), reply([text("done")]))
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/api/agent", body=json.dumps({"message": "hi"}).encode(), headers={"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"})
        c.sock.close()
        self.assertEqual(self.req("GET", "/api/status")[0], 200)


if __name__ == "__main__":
    unittest.main()
