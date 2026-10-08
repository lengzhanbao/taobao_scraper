"""Synthetic regression cases for misleading timestamps and incomplete archives."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import time
import uuid
from unittest.mock import patch

import pytest

import finalize_7min_segments as recovery
import setup_quick
from src.control.server import archived_segment_records, verified_progress_records, Manager
from src.control.settings import defaults
from src.detector.detect_digital import aggregate_detail_responses, check_one
from src.parser import parse_data
from src.utils.safe_io import atomic_json, digest, probe_video
from src.utils.segment_evidence import (digital_observation, video_validation, inspect_archive,
                                       EvidenceCache, observation_counts)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "_control" / "evidence_tests" / (time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])


@pytest.fixture
def folder():
    path = ARTIFACTS / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


def detail(flag=True, lid="123", captured=1100, title="测试直播"):
    return {"t": captured, "url": "https://h5api.m.taobao.com/h5/mtop.roomstudio.live.detail.get/1.0/",
            "body": json.dumps({"liveId": lid, "isDigitalAnchorLive": flag,
                                "liveTitle": title, "accountName": "测试店"})}


def source_segment(study, finalized=True, responses=None):
    attempt = study / "_staging" / "browser_9223" / "room_123" / "第一段" / "attempt_1"
    attempt.mkdir(parents=True)
    video = attempt / "video.flv"
    video.write_bytes(b"synthetic video retained")
    payload = {"recording_id": "rec-123", "segment_index": 1, "max_minutes": 20,
               "recorded_files": [str(video.resolve())], "record_start_t": 1000,
               "record_end_t": 1120, "live_url": "https://tbzb.taobao.com/live?liveId=123",
               "responses": responses if responses is not None else [detail()]}
    payload["captured_count"] = len(payload["responses"])
    source = attempt / ("data_20261008_120000_final.json" if finalized else "data_20261008_120000.json")
    atomic_json(source, payload)
    return source, video, payload


def make_archive(folder, responses=None):
    source, video, payload = source_segment(folder, responses=responses)
    sessions = folder / "sessions"
    with patch.object(parse_data, "SESSIONS", str(sessions)), \
            patch.object(parse_data, "probe_video", return_value=600), contextlib.redirect_stdout(io.StringIO()):
        assert parse_data.process_room(str(source.parents[2]))
    manifest = next(sessions.glob("*/archive_manifest.json"))
    return manifest, source, video


def test_recovery_rejects_two_minute_video_despite_old_start_and_final(folder):
    source, video, _ = source_segment(folder, finalized=False)
    original = {path: path.read_bytes() for path in (source, video)}
    # Wall clock could be ten minutes or ten years later: measured duration wins.
    with patch("src.utils.segment_evidence.probe_video", return_value=120):
        rows = recovery.run(folder, "synthetic", folder / "report", create_finals=True)
    assert not rows[0]["technical_valid"]
    assert "120.0" in rows[0]["error"]
    assert not list((folder / "report").rglob("*_final.json"))
    assert all(path.read_bytes() == content for path, content in original.items())
    atomic_json(source.with_name(source.stem + "_final.json"), json.loads(source.read_text(encoding="utf-8")))
    with patch("src.utils.segment_evidence.probe_video", return_value=120):
        rows = recovery.run(folder, "synthetic", folder / "existing_final_report")
    assert len(rows) == 1 and not rows[0]["technical_valid"]


def test_recovery_attempt_and_existing_session_preserve_timestamps(folder):
    source, video, payload = source_segment(folder, finalized=False)
    (folder / "sessions" / "already_archived_123").mkdir(parents=True)
    before = source.read_bytes(), video.read_bytes()
    with patch("src.utils.segment_evidence.probe_video", return_value=600):
        default_rows = recovery.run(folder, "synthetic", folder / "report_only")
        rows = recovery.run(folder, "synthetic", folder / "candidates_report", create_finals=True)
    assert "candidate_json" not in default_rows[0]
    candidate = json.loads(Path(rows[0]["candidate_json"]).read_text(encoding="utf-8"))
    assert candidate["record_start_t"] == payload["record_start_t"]
    assert candidate["record_end_t"] == payload["record_end_t"]
    assert candidate["video_duration_seconds"] == 600
    assert source.read_bytes() == before[0] and video.read_bytes() == before[1]
    assert not source.with_name(source.stem + "_final.json").exists()


def test_recovery_missing_capture_proof_is_report_only(folder):
    source, _, payload = source_segment(folder, finalized=False)
    payload.pop("captured_count")
    atomic_json(source, payload)
    with patch("src.utils.segment_evidence.probe_video", return_value=600):
        rows = recovery.run(folder, "synthetic", folder / "report", create_finals=True)
    assert rows[0]["technical_valid"] and not rows[0]["candidate_eligible"]
    assert "candidate_json" not in rows[0]


def test_parser_measures_before_copying_short_legacy_final(folder):
    source, video, _ = source_segment(folder)
    before = source.read_bytes(), video.read_bytes()
    with patch.object(parse_data, "SESSIONS", str(folder / "sessions")), \
            patch.object(parse_data, "probe_video", return_value=120):
        with pytest.raises(ValueError, match="120.0"):
            parse_data.process_room(str(source.parents[2]))
    assert not (folder / "sessions").exists()
    assert (source.read_bytes(), video.read_bytes()) == before


@pytest.mark.parametrize("missing_kind", ["raw_json", "video", "comments", "summary"])
def test_archive_missing_member_never_counts_as_complete(folder, missing_kind):
    manifest_path, source, video = make_archive(folder)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["summary"] if missing_kind == "summary" else manifest["segments"][0][missing_kind]
    entry["destination"] = str(manifest_path.parent / ("absent_" + missing_kind))
    atomic_json(manifest_path, manifest, backup=True)
    with patch("src.utils.segment_evidence.probe_video", return_value=600):
        _, records, _, errors = inspect_archive(manifest_path, "synthetic")
    assert not records and errors
    assert source.exists() and video.exists()


def test_archive_captured_empty_comments_are_valid(folder):
    empty = {"t": 1100, "url": "https://h5api.m.taobao.com/h5/mtop.taobao.iliad.comment.query.latest/1.0/",
             "body": json.dumps({"ret": ["SUCCESS::调用成功"], "data": {"comments": []}})}
    manifest_path, _, _ = make_archive(folder, responses=[detail(), empty])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["segments"][0]["comment_extraction"]["state"] == "empty_confirmed"
    with patch("src.utils.segment_evidence.probe_video", return_value=600):
        _, records, observations, errors = inspect_archive(manifest_path, "synthetic")
    assert records == {"rec-123"} and not errors
    assert observations["rec-123"]["all_true"] is True


def test_archive_comment_capture_failure_differs_from_empty(folder):
    failed = {"t": 1100, "url": "https://h5api.m.taobao.com/h5/mtop.taobao.iliad.comment.query.latest/1.0/",
              "body": "invalid JSON"}
    manifest_path, _, _ = make_archive(folder, responses=[detail(), failed])
    with patch("src.utils.segment_evidence.probe_video", return_value=600):
        _, records, _, errors = inspect_archive(manifest_path, "synthetic")
    assert not records and any("弹幕提取失败" in error for error in errors)
    assert parse_data.comment_capture_state([], 0)["state"] == "not_captured"


def test_archive_actual_duration_and_same_size_corruption(folder):
    manifest_path, _, _ = make_archive(folder)
    with patch("src.utils.segment_evidence.probe_video", return_value=120):
        _, records, _, errors = inspect_archive(manifest_path, "synthetic")
    assert not records and errors
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archived_video = Path(manifest["segments"][0]["video"]["destination"])
    cache = EvidenceCache()
    with patch("src.utils.segment_evidence.probe_video", return_value=600) as probe:
        assert inspect_archive(manifest_path, "synthetic", cache)[1] == {"rec-123"}
        assert inspect_archive(manifest_path, "synthetic", cache)[1] == {"rec-123"}
        assert probe.call_count == 1
        archived_video.write_bytes(b"x" * archived_video.stat().st_size)
        assert not inspect_archive(manifest_path, "synthetic", cache)[1]


def test_probe_timeout_is_retained_as_a_segment_issue(folder):
    manifest_path, source, video = make_archive(folder)
    before = source.read_bytes(), video.read_bytes()
    with patch("src.utils.segment_evidence.probe_video", side_effect=subprocess.TimeoutExpired("synthetic", 40)):
        _, records, _, issues = inspect_archive(manifest_path, "synthetic")
        rows = recovery.run(folder, "synthetic", folder / "timeout_report")
    assert not records and issues
    assert not rows[0]["technical_valid"] and "timed out" in rows[0]["error"]
    assert before == (source.read_bytes(), video.read_bytes())


def test_old_size_only_manifest_and_wallclock_final_are_unverified(folder):
    source, video, payload = source_segment(folder)
    atomic_json(folder / "sessions" / "old_123" / "archive_manifest.json",
                {"room_id": "123", "source_preserved": True,
                 "entries": [{"recording_id": "rec-123", "destination": str(video), "bytes": video.stat().st_size}]})
    assert archived_segment_records(folder) == {"123": set()}
    payload["video_duration_seconds"] = 600
    atomic_json(source, payload)
    receipt = {"recording_id": "rec-123", "segment_index": 1, "final_json": str(source),
               "final_size": source.stat().st_size, "final_mtime_ns": source.stat().st_mtime_ns,
               "video_path": str(video), "video_size": video.stat().st_size,
               "video_mtime_ns": video.stat().st_mtime_ns, "video_duration_seconds": 600}
    records, legacy, states = verified_progress_records(
        {"schema_version": 2, "rooms": {"123": {"count": 3, "segments": [receipt]}}}, folder)
    assert not records and legacy["123"] == 3 and states["123"] == "unverified"


def test_recorded_flags_are_observations_without_reclassification():
    data = {"record_start_t": 1000, "record_end_t": 1600,
            "responses": [detail(True), detail(False, captured=1400), detail(False, lid="999")]}
    result = digital_observation(data, "123")
    assert result["all_true"] is False
    assert result["flag_summary"]["true_count"] == result["flag_summary"]["false_count"] == 1
    assert result["unlinked_response_count"] == 1
    assert result["maximum_observation_gap_seconds"] == 300
    data["responses"] = [detail(True, title="智能主播"), detail(False, lid="999")]
    assert digital_observation(data, "123")["all_true"] is True
    assert digital_observation(data, "123")["title_keywords"] == ["智能主播"]
    assert digital_observation({"responses": []}, "123")["all_true"] is None
    assert observation_counts({"a": result, "b": {"all_true": None}}) == {"all_true": 0, "not_all_true": 1, "missing": 1}


def test_detection_uses_later_false_reply_and_preserves_title_rule():
    responses = [detail(True), detail(False)]
    assert aggregate_detail_responses(responses, "123")["platformHasFalse"]
    assert not aggregate_detail_responses(responses, "123")["platformAllTrue"]
    page = type("Page", (), {"listen": type("Listener", (), {"start": lambda *args: None})(),
                              "get": lambda *args, **kwargs: None})()
    with patch("src.detector.detect_digital.collect_detail_responses", return_value=responses):
        assert check_one(page, "123")["status"] == "mixed"
    responses[1] = detail(False, title="虚拟主播")
    with patch("src.detector.detect_digital.collect_detail_responses", return_value=responses):
        result = check_one(page, "123")
    assert result["status"] == "digital" and result["platformHasFalse"]
    assert result["observationBasis"] == "captured_detection_window_only"


def test_nonfinite_video_duration_is_rejected():
    response = subprocess.CompletedProcess([], 0, stdout=json.dumps({"format": {"duration": "nan"},
                         "streams": [{"codec_type": "video"}]}).encode())
    with patch("src.utils.safe_io.subprocess.run", return_value=response):
        with pytest.raises(ValueError):
            probe_video("synthetic.flv", "synthetic")


def test_manager_deduplicates_record_ids_across_instances_and_keeps_flags(folder):
    config = defaults(folder)
    for item in config["instances"]:
        item["enabled"] = item["id"] in (1, 2)
    for instance, flag in ((1, True), (2, False)):
        attempt = folder / "_staging" / f"browser_{9222 + instance}" / "room_123" / "segment"
        attempt.mkdir(parents=True)
        video = attempt / "video.flv"
        video.write_bytes(b"synthetic" + bytes([instance]))
        data = {"recording_id": f"rec-{instance}", "segment_index": instance,
                "recorded_files": [str(video)], "max_minutes": 20, "video_duration_seconds": 600,
                "record_start_t": 1000, "record_end_t": 1600, "responses": [detail(flag)],
                "technical_validation": video_validation(video, {"max_minutes": 20}, "synthetic", probe=lambda *_: 600)}
        final = attempt / "data_final.json"
        atomic_json(final, data)
        receipt = {"recording_id": f"rec-{instance}", "segment_index": instance,
                   "final_json": str(final), "final_size": final.stat().st_size,
                   "final_mtime_ns": final.stat().st_mtime_ns, "final_sha256": digest(final),
                   "video_path": str(video), "video_size": video.stat().st_size,
                   "video_mtime_ns": video.stat().st_mtime_ns, "video_sha256": digest(video),
                   "video_duration_seconds": 600, "validation_schema_version": 1}
        atomic_json(folder / "_control" / f"progress_{9222 + instance}.json",
                    {"schema_version": 2, "rooms": {"123": {"count": 1, "segments": [receipt]}}})
        config_dir = folder / "_config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / f"urls_{instance}.txt").write_text("https://tbzb.taobao.com/live?liveId=123", encoding="utf-8")
    state = Manager(config, control=folder / "panel_control").state()
    assert state["totals"]["rooms"] == 1
    assert state["totals"]["valid_segments"] == 2
    assert state["totals"]["digital_markers"] == {"all_true": 1, "not_all_true": 1, "missing": 0}


def test_setup_nonzero_ffmpeg_status_is_not_success():
    failure = subprocess.CompletedProcess([], 1, stdout="", stderr="synthetic missing ffmpeg")
    with patch("setup_quick.subprocess.run", return_value=failure), contextlib.redirect_stdout(io.StringIO()):
        assert not setup_quick.run_command(["ffmpeg", "-version"], check=False)
        assert not setup_quick.check_ffmpeg()
