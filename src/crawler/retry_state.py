"""Durable per-instance room failure cooldowns; fail closed on corrupt state."""
import json
import math
import time
from pathlib import Path

from src.utils.safe_io import atomic_json


class RetryStateError(RuntimeError):
    pass


class RetryState:
    def __init__(self, path, run_id, *, cooldown_seconds=7200, max_failures=3, clock=time.time):
        self.path = Path(path)
        self.run_id = str(run_id)
        self.cooldown_seconds = int(cooldown_seconds)
        self.max_failures = int(max_failures)
        self.clock = clock
        if self.cooldown_seconds < 1 or self.max_failures < 1:
            raise ValueError("invalid retry limits")
        self.data = self._load()

    def _load(self):
        if not self.path.exists():
            return {"schema_version": 1, "run_id": self.run_id, "rooms": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(raw, dict) or raw.get("schema_version") != 1
                    or not isinstance(raw.get("run_id"), str)
                    or not isinstance(raw.get("rooms"), dict)):
                raise ValueError("schema")
            for live_id, entry in raw["rooms"].items():
                if (not isinstance(live_id, str) or not isinstance(entry, dict)
                        or type(entry.get("failure_count")) is not int
                        or not 0 <= entry["failure_count"] <= self.max_failures
                        or not isinstance(entry.get("last_failure_t"), (int, float))
                        or not math.isfinite(entry["last_failure_t"])
                        or type(entry.get("retry_exhausted")) is not bool):
                    raise ValueError("room entry")
                if entry["retry_exhausted"] != (entry["failure_count"] >= self.max_failures):
                    raise ValueError("retry exhaustion mismatch")
                cooldown_until = entry.get("cooldown_until")
                if cooldown_until is None:
                    # Migrate older state without losing its outstanding cooldown.
                    cooldown_until = float(entry["last_failure_t"]) + self.cooldown_seconds
                    entry["cooldown_until"] = cooldown_until
                if (not isinstance(cooldown_until, (int, float))
                        or not math.isfinite(cooldown_until)):
                    raise ValueError("cooldown deadline")
        except (OSError, ValueError, TypeError) as exc:
            raise RetryStateError(f"retry state corrupt: {type(exc).__name__}") from exc
        if raw["run_id"] != self.run_id:
            # Failure limits reset per run; outstanding room cooldowns survive run changes.
            now = self.clock()
            rooms = {}
            for live_id, entry in raw["rooms"].items():
                cooldown_until = float(entry["cooldown_until"])
                if cooldown_until > now:
                    rooms[live_id] = {"failure_count": 0, "last_failure_t": entry["last_failure_t"],
                                      "cooldown_until": cooldown_until, "retry_exhausted": False,
                                      "last_reason": entry.get("last_reason", "")}
            migrated = {"schema_version": 1, "run_id": self.run_id, "rooms": rooms}
            atomic_json(self.path, migrated, backup=True)
            return migrated
        return raw

    def _save(self):
        atomic_json(self.path, self.data, backup=self.path.exists())

    def entry(self, live_id):
        return dict(self.data["rooms"].get(str(live_id), {}))

    def eligible(self, live_id, now=None):
        entry = self.data["rooms"].get(str(live_id))
        if not entry:
            return True, 0
        if entry["retry_exhausted"] or entry["failure_count"] >= self.max_failures:
            return False, None
        now = self.clock() if now is None else now
        remaining = float(entry.get("cooldown_until", float(entry["last_failure_t"]) +
                                    self.cooldown_seconds)) - float(now)
        return remaining <= 0, max(0, remaining)

    def failed(self, live_id, reason="record_cycle_failed"):
        key = str(live_id)
        previous = self.data["rooms"].get(key, {"failure_count": 0})
        count = int(previous["failure_count"]) + 1
        exhausted = count >= self.max_failures
        now = self.clock()
        self.data["rooms"][key] = {"failure_count": count, "last_failure_t": now,
                                   "cooldown_until": now + self.cooldown_seconds,
                                   "retry_exhausted": exhausted, "last_reason": str(reason)[:160]}
        self._save()
        return dict(self.data["rooms"][key])

    def succeeded(self, live_id):
        if str(live_id) in self.data["rooms"]:
            del self.data["rooms"][str(live_id)]
            self._save()
