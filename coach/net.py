"""The only place in the app that opens a network connection.

Every outbound request goes through ``Egress.request``. It enforces a host
allowlist, refuses plain HTTP except to this computer, refuses redirects, and
writes a ledger entry. Request headers (where API keys travel) are never
logged.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .ledger import Ledger

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class EgressBlocked(Exception):
    """The destination is not on the allowlist."""


class HttpError(Exception):
    def __init__(self, status: int, body: str, url: str, headers: Any = None) -> None:
        super().__init__(f"HTTP {status} from {urllib.parse.urlsplit(url).hostname}")
        self.status = status
        self.body = body
        self.url = url
        self.headers = headers or {}

    def message(self) -> str:
        """Best-effort human-readable message from a JSON error body."""
        try:
            data = json.loads(self.body)
        except ValueError:
            return self.body.strip()[:300]
        if isinstance(data, list) and data:
            data = data[0]
        if isinstance(data, dict):
            err = data.get("error", data)
            if isinstance(err, dict):
                return str(err.get("message") or err.get("type") or err)[:300]
            return str(err)[:300]
        return str(data)[:300]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None  # a 3xx becomes an HttpError; we never follow it


class Egress:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self._allowed: dict[str, str] = {}
        self._opener = urllib.request.build_opener(
            _NoRedirect,
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )

    def allow(self, base_url: str, kind: str) -> None:
        self._allowed[_host_of(base_url)] = kind

    def allowed_hosts(self) -> dict[str, str]:
        return dict(self._allowed)

    def reset(self, keep_kinds: tuple[str, ...] = ()) -> None:
        self._allowed = {h: k for h, k in self._allowed.items() if k in keep_kinds}

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: Any = None,
        timeout: float = 90.0,
        note: str = "",
        keep_detail: bool = False,
    ) -> tuple[int, bytes]:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        kind = self._allowed.get(host)
        if kind is None:
            raise EgressBlocked(f"Blocked request to {host!r}: not on the allowlist")
        if parts.scheme != "https" and host not in LOOPBACK_HOSTS:
            raise EgressBlocked(f"Blocked request to {host!r}: HTTPS is required")

        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        hdrs.setdefault("User-Agent", "garmin-ai-coach/0.1 (local personal tool)")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)

        path = parts.path + (f"?{parts.query}" if parts.query else "")
        started = time.time()
        status: int | str = "error"
        payload = b""
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                status = resp.status
                payload = resp.read()
                return resp.status, payload
        except urllib.error.HTTPError as exc:
            status = exc.code
            payload = exc.read() or b""
            raise HttpError(
                exc.code, payload.decode("utf-8", "replace"), url, exc.headers
            ) from None
        finally:
            self.ledger.record(
                kind=kind,
                host=host,
                method=method,
                path=path,
                bytes_out=len(data or b""),
                bytes_in=len(payload),
                status=status,
                ms=int((time.time() - started) * 1000),
                note=note,
                detail=body if keep_detail else None,
            )

    def get_json(self, url: str, **kwargs: Any) -> Any:
        _, payload = self.request("GET", url, **kwargs)
        return json.loads(payload) if payload else None

    def post_json(self, url: str, body: Any, **kwargs: Any) -> Any:
        _, payload = self.request("POST", url, body=body, **kwargs)
        return json.loads(payload) if payload else None


def _host_of(base_url: str) -> str:
    host = urllib.parse.urlsplit(base_url).hostname
    if not host:
        raise ValueError(f"Not a URL: {base_url!r}")
    return host
