import copy
import json
from pathlib import Path

import pytest

from src.control.settings import build_environment, defaults, migrate_settings, validate
from src.control.server import Manager
from src.utils.runtime_options import RUNTIME_FIELDS, effective_settings, read_runtime_options
from src.control.server import verified_progress_records
from src.utils.safe_io import digest
from src.utils.segment_evidence import EvidenceCache


def test_runtime_field_contract_and_environment_fallbacks():
    assert set(RUNTIME_FIELDS) == set(read_runtime_options({}))
    assert read_runtime_options({"LIVE_PAGE_REFRESH_SECONDS": "60"})["page_refresh_seconds"] == 60
    assert read_runtime_options({"LIVE_MAX_ROOM_FAILURES": "99"})["max_room_failures"] == 3
    assert read_runtime_options({"LIVE_CHECKPOINT_SECONDS": "x"})["checkpoint_seconds"] == 30
    assert read_runtime_options({"LIVE_CHECKPOINT_SECONDS": True})["checkpoint_seconds"] == 30
    for name, spec in RUNTIME_FIELDS.items():
        assert spec["env"] == "LIVE_" + name.upper()
        assert spec["min"] <= spec["default"] <= spec["max"]


def test_settings_defaults_migrate_and_validate_ranges():
    config = defaults()
    assert config["room_gap_seconds"] == 0
    assert config["page_refresh_seconds"] == 300
    old = {key: value for key, value in config.items()
           if key not in RUNTIME_FIELDS}
    old["instances"] = [{key: value for key, value in row.items()
                          if key not in {"max_minutes", "cooldown_minutes",
                                         "room_gap_seconds", "page_refresh_seconds"}}
                         for row in config["instances"]]
    migrated = migrate_settings(old)
    assert migrated["max_room_failures"] == 3
    assert migrated["instances"][0]["room_gap_seconds"] is None
    migrated["cooldown_minutes"] = 0
    assert validate(migrated)["cooldown_minutes"] == 0
    for name, bad in (("max_collected", 2001), ("page_refresh_seconds", 1),
                      ("checkpoint_seconds", True), ("listen_timeout_seconds", 1.5)):
        invalid = copy.deepcopy(migrated)
        invalid[name] = bad
        with pytest.raises(ValueError):
            validate(invalid)
    invalid = copy.deepcopy(migrated)
    invalid["instances"][0]["cooldown_minutes"] = -1
    with pytest.raises(ValueError):
        validate(invalid)


def test_effective_instance_values_and_environment_are_snapshotted():
    config = defaults()
    config["room_gap_seconds"] = 40
    config["page_refresh_seconds"] = 600
    config["max_minutes"] = 20
    config["cooldown_minutes"] = 120
    instance = config["instances"][0]
    instance.update(max_minutes=12, cooldown_minutes=0, room_gap_seconds=5,
                    page_refresh_seconds=None)
    effective = effective_settings(config, instance)
    assert (effective["max_minutes"], effective["cooldown_minutes"]) == (12, 0)
    assert (effective["room_gap_seconds"], effective["page_refresh_seconds"]) == (5, 600)
    env = build_environment(config, instance, "urls.txt", "status.json", "stop", "run")
    assert env["LIVE_MAX_MIN"] == "12"
    assert env["LIVE_COOLDOWN_SEC"] == "0"
    assert env["LIVE_ROOM_GAP_SECONDS"] == "5"
    assert env["LIVE_PAGE_REFRESH_SECONDS"] == "600"
    assert env["LIVE_MAX_COLLECTED"] == str(config["max_collected"])


def test_final_json_summary_cache_invalidates_and_returns_independent_copy(tmp_path):
    staging = tmp_path / "_staging" / "browser_1" / "room_123"
    staging.mkdir(parents=True)
    video = staging / "segment.flv"
    video.write_bytes(b"synthetic media identity")
    final_path = staging / "segment_final.json"
    proof = {"schema_version": 1, "valid": True, "duration_source": "ffprobe",
             "duration_seconds": 60.0, "minimum_duration_seconds": 54.0,
             "video_sha256": digest(video)}
    final = {"recorded_files": [str(video)], "technical_validation": proof,
             "video_duration_seconds": 60.0, "max_minutes": 1,
             "recording_id": "record-1", "segment_index": 1,
             "record_start_t": 10.0, "record_end_t": 70.0,
             "responses": [{"url": "https://h5/mtop.live.detail.get/1.0/",
                            "t": 20.0, "body": json.dumps({"liveId": "123",
                            "isDigitalAnchorLive": True})}]}
    parsed_cache = {}
    evidence_cache = EvidenceCache()

    def run():
        final_stat, video_stat = final_path.stat(), video.stat()
        receipt = {"final_json": str(final_path), "video_path": str(video),
                   "final_size": final_stat.st_size, "final_mtime_ns": final_stat.st_mtime_ns,
                   "video_size": video_stat.st_size, "video_mtime_ns": video_stat.st_mtime_ns,
                   "validation_schema_version": 1, "final_sha256": digest(final_path),
                   "video_sha256": digest(video), "recording_id": "record-1",
                   "segment_index": 1, "video_duration_seconds": 60.0}
        observations = {}
        records, _, _ = verified_progress_records(
            {"schema_version": 2, "rooms": {"123": {"segments": [receipt]}}},
            tmp_path, observations, evidence_cache, parsed_cache)
        return records, observations

    final_path.write_text(json.dumps(final), encoding="utf-8")
    records, first = run()
    assert records["123"] == {"record-1"}
    first["123"]["record-1"]["all_true"] = "caller mutation"

    final["responses"][0]["body"] = json.dumps({"liveId": "123", "isDigitalAnchorLive": False})
    final_path.write_text(json.dumps(final), encoding="utf-8")
    records, second = run()
    assert records["123"] == {"record-1"}
    assert second["123"]["record-1"]["all_true"] is False
    second["123"]["record-1"]["all_true"] = "another caller mutation"
    _, third = run()
    assert third["123"]["record-1"]["all_true"] is False


def test_run_operation_timeout_uses_effective_instance_duration(tmp_path):
    config = defaults()
    config["instances"][0]["max_minutes"] = 80
    manager = Manager(control=tmp_path / "panel-control")
    run = {"settings": config}
    assert manager._operation_timeout("pause", run, 1) == 80 * 60 + 120
    assert manager._operation_timeout("pause", run, 2) == 20 * 60 + 120
    assert manager._operation_timeout("pause", {"settings": {"max_minutes": 20}}, 5) == 20 * 60 + 120
