"""Read-only client for the Garmin Connect Personal API.

Endpoints mirror Garmin's official reference client
(https://github.com/garmin/connect-personal-api-python). Only GET requests to
the paths listed in ``ALLOWED_PATHS`` are possible; nothing here can write to
a Garmin account.

The API key and the 24-hour bearer token live in this object's memory only.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.parse
from typing import Any

from .net import Egress, HttpError

DEFAULT_BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
KEY_PAGE = "https://connect.garmin.com/app/settings/personalApiToken"

_D = r"\d{4}-\d{2}-\d{2}"
ALLOWED_PATHS = [
    re.compile(p)
    for p in (
        rf"^/usersummary-service/stats/(stress|steps|hydration)/daily/{_D}/{_D}$",
        r"^/usersummary-service/v2/dailysummaries$",
        rf"^/goal-service/goal/user/effective/weightgoal/{_D}/{_D}$",
        rf"^/weight-service/weight/range/{_D}/{_D}$",
        rf"^/nutrition-service/food/logs/{_D}$",
        r"^/userprofile-service/userprofile/settings$",
        r"^/sleep-service/sleep/(dailySleep|dailySleepSummariesByDate)$",
        r"^/wellness-service/wellness/bodyBattery/reports/daily$",
        r"^/wellness-service/wellness/dailyHeartRate$",
        rf"^/wellness-service/wellness/daily/spo2acclimation/{_D}$",
        rf"^/hrv-service/hrv/{_D}$",
        rf"^/hrv-service/hrv/daily/{_D}/{_D}$",
        r"^/biometric-service/heartRateZones/v2$",
        rf"^/periodichealth-service/menstrualcycle/(summary|dayview)/{_D}$",
        rf"^/fitnessage-service/fitnessage/v2/{_D}$",
        rf"^/wellnessactivity-service/activity/summary/range/{_D}/{_D}$",
        r"^/activitylist-service/activities/search/activities$",
        r"^/activity-service/activity/activityTypes/simple$",
        r"^/activity-service/activity/\d{1,20}(/workouts)?$",
    )
]

CACHE_TTL_SECONDS = 15 * 60
MIN_INTERVAL_SECONDS = 0.25  # be polite: Garmin publishes no rate limits
TOKEN_REFRESH_WINDOW = 60 * 60


class GarminError(Exception):
    pass


def resolve_base_url() -> str:
    """Garmin's host, or a loopback override for tests (never another host)."""
    override = os.environ.get("GARMIN_PERSONAL_API_BASE_URL", "").rstrip("/")
    if override:
        host = urllib.parse.urlsplit(override).hostname
        if host in ("127.0.0.1", "localhost"):
            return override
    return DEFAULT_BASE_URL


class GarminClient:
    is_demo = False

    def __init__(self, api_key: str, egress: Egress) -> None:
        key = (api_key or "").strip()
        if not key:
            raise GarminError("Paste your Garmin Personal API key first.")
        self._key = key
        self._egress = egress
        self.base_url = resolve_base_url()
        egress.allow(self.base_url, "garmin")
        self._token: str | None = None
        self.token_expires_at: float = 0.0
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._last_call = 0.0

    # -- auth ---------------------------------------------------------------

    def connect(self) -> float:
        """Exchange the key for a bearer token. Returns the expiry time."""
        self._refresh_token(force=True)
        return self.token_expires_at

    def _refresh_token(self, force: bool = False) -> str:
        if (
            not force
            and self._token
            and self.token_expires_at - time.time() > TOKEN_REFRESH_WINDOW
        ):
            return self._token
        try:
            _, payload = self._egress.request(
                "POST",
                self.base_url + TOKEN_PATH,
                headers={"Accept": "*/*", "Authorization": f"ApiKey {self._key}"},
                timeout=30,
                note="Exchange API key for a 24-hour access token",
            )
        except HttpError as exc:
            if exc.status in (401, 403):
                raise GarminError(
                    "Garmin rejected this API key. It may be mistyped, expired or "
                    f"deleted. Create a new one at {KEY_PAGE}"
                ) from None
            raise GarminError(f"Garmin token request failed (HTTP {exc.status}).") from None
        token = payload.decode("utf-8", "replace").strip()
        if token.startswith("{"):  # tolerate a JSON-wrapped token
            try:
                obj = json.loads(token)
                token = obj.get("access_token") or obj.get("token") or token
            except ValueError:
                pass
        self._token = token
        self.token_expires_at = _jwt_exp(token) or (time.time() + 23 * 3600)
        return token

    # -- data ---------------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not any(p.match(path) for p in ALLOWED_PATHS):
            raise GarminError(f"Path is not on the read-only allowlist: {path}")
        query = urllib.parse.urlencode(params or {})
        cache_key = f"{path}?{query}"
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
                return hit[1]
            wait = MIN_INTERVAL_SECONDS - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            data = self._get_uncached(path, query)
            self._last_call = time.time()
            self._cache[cache_key] = (time.time(), data)
            return data

    def _get_uncached(self, path: str, query: str, retry: bool = True) -> Any:
        token = self._refresh_token()
        url = self.base_url + path + (f"?{query}" if query else "")
        try:
            _, payload = self._egress.request(
                "GET",
                url,
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                timeout=45,
            )
        except HttpError as exc:
            if exc.status == 401 and retry:
                self._refresh_token(force=True)
                return self._get_uncached(path, query, retry=False)
            if exc.status == 429:
                raise GarminError(
                    "Garmin is rate-limiting requests. Wait a minute and try again."
                ) from None
            if exc.status in (400, 404):
                raise GarminError(
                    f"Garmin returned no data for this request (HTTP {exc.status}): "
                    f"{exc.message()[:160]}"
                ) from None
            raise GarminError(f"Garmin request failed (HTTP {exc.status}).") from None
        if not payload:
            return None
        try:
            return json.loads(payload)
        except ValueError:
            raise GarminError("Garmin returned a response that is not JSON.") from None

    def forget(self) -> None:
        self._key = ""
        self._token = None
        self._cache.clear()


def _jwt_exp(token: str) -> float | None:
    """Read the ``exp`` claim of a JWT (not verified; only used for timing)."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None
