"""Print inputs and the Python privacy filter's outputs as JSON, so the
JavaScript port can be checked against them.  Usage: python -m tests.parity_dump"""

import datetime as dt
import json

from coach import privacy
from coach.demo import DemoGarmin

from .test_app import PrivacyTests

g = DemoGarmin()
today = dt.date.today()
start = (today - dt.timedelta(days=69)).isoformat()
inputs = {
    "raw_record": PrivacyTests.RAW,
    "sleep_70_days": g.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": start, "endDate": today.isoformat()}),
    "heart_rate_day": g.get("/wellness-service/wellness/dailyHeartRate", {"date": today.isoformat()}),
    "activities": g.get("/activitylist-service/activities/search/activities", {"start": 0, "limit": 30}),
    "profile": g.get("/userprofile-service/userprofile/settings"),
    "hrv_detail": g.get(f"/hrv-service/hrv/{today.isoformat()}"),
    "wide_rows": [{f"field{i}": i * 1.23456 for i in range(40)} | {"activityId": 900 + n, "activityName": f"Run {n}", "distance": 1000.5 * n, "averageHR": 140 + n, "startLatitude": 1.0, "duration": 300.123 * n, "calories": 50 * n} for n in range(12)],
    "big_blobs": [{"calendarDate": f"2026-01-{(d % 28) + 1:02d}", "blob": "z" * 500, "n": d} for d in range(300)],
}
cases = []
for name, data in inputs.items():
    for level in privacy.LEVELS:
        scrubbed = privacy.scrub(data, level, privacy.AliasMap())
        case = {"name": name, "level": level, "input": data, "scrub": scrubbed, "compact": privacy.compact(scrubbed), "compact_small": privacy.compact(scrubbed, 8000)}
        if isinstance(scrubbed, list):
            rows = [r for r in scrubbed if isinstance(r, dict)]
            case["week"] = privacy.aggregate(rows, "week")
            case["month"] = privacy.aggregate(rows, "month")
            case["slim"] = privacy.slim_activities(scrubbed)
        cases.append(case)
print(json.dumps(cases))
