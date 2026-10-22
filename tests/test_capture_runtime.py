"""Synthetic capture-attribution and retry-configuration regressions."""
import json
import threading
from pathlib import Path
import pytest

from src.crawler.retry_state import RetryState
from src.crawler.recording import record_segment
from src.crawler.response_attribution import attribution_status, detail_live_urls
from src.utils.runtime_options import read_runtime_options


def test_runtime_options_clamp_each_integer_setting():
    values = read_runtime_options({"LIVE_ROOM_GAP_SECONDS": "0",
                                  "LIVE_LISTEN_TIMEOUT_SECONDS": "31",
                                  "LIVE_MAX_COLLECTED": "900"})
    assert values["room_gap_seconds"] == 0
    assert values["listen_timeout_seconds"] == 2
    assert values["max_collected"] == 900


def test_detail_url_pairing_and_late_or_missing_attribution():
    parsed = {"data": [{"liveId": "old", "liveUrl": "url-old"},
                       {"liveId": "current", "liveUrl": "url-current"}]}
    assert detail_live_urls(parsed) == [("old", "url-old"), ("current", "url-current")]
    assert attribution_status(["old"], "current", "nav-current") == "conflict"
    assert attribution_status([], "current", "nav-current") == "unknown"
    assert attribution_status(["current"], "current", "nav-current") == "matched"
    nested = {"rooms": [{"liveId": "current", "stream": {"liveUrl": "nested"}},
                        {"liveId": "other", "stream": {"liveUrl": "other-url"}}]}
    assert detail_live_urls(nested) == [("current", "nested"), ("other", "other-url")]
    ambiguous = {"liveId": "current", "liveUrl": "one",
                 "stream": {"liveUrl": "two"}}
    assert {url for live_id, url in detail_live_urls(ambiguous) if live_id == "current"} == {"one", "two"}


def test_retry_state_new_run_preserves_cooldown_with_lower_limit(tmp_path):
    path = tmp_path / "retry.json"
    path.write_text(json.dumps({"schema_version": 1, "run_id": "old", "rooms": {
        "123": {"failure_count": 3, "last_failure_t": 1000,
                "cooldown_until": 9000, "retry_exhausted": True,
                "last_reason": "old policy"}}}), encoding="utf-8")
    state = RetryState(path, "new", cooldown_seconds=60, max_failures=1, clock=lambda: 2000)
    entry = state.entry("123")
    assert entry["failure_count"] == 0
    assert entry["cooldown_until"] == 9000
    assert state.eligible("123", now=2000) == (False, 7000)


def test_retry_state_same_run_uses_current_attempt_limit(tmp_path):
    path = tmp_path / "retry.json"
    path.write_text(json.dumps({"schema_version": 1, "run_id": "same", "rooms": {
        "123": {"failure_count": 3, "last_failure_t": 1000,
                "cooldown_until": 1000, "retry_exhausted": False,
                "last_reason": "old policy"}}}), encoding="utf-8")
    state = RetryState(path, "same", cooldown_seconds=60, max_failures=2, clock=lambda: 2000)
    assert state.eligible("123", now=2000) == (False, None)


@pytest.mark.parametrize("failure", ["open", "flush"])
def test_journal_setup_failure_releases_capture_and_sink_locks(tmp_path, monkeypatch, failure):
    capture_lock, sink_lock = threading.Lock(), threading.Lock()
    state = {"collected": [], "journal": None, "scan_journal_path": None,
             "journal_status": {"error": False}, "sink_lock": sink_lock}
    original_open = Path.open

    class BrokenJournal:
        def write(self, _text):
            return None

        def flush(self):
            raise OSError("synthetic flush failure")

        def close(self):
            return None

    def fake_open(path, mode="r", *args, **kwargs):
        if path.name.endswith("_responses.jsonl"):
            if failure == "open":
                raise OSError("synthetic open failure")
            return BrokenJournal()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    with pytest.raises(OSError):
        record_segment(url="https://test", live_id="123", room_dir=tmp_path / "room",
                       stream_url="synthetic://stream", segment_index=1, seg_name="第一段",
                       ffmpeg="unused", ffprobe="unused", max_minutes=20,
                       user_agent="test", cookie_header="", state=state, lock=capture_lock,
                       page=object(), log=lambda *_: None, publish_status=lambda *_a, **_k: None)
    assert state["journal_error"] is True
    assert state["journal_status"]["error"] is True

    acquired = threading.Event()

    def listener_lock_path():
        with capture_lock:
            with sink_lock:
                acquired.set()

    thread = threading.Thread(target=listener_lock_path, daemon=True)
    thread.start()
    assert acquired.wait(1)
    thread.join(1)
