"""In-memory record of every request that leaves this computer.

The ledger is the app's audit trail. It never holds API keys (they travel in
headers, which are not recorded) and it is never written to disk unless the
user exports it from the UI.
"""

from __future__ import annotations

import threading
import time
from typing import Any

MAX_ENTRIES = 400
MAX_DETAILS = 40  # full request bodies kept for inspection


class Ledger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        self._details: dict[int, Any] = {}
        self._next_id = 1

    def record(
        self,
        *,
        kind: str,
        host: str,
        method: str,
        path: str,
        bytes_out: int,
        bytes_in: int,
        status: int | str,
        ms: int,
        note: str = "",
        detail: Any = None,
    ) -> int:
        with self._lock:
            entry_id = self._next_id
            self._next_id += 1
            self._entries.append(
                {
                    "id": entry_id,
                    "ts": time.time(),
                    "kind": kind,  # "garmin" | "model" | "local-model"
                    "host": host,
                    "method": method,
                    "path": path,
                    "bytes_out": bytes_out,
                    "bytes_in": bytes_in,
                    "status": status,
                    "ms": ms,
                    "note": note,
                    "has_detail": detail is not None,
                }
            )
            if detail is not None:
                self._details[entry_id] = detail
                for old in sorted(self._details)[:-MAX_DETAILS]:
                    del self._details[old]
                    for e in self._entries:
                        if e["id"] == old:
                            e["has_detail"] = False
            del self._entries[:-MAX_ENTRIES]
            return entry_id

    def entries(self, since_id: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._entries if e["id"] > since_id]

    def detail(self, entry_id: int) -> Any:
        with self._lock:
            return self._details.get(entry_id)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._details.clear()
