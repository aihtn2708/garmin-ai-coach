"""Local web server: serves the UI and a small JSON API on 127.0.0.1 only.

Protections, in the order a request meets them:
  1. The socket is bound to the loopback interface; other machines cannot connect.
  2. The Host header must be this server's own address (blocks DNS rebinding).
  3. API calls must carry the random session token printed at start-up, so
     other web pages and other local programs cannot use the API.
  4. POSTs must be JSON from this page's own origin, and are size-limited.
  5. Responses carry a Content-Security-Policy that forbids third-party
     scripts, styles, images, frames and network connections.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__, keystore, providers
from .agent import run_turn
from .demo import DemoGarmin
from .garmin import KEY_PAGE, GarminClient, GarminError
from .ledger import Ledger
from .net import Egress, EgressBlocked
from .privacy import LEVELS, AliasMap
from .providers import ProviderError
from .tools import CATEGORIES, Toolbox

WEB_DIR = Path(__file__).resolve().parent.parent / "docs"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/core.js": ("core.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
for _subset in ("latin", "latin-ext", "vietnamese"):
    for _style in ("600-normal", "700-italic"):
        _name = f"fonts/barlow-condensed-{_subset}-{_style}.woff2"
        STATIC[f"/{_name}"] = (_name, "font/woff2")
MAX_BODY = 64 * 1024
MAX_MESSAGE_CHARS = 4000
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class AppState:
    def __init__(self) -> None:
        self.ledger = Ledger()
        self.egress = Egress(self.ledger)
        self.garmin: Any = None
        self.aliases = AliasMap()
        self.keys: dict[str, str] = {}
        self.provider: str | None = None
        self.model: str | None = None
        self.options: dict[str, Any] = {"block_training": True}
        self.categories = {c for c, (_, _, on) in CATEGORIES.items() if on}
        self.level = "balanced"
        self.history: list[tuple[str, str]] = []
        self.remember = False
        self.chat_lock = threading.Lock()
        self.lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        saved = [p for p in providers.PROVIDERS if keystore.load(f"provider:{p}")] if keystore.AVAILABLE else []
        return {
            "version": __version__,
            "mode": "local",
            "garmin": {
                "connected": self.garmin is not None,
                "demo": bool(self.garmin and self.garmin.is_demo),
                "token_expires_at": getattr(self.garmin, "token_expires_at", 0) or 0,
                "saved_key": bool(keystore.load("garmin")) if keystore.AVAILABLE else False,
                "key_page": KEY_PAGE,
            },
            "provider": {
                "name": self.provider,
                "model": self.model,
                "has_key": sorted(self.keys),
                "saved": saved,
            },
            "options": self.options,
            "categories": [
                {"id": c, "label": label, "description": desc, "on": c in self.categories}
                for c, (label, desc, _) in CATEGORIES.items()
            ],
            "level": self.level,
            "providers": providers.public_info(),
            "keystore": {"available": keystore.AVAILABLE, "remember": self.remember},
            "busy": self.chat_lock.locked(),
            "turns": len(self.history),
            "allowed_hosts": self.egress.allowed_hosts(),
        }

    def wipe(self) -> None:
        if self.garmin is not None:
            self.garmin.forget()
        self.garmin = None
        self.keys.clear()
        self.provider = self.model = None
        self.history.clear()
        self.aliases.clear()
        self.ledger.clear()
        self.egress.reset()
        self.remember = False
        keystore.delete("garmin")
        for name in providers.PROVIDERS:
            keystore.delete(f"provider:{name}")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def make_handler(state: AppState, token: str, port: int, stop: threading.Event) -> type[BaseHTTPRequestHandler]:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "GarminAICoach"
        sys_version = ""

        def log_message(self, *args: Any) -> None:  # no request logs on disk or console
            pass

        # -- plumbing ---------------------------------------------------------------

        def _headers(self, status: int, ctype: str, length: int | None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _json(self, obj: Any, status: int = 200) -> None:
            data = json.dumps(obj, default=str).encode("utf-8")
            self._headers(status, "application/json; charset=utf-8", len(data))
            self.wfile.write(data)

        def _guard(self, api: bool) -> None:
            if self.headers.get("Host", "") not in hosts:
                raise ApiError(403, "Unexpected Host header.")
            origin = self.headers.get("Origin")
            if origin and origin not in origins:
                raise ApiError(403, "Cross-origin requests are not allowed.")
            if api:
                sent = self.headers.get("X-Session-Token", "")
                if not hmac.compare_digest(sent.encode(), token.encode()):
                    raise ApiError(401, "Missing or wrong session token. Open the link printed in the terminal.")

        def _body(self) -> dict[str, Any]:
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ApiError(415, "JSON required.")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ApiError(400, "Bad Content-Length.") from None
            if length > MAX_BODY:
                raise ApiError(413, "Request too large.")
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                raise ApiError(400, "Invalid JSON.") from None
            if not isinstance(data, dict):
                raise ApiError(400, "Expected a JSON object.")
            return data

        # -- routes -----------------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            url = urllib.parse.urlsplit(self.path)
            try:
                if url.path in STATIC:
                    self._guard(api=False)
                    name, ctype = STATIC[url.path]
                    data = (WEB_DIR / name).read_bytes()
                    if name == "index.html":  # tell the page it has a local server behind it
                        data = data.replace(b'<html lang="en">', b'<html lang="en" data-mode="local">', 1)
                    self._headers(200, ctype, len(data))
                    self.wfile.write(data)
                    return
                self._guard(api=True)
                query = urllib.parse.parse_qs(url.query)
                if url.path == "/api/state":
                    self._json(state.snapshot())
                elif url.path == "/api/ledger":
                    since = int(query.get("since", ["0"])[0])
                    self._json({"entries": state.ledger.entries(since)})
                elif url.path == "/api/ledger/detail":
                    entry_id = int(query.get("id", ["0"])[0])
                    self._json({"detail": state.ledger.detail(entry_id)})
                else:
                    raise ApiError(404, "Not found.")
            except ApiError as exc:
                self._json({"error": exc.message}, exc.status)
            except (ValueError, OSError):
                self._json({"error": "Bad request."}, 400)

        def do_POST(self) -> None:  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            try:
                self._guard(api=True)
                body = self._body()
                if path == "/api/chat":
                    self._chat(body)
                    return
                route = ROUTES.get(path)
                if route is None:
                    raise ApiError(404, "Not found.")
                result = route(body)
                self._json(result if result is not None else state.snapshot())
            except ApiError as exc:
                self._json({"error": exc.message}, exc.status)
            except (GarminError, ProviderError, EgressBlocked) as exc:
                self._json({"error": str(exc)}, 400)
            except OSError as exc:
                self._json({"error": f"Network problem: {exc}"}, 502)

        # -- chat (streamed as one JSON object per line) ----------------------------

        def _chat(self, body: dict[str, Any]) -> None:
            message = str(body.get("message", "")).strip()
            if not message:
                raise ApiError(400, "Type a question first.")
            if len(message) > MAX_MESSAGE_CHARS:
                raise ApiError(400, f"Keep questions under {MAX_MESSAGE_CHARS} characters.")
            if state.garmin is None:
                raise ApiError(400, "Connect Garmin (or load the sample data) first.")
            if not state.provider or not state.model:
                raise ApiError(400, "Choose an AI model first.")
            info = providers.PROVIDERS[state.provider]
            key = state.keys.get(state.provider, "")
            if info["needs_key"] and not key:
                raise ApiError(400, f"Add your {info['label']} API key first.")
            if not state.chat_lock.acquire(blocking=False):
                raise ApiError(409, "Still working on the previous question.")

            alive = True

            def emit(event: dict[str, Any]) -> None:
                nonlocal alive
                if not alive:
                    return
                try:
                    self.wfile.write(json.dumps(event, default=str).encode("utf-8") + b"\n")
                    self.wfile.flush()
                except OSError:
                    alive = False

            try:
                self._headers(200, "application/x-ndjson; charset=utf-8", None)
                toolbox = Toolbox(state.garmin, set(state.categories), state.level, state.aliases)
                try:
                    answer = run_turn(
                        provider=state.provider,
                        key=key,
                        model=state.model,
                        options=dict(state.options),
                        egress=state.egress,
                        toolbox=toolbox,
                        history=list(state.history),
                        user_text=message,
                        emit=emit,
                    )
                    state.history.append((message, answer))
                    emit({"type": "answer", "text": answer})
                except (ProviderError, GarminError, EgressBlocked) as exc:
                    emit({"type": "error", "text": str(exc)})
                except Exception as exc:  # keep the server alive; report without details
                    emit({"type": "error", "text": f"Something went wrong ({type(exc).__name__})."})
            finally:
                state.chat_lock.release()

    # -- route handlers --------------------------------------------------------------

    def garmin_connect(body: dict[str, Any]) -> None:
        with state.lock:
            if body.get("demo"):
                client: Any = DemoGarmin()
            else:
                key = str(body.get("api_key") or "").strip()
                if body.get("use_saved"):
                    key = keystore.load("garmin") or ""
                    if not key:
                        raise ApiError(400, "No saved Garmin key found.")
                client = GarminClient(key, state.egress)
                client.connect()
                if state.remember:
                    keystore.save("garmin", key)
            if state.garmin is not None:
                state.garmin.forget()
            state.garmin = client
            state.aliases.clear()

    def garmin_disconnect(body: dict[str, Any]) -> None:
        with state.lock:
            if state.garmin is not None:
                state.garmin.forget()
            state.garmin = None
            state.aliases.clear()
            keystore.delete("garmin")

    def provider_models(body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("provider", ""))
        if name not in providers.PROVIDERS:
            raise ApiError(400, "Unknown provider.")
        key = str(body.get("api_key") or "").strip()
        if not key:
            key = state.keys.get(name) or (keystore.load(f"provider:{name}") or "")
        if providers.PROVIDERS[name]["needs_key"] and not key:
            raise ApiError(400, "Paste an API key first.")
        models = providers.list_models(name, key, state.egress)
        if not models:
            raise ApiError(400, "This account has no chat models that support tools.")
        with state.lock:
            if key:
                state.keys[name] = key
                if state.remember:
                    keystore.save(f"provider:{name}", key)
        return {"models": models, "state": state.snapshot()}

    def provider_select(body: dict[str, Any]) -> None:
        name = str(body.get("provider", ""))
        model = str(body.get("model", "")).strip()
        if name not in providers.PROVIDERS or not model or len(model) > 200:
            raise ApiError(400, "Choose a provider and a model.")
        with state.lock:
            state.provider, state.model = name, model

    def settings(body: dict[str, Any]) -> None:
        with state.lock:
            if "categories" in body:
                chosen = body["categories"]
                if not isinstance(chosen, list):
                    raise ApiError(400, "categories must be a list.")
                state.categories = {c for c in chosen if c in CATEGORIES}
            if "level" in body:
                if body["level"] not in LEVELS:
                    raise ApiError(400, "Unknown privacy level.")
                state.level = body["level"]
            if "block_training" in body:
                state.options["block_training"] = bool(body["block_training"])

    def remember(body: dict[str, Any]) -> None:
        if not keystore.AVAILABLE:
            raise ApiError(400, "No system keychain is available. Install the 'keyring' package.")
        with state.lock:
            state.remember = bool(body.get("on"))
            if state.remember:
                if isinstance(state.garmin, GarminClient):
                    keystore.save("garmin", state.garmin._key)  # noqa: SLF001
                for name, key in state.keys.items():
                    keystore.save(f"provider:{name}", key)
            else:
                keystore.delete("garmin")
                for name in providers.PROVIDERS:
                    keystore.delete(f"provider:{name}")

    def chat_reset(body: dict[str, Any]) -> None:
        state.history.clear()

    def wipe(body: dict[str, Any]) -> None:
        with state.lock:
            state.wipe()

    def quit_app(body: dict[str, Any]) -> dict[str, Any]:
        threading.Timer(0.3, stop.set).start()  # let this response reach the browser first
        return {"ok": True}

    ROUTES = {
        "/api/garmin/connect": garmin_connect,
        "/api/garmin/disconnect": garmin_disconnect,
        "/api/provider/models": provider_models,
        "/api/provider/select": provider_select,
        "/api/settings": settings,
        "/api/keys/remember": remember,
        "/api/chat/reset": chat_reset,
        "/api/wipe": wipe,
        "/api/quit": quit_app,
    }
    return Handler


def serve(port: int = 8765, open_browser: bool = True) -> None:
    state = AppState()
    token = secrets.token_urlsafe(32)
    stop = threading.Event()
    httpd = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), make_handler(state, token, candidate, stop))
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"No free port between {port} and {port + 19}.")
    httpd.daemon_threads = True

    # The token rides in the URL fragment, which browsers never send to a server.
    url = f"http://127.0.0.1:{port}/#t={token}"
    print(f"\n  Garmin AI Coach {__version__} is running on this computer only.\n")
    print(f"  Open:  {url}\n")
    print("  Keys and data stay in this program's memory. Press Ctrl+C to quit and forget them.\n")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if state.garmin is not None:
            state.garmin.forget()
        state.keys.clear()
        httpd.shutdown()
        print("\n  Stopped. Keys and data in memory are gone.\n")
