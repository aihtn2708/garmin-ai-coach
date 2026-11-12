"""The fixed set of actions a model may request.

A model never gets a URL, a key or a free-form query. It can only name one of
these tools with validated arguments; the server then decides which read-only
Garmin endpoint to call and filters the result through ``privacy`` before the
model sees it. Tools for categories the user has switched off are not offered
to the model at all.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from . import privacy
from .garmin import GarminError

# category -> (label, description, on by default)
CATEGORIES: dict[str, tuple[str, str, bool]] = {
    "activities": ("Activities", "Runs, rides and other recorded workouts", True),
    "sleep": ("Sleep", "Sleep duration, stages and scores", True),
    "heart": ("Heart rate and HRV", "Resting and daily heart rate, heart rate variability", True),
    "stress_energy": ("Stress and Body Battery", "Daily stress and Body Battery levels", True),
    "daily": ("Daily activity", "Steps, daily summaries, SpO2, fitness age", True),
    "profile": ("Profile and zones", "Age, weight, height and heart rate zones (no name or email)", True),
    "body": ("Weight and nutrition", "Weigh-ins, hydration and food logs", False),
    "cycle": ("Menstrual cycle", "Cycle tracking data", False),
}

# metric -> (category, path builder, max days per request, wrapper key hints)
RANGE_METRICS: dict[str, tuple[str, Callable[[str, str], tuple[str, dict[str, Any]]], int]] = {
    "daily_summary": ("daily", lambda s, e: ("/usersummary-service/v2/dailysummaries", {"startDate": s, "endDate": e}), 365),
    "steps": ("daily", lambda s, e: (f"/usersummary-service/stats/steps/daily/{s}/{e}", {}), 28),
    "sleep": ("sleep", lambda s, e: ("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": s, "endDate": e}), 31),
    "hrv": ("heart", lambda s, e: (f"/hrv-service/hrv/daily/{s}/{e}", {}), 28),
    "stress": ("stress_energy", lambda s, e: (f"/usersummary-service/stats/stress/daily/{s}/{e}", {}), 28),
    "body_battery": ("stress_energy", lambda s, e: ("/wellness-service/wellness/bodyBattery/reports/daily", {"startDate": s, "endDate": e}), 31),
    "weight": ("body", lambda s, e: (f"/weight-service/weight/range/{s}/{e}", {"includeAll": "true"}), 31),
    "hydration": ("body", lambda s, e: (f"/usersummary-service/stats/hydration/daily/{s}/{e}", {}), 31),
}

DAY_METRICS: dict[str, tuple[str, Callable[[str], tuple[str, dict[str, Any]]]]] = {
    "sleep_detail": ("sleep", lambda d: ("/sleep-service/sleep/dailySleep", {"date": d})),
    "heart_rate": ("heart", lambda d: ("/wellness-service/wellness/dailyHeartRate", {"date": d})),
    "hrv_detail": ("heart", lambda d: (f"/hrv-service/hrv/{d}", {})),
    "spo2": ("daily", lambda d: (f"/wellness-service/wellness/daily/spo2acclimation/{d}", {})),
    "fitness_age": ("daily", lambda d: (f"/fitnessage-service/fitnessage/v2/{d}", {})),
    "food_log": ("body", lambda d: (f"/nutrition-service/food/logs/{d}", {})),
    "menstrual_cycle": ("cycle", lambda d: (f"/periodichealth-service/menstrualcycle/summary/{d}", {})),
}

MAX_RANGE_DAYS = 92  # longer spans are allowed only for daily_summary
MAX_GARMIN_CALLS_PER_TURN = 24
_DATE = {"type": "string", "description": "Calendar date, YYYY-MM-DD"}


class ToolError(Exception):
    """A problem the model can read and recover from."""


class Toolbox:
    def __init__(self, garmin: Any, categories: set[str], level: str, aliases: privacy.AliasMap) -> None:
        self.garmin = garmin
        self.categories = categories
        self.level = level
        self.aliases = aliases
        self.calls_this_turn = 0

    # -- what the model is told ----------------------------------------------

    def specs(self) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        ranges = [m for m, (cat, _, _) in RANGE_METRICS.items() if cat in self.categories]
        days = [m for m, (cat, _) in DAY_METRICS.items() if cat in self.categories]
        if ranges:
            specs.append(
                {
                    "name": "get_range",
                    "description": (
                        "Daily values of one health metric over a date range (inclusive). "
                        f"Up to {MAX_RANGE_DAYS} days per call; daily_summary allows up to 365. "
                        "For spans over about a month, set group_by to week or month to get "
                        "averages instead of every day."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "metric": {"type": "string", "enum": ranges},
                            "start_date": _DATE,
                            "end_date": _DATE,
                            "group_by": {"type": "string", "enum": ["day", "week", "month"]},
                        },
                        "required": ["metric", "start_date", "end_date"],
                    },
                }
            )
        if days:
            specs.append(
                {
                    "name": "get_day",
                    "description": "Detailed data for one metric on a single day.",
                    "parameters": {
                        "type": "object",
                        "properties": {"metric": {"type": "string", "enum": days}, "date": _DATE},
                        "required": ["metric", "date"],
                    },
                }
            )
        if "activities" in self.categories:
            specs.append(
                {
                    "name": "list_activities",
                    "description": (
                        "Recorded activities, newest first. Each has a short reference such "
                        "as A3 that get_activity accepts."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "start_date": _DATE,
                            "end_date": _DATE,
                            "activity_type": {
                                "type": "string",
                                "description": "Optional parent type, e.g. running, cycling, swimming",
                            },
                            "limit": {"type": "integer", "description": "1 to 50, default 20"},
                        },
                    },
                }
            )
            specs.append(
                {
                    "name": "get_activity",
                    "description": "Full summary and splits for one activity from list_activities.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "activity": {"type": "string", "description": "Reference such as A3"}
                        },
                        "required": ["activity"],
                    },
                }
            )
        if "profile" in self.categories:
            specs.append(
                {
                    "name": "get_profile",
                    "description": "Age, sex, weight, height, units and heart rate zones.",
                    # Some providers reject a tool with no parameters, so there is one.
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "include_zones": {
                                "type": "boolean",
                                "description": "Include heart rate zones (default true)",
                            }
                        },
                    },
                }
            )
        specs.append(
            {
                "name": "show_chart",
                "description": (
                    "Draw a chart for the user next to your answer. Use it when a trend or "
                    "comparison is easier to see than to read. One y-axis only: every series "
                    "must share the same unit. At most 4 series and 120 points."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "kind": {"type": "string", "enum": ["line", "bar"]},
                        "x_labels": {"type": "array", "items": {"type": "string"}},
                        "y_label": {"type": "string", "description": "Unit, e.g. hours, bpm, km"},
                        "series": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "values": {"type": "array", "items": {"type": "number"}},
                                },
                                "required": ["name", "values"],
                            },
                        },
                    },
                    "required": ["title", "kind", "x_labels", "series"],
                },
            }
        )
        return specs

    # -- execution -------------------------------------------------------------

    def run(self, name: str, args: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
        """Returns (result for the model, optional event for the UI)."""
        if not isinstance(args, dict):
            raise ToolError("Arguments must be a JSON object.")
        try:
            if name == "get_range":
                return self._get_range(args), None
            if name == "get_day":
                return self._get_day(args), None
            if name == "list_activities":
                return self._list_activities(args), None
            if name == "get_activity":
                return self._get_activity(args), None
            if name == "get_profile":
                return self._get_profile(args.get("include_zones") is not False), None
            if name == "show_chart":
                chart = _clean_chart(args)
                return {"status": "The chart is now visible to the user."}, {"type": "chart", "chart": chart}
        except GarminError as exc:
            raise ToolError(str(exc)) from None
        raise ToolError(f"Unknown tool: {name}")

    def _fetch(self, path: str, params: dict[str, Any]) -> Any:
        self.calls_this_turn += 1
        if self.calls_this_turn > MAX_GARMIN_CALLS_PER_TURN:
            raise ToolError("Too many data requests for one question. Answer with what you have.")
        return self.garmin.get(path, params or None)

    def _require(self, category: str) -> None:
        if category not in self.categories:
            raise ToolError("The user has not allowed access to this kind of data.")

    def _get_range(self, args: dict[str, Any]) -> Any:
        metric = str(args.get("metric", ""))
        if metric not in RANGE_METRICS:
            raise ToolError(f"Unknown metric {metric!r}.")
        category, build, chunk = RANGE_METRICS[metric]
        self._require(category)
        start, end = _date(args.get("start_date")), _date(args.get("end_date"))
        if start > end:
            raise ToolError("start_date is after end_date.")
        limit = 365 if metric == "daily_summary" else MAX_RANGE_DAYS
        if (end - start).days + 1 > limit:
            raise ToolError(f"Range too long for {metric}: at most {limit} days per call.")
        group_by = args.get("group_by") or "day"

        parts: list[Any] = []
        cursor = start
        while cursor <= end:
            stop = min(end, cursor + dt.timedelta(days=chunk - 1))
            path, params = build(cursor.isoformat(), stop.isoformat())
            parts.append(self._fetch(path, params))
            cursor = stop + dt.timedelta(days=1)

        rows = _merge(parts)
        clean = privacy.scrub(rows, self.level, self.aliases)
        if group_by in ("week", "month") and isinstance(clean, list):
            clean = privacy.aggregate([r for r in clean if isinstance(r, dict)], group_by)
        return {
            "metric": metric,
            "range": f"{start} to {end}",
            "group_by": group_by,
            "data": privacy.compact(clean),
        }

    def _get_day(self, args: dict[str, Any]) -> Any:
        metric = str(args.get("metric", ""))
        if metric not in DAY_METRICS:
            raise ToolError(f"Unknown metric {metric!r}.")
        category, build = DAY_METRICS[metric]
        self._require(category)
        day = _date(args.get("date")).isoformat()
        path, params = build(day)
        data = self._fetch(path, params)
        if data in (None, [], {}):
            return {"metric": metric, "date": day, "data": None, "note": "No data recorded for this day."}
        clean = privacy.scrub(data, self.level, self.aliases)
        return {"metric": metric, "date": day, "data": privacy.compact(clean, 14_000)}

    def _list_activities(self, args: dict[str, Any]) -> Any:
        self._require("activities")
        try:
            limit = max(1, min(50, int(args.get("limit") or 20)))
        except (TypeError, ValueError):
            raise ToolError("limit must be a number.") from None
        params: dict[str, Any] = {"start": 0, "limit": limit}
        if args.get("start_date"):
            params["startDate"] = _date(args["start_date"]).isoformat()
        if args.get("end_date"):
            params["endDate"] = _date(args["end_date"]).isoformat()
        kind = str(args.get("activity_type") or "").strip().lower()
        if kind:
            if not kind.replace("_", "").isalnum() or len(kind) > 40:
                raise ToolError("activity_type must be a simple key such as running.")
            params["activityType"] = kind
        data = self._fetch("/activitylist-service/activities/search/activities", params)
        if isinstance(data, dict):  # tolerate a wrapped list
            data = next((v for v in data.values() if isinstance(v, list)), [])
        clean = privacy.slim_activities(privacy.scrub(data or [], self.level, self.aliases))
        return {
            "count": len(clean),
            "units": "distance in metres, duration in seconds, speed in m/s unless a field says otherwise",
            "activities": privacy.compact(clean),
        }

    def _get_activity(self, args: dict[str, Any]) -> Any:
        self._require("activities")
        ref = str(args.get("activity", ""))
        real_id = self.aliases.resolve(ref)
        if real_id is None:
            raise ToolError(f"Unknown activity reference {ref!r}. Call list_activities first.")
        data = self._fetch(f"/activity-service/activity/{real_id}", {})
        if data in (None, {}):
            return {"activity": ref, "data": None, "note": "No details available."}
        return {"activity": ref, "data": privacy.compact(privacy.scrub(data, self.level, self.aliases), 14_000)}

    def _get_profile(self, include_zones: bool = True) -> Any:
        self._require("profile")
        out: dict[str, Any] = {}
        out["profile"] = privacy.compact(
            privacy.scrub(self._fetch("/userprofile-service/userprofile/settings", {}), self.level), 6_000
        )
        if not include_zones:
            return out
        try:
            zones = self._fetch("/biometric-service/heartRateZones/v2", {})
            out["heart_rate_zones"] = privacy.compact(privacy.scrub(zones, self.level), 4_000)
        except GarminError as exc:
            out["heart_rate_zones"] = f"unavailable: {exc}"
        return out


def _date(value: Any) -> dt.date:
    try:
        day = dt.date.fromisoformat(str(value))
    except ValueError:
        raise ToolError(f"Dates must look like 2026-01-31, got {value!r}.") from None
    if day > dt.date.today() + dt.timedelta(days=1):
        raise ToolError(f"{day} is in the future.")
    if day.year < 2005:
        raise ToolError(f"{day} is too far in the past.")
    return day


def _merge(parts: list[Any]) -> Any:
    """Join chunked responses. Lists are concatenated; a dict that wraps a
    single list (e.g. {"data": [...]}) is unwrapped first."""
    rows: list[Any] = []
    other: list[Any] = []
    for part in parts:
        if isinstance(part, dict):
            lists = [v for v in part.values() if isinstance(v, list)]
            if len(lists) == 1:
                part = lists[0]
        if isinstance(part, list):
            rows.extend(part)
        elif part not in (None, {}):
            other.append(part)
    if rows and not other:
        return rows
    if other and not rows:
        return other[0] if len(other) == 1 else other
    return {"rows": rows, "other": other} if rows else []


def _clean_chart(args: dict[str, Any]) -> dict[str, Any]:
    labels = [str(x)[:24] for x in (args.get("x_labels") or [])][:120]
    series = []
    for raw in (args.get("series") or [])[:4]:
        if not isinstance(raw, dict):
            continue
        values = []
        for v in (raw.get("values") or [])[: len(labels)]:
            values.append(float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None)
        if any(v is not None for v in values):
            series.append({"name": str(raw.get("name", "Series"))[:40], "values": values})
    if not labels or not series:
        raise ToolError("A chart needs x_labels and at least one series with numbers.")
    kind = args.get("kind") if args.get("kind") in ("line", "bar") else "line"
    return {
        "title": str(args.get("title", "Chart"))[:90],
        "kind": kind,
        "x_labels": labels,
        "y_label": str(args.get("y_label", ""))[:30],
        "series": series,
    }
