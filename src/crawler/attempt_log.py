"""Append-only collection-attempt ledger.

The raw responses and media prove what was captured; this ledger explains why a
room entered or left the sample: not live, filtered as non-digital, network
failure, recording failure or archive failure. It never replaces raw evidence.
"""
import datetime
import json
import os
from pathlib import Path
import uuid

SCHEMA_VERSION = 1

SCAN_DECISIONS = {
    "success_platform_flag": ("recorded", None),
    "success_title_rule": ("recorded", None),
    "non_digital": ("skipped_not_eligible", "platform_flag_false"),
    "missing_digital_flag": ("skipped_not_eligible", "digital_flag_missing"),
    "network_or_navigation_error": ("failed_scan", "network_or_navigation_error"),
    "captcha_timeout_or_stop": ("failed_scan", "captcha_timeout_or_stop"),
    "journal_write_error": ("failed_scan", "journal_write_error"),
    "ambiguous_live_detail_response": ("failed_scan", "ambiguous_live_id"),
    "missing_stream_or_unattributed_response": ("failed_scan", "stream_or_detail_not_attributed"),
}


def utc_iso(epoch=None):
    moment = (datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
              if epoch is not None else datetime.datetime.now(datetime.timezone.utc))
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def classify_scan_result(result):
    """Map an existing console scan label to (decision, failure_reason)."""
    return SCAN_DECISIONS.get(str(result or ""), ("failed_scan", str(result or "unknown_scan_result")))


class CollectionAttemptWriter:
    """One writer per crawler instance; failures are reported, never raised."""

    def __init__(self, path, run_id="manual", instance_id=None):
        self.path = Path(path) if path else None
        self.run_id = str(run_id)
        self.instance_id = instance_id
        self.error = None

    def record(self, *, live_id, stage, decision, failure_reason=None,
               attempt_start_t=None, attempt_end_t=None, retry_count=None,
               recording_id=None, segment_index=None, recording_duration_seconds=None,
               target_duration_met=None, quality_state=None, detail=None,
               attempt_id=None):
        row = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "instance_id": self.instance_id,
            "live_id": str(live_id) if live_id is not None else None,
            "attempt_id": attempt_id or uuid.uuid4().hex,
            "stage": stage,
            "decision": decision,
            "failure_reason": failure_reason,
            "attempt_start_time": utc_iso(attempt_start_t),
            "attempt_end_time": utc_iso(attempt_end_t),
            "attempt_start_t": attempt_start_t,
            "attempt_end_t": attempt_end_t,
            "retry_count": retry_count,
            "recording_id": recording_id,
            "segment_index": segment_index,
            "recording_duration_seconds": recording_duration_seconds,
            "target_duration_met": target_duration_met,
            "quality_state": quality_state,
            "detail": detail or {},
        }
        if self.path is None:
            return row
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            fd = os.open(str(self.path), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
            finally:
                os.close(fd)
            self.error = None
        except OSError as error:
            self.error = error
            return row
        return row
