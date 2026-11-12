"""Synthetic data so the app can be tried without a Garmin key.

Field names are plausible but invented: this is not a copy of Garmin's
schema. Nothing here touches the network.
"""

from __future__ import annotations

import datetime as dt
import math
import random
import re
import time
from typing import Any


class DemoGarmin:
    is_demo = True
    token_expires_at = 0.0

    def connect(self) -> float:
        return 0.0

    def forget(self) -> None:
        pass

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", path)
        start = params.get("startDate") or (dates[0] if dates else None)
        end = params.get("endDate") or (dates[-1] if dates else None)
        day = params.get("date") or (dates[0] if dates else None)
        time.sleep(0.05)

        if "stats/stress" in path:
            return [{"calendarDate": d, "values": {"overallStressLevel": _day(d)["stress"]}} for d in _days(start, end)]
        if "stats/steps" in path:
            return [{"calendarDate": d, "totalSteps": _day(d)["steps"], "stepGoal": 9000, "totalDistance": round(_day(d)["steps"] * 0.78)} for d in _days(start, end)]
        if "stats/hydration" in path:
            return [{"calendarDate": d, "valueInML": 1500 + _day(d)["steps"] // 12, "goalInML": 2400} for d in _days(start, end)]
        if "dailysummaries" in path:
            return {"data": [_summary(d) for d in _days(start, end)]}
        if "dailySleepSummariesByDate" in path:
            return [_sleep(d) for d in _days(start, end)]
        if path.endswith("/dailySleep"):
            s = _sleep(day)
            s["sleepLevels"] = [{"startGMT": f"{day}T{h:02d}:00:00", "activityLevel": random.Random(day + str(h)).choice([0, 1, 1, 2])} for h in range(0, 7)]
            return {"dailySleepDTO": s, "userProfilePK": 12345678}
        if "bodyBattery" in path:
            return [{"date": d, "charged": _day(d)["bb_high"] - _day(d)["bb_low"], "drained": _day(d)["bb_high"] - _day(d)["bb_low"] - 3, "highest": _day(d)["bb_high"], "lowest": _day(d)["bb_low"]} for d in _days(start, end)]
        if "dailyHeartRate" in path:
            rnd = random.Random("hr" + str(day))
            base = _day(day)["rhr"]
            series = [[1_760_000_000_000 + i * 120_000, base + int(18 * abs(math.sin(i / 55))) + rnd.randint(0, 9)] for i in range(720)]
            return {"calendarDate": day, "restingHeartRate": base, "maxHeartRate": max(v for _, v in series), "minHeartRate": base - 3, "heartRateValues": series, "userProfilePK": 12345678}
        if "/hrv-service/hrv/daily/" in path:
            return {"hrvSummaries": [{"calendarDate": d, "lastNightAvg": _day(d)["hrv"], "weeklyAvg": _day(d)["hrv_week"], "status": "BALANCED" if _day(d)["hrv"] > 52 else "UNBALANCED", "baseline": {"balancedLow": 53, "balancedUpper": 68}} for d in _days(start, end)]}
        if "/hrv-service/hrv/" in path:
            d = dates[0]
            return {"hrvSummary": {"calendarDate": d, "lastNightAvg": _day(d)["hrv"], "lastNight5MinHigh": _day(d)["hrv"] + 21, "status": "BALANCED"}, "hrvReadings": [{"hrvValue": _day(d)["hrv"] + random.Random(d + str(i)).randint(-12, 14), "readingTimeGMT": f"{d}T{i // 12:02d}:{(i % 12) * 5:02d}:00"} for i in range(84)]}
        if "heartRateZones" in path:
            return [{"sport": "DEFAULT", "trainingMethod": "HR_MAX", "maxHeartRateUsed": 188, "restingHeartRateUsed": 50, "zone1Floor": 94, "zone2Floor": 113, "zone3Floor": 132, "zone4Floor": 150, "zone5Floor": 169}]
        if "userprofile/settings" in path:
            return {"id": 12345678, "displayName": "demo-athlete", "userData": {"gender": "MALE", "weight": 71500.0, "height": 176.0, "birthDate": "1991-05-14", "measurementSystem": "metric", "vo2MaxRunning": 52.0, "lactateThresholdHeartRate": 168}, "userSleep": {"sleepTime": 82800, "wakeTime": 21600}}
        if "fitnessage" in path:
            return {"chronologicalAge": 35, "fitnessAge": 29.4, "achievableFitnessAge": 27.0}
        if "spo2acclimation" in path:
            return {"calendarDate": dates[0], "averageSpO2": 96, "lowestSpO2": 91, "latestSpO2": 97}
        if "weight/range" in path:
            return {"dailyWeightSummaries": [{"summaryDate": d, "latestWeight": {"weight": 71500 + int(600 * math.sin(_ordinal(d) / 9)), "bodyFat": 14.2}} for d in _days(start, end)[::3]]}
        if "food/logs" in path:
            return {"calendarDate": dates[0], "totalCalories": 2450, "carbsGrams": 310, "proteinGrams": 120, "fatGrams": 78}
        if "menstrualcycle" in path:
            return {"note": "No cycle data in the demo profile."}
        if "activityTypes" in path:
            return [{"typeKey": k} for k in ("running", "cycling", "swimming", "strength_training", "walking")]
        if "search/activities" in path:
            acts = _activities(params.get("startDate"), params.get("endDate"))
            kind = params.get("activityType")
            if kind:
                acts = [a for a in acts if a["activityType"]["typeKey"] == kind]
            first = int(params.get("start", 0))
            return acts[first : first + int(params.get("limit", 20))]
        match = re.search(r"/activity-service/activity/(\d+)(/workouts)?$", path)
        if match:
            if match.group(2):
                return []
            act = next((a for a in _activities(None, None, 400) if str(a["activityId"]) == match.group(1)), None)
            if not act:
                return None
            detail = dict(act)
            detail["splitSummaries"] = [{"splitNumber": i + 1, "distance": 1000, "duration": round(act["duration"] / max(1, act["distance"] / 1000) * (1 + 0.03 * math.sin(i)), 1), "averageHR": act["averageHR"] + i % 4} for i in range(int(act["distance"] // 1000))]
            return detail
        return None


def _ordinal(d: str) -> int:
    return dt.date.fromisoformat(d).toordinal()


def _days(start: str | None, end: str | None) -> list[str]:
    today = dt.date.today()
    e = min(dt.date.fromisoformat(end), today) if end else today
    s = dt.date.fromisoformat(start) if start else e - dt.timedelta(days=6)
    out = []
    while s <= e and len(out) < 400:
        out.append(s.isoformat())
        s += dt.timedelta(days=1)
    return out


def _day(d: str) -> dict[str, int]:
    rnd = random.Random("day" + d)
    o = _ordinal(d)
    wave = math.sin(o / 6.5)
    hard = dt.date.fromisoformat(d).weekday() in (1, 3, 5)
    hrv = int(58 + 7 * wave + rnd.randint(-5, 5) - (6 if hard else 0))
    return {
        "hrv": hrv,
        "hrv_week": int(58 + 7 * math.sin((o - 3) / 6.5)),
        "rhr": int(49 - 2 * wave + rnd.randint(0, 3) + (2 if hard else 0)),
        "stress": int(31 - 6 * wave + rnd.randint(-4, 8)),
        "steps": int(8200 + 2600 * wave + rnd.randint(-1500, 2500) + (4200 if hard else 0)),
        "sleep_min": int(432 + 34 * wave + rnd.randint(-45, 40)),
        "bb_high": min(100, int(84 + 9 * wave + rnd.randint(-6, 6))),
        "bb_low": int(19 + rnd.randint(-6, 9)),
    }


def _summary(d: str) -> dict[str, Any]:
    v = _day(d)
    return {
        "calendarDate": d,
        "userProfileId": 12345678,
        "totalSteps": v["steps"],
        "totalKilocalories": 2150 + v["steps"] // 20,
        "activeKilocalories": 380 + v["steps"] // 25,
        "restingHeartRate": v["rhr"],
        "averageStressLevel": v["stress"],
        "bodyBatteryHighestValue": v["bb_high"],
        "bodyBatteryLowestValue": v["bb_low"],
        "sleepingSeconds": v["sleep_min"] * 60,
        "moderateIntensityMinutes": 22 + v["steps"] // 900,
        "vigorousIntensityMinutes": 9 + v["steps"] // 1500,
        "floorsAscended": 6 + v["steps"] // 2500,
    }


def _sleep(d: str) -> dict[str, Any]:
    v = _day(d)
    total = v["sleep_min"] * 60
    return {
        "calendarDate": d,
        "userProfilePK": 12345678,
        "sleepTimeSeconds": total,
        "deepSleepSeconds": int(total * 0.19),
        "lightSleepSeconds": int(total * 0.56),
        "remSleepSeconds": int(total * 0.21),
        "awakeSleepSeconds": int(total * 0.04),
        "sleepScores": {"overall": {"value": max(40, min(96, 44 + v["sleep_min"] // 12))}},
        "avgOvernightHrv": v["hrv"],
        "restingHeartRate": v["rhr"],
    }


def _activities(start: str | None, end: str | None, span: int = 120) -> list[dict[str, Any]]:
    today = dt.date.today()
    lo = dt.date.fromisoformat(start) if start else today - dt.timedelta(days=span)
    hi = min(dt.date.fromisoformat(end), today) if end else today
    out = []
    day = hi
    while day >= lo and len(out) < 400:
        wd = day.weekday()
        rnd = random.Random("act" + day.isoformat())
        if wd in (1, 3, 5, 6) and rnd.random() > 0.12:
            long_run = wd == 6
            ride = wd == 3 and rnd.random() > 0.5
            km = rnd.uniform(16, 24) if long_run else rnd.uniform(28, 45) if ride else rnd.uniform(6, 11)
            pace = rnd.uniform(118, 135) if ride else rnd.uniform(298, 345)  # sec/km
            dur = km * pace
            out.append(
                {
                    "activityId": 20_000_000_000 + day.toordinal(),
                    "ownerId": 12345678,
                    "activityName": ("Riverside Ride" if ride else "Sunday Long Run" if long_run else "Lunch Run"),
                    "locationName": "Riverside",
                    "startTimeLocal": f"{day.isoformat()} {'06:10:00' if long_run else '12:15:00'}",
                    "activityType": {"typeId": 2 if ride else 1, "typeKey": "cycling" if ride else "running"},
                    "distance": round(km * 1000, 1),
                    "duration": round(dur, 1),
                    "averageSpeed": round(km * 1000 / dur, 3),
                    "averageHR": int((132 if ride else 148) + rnd.randint(-6, 9)),
                    "maxHR": int(171 + rnd.randint(-5, 10)),
                    "calories": int(km * (24 if ride else 68)),
                    "elevationGain": round(km * rnd.uniform(4, 11), 1),
                    "aerobicTrainingEffect": round(rnd.uniform(2.4, 4.2), 1),
                    "startLatitude": 10.0,
                    "startLongitude": 20.0,
                }
            )
        day -= dt.timedelta(days=1)
    return out
