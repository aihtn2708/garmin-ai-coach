"""Loopback stand-ins for Garmin and for model providers. Test use only."""

from __future__ import annotations

import base64
import datetime as dt
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from coach.demo import DemoGarmin


def _jwt(exp: float) -> str:
    enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc({'exp': exp})}.sig"


class _Base(BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:
        pass

    def reply(self, status: int, body: Any, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")  # lets the browser build call these mocks
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802  (CORS preflight)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", self.headers.get("Access-Control-Request-Headers", "*"))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def body(self) -> Any:
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n) or b"{}")


def start(handler: type, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    srv.daemon_threads = True
    srv.seen = []  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class GarminMock(_Base):
    """Accepts key 'good-key'. The first bearer token it hands out is rejected
    once, to exercise the client's refresh path."""

    demo = DemoGarmin()
    tokens: list[str] = []
    reject_first = True

    def do_POST(self) -> None:  # noqa: N802
        self.server.seen.append(("POST", self.path))
        if self.headers.get("Authorization") != "ApiKey good-key":
            return self.reply(401, {"message": "bad key"})
        token = _jwt(time.time() + 86400) + str(len(GarminMock.tokens))
        GarminMock.tokens.append(token)
        self.reply(200, token.encode(), "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        self.server.seen.append(("GET", self.path))
        auth = self.headers.get("Authorization", "")
        if not GarminMock.tokens or auth != f"Bearer {GarminMock.tokens[-1]}":
            return self.reply(401, {"message": "bad token"})
        if GarminMock.reject_first and len(GarminMock.tokens) == 1:
            return self.reply(401, {"message": "expired"})
        from urllib.parse import parse_qs, urlsplit

        url = urlsplit(self.path)
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.reply(200, self.demo.get(url.path, params))


def _dates() -> tuple[str, str]:
    today = dt.date.today()
    return (today - dt.timedelta(days=13)).isoformat(), today.isoformat()


class OpenAIMock(_Base):
    """Scripted model: asks for sleep, then draws a chart, then answers.
    Insists that its thought signature comes back untouched."""

    def do_GET(self) -> None:  # noqa: N802
        self.server.seen.append(("GET", self.path, None))
        self.reply(200, {"data": [{"id": "models/gemini-9.9-flash"}, {"id": "mock-tools-model"}, {"id": "text-embedding-1"}]})

    def do_POST(self) -> None:  # noqa: N802
        body = self.body()
        self.server.seen.append(("POST", self.path, body))
        msgs = body["messages"]
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        if body.get("tool_choice") == "none":
            return self.reply(200, {"choices": [{"message": {"role": "assistant", "content": "Forced final answer."}}]})
        if not tool_msgs:
            start, end = _dates()
            return self.reply(200, {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_a", "type": "function",
                "function": {"name": "get_range", "arguments": json.dumps({"metric": "sleep", "start_date": start, "end_date": end})},
                "extra_content": {"google": {"thought_signature": "SIG123"}},
            }]}}], "usage": {"prompt_tokens": 900, "completion_tokens": 30}})
        echoed = [m for m in msgs if m["role"] == "assistant" and m.get("tool_calls")]
        if echoed[0]["tool_calls"][0].get("extra_content", {}).get("google", {}).get("thought_signature") != "SIG123":
            return self.reply(400, {"error": {"message": "Function call is missing a thought_signature"}})
        if len(tool_msgs) == 1:
            data = json.loads(tool_msgs[0]["content"])
            table = data["data"]
            col = table["columns"].index("sleepTimeSeconds")
            day = table["columns"].index("calendarDate")
            return self.reply(200, {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_b", "type": "function",
                "function": {"name": "show_chart", "arguments": json.dumps({
                    "title": "Sleep per night, last 14 days", "kind": "bar", "y_label": "hours",
                    "x_labels": [r[day][5:] for r in table["rows"]],
                    "series": [{"name": "Sleep", "values": [round(r[col] / 3600, 2) for r in table["rows"]]}],
                })},
            }]}}], "usage": {"prompt_tokens": 2100, "completion_tokens": 160}})
        self.reply(200, {"choices": [{"message": {"role": "assistant", "content": (
            "## Sleep, last 14 days\n\nYou averaged **7 h 12 min** a night.\n\n"
            "- Shortest night: 6 h 05 min\n- Longest night: 8 h 01 min\n\n"
            "| Week | Average |\n|---|---|\n| This week | 7 h 20 min |\n| Last week | 7 h 04 min |\n\n"
            "Hard training days were followed by slightly shorter nights. [ignore](http://evil.example/x)"
        )}}], "usage": {"prompt_tokens": 2400, "completion_tokens": 120}})


class AnthropicMock(_Base):
    def do_GET(self) -> None:  # noqa: N802
        if self.headers.get("x-api-key") != "sk-ant-test":
            return self.reply(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
        self.reply(200, {"data": [{"id": "claude-big-9", "display_name": "Claude Big 9"}, {"id": "claude-haiku-9", "display_name": "Claude Haiku 9"}]})

    def do_POST(self) -> None:  # noqa: N802
        body = self.body()
        self.server.seen.append(("POST", self.path, body))
        assert self.headers.get("anthropic-version") == "2023-06-01"
        results = [m for m in body["messages"] if m["role"] == "user" and isinstance(m["content"], list)]
        if not results:
            return self.reply(200, {"content": [
                {"type": "text", "text": "Let me look."},
                {"type": "tool_use", "id": "tu_1", "name": "list_activities", "input": {"limit": 5}},
            ], "stop_reason": "tool_use", "usage": {"input_tokens": 800, "output_tokens": 40}})
        assert results[0]["content"][0]["tool_use_id"] == "tu_1"
        self.reply(200, {"content": [{"type": "text", "text": "You ran three times."}], "stop_reason": "end_turn",
                         "usage": {"input_tokens": 1500, "output_tokens": 20}})
