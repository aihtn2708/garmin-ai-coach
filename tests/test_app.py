"""Run with:  python -m unittest discover -s tests -v   (no network, no keys needed)"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request

from coach import privacy, providers
from coach.agent import run_turn
from coach.demo import DemoGarmin
from coach.garmin import GarminClient, GarminError
from coach.ledger import Ledger
from coach.net import Egress, EgressBlocked, HttpError
from coach.server import AppState, make_handler
from coach.tools import Toolbox, ToolError

from . import mocks


def toolbox(categories=None, level="balanced"):
    cats = categories or {"activities", "sleep", "heart", "stress_energy", "daily", "profile"}
    return Toolbox(DemoGarmin(), cats, level, privacy.AliasMap())


class PrivacyTests(unittest.TestCase):
    RAW = {
        "activityId": 987654321, "ownerId": 55, "userProfilePK": 55, "displayName": "jane.doe",
        "ownerFullName": "Jane Doe", "activityName": "Riverside Run", "locationName": "Springfield",
        "startLatitude": 1.23, "startLongitude": 4.56, "deviceId": 3, "profileImageUrlLarge": "https://x/y.png",
        "distance": 10000.0, "averageHR": 150, "valid": True, "activityType": {"typeId": 1, "typeKey": "running"},
        "userData": {"birthDate": "1990-01-01", "weight": 70000.0, "email": "j@example.com"},
        "geoPolylineDTO": {"polyline": [[1, 2]]},
    }

    def test_balanced_removes_identity_and_location(self):
        out = privacy.scrub(self.RAW, "balanced", privacy.AliasMap())
        text = json.dumps(out)
        for leaked in ("987654321", "jane", "Jane", "1.23", "4.56", "y.png", "1990", "example.com", "polyline"):
            self.assertNotIn(leaked, text)
        self.assertEqual(out["activity"], "A1")
        self.assertEqual(out["activityName"], "Riverside Run")
        self.assertEqual(out["distance"], 10000.0)
        self.assertTrue(out["valid"])
        self.assertIn("ageYears", out["userData"])
        self.assertEqual(out["activityType"], {"typeKey": "running"})

    def test_strict_also_removes_free_text(self):
        text = json.dumps(privacy.scrub(self.RAW, "strict", privacy.AliasMap()))
        self.assertNotIn("Riverside", text)
        self.assertNotIn("Springfield", text)
        self.assertIn("running", text)

    def test_alias_round_trip(self):
        a = privacy.AliasMap()
        self.assertEqual(a.alias(111), "A1")
        self.assertEqual(a.alias(222), "A2")
        self.assertEqual(a.alias(111), "A1")
        self.assertEqual(a.resolve("a2"), "222")
        self.assertIsNone(a.resolve("A9"))

    def test_compact_tables_and_series(self):
        rows = [{"calendarDate": f"2026-01-{d:02d}", "x": d, "same": 1, "none": None} for d in range(1, 31)]
        table = privacy.compact(rows)
        self.assertEqual(table["columns"], ["calendarDate", "x"])
        self.assertEqual(len(table["rows"]), 30)
        self.assertEqual(table["same_for_all_rows"], {"same": 1})
        series = privacy.compact({"v": list(range(1000))})
        self.assertEqual(series["v"]["_summary_of"], 1000)
        self.assertLessEqual(len(series["v"]["every_nth_sample"]["values"]), 60)

    def test_compact_respects_budget(self):
        big = [{"calendarDate": f"2026-01-{(d % 28) + 1:02d}", "blob": "z" * 500, "n": d} for d in range(300)]
        self.assertLessEqual(len(json.dumps(privacy.compact(big, 8000))), 8200)


class ToolTests(unittest.TestCase):
    def test_disabled_category_is_not_offered_or_runnable(self):
        tb = toolbox({"sleep"})
        names = {t["name"] for t in tb.specs()}
        self.assertEqual(names, {"get_range", "get_day", "show_chart"})
        metrics = next(t for t in tb.specs() if t["name"] == "get_range")["parameters"]["properties"]["metric"]["enum"]
        self.assertEqual(metrics, ["sleep"])
        with self.assertRaises(ToolError):
            tb.run("get_range", {"metric": "hrv", "start_date": "2026-01-01", "end_date": "2026-01-02"})
        with self.assertRaises(ToolError):
            tb.run("list_activities", {})

    def test_range_validation(self):
        tb = toolbox()
        today = dt.date.today()
        with self.assertRaises(ToolError):
            tb.run("get_range", {"metric": "sleep", "start_date": "../../etc", "end_date": "2026-01-02"})
        with self.assertRaises(ToolError):
            tb.run("get_range", {"metric": "sleep", "start_date": (today - dt.timedelta(days=200)).isoformat(), "end_date": today.isoformat()})
        with self.assertRaises(ToolError):
            tb.run("get_activity", {"activity": "123456"})  # raw IDs are never accepted

    def test_chunked_range_and_grouping(self):
        tb = toolbox()
        today = dt.date.today()
        start = today - dt.timedelta(days=69)
        out, _ = tb.run("get_range", {"metric": "stress", "start_date": start.isoformat(), "end_date": today.isoformat()})
        self.assertEqual(len(out["data"]["rows"]), 70)
        self.assertEqual(tb.calls_this_turn, 3)  # 28-day chunks
        out, _ = tb.run("get_range", {"metric": "sleep", "start_date": start.isoformat(), "end_date": today.isoformat(), "group_by": "month"})
        self.assertTrue(all("period" in row and "avg.sleepTimeSeconds" in row for row in out["data"]))

    def test_activity_alias_flow(self):
        tb = toolbox()
        out, _ = tb.run("list_activities", {"limit": 3})
        self.assertNotIn("activityId", json.dumps(out))
        detail, _ = tb.run("get_activity", {"activity": "A1"})
        self.assertEqual(detail["activity"], "A1")
        self.assertNotIn("Latitude", json.dumps(detail))

    def test_chart_is_sanitised(self):
        tb = toolbox()
        _, event = tb.run("show_chart", {"title": "t", "kind": "pie", "x_labels": ["a", "b"], "series": [{"name": "s", "values": [1, "x", 3]}] * 9})
        self.assertEqual(event["chart"]["kind"], "line")
        self.assertEqual(len(event["chart"]["series"]), 4)
        self.assertEqual(event["chart"]["series"][0]["values"], [1.0, None])


class NetAndGarminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = mocks.start(mocks.GarminMock)
        os.environ["GARMIN_PERSONAL_API_BASE_URL"] = cls.url

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("GARMIN_PERSONAL_API_BASE_URL", None)
        cls.srv.shutdown(); cls.srv.server_close()

    def test_egress_allowlist(self):
        eg = Egress(Ledger())
        with self.assertRaises(EgressBlocked):
            eg.request("GET", "https://example.com/")
        eg.allow("http://example.com", "model")
        with self.assertRaises(EgressBlocked):  # plain HTTP to a remote host
            eg.request("GET", "http://example.com/")

    def test_override_cannot_point_at_other_hosts(self):
        from coach.garmin import DEFAULT_BASE_URL, resolve_base_url

        os.environ["GARMIN_PERSONAL_API_BASE_URL"] = "https://evil.example"
        try:
            self.assertEqual(resolve_base_url(), DEFAULT_BASE_URL)
        finally:
            os.environ["GARMIN_PERSONAL_API_BASE_URL"] = self.url

    def test_bad_key(self):
        with self.assertRaises(GarminError):
            GarminClient("wrong", Egress(Ledger())).connect()

    def test_token_refresh_cache_and_allowlist(self):
        mocks.GarminMock.tokens.clear()
        ledger = Ledger()
        client = GarminClient("good-key", Egress(ledger))
        self.assertGreater(client.connect(), time.time())
        data = client.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": "2026-10-01", "endDate": "2026-10-03"})
        self.assertEqual(len(data), 3)  # first token was rejected once; client refreshed and retried
        self.assertEqual(len(mocks.GarminMock.tokens), 2)
        before = len(ledger.entries())
        client.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": "2026-10-01", "endDate": "2026-10-03"})
        self.assertEqual(len(ledger.entries()), before)  # served from memory cache
        for bad in ("/activity-service/activity/1/../../x", "/upload-service/upload", "/userprofile-service/userprofile/settings/x"):
            with self.assertRaises(GarminError):
                client.get(bad)
        self.assertFalse(any("good-key" in json.dumps(e) for e in ledger.entries()))


class ProviderAndAgentTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: dict(v) for k, v in providers.PROVIDERS.items()}

    def tearDown(self):
        providers.PROVIDERS.clear()
        providers.PROVIDERS.update(self._saved)

    def _run(self, provider, key, model, question):
        events = []
        ledger = Ledger()
        answer = run_turn(provider=provider, key=key, model=model, options={}, egress=Egress(ledger),
                          toolbox=toolbox(), history=[("earlier q", "earlier a")], user_text=question, emit=events.append)
        return answer, events, ledger

    def test_openai_format_full_loop_with_thought_signature(self):
        srv, url = mocks.start(mocks.OpenAIMock)
        providers.PROVIDERS["gemini"]["base_url"] = url
        models = providers.list_models("gemini", "k", Egress(Ledger()))
        self.assertEqual([m["id"] for m in models], ["gemini-9.9-flash"])
        answer, events, ledger = self._run("gemini", "secret-key-1", "gemini-9.9-flash", "How did I sleep?")
        self.assertIn("7 h 12 min", answer)
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds.count("tool"), 2)
        chart = next(e for e in events if e["type"] == "chart")["chart"]
        self.assertEqual(len(chart["x_labels"]), 14)
        self.assertEqual(events[-1], {"type": "usage", "tokens_in": 5400, "tokens_out": 310})
        posts = [b for (m, _, b) in srv.seen if m == "POST"]
        self.assertEqual(posts[0]["messages"][1:3], [{"role": "user", "content": "earlier q"}, {"role": "assistant", "content": "earlier a"}])
        self.assertEqual(posts[1]["messages"][-1]["name"], "get_range")  # Gemini wants the tool name echoed
        dump = json.dumps([e for e in ledger.entries()] + [ledger.detail(e["id"]) for e in ledger.entries()])
        self.assertNotIn("secret-key-1", dump)
        self.assertNotIn("userProfilePK", dump)
        srv.shutdown(); srv.server_close()

    def test_openrouter_sends_data_collection_deny(self):
        srv, url = mocks.start(mocks.OpenAIMock)
        providers.PROVIDERS["openrouter"]["base_url"] = url
        chat = providers.Chat("openrouter", "k", "m", Egress(Ledger()), "sys", [], "hi", {"block_training": True})
        chat.send([])
        self.assertEqual(srv.seen[-1][2]["provider"], {"data_collection": "deny"})
        srv.shutdown(); srv.server_close()

    def test_anthropic_format(self):
        srv, url = mocks.start(mocks.AnthropicMock)
        providers.PROVIDERS["anthropic"]["base_url"] = url
        with self.assertRaises(providers.ProviderError):
            providers.list_models("anthropic", "nope", Egress(Ledger()))
        models = providers.list_models("anthropic", "sk-ant-test", Egress(Ledger()))
        self.assertEqual(models[0]["id"], "claude-haiku-9")
        answer, events, _ = self._run("anthropic", "sk-ant-test", "claude-haiku-9", "How much did I run?")
        self.assertEqual(answer, "You ran three times.")
        second = srv.seen[-1][2]
        self.assertEqual(second["messages"][-2]["content"][1]["type"], "tool_use")
        self.assertIn("system", second)
        srv.shutdown(); srv.server_close()


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer

        cls.model_srv, model_url = mocks.start(mocks.OpenAIMock)
        cls._saved = dict(providers.PROVIDERS["ollama"])
        providers.PROVIDERS["ollama"]["base_url"] = model_url + "/v1"
        cls.state = AppState()
        cls.token = "test-token"
        cls.stop = threading.Event()
        probe = ThreadingHTTPServer(("127.0.0.1", 0), None)
        cls.port = probe.server_address[1]
        probe.server_close()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), make_handler(cls.state, cls.token, cls.port, cls.stop))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        providers.PROVIDERS["ollama"] = cls._saved
        cls.httpd.shutdown(); cls.httpd.server_close()
        cls.model_srv.shutdown(); cls.model_srv.server_close()

    def call(self, path, body=None, headers=None, token=True):
        hdrs = {"Host": f"127.0.0.1:{self.port}"}
        if token:
            hdrs["X-Session-Token"] = self.token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_1_guards(self):
        status, headers, _ = self.call("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn(b'data-mode="local"', self.call("/", token=False)[2])
        self.assertEqual(self.call("/core.js", token=False)[0], 200)
        self.assertEqual(self.call("/api/state", token=False)[0], 401)
        self.assertEqual(self.call("/api/state", headers={"X-Session-Token": "wrong"}, token=False)[0], 401)
        self.assertEqual(self.call("/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("/api/wipe", {}, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("/api/wipe", {}, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("/../coach/server.py", token=False)[0], 401)
        self.assertEqual(self.call("/api/chat", {"message": "hi"})[0], 400)  # nothing connected yet

    def test_2_full_session(self):
        self.assertEqual(self.call("/api/garmin/connect", {"demo": True})[0], 200)
        status, _, raw = self.call("/api/provider/models", {"provider": "ollama"})
        self.assertEqual(status, 200, raw)
        self.assertEqual(self.call("/api/provider/select", {"provider": "ollama", "model": "mock-tools-model"})[0], 200)
        self.assertEqual(self.call("/api/settings", {"level": "strict"})[0], 200)
        status, headers, raw = self.call("/api/chat", {"message": "How did I sleep?"})
        self.assertEqual(status, 200)
        events = [json.loads(line) for line in raw.decode().splitlines() if line]
        self.assertEqual(events[-1]["type"], "answer")
        self.assertTrue(any(e["type"] == "chart" for e in events))
        state = json.loads(self.call("/api/state")[2])
        self.assertEqual(state["turns"], 1)
        self.assertEqual(state["level"], "strict")
        entries = json.loads(self.call("/api/ledger")[2])["entries"]
        self.assertTrue(all(e["kind"] == "local-model" for e in entries))
        detail = json.loads(self.call(f"/api/ledger/detail?id={entries[-1]['id']}")[2])["detail"]
        self.assertEqual(detail["messages"][-1]["role"], "tool")
        self.assertEqual(self.call("/api/wipe", {})[0], 200)
        state = json.loads(self.call("/api/state")[2])
        self.assertFalse(state["garmin"]["connected"])
        self.assertEqual(state["turns"], 0)
        self.assertEqual(json.loads(self.call("/api/ledger")[2])["entries"], [])


if __name__ == "__main__":
    unittest.main()
