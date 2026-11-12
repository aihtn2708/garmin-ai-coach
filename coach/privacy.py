"""Data minimisation: what gets removed before anything is shown to a model.

Two steps run on every Garmin response before it becomes model input:

1. ``scrub``   removes identifiers, location and (in strict mode) free text.
2. ``compact`` shrinks the result so only a bounded amount of data is sent.

The rules are name-based on purpose: Garmin can add fields at any time, so a
key that *looks* like an identifier or a coordinate is dropped even if this
code has never seen it.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

LEVELS = ("balanced", "strict")

# Always removed.
_IDENTITY = re.compile(
    r"^(id|pk|.*(Id|ID|Pk|PK|Uuid|UUID|Guid|GUID))$"
    r"|^(owner|user(Name|Profile)|displayName|fullName|firstName|lastName|nickName)"
    r"|email|phone|profileImage|imageUrl|avatar|Url$|url$|serial|macAddress|deviceName"
    r"|^(timeZone|timezone)",
)
_LOCATION = re.compile(
    r"latitude|longitude|^(lat|lon|lng)$|polyline|geo(Point|Json)?s?$|startPoint|endPoint|coordinates",
    re.IGNORECASE,
)
# Removed in strict mode only: free text that can name places or people.
_FREE_TEXT = re.compile(
    r"^(activityName|name|locationName|location|description|comments?|notes?|city|country|address"
    r"|courseName|eventName|title)$",
    re.IGNORECASE,
)
_KEEP = {"activityId"}  # replaced by a session-local alias, never sent raw


class AliasMap:
    """Maps real activity IDs to short session-local aliases (A1, A2, ...)."""

    def __init__(self) -> None:
        self._by_id: dict[str, str] = {}
        self._by_alias: dict[str, str] = {}

    def alias(self, real_id: Any) -> str:
        key = str(real_id)
        if key not in self._by_id:
            alias = f"A{len(self._by_id) + 1}"
            self._by_id[key] = alias
            self._by_alias[alias] = key
        return self._by_id[key]

    def resolve(self, alias: str) -> str | None:
        return self._by_alias.get(str(alias).strip().upper())

    def clear(self) -> None:
        self._by_id.clear()
        self._by_alias.clear()


def scrub(obj: Any, level: str = "balanced", aliases: AliasMap | None = None) -> Any:
    strict = level == "strict"

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in value.items():
                k = str(key)
                if k == "activityId" and aliases is not None and item is not None:
                    out["activity"] = aliases.alias(item)
                    continue
                if k == "birthDate":
                    age = _age(item)
                    if age is not None:
                        out["ageYears"] = age
                    continue
                if k not in _KEEP and (_IDENTITY.search(k) or _LOCATION.search(k)):
                    continue
                if strict and _FREE_TEXT.match(k) and isinstance(item, str):
                    continue
                out[k] = walk(item)
            return out
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    return walk(obj)


def _age(birth: Any) -> int | None:
    try:
        born = dt.date.fromisoformat(str(birth)[:10])
    except ValueError:
        return None
    today = dt.date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# -- compaction ------------------------------------------------------------

_LEVELS = [(400, 48), (200, 24), (100, 12), (40, 8)]  # (max rows/items, samples kept)
_EMPTY = (None, "", [], {})


def compact(obj: Any, max_chars: int = 24_000) -> Any:
    """Shrink ``obj`` until its JSON fits in ``max_chars``.

    Lists of records become a column/row table (keys are sent once instead of
    once per row), long numeric series become a summary plus samples, and
    empty values are dropped.
    """
    result: Any = None
    for max_items, samples in _LEVELS:
        result = _shrink(obj, max_items, samples)
        if _size(result) <= max_chars:
            return result
    text = json.dumps(result, separators=(",", ":"), default=str)
    return {
        "_note": "Result was too large and has been cut off. Ask for a shorter date "
        "range, or use group_by week or month.",
        "partial": text[:max_chars],
    }


def _size(obj: Any) -> int:
    return len(json.dumps(obj, separators=(",", ":"), default=str))


def _shrink(value: Any, max_items: int, samples: int) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = _shrink(v, max_items, samples)
            if v in _EMPTY:
                continue
            out[k] = v
        return out
    if isinstance(value, list):
        if len(value) > samples and all(_is_num(v) for v in value):
            return _summarise([v for v in value if v is not None], value, samples)
        if len(value) > samples and all(_is_pair(v) for v in value):
            nums = [v[-1] for v in value if v[-1] is not None]
            return _summarise(nums, value, samples)
        if len(value) >= 4 and all(isinstance(v, dict) for v in value):
            return to_table(value, max_items, samples)
        items = value
        note = None
        if len(items) > max_items:
            note = {"_note": f"{len(items) - max_items} more items not shown"}
            items = items[:max_items]
        shrunk = [_shrink(v, max_items, samples) for v in items]
        if note:
            shrunk.append(note)
        return shrunk
    if isinstance(value, float):
        return round(value, 2) if abs(value) >= 10 else round(value, 3)
    return value


def _is_num(v: Any) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def _is_pair(v: Any) -> bool:
    return isinstance(v, (list, tuple)) and 2 <= len(v) <= 3 and _is_num(v[-1])


def _summarise(nums: list[float], original: list[Any], samples: int) -> dict[str, Any]:
    step = max(1, -(-len(original) // samples))
    summary: dict[str, Any] = {"_summary_of": len(original)}
    if nums:
        summary.update(
            min=min(nums),
            max=max(nums),
            mean=round(sum(nums) / len(nums), 2),
            first=nums[0],
            last=nums[-1],
        )
    summary["every_nth_sample"] = {"n": step, "values": original[::step]}
    return summary


def _flatten(row: dict[str, Any], prefix: str, out: dict[str, Any]) -> None:
    for k, v in row.items():
        if isinstance(v, dict):
            _flatten(v, f"{prefix}{k}.", out)
        else:
            out[f"{prefix}{k}"] = v


def to_table(rows: list[dict[str, Any]], max_items: int = 400, samples: int = 48) -> dict[str, Any]:
    flat = []
    for row in rows:
        out: dict[str, Any] = {}
        _flatten(row, "", out)
        flat.append({k: _shrink(v, max_items, samples) for k, v in out.items()})
    columns: list[str] = []
    for row in flat:
        for k in row:
            if k not in columns:
                columns.append(k)
    columns = [c for c in columns if any(r.get(c) not in _EMPTY for r in flat)]
    same: dict[str, Any] = {}
    if len(flat) > 1:
        for c in list(columns):
            first = flat[0].get(c)
            if all(r.get(c) == first for r in flat):
                same[c] = first
                columns.remove(c)
    table: dict[str, Any] = {"columns": columns}
    shown = flat[:max_items]
    table["rows"] = [[r.get(c) for c in columns] for r in shown]
    if same:
        table["same_for_all_rows"] = same
    if len(flat) > max_items:
        table["_note"] = f"{len(flat) - max_items} more rows not shown"
    return table


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def aggregate(rows: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    """Average numeric fields per ISO week or month. Rows need a date field."""
    flat = []
    for row in rows:
        out: dict[str, Any] = {}
        _flatten(row, "", out)
        flat.append(out)
    date_col = next(
        (
            k
            for k in (flat[0] if flat else {})
            if "date" in k.lower() and isinstance(flat[0][k], str) and _ISO_DATE.match(flat[0][k])
        ),
        None,
    )
    if date_col is None:
        return rows
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in flat:
        try:
            day = dt.date.fromisoformat(str(row.get(date_col))[:10])
        except ValueError:
            continue
        if group_by == "month":
            label = day.strftime("%Y-%m")
        else:
            monday = day - dt.timedelta(days=day.weekday())
            label = f"week of {monday.isoformat()}"
        buckets.setdefault(label, []).append(row)
    result = []
    for label, members in buckets.items():
        agg: dict[str, Any] = {"period": label, "days_with_data": len(members)}
        keys: list[str] = []
        for m in members:
            for k in m:
                if k not in keys:
                    keys.append(k)
        for k in keys:
            vals = [m[k] for m in members if isinstance(m.get(k), (int, float)) and not isinstance(m.get(k), bool)]
            if vals:
                agg[f"avg.{k}"] = round(sum(vals) / len(vals), 2)
        result.append(agg)
    return result


# -- activity list: keep the fields that matter for analysis ----------------

_ACTIVITY_FIELDS = (
    "activity", "activityName", "startTimeLocal", "activityType", "distance", "duration",
    "movingDuration", "elapsedDuration", "elevationGain", "elevationLoss", "averageSpeed",
    "maxSpeed", "averageHR", "maxHR", "calories", "steps", "avgPower", "normPower",
    "averageRunningCadenceInStepsPerMinute", "averageBikingCadenceInRevPerMinute",
    "avgStrideLength", "aerobicTrainingEffect", "anaerobicTrainingEffect",
    "activityTrainingLoad", "trainingEffectLabel", "vO2MaxValue", "lapCount",
    "moderateIntensityMinutes", "vigorousIntensityMinutes", "hrTimeInZone_1",
    "hrTimeInZone_2", "hrTimeInZone_3", "hrTimeInZone_4", "hrTimeInZone_5", "locationName",
)


def slim_activities(items: Any) -> Any:
    """For wide activity rows, keep a known-useful subset of columns."""
    if not isinstance(items, list):
        return items
    out = []
    for row in items:
        if not isinstance(row, dict) or len(row) <= 30:
            out.append(row)
            continue
        kept = {k: row[k] for k in _ACTIVITY_FIELDS if k in row}
        if len(kept) < 4:  # unfamiliar schema: fall back to generic compaction
            out.append(row)
            continue
        kind = kept.get("activityType")
        if isinstance(kind, dict):
            kept["activityType"] = kind.get("typeKey", kind)
        out.append(kept)
    return out
