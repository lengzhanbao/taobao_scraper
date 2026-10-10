"""Synthetic stability checks; all files stay in pytest's temporary E-drive tree."""
import json
from pathlib import Path

import pytest

from src.crawler.retry_state import RetryState, RetryStateError
from src.crawler.recovery import reconcile_room
from src.crawler.recording import _attempt_outcome, _save_attempt_diagnostic, _stderr_summary
from src.utils.safe_io import atomic_json, digest


def make_final(root, *, run_id="run-a", live_id="123", index=1, recording_id="rec-a"):
    attempt = root / f"room_{live_id}" / f"segment_{index}" / recording_id
    attempt.mkdir(parents=True)
    video = attempt / "video.flv"
    video.write_bytes(b"synthetic-video-content")
    final = attempt / "data_final.json"
    payload = {"run_id": run_id, "recording_id": recording_id, "segment_index": index,
               "live_url": f"https://tbzb.taobao.com/live?liveId={live_id}",
               "recorded_files": [str(video.resolve())], "video_duration_seconds": 54.0,
               "max_minutes": 1,
               "technical_validation": {"schema_version": 1, "valid": True,
                   "duration_source": "ffprobe", "duration_seconds": 54.0,
                   "minimum_duration_seconds": 54.0, "video_sha256": digest(video)}}
    atomic_json(final, payload)
    return final, video


def setup_reconcile(tmp_path, *, url_count=0, progress=None, candidates=1):
    stage = tmp_path / "staging"
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    urls = run_dir / "run_urls.txt"
    urls.write_text(f"https://tbzb.taobao.com/live?liveId=123,已录制{url_count}/3\n" if url_count else
                    "https://tbzb.taobao.com/live?liveId=123,待录制\n", encoding="utf-8")
    progress_path = tmp_path / "progress.json"
    if progress is not None:
        atomic_json(progress_path, progress)
    for idx in range(candidates):
        make_final(stage, recording_id=f"rec-{idx}")
    return stage, urls, progress_path


def run_reconcile(tmp_path, stage, urls, progress):
    return reconcile_room(urls_path=urls, progress_path=progress, staging_root=stage,
                          report_path=tmp_path / "run" / "recovery_1.jsonl", run_id="run-a",
                          instance_id=1, live_id="123", target=3)


def test_retry_failures_persist_cooldown_exhaustion_and_success_reset(tmp_path):
    now = [1000.0]
    state_path = tmp_path / "retry.json"
    state = RetryState(state_path, "run-a", cooldown_seconds=7200, max_failures=3,
                       clock=lambda: now[0])
    assert state.failed("123")["failure_count"] == 1
    restarted = RetryState(state_path, "run-a", cooldown_seconds=7200, max_failures=3,
                           clock=lambda: now[0])
    assert restarted.eligible("123") == (False, 7200)
    now[0] += 7201
    assert restarted.eligible("123")[0]
    assert restarted.failed("123")["failure_count"] == 2
    now[0] += 7201
    assert restarted.failed("123")["retry_exhausted"]
    assert restarted.eligible("123")[0] is False
    assert restarted.eligible("other")[0] is True
    other_instance = RetryState(tmp_path / "retry-instance2.json", "run-a", clock=lambda: now[0])
    assert other_instance.eligible("123")[0]
    restarted.succeeded("123")
    assert restarted.eligible("123")[0]
    restarted.failed("123")
    next_run = RetryState(state_path, "run-b", cooldown_seconds=7200, max_failures=3,
                          clock=lambda: now[0])
    assert not next_run.eligible("123")[0]
    assert next_run.entry("123")["failure_count"] == 0


def test_new_run_keeps_active_cooldown_and_resets_run_failures(tmp_path):
    now = [1000.0]
    path = tmp_path / "retry.json"
    first = RetryState(path, "run-a", clock=lambda: now[0])
    first.failed("123")

    new_run = RetryState(path, "run-b", clock=lambda: now[0])
    assert new_run.entry("123")["failure_count"] == 0
    assert new_run.eligible("123") == (False, 7200)
    restarted = RetryState(path, "run-b", clock=lambda: now[0])
    assert restarted.eligible("123") == (False, 7200)


def test_new_run_can_retry_after_preserved_cooldown_expires(tmp_path):
    now = [1000.0]
    path = tmp_path / "retry.json"
    RetryState(path, "run-a", clock=lambda: now[0]).failed("123")
    now[0] += 7201

    new_run = RetryState(path, "run-b", clock=lambda: now[0])
    assert new_run.eligible("123") == (True, 0)
    assert new_run.entry("123") == {}


def test_exhausted_room_new_run_respects_last_failure_deadline_only(tmp_path):
    now = [1000.0]
    path = tmp_path / "retry.json"
    state = RetryState(path, "run-a", clock=lambda: now[0])
    state.failed("123")
    now[0] += 7201
    state = RetryState(path, "run-a", clock=lambda: now[0])
    state.failed("123")
    now[0] += 7201
    state = RetryState(path, "run-a", clock=lambda: now[0])
    state.failed("123")
    assert state.entry("123")["retry_exhausted"] is True
    deadline = state.entry("123")["cooldown_until"]

    next_run = RetryState(path, "run-b", clock=lambda: now[0])
    assert next_run.entry("123")["failure_count"] == 0
    assert next_run.entry("123")["retry_exhausted"] is False
    assert next_run.entry("123")["cooldown_until"] == deadline
    assert next_run.eligible("123") == (False, 7200)
    assert next_run.eligible("other") == (True, 0)
    now[0] += 7201
    assert next_run.eligible("123") == (True, 0)


def test_new_run_drops_expired_cooldowns_but_keeps_other_active_room(tmp_path):
    now = [5000.0]
    path = tmp_path / "retry.json"
    state = RetryState(path, "run-a", clock=lambda: now[0])
    state.failed("123")
    state.failed("456")
    # Make one deadline expire while the other remains active.
    state.data["rooms"]["123"]["cooldown_until"] = now[0] - 1
    state._save()
    state.data["rooms"]["123"]["retry_exhausted"] = False
    # Keep a valid below-limit record by using only one failure for each room.
    state.data["rooms"]["123"]["failure_count"] = 1
    state.data["rooms"]["456"]["failure_count"] = 1
    state._save()
    next_run = RetryState(path, "run-b", clock=lambda: now[0])
    assert next_run.eligible("123") == (True, 0)
    assert next_run.eligible("456")[0] is False


def test_retry_state_corruption_fails_closed_and_does_not_reset(tmp_path):
    path = tmp_path / "retry.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RetryStateError):
        RetryState(path, "new-run")
    assert path.read_text(encoding="utf-8") == "{broken"


def test_reconcile_repairs_final_before_url_count_and_is_idempotent(tmp_path):
    stage, urls, progress = setup_reconcile(tmp_path)
    first = run_reconcile(tmp_path, stage, urls, progress)
    assert first["status"] == "reconciled"
    assert "已录制1/3" in urls.read_text(encoding="utf-8")
    state = json.loads(progress.read_text(encoding="utf-8"))
    assert state["rooms"]["123"]["count"] == 1
    assert len(state["rooms"]["123"]["segments"]) == 1
    second = run_reconcile(tmp_path, stage, urls, progress)
    assert second["status"] == "already_reconciled"
    assert len(json.loads(progress.read_text(encoding="utf-8"))["rooms"]["123"]["segments"]) == 1
    assert list((tmp_path / "run").glob("run_urls.txt.reconcile_bak_*"))


def test_reconcile_repairs_url_already_written_progress_not_yet(tmp_path):
    progress_state = {"schema_version": 2, "rooms": {"123": {"count": 0, "target": 3,
                                                                     "updated_t": 1, "segments": []}},
                      "legacy_counts": {}}
    stage, urls, progress = setup_reconcile(tmp_path, url_count=1, progress=progress_state)
    result = run_reconcile(tmp_path, stage, urls, progress)
    assert result["status"] == "reconciled"
    state = json.loads(progress.read_text(encoding="utf-8"))
    assert state["rooms"]["123"]["count"] == 1
    assert len(state["rooms"]["123"]["segments"]) == 1


def test_reconcile_ignores_previous_receipted_final_when_url_is_already_advanced(tmp_path):
    stage = tmp_path / "staging"
    old_final, old_video = make_final(stage, index=1, recording_id="rec-old")
    make_final(stage, index=2, recording_id="rec-new")
    old_data = json.loads(old_final.read_text(encoding="utf-8"))
    old_receipt = {"segment_index": 1, "recording_id": "rec-old",
                   "final_json": str(old_final.resolve()), "final_size": old_final.stat().st_size,
                   "final_mtime_ns": old_final.stat().st_mtime_ns,
                   "video_path": str(old_video.resolve()), "video_size": old_video.stat().st_size,
                   "video_mtime_ns": old_video.stat().st_mtime_ns,
                   "video_duration_seconds": 54.0, "validation_schema_version": 1,
                   "final_sha256": digest(old_final), "video_sha256": digest(old_video)}
    progress_state = {"schema_version": 2, "rooms": {"123": {"count": 1, "target": 3,
                                                                     "updated_t": 1, "segments": [old_receipt]}},
                      "legacy_counts": {}}
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    urls = run_dir / "run_urls.txt"
    urls.write_text("https://tbzb.taobao.com/live?liveId=123,已录制1/3\n", encoding="utf-8")
    progress = tmp_path / "progress.json"
    atomic_json(progress, progress_state)
    result = run_reconcile(tmp_path, stage, urls, progress)
    assert result["status"] == "reconciled"
    updated = json.loads(progress.read_text(encoding="utf-8"))["rooms"]["123"]
    assert updated["count"] == 2
    assert {row["recording_id"] for row in updated["segments"]} == {"rec-old", "rec-new"}


def test_reconcile_refuses_url_file_outside_run_control(tmp_path):
    stage, urls, progress = setup_reconcile(tmp_path)
    unsafe_url_file = tmp_path / "formal_urls.txt"
    unsafe_url_file.write_bytes(urls.read_bytes())
    before = unsafe_url_file.read_bytes()
    result = reconcile_room(urls_path=unsafe_url_file, progress_path=progress,
                            staging_root=stage, report_path=tmp_path / "run" / "recovery_1.jsonl",
                            run_id="run-a", instance_id=1, live_id="123", target=3)
    assert result["status"] == "needs_review"
    assert result["reason"] == "url_snapshot_outside_run_control"
    assert unsafe_url_file.read_bytes() == before


@pytest.mark.parametrize("kind", ["conflict", "missing"])
def test_reconcile_conflict_or_missing_proof_reports_without_mutation(tmp_path, kind):
    progress_state = {"schema_version": 2, "rooms": {"123": {"count": 0, "target": 3,
                                                                     "updated_t": 1, "segments": []}},
                      "legacy_counts": {}}
    stage, urls, progress = setup_reconcile(tmp_path, progress=progress_state,
                                             candidates=2 if kind == "conflict" else 1)
    if kind == "missing":
        final = next(stage.rglob("*_final.json"))
        data = json.loads(final.read_text(encoding="utf-8"))
        data["technical_validation"]["video_sha256"] = "0" * 64
        atomic_json(final, data)
    before_url, before_progress = urls.read_bytes(), progress.read_bytes()
    result = run_reconcile(tmp_path, stage, urls, progress)
    assert result["status"] == "needs_review"
    assert urls.read_bytes() == before_url
    assert progress.read_bytes() == before_progress


def test_ffmpeg_attempt_diagnostics_capture_stages_codes_and_redact_secrets(tmp_path):
    assert _attempt_outcome(0) == ("completed", None)
    stage, warning = _attempt_outcome(1)
    assert stage == "ffmpeg_exit" and warning
    assert _attempt_outcome(None, failure="connect_timeout")[0] == "connect_timeout"
    assert _attempt_outcome(0, failure="stream_stalled")[0] == "stream_stalled"
    log = tmp_path / "stderr.log"
    log.write_text("Cookie: session-secret\nAuthorization: bearer-secret\n"
                   "https://x.invalid/live?token=private&ok=1\n" + "x" * 3000, encoding="utf-8")
    summary = _stderr_summary(log)
    assert len(summary) <= 1200
    assert "session-secret" not in summary and "bearer-secret" not in summary
    assert "token=private" not in summary
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    _save_attempt_diagnostic(attempt_dir, 1, run_id="r", recording_id="rec", live_id="123",
                             log_path=log, returncode=1, failure_stage="ffmpeg_exit", warning=warning)
    diagnostic = json.loads((attempt_dir / "ffmpeg_attempt1_diagnostic.json").read_text(encoding="utf-8"))
    assert diagnostic["returncode"] == 1 and diagnostic["failure_stage"] == "ffmpeg_exit"
    assert "session-secret" not in diagnostic["stderr_summary"]
