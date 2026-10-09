"""Append-only, single-writer event shards for controller and crawler telemetry."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading

EVENT_SHARD_BYTES = 512 * 1024
MAX_PAGE_SIZE = 200


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _event_files(directory):
    return sorted(Path(directory).glob("*.jsonl"))


def _last_sequence(path):
    try:
        with path.open(encoding="utf-8") as stream:
            last = 0
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and type(row.get("writer_seq")) is int:
                    last = max(last, row["writer_seq"])
            return last
    except OSError:
        return 0
    return 0


class EventWriter:
    """One process owns one writer_id; telemetry errors never escape emit()."""

    def __init__(self, directory, run_id, writer_id, instance_id=None):
        self.directory = Path(directory)
        self.run_id = str(run_id)
        self.writer_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(writer_id))
        self.instance_id = instance_id
        self._lock = threading.Lock()
        self.telemetry_gap = False
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Monitoring setup failure must not abort controller or crawler work.
            self.telemetry_gap = True
            self.sequence = 0
            self._sequence_available = False
            return
        sequence_path = self.directory / f"{self.writer_id}.seq.json"
        corrupted = False
        try:
            persisted = json.loads(sequence_path.read_text(encoding="utf-8")).get("last_seq", 0)
            persisted = persisted if type(persisted) is int and persisted >= 0 else 0
        except FileNotFoundError:
            persisted = 0
        except (OSError, ValueError, TypeError, AttributeError):
            self.telemetry_gap = True
            corrupted = True
            persisted = 0
        logged = max((_last_sequence(path) for path in _event_files(self.directory)
                      if path.name.startswith(self.writer_id + "_")), default=0)
        self.sequence = max(persisted, logged)
        self._sequence_available = not corrupted or logged > 0

    def emit(self, *, status, source, operation_id=None, live_id=None, segment_index=None,
             recording_id=None, details=None):
        with self._lock:
            if not self._sequence_available:
                self.telemetry_gap = True
                return None
            self.sequence += 1
            row = {
                "event_id": f"{self.run_id}:{self.writer_id}:{self.sequence}",
                "run_id": self.run_id,
                "instance_id": self.instance_id,
                "time_utc": utc_now(),
                "writer_id": self.writer_id,
                "writer_seq": self.sequence,
                "operation_id": operation_id,
                "live_id": str(live_id) if live_id is not None else None,
                "segment_index": segment_index,
                "recording_id": recording_id,
                "status": str(status),
                "details": details if isinstance(details, dict) else {},
                "source": str(source),
            }
            data = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            try:
                from src.utils.safe_io import atomic_json
                atomic_json(self.directory / f"{self.writer_id}.seq.json",
                            {"writer_id": self.writer_id, "last_seq": self.sequence})
                files = [path for path in _event_files(self.directory)
                         if path.name.startswith(self.writer_id + "_")]
                shard = files[-1] if files else self.directory / f"{self.writer_id}_000001.jsonl"
                if shard.exists() and shard.stat().st_size + len(data) > EVENT_SHARD_BYTES:
                    index = int(shard.stem.rsplit("_", 1)[-1]) + 1
                    shard = self.directory / f"{self.writer_id}_{index:06d}.jsonl"
                fd = os.open(str(shard), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
                try:
                    view = memoryview(data)
                    while view:
                        written = os.write(fd, view)
                        view = view[written:]
                finally:
                    os.close(fd)
                self.telemetry_gap = False
                return row
            except OSError:
                self.telemetry_gap = True
                return None


def _decode_cursor(cursor):
    if not cursor:
        return {}
    try:
        payload = base64.urlsafe_b64decode(cursor.encode("ascii") + b"==")
        value = json.loads(payload)
        seen = value.get("seen", {})
        if not isinstance(seen, dict) or any(type(seq) is not int or seq < 0 for seq in seen.values()):
            raise ValueError
        return seen
    except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
        raise ValueError("事件游标无效，请重新加载时间线")


def read_event_page(directory, *, cursor=None, limit=100, instance_id=None):
    """Read stable per-writer sequence cursors; shards are retained and never removed."""
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"事件分页大小必须是 1—{MAX_PAGE_SIZE}")
    seen = _decode_cursor(cursor)
    events = {}
    for path in _event_files(directory):
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    writer = item.get("writer_id")
                    seq = item.get("writer_seq")
                    event_id = item.get("event_id")
                    if not isinstance(writer, str) or type(seq) is not int or not isinstance(event_id, str):
                        continue
                    if seq <= seen.get(writer, 0):
                        continue
                    if instance_id is not None and item.get("instance_id") not in (None, instance_id):
                        continue
                    events[event_id] = item
        except OSError:
            continue
    # Cursor advances by each writer's sequence. Order pages by that same
    # sequence so wall-clock adjustments cannot make a later event hide an
    # earlier sequence from the next page. The UI sorts accumulated rows by UTC.
    ordered = sorted(events.values(), key=lambda item: (item["writer_id"], item["writer_seq"]))
    page = ordered[:limit]
    next_seen = dict(seen)
    for item in page:
        writer = item["writer_id"]
        next_seen[writer] = max(next_seen.get(writer, 0), item["writer_seq"])
    encoded = base64.urlsafe_b64encode(json.dumps({"seen": next_seen}, sort_keys=True,
                                                   separators=(",", ":")).encode()).decode().rstrip("=")
    return {"events": page, "cursor": encoded, "has_more": len(ordered) > limit}
