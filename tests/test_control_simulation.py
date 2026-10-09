"""Retained, synthetic self-checks. Never opens a browser or real research data."""
import ast
import contextlib
import copy
import datetime
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / "_control" / "selfchecks" / (time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])
ARTIFACTS.mkdir(parents=True, exist_ok=False)
os.environ["LIVE_STUDY_ROOT"] = str(ARTIFACTS / "study")

from src.control.settings import defaults, validate, build_environment, read_url_list
from src.control.server import (Manager, handler_for, process_birth, verified_progress_counts,
                                verified_progress_records, archived_segment_counts,
                                archived_segment_records, compatible_existing_service)
from src.utils.safe_io import atomic_json, verified_copy, PortLock, digest
from src.utils.segment_evidence import video_validation, validation_matches
from src.crawler.recording import record_segment
from src.crawler.runtime_control import apply_control_command, pause_requested, wait_while_paused
from src.control.event_store import EventWriter, read_event_page
from src.control.operation_store import OperationStore
from src.parser import parse_data


class Checks(unittest.TestCase):
    def folder(self, name):
        folder = ARTIFACTS / name
        folder.mkdir(parents=True, exist_ok=False)
        return folder

    def config(self, folder):
        config = defaults(folder / "study")
        (Path(config["study_root"]) / "_config").mkdir(parents=True)
        for row in config["instances"]:
            row["enabled"] = row["id"] == 1
        return config

    def test_01_validation_and_environment(self):
        config = defaults(ARTIFACTS / "validation")
        self.assertEqual(validate(config)["instances"][3]["segments"], 4)
        for field, value in (("max_minutes", 0), ("max_minutes", True), ("batch_rooms", 0),
                             ("batch_rooms", -1), ("batch_rooms", True), ("batch_rooms", 1.5),
                             ("python", "relative.exe"), ("cooldown_minutes", -1)):
            bad = copy.deepcopy(config)
            bad[field] = value
            with self.assertRaises(ValueError):
                validate(bad)
        env = build_environment(config, config["instances"][3], "snapshot", "status", "stop", "run")
        self.assertEqual(env["LIVE_MAX_ROUND"], "4")
        self.assertEqual(env["LIVE_MAX_MIN"], "20")
        self.assertEqual(env["LIVE_COOLDOWN_SEC"], "7200")
        self.assertEqual(env["LIVE_BATCH_ROOMS"], "6")
        for threshold in (1, 5, 101, 1000000):
            config["batch_rooms"] = threshold
            self.assertEqual(validate(config)["batch_rooms"], threshold)
        env = build_environment(config, config["instances"][0], "snapshot", "status", "stop", "run", "pause.json")
        self.assertEqual(env["LIVE_PAUSE_FILE"], "pause.json")

    def test_02_url_deduplication(self):
        folder = self.folder("urls")
        path = folder / "urls.txt"
        path.write_text("https://tbzb.taobao.com/live?liveId=123,已录制1/3\n"
                        "https://tbzb.taobao.com/live?liveId=123,已录制2/3,已录制2/3\n"
                        "# https://tbzb.taobao.com/live?liveId=999\n", encoding="utf-8")
        rows = read_url_list(path, 4)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["target"], 4)

    def test_03_verified_copy_and_backup(self):
        folder = self.folder("copies")
        source, dest = folder / "source.bin", folder / "copy.bin"
        source.write_bytes(b"abcdef")
        entry = verified_copy(source, dest)
        self.assertEqual(entry["bytes"], 6)
        dest.write_bytes(b"abcdeg")
        with self.assertRaises(ValueError):
            verified_copy(source, dest)
        self.assertEqual(source.read_bytes(), b"abcdef")
        self.assertEqual(dest.read_bytes(), b"abcdeg")
        settings = folder / "settings.json"
        atomic_json(settings, {"x": 1})
        atomic_json(settings, {"x": 2}, backup=True)
        backups = list(folder.glob("settings.json.bak_*"))
        self.assertEqual(json.loads(backups[0].read_text())["x"], 1)

    def test_04_port_lock_and_process_identity(self):
        folder = self.folder("locks")
        first = PortLock(folder / "port.lock")
        try:
            with self.assertRaises(RuntimeError):
                PortLock(folder / "port.lock")
        finally:
            first.close()
        second = PortLock(folder / "port.lock")
        second.close()
        self.assertIsNotNone(process_birth(os.getpid()))

    def test_05_mocked_launch_snapshot_and_graceful_stop(self):
        folder = self.folder("manager")
        config = self.config(folder)
        source = Path(config["study_root"]) / "_config" / "urls_1.txt"
        source.write_text("https://tbzb.taobao.com/live?liveId=123,已录制1/3\n", encoding="utf-8")
        before = source.read_bytes()
        manager = Manager(config, control=folder / "control")
        class Child:
            pid = 12345
        with patch.object(manager, "preflight", return_value={"ok": True}), \
                patch("src.control.server.subprocess.Popen", return_value=Child()) as popen, \
                patch("src.control.server.process_birth", return_value="fake-birth"):
            run = manager.start()
            kwargs = popen.call_args.kwargs
            self.assertEqual(kwargs["env"]["LIVE_MAX_ROUND"], "3")
            self.assertNotEqual(kwargs["env"]["LIVE_URLS_FILE"], str(source))
            self.assertEqual(len(run["jobs"]), 1)
            self.assertEqual(manager.stop()["requested"], 1)
            self.assertTrue(Path(run["jobs"][0]["stop_file"]).exists())
            restored = Manager(config, control=folder / "control")
            self.assertTrue(restored.jobs()[0]["alive"])
        self.assertEqual(source.read_bytes(), before)

    def test_06_http_local_routes_and_origin_guard(self):
        folder = self.folder("http")
        manager = Manager(self.config(folder), control=folder / "control")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(manager, "synthetic-token", "http://127.0.0.1:0"))
        origin = f"http://127.0.0.1:{server.server_port}"
        server.RequestHandlerClass = handler_for(manager, "synthetic-token", origin)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(origin + "/", timeout=5) as response:
                self.assertIn("采集控制台", response.read().decode())
            with urlopen(origin + "/api/state", timeout=5) as response:
                self.assertEqual(json.load(response)["token"], "synthetic-token")
            malicious = Request(origin + "/api/start", b"{}", headers={"Origin": "http://external.test", "X-Control-Token": "synthetic-token"})
            with self.assertRaises(HTTPError) as error:
                urlopen(malicious, timeout=5)
            self.assertEqual(error.exception.code, 403)
            legitimate = Request(origin + "/api/settings", json.dumps(manager.settings).encode(),
                                 headers={"Origin": origin, "X-Control-Token": "synthetic-token"})
            with urlopen(legitimate, timeout=5) as response:
                self.assertEqual(response.status, 200)
            with urlopen(origin + '/api/urls?instance=1', timeout=5) as response:
                document = json.load(response)
            request = Request(origin + '/api/urls', json.dumps({'instance_id': 1, 'text': '987654321',
                              'revision': document['revision'], 'mode': 'append'}).encode(),
                              headers={'Origin': origin, 'X-Control-Token': 'synthetic-token'})
            with urlopen(request, timeout=5) as response:
                saved = json.load(response)
                self.assertIn('987654321', [row['live_id'] for row in saved['rows']])
            correction = Request(origin + '/api/urls/correct-count', json.dumps({
                'instance_id': 1, 'live_id': '987654321', 'count': 0,
                'reason': '合成路由测试', 'confirmed': True, 'revision': saved['revision']}).encode(),
                headers={'Origin': origin, 'X-Control-Token': 'synthetic-token'})
            with urlopen(correction, timeout=5) as response:
                self.assertTrue(json.load(response)['correction_id'])
            malicious_urls = Request(origin + '/api/urls', b'{}', headers={'Origin': 'http://external.test',
                                     'X-Control-Token': 'synthetic-token'})
            with self.assertRaises(HTTPError) as error:
                urlopen(malicious_urls, timeout=5)
            self.assertEqual(error.exception.code, 403)
            with patch.object(manager, "set_paused", return_value={"paused": True}) as pause:
                for route, expected in (("pause", True), ("resume", False)):
                    request = Request(origin + "/api/" + route, b'{"instance_id":2}',
                                      headers={"Origin": origin, "X-Control-Token": "synthetic-token"})
                    with urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                    pause.assert_called_with(2, expected, None)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def make_room(self, root, lid, flag=True, segments=(1, 2, 3), run_id=None):
        room = root / f"room_{lid}"
        for index in segments:
            segment = room / f"segment_{index}"
            segment.mkdir(parents=True)
            video = segment / "video.flv"
            video.write_bytes(f"synthetic room {lid} segment {index}".encode())
            body = {"liveId": str(lid), "liveTitle": "测试直播", "accountName": "测试店",
                    "isDigitalAnchorLive": flag, "viewCount": "12"}
            atomic_json(segment / f"data_20261008_12000{index}_final.json", {
                "live_url": f"https://tbzb.taobao.com/live?liveId={lid}", "recorded_files": [str(video)],
                "recording_id": f"{lid}-{index}", "segment_index": index,
                **({"run_id": run_id} if run_id is not None else {}),
                "record_start_t": 1780000000 + index * 8000, "record_end_t": 1780000600 + index * 8000,
                "responses": [{"t": 1780000000, "url": "https://h5api.m.taobao.com/h5/mtop.roomstudio.live.detail.get/1.0/",
                               "body": json.dumps(body, ensure_ascii=False)}],
            })
        return room

    def test_07_six_room_archive_and_conflict(self):
        folder = self.folder("archive")
        rooms = [self.make_room(folder / "staging", lid, flag=lid != 6,
                                run_id="synthetic-run-1") for lid in range(1, 7)]
        with patch.object(parse_data, "SESSIONS", str(folder / "sessions")), \
                patch.object(parse_data, "probe_video", return_value=600), contextlib.redirect_stdout(io.StringIO()):
            for room in rooms:
                self.assertTrue(parse_data.process_room(str(room)))
            self.assertTrue(parse_data.process_room(str(rooms[0])))
            first_session = next(p for p in (folder / "sessions").iterdir() if p.name.endswith("_1"))
            manifest = json.loads((first_session / "archive_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["entries"]), 6)
            self.assertEqual({entry["run_id"] for entry in manifest["segments"]}, {"synthetic-run-1"})
            self.assertTrue(manifest["source_preserved"])
            video = next((first_session / "video").glob("*.flv"))
            video.write_bytes(b"conflicting synthetic archive")
            with self.assertRaises(ValueError):
                parse_data.process_room(str(rooms[0]))
        self.assertEqual(len(list((folder / "staging").rglob("*.flv"))), 18)
        self.assertEqual(len(list((folder / "staging").rglob("*_final.json"))), 18)
        import csv
        false_session = next(p for p in (folder / "sessions").iterdir() if p.name.endswith("_6"))
        with next((false_session / "crawler").glob("lives_summary_*.csv")).open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["段落是否含false"], "是")

    def test_08_mock_recording_and_failed_final_write(self):
        folder = self.folder("recording")
        def attempt(fail_final):
            state = {"collected": [{"t": 1, "url": "test", "body": "{}", "response_sequence": 1}], "journal": None}
            phases = []
            event_writer = EventWriter(folder / f"events_{fail_final}", "synthetic-run", "worker_1", 1)
            blocker = folder / f"event_blocker_{fail_final}.bin"
            blocker.write_bytes(b"not a directory")
            event_writer.directory = blocker
            def publish(phase, **kwargs):
                phases.append(phase)
                event_writer.emit(status=phase, source="worker")
            class Child:
                pid = 12345
                calls = 0
                def poll(self):
                    self.calls += 1
                    return None if self.calls < 4 else 0
            def launch(command, **kwargs):
                Path(command[-1]).write_bytes(b"synthetic" * 512)
                return Child()
            def write(path, data, **kwargs):
                if fail_final and str(path).endswith("_final.json"):
                    raise OSError("synthetic disk error")
                atomic_json(path, data, **kwargs)
            with patch("src.crawler.recording.subprocess.Popen", side_effect=launch), \
                    patch("src.crawler.recording.probe_video", return_value=600), \
                    patch("src.crawler.recording.stop_recorder"), \
                    patch("src.crawler.recording.time.sleep"), \
                    patch("src.crawler.recording.atomic_json", side_effect=write):
                result = record_segment(url="https://test", live_id="123", room_dir=folder / str(fail_final),
                                      stream_url="synthetic://stream", segment_index=2, seg_name="第二段",
                                      ffmpeg="synthetic", ffprobe="synthetic", max_minutes=20, user_agent="test",
                                      cookie_header="", state=state, lock=threading.Lock(), page=None,
                                      log=lambda message: None, publish_status=publish, run_id="synthetic-run")
                self.assertIn("validating", phases)
                self.assertEqual(phases[-1], "segment_failed" if fail_final else "segment_completed")
                self.assertTrue(event_writer.telemetry_gap)
                return result
        self.assertTrue(attempt(False))
        self.assertFalse(attempt(True))
        self.assertEqual(len(list(folder.rglob("*.flv"))), 2)
        finals = list(folder.rglob("*_final.json"))
        self.assertEqual(len(finals), 1)
        self.assertEqual(json.loads(finals[0].read_text(encoding="utf-8"))["run_id"], "synthetic-run")

    def test_23_event_disabled_vs_write_failure_same_media_validity_and_archive(self):
        folder = self.folder("event_archive_equivalence")
        mode_root = folder / "captured_with_event_failure"
        room = mode_root / "staging" / "room_123"
        room.mkdir(parents=True)
        fixed_datetime = datetime.datetime(2026, 10, 9, 12, 0, 0)
        datetime_values = iter(fixed_datetime + datetime.timedelta(seconds=index) for index in range(3))
        fixed_timestamp = 1791547200
        real_monotonic = time.monotonic

        class FixedDatetime:
            @staticmethod
            def now():
                return next(datetime_values)

        class FixedTime:
            time = staticmethod(lambda: fixed_timestamp)
            monotonic = staticmethod(real_monotonic)
            sleep = staticmethod(lambda _seconds: None)

        capture_writer = EventWriter(mode_root / "events", "same-synthetic-run", "worker_1", 1)
        blocker = mode_root / "event_write_blocker.bin"
        blocker.write_bytes(b"not a directory")
        capture_writer.directory = blocker

        phases = []
        def publish(phase, **_fields):
            phases.append(phase)
            capture_writer.emit(status=phase, source="worker")

        class Child:
            pid = 54321
            def __init__(self):
                self.calls = 0
            def poll(self):
                self.calls += 1
                return None if self.calls < 4 else 0

        def launch(command, **_kwargs):
            Path(command[-1]).write_bytes(b"identical synthetic video" * 256)
            return Child()

        identifiers = iter(uuid.UUID(int=value) for value in (1, 2, 3))
        with patch("src.crawler.recording.subprocess.Popen", side_effect=launch), \
                patch("src.crawler.recording.probe_video", return_value=600), \
                patch("src.crawler.recording.stop_recorder"), \
                patch("src.crawler.recording.datetime", SimpleNamespace(datetime=FixedDatetime)), \
                patch("src.crawler.recording.time", FixedTime), \
                patch("src.crawler.recording.uuid", SimpleNamespace(uuid4=lambda: next(identifiers))), \
                patch("src.utils.segment_evidence.time", FixedTime):
            for index, seg_name in enumerate(("第一段", "第二段", "第三段"), 1):
                state = {"collected": [{"t": fixed_timestamp, "url": "test", "body": "{}",
                                        "response_sequence": 1}], "journal": None}
                self.assertTrue(record_segment(url="https://tbzb.taobao.com/live?liveId=123",
                    live_id="123", room_dir=room, stream_url="synthetic://stream",
                    segment_index=index, seg_name=seg_name, ffmpeg="synthetic", ffprobe="synthetic",
                    max_minutes=20, user_agent="synthetic", cookie_header="", state=state,
                    lock=threading.Lock(), page=None, log=lambda *_: None,
                    publish_status=publish, run_id="same-synthetic-run"))
        self.assertIn("segment_completed", phases)
        self.assertTrue(capture_writer.telemetry_gap)

        final_files = sorted(room.rglob("*_final.json"))
        videos = sorted(room.rglob("*.flv"))
        self.assertEqual((len(final_files), len(videos)), (3, 3))
        source_snapshot = {path: path.read_bytes() for path in [*final_files, *videos]}
        validity_results = [validation_matches(json.loads(path.read_text(encoding="utf-8")))
                            for path in final_files]
        self.assertEqual(validity_results, [True, True, True])

        results = {}
        for event_mode in ("disabled", "write_failure"):
            output_root = folder / f"archive_{event_mode}"
            if event_mode == "write_failure":
                output_root.mkdir(parents=True)
                archive_writer = EventWriter(output_root / "events", "same-synthetic-run", "controller")
                archive_blocker = output_root / "event_write_blocker.bin"
                archive_blocker.write_bytes(b"not a directory")
                archive_writer.directory = archive_blocker
                self.assertIsNone(archive_writer.emit(status="archive_start", source="controller"))
                self.assertTrue(archive_writer.telemetry_gap)
            with patch.object(parse_data, "SESSIONS", str(output_root / "sessions")), \
                    patch.object(parse_data, "probe_video", return_value=600), \
                    patch("src.utils.segment_evidence.time", FixedTime), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(parse_data.process_room(str(room)))
            observations, diagnostics = {}, {}
            with patch("src.utils.segment_evidence.probe_video", return_value=600), \
                    patch("src.utils.segment_evidence.time", FixedTime):
                records = archived_segment_records(output_root, "synthetic", observations, diagnostics)
            self.assertFalse(any(diagnostics.values()))
            self.assertEqual(len(records.get("123", set())), 3)
            self.assertEqual(len(observations.get("123", {})), 3)
            manifest_path = next((output_root / "sessions").glob("*/archive_manifest.json"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["segment_count"], 3)
            results[event_mode] = {"records": records, "observations": observations,
                                   "manifest": manifest}

        disabled, failed = results["disabled"], results["write_failure"]
        self.assertEqual(disabled["records"], failed["records"])
        self.assertEqual(disabled["observations"], failed["observations"])
        def remove_archive_paths(value):
            if isinstance(value, dict):
                return {key: remove_archive_paths(item) for key, item in value.items()
                        if key not in ("source", "destination")}
            if isinstance(value, list):
                return [remove_archive_paths(item) for item in value]
            return value
        self.assertEqual(remove_archive_paths(disabled["manifest"]),
                         remove_archive_paths(failed["manifest"]))
        self.assertEqual({path: path.read_bytes() for path in [*final_files, *videos]}, source_snapshot)

    def test_09_legacy_plain_url_count_and_target_override(self):
        folder = self.folder("crawler_helpers")
        path = folder / "urls.txt"
        path.write_text("https://tbzb.taobao.com/live?liveId=1234,待录制\nhttps://tbzb.taobao.com/live?liveId=123\n", encoding="utf-8")
        tree = ast.parse((ROOT / "src" / "crawler" / "taobao_crawler.py").read_text(encoding="utf-8"))
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"read_urls", "mark_recorded"}]
        import re
        namespace = {"os": os, "json": json, "time": time, "re": re, "URLS_FILE": str(path),
                     "MAX_ROUND": 7, "MAX_ROUND_OVERRIDE": True, "atomic_json": atomic_json}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "synthetic_helpers", "exec"), namespace)
        namespace["mark_recorded"]("123", 2, 7)
        rows = namespace["read_urls"]()
        self.assertEqual(rows[0][2], 0)
        self.assertEqual(rows[1][2:], (2, 7))

    def test_10_independent_pause_resume_and_snapshot_progress(self):
        folder = self.folder("pause_manager")
        config = self.config(folder)
        config["instances"][1]["enabled"] = True
        for index in (1, 2):
            (Path(config["study_root"]) / "_config" / f"urls_{index}.txt").write_text(
                f"https://tbzb.taobao.com/live?liveId={index},已录制1/3\n", encoding="utf-8")
        manager = Manager(config, control=folder / "control")
        class Child:
            pid = 12345
        with patch.object(manager, "preflight", return_value={"ok": True}), \
                patch("src.control.server.subprocess.Popen", return_value=Child()), \
                patch("src.control.server.process_birth", return_value="fake-birth"):
            run = manager.start()
            self.assertEqual(len(run["jobs"]), 2)
            accepted = manager.set_paused(1, True, "pause-first")
            self.assertEqual(accepted["status"], "pending")
            self.assertFalse(pause_requested(run["jobs"][0]["pause_file"]))
            self.assertFalse(pause_requested(run["jobs"][1]["pause_file"]))
            # Worker applies pause only at safe checkpoint and persists matching receipt.
            job = run["jobs"][0]
            receipt_dir = Path(job["receipt_dir"])
            processed = apply_control_command(job["command_file"], Path(run["run_dir"]) / "commands_lock_1", job["stop_file"],
                job["pause_file"], receipt_dir, receipt_dir / "state.json", run_id=run["run_id"],
                instance_id=1, processed_seq=0,
                acknowledge=lambda command, status, details: _test_ack(receipt_dir, command, status, details))
            self.assertEqual(processed[0], accepted["command_seq"])
            self.assertTrue(pause_requested(job["pause_file"]))
            self.assertEqual(json.loads((receipt_dir / "pause-first.json").read_text())["status"], "applied")
            restored = Manager(config, control=folder / "control")
            self.assertTrue(restored.jobs()[0]["pause_requested"])
            # Edit the next run's directory and targets; active progress must stay on its snapshot.
            next_config = copy.deepcopy(config)
            next_config["study_root"] = str(folder / "next_study")
            next_config["instances"][0]["segments"] = 1
            next_config["instances"][1]["enabled"] = False
            manager.save(next_config)
            state = manager.state()
            self.assertEqual(state["totals"]["target_segments"], 6)
            self.assertEqual(state["totals"]["recorded_segments"], 2)
            self.assertIsNone(state["totals"]["completed_segments"])
            self.assertIsNone(state["totals"]["valid_segments"])
            self.assertIsNone(state["totals"]["progress_percent"])
            self.assertEqual(state["totals"]["unverified_rooms"], 2)
            resumed = manager.set_paused(1, False, "resume-second")
            self.assertEqual(resumed["status"], "pending")
            apply_control_command(job["command_file"], Path(run["run_dir"]) / "commands_lock_1", job["stop_file"],
                job["pause_file"], receipt_dir, receipt_dir / "state.json", run_id=run["run_id"],
                instance_id=1, processed_seq=processed[0],
                acknowledge=lambda command, status, details: _test_ack(receipt_dir, command, status, details))
            self.assertFalse(pause_requested(run["jobs"][0]["pause_file"]))
            self.assertTrue(Path(run["jobs"][0]["pause_file"]).exists())
            legacy_path = run["jobs"][0].pop("pause_file")
            with self.assertRaisesRegex(ValueError, "旧版本"):
                manager.set_paused(1, True)
            run["jobs"][0]["pause_file"] = legacy_path
            for invalid_id in (0, 6, True, "1"):
                with self.assertRaises(ValueError):
                    manager.set_paused(invalid_id, True)
            self.assertEqual(manager.stop()["requested"], 2)
            with self.assertRaises(ValueError):
                manager.set_paused(1, False)

    def test_18_pause_stop_resume_races_and_worker_receipts(self):
        folder = self.folder("control_races")
        config = self.config(folder)
        (Path(config["study_root"]) / "_config" / "urls_1.txt").write_text(
            "https://tbzb.taobao.com/live?liveId=123\n", encoding="utf-8")
        manager = Manager(config, control=folder / "control")
        class Child:
            pid = 12345
        with patch.object(manager, "preflight", return_value={"ok": True}), \
                patch("src.control.server.subprocess.Popen", return_value=Child()), \
                patch("src.control.server.process_birth", return_value="fake-birth"):
            run = manager.start()
            job = run["jobs"][0]
            receipt_dir = Path(job["receipt_dir"])
            pause = manager.set_paused(1, True, "race-pause")
            stop = manager.stop("race-stop")
            resume_rejected = False
            try:
                manager.set_paused(1, False, "race-resume")
            except ValueError:
                resume_rejected = True
            self.assertTrue(resume_rejected)
            self.assertEqual(stop["operations"][0]["operation_id"], "race-stop")
            self.assertTrue(Path(job["stop_file"]).exists())
            pause_row = next(row for row in manager.operation_list(run["run_id"])
                             if row["operation_id"] == "race-pause")
            self.assertEqual(pause_row["status"], "superseded")

            # A worker paused in its wait loop sees a newly written stop intent and acknowledges it.
            atomic_json(job["pause_file"], {"paused": True})
            stop_path_during_wait = folder / "stop-arrives-during-pause.request"
            stop_command = {"run_id": run["run_id"], "instance_id": 1,
                "operation_id": "race-stop", "command_seq": stop["operations"][0]["command_seq"],
                "operation": "stop", "desired_state": "stop"}
            crawler_tree = ast.parse((ROOT / "src" / "crawler" / "taobao_crawler.py").read_text(encoding="utf-8"))
            stop_function = next(node for node in crawler_tree.body
                                 if isinstance(node, ast.FunctionDef) and node.name == "stop_requested")
            def write_receipt(command, status, details):
                return _test_ack(receipt_dir, command, status, details)
            stop_namespace = {"STOP_FILE": str(stop_path_during_wait), "RUN_ID": run["run_id"],
                "INSTANCE_ID": 1, "RECEIPT_DIR": str(receipt_dir), "STATUS_FILE": None,
                "status_data": {}, "telemetry_gap": False, "EVENTS": None, "Path": Path,
                "os": os, "time": time, "atomic_json": atomic_json,
                "_load_json_file": lambda path: json.loads(Path(path).read_text(encoding="utf-8"))
                    if Path(path).exists() else {}, "_write_operation_receipt": write_receipt}
            exec(compile(ast.Module(body=[stop_function], type_ignores=[]), "synthetic_stop_ack", "exec"),
                 stop_namespace)
            def write_stop_intent(_seconds):
                atomic_json(stop_path_during_wait, stop_command)
            status_phases = []
            completed = wait_while_paused(job["pause_file"], stop_requested=stop_namespace["stop_requested"],
                publish_status=lambda phase, **kwargs: status_phases.append(phase), log=lambda *_: None,
                sleep=write_stop_intent)
            self.assertFalse(completed)
            self.assertLessEqual(status_phases.count("paused"), 1)
            self.assertEqual(json.loads((receipt_dir / "race-stop.json").read_text(encoding="utf-8"))["status"], "applied")
            # A stale pause command cannot apply after stop marker exists.
            sequence, command = apply_control_command(job["command_file"], Path(run["run_dir"]) / "commands_lock_1",
                job["stop_file"], job["pause_file"], receipt_dir, receipt_dir / "state.json",
                run_id=run["run_id"], instance_id=1, processed_seq=0,
                acknowledge=lambda *args: self.fail("pause/resume command acknowledged after stop"))
            self.assertEqual(sequence, 0)
            self.assertIsNone(command)
            self.assertEqual(json.loads((receipt_dir / "race-stop.json").read_text(encoding="utf-8"))["status"], "applied")

    def test_19_operation_sequences_idempotency_and_event_paging(self):
        folder = self.folder("event_paging")
        store = OperationStore(folder / "study")
        first, created = store.create_request(run_id="r1", instance_id=1, operation_id="same",
            operation="pause", desired_state=True)
        self.assertTrue(created)
        repeated, created = store.create_request(run_id="r1", instance_id=1, operation_id="same",
            operation="pause", desired_state=True)
        self.assertFalse(created)
        self.assertEqual(first["command_seq"], repeated["command_seq"])
        second, created = store.create_request(run_id="r2", instance_id=1, operation_id="next",
            operation="resume", desired_state=False)
        self.assertTrue(created)
        self.assertEqual(second["command_seq"], first["command_seq"] + 1)
        restarted, created = OperationStore(folder / "study").create_request(run_id="r2", instance_id=1,
            operation_id="after-restart", operation="pause", desired_state=True)
        self.assertTrue(created)
        self.assertEqual(restarted["command_seq"], second["command_seq"] + 1)
        with self.assertRaises(ValueError):
            store.create_request(run_id="r1", instance_id=1, operation_id="same",
                operation="resume", desired_state=False)
        events = folder / "events"
        controller = EventWriter(events, "r1", "controller")
        worker = EventWriter(events, "r1", "worker_1", 1)
        for index in range(3):
            controller.emit(status="accepted", source="controller", operation_id=f"op{index}")
        worker.emit(status="applied", source="worker", operation_id="op0")
        page1 = read_event_page(events, limit=2)
        page2 = read_event_page(events, cursor=page1["cursor"], limit=2)
        page3 = read_event_page(events, cursor=page2["cursor"], limit=2)
        all_ids = [item["event_id"] for page in (page1, page2, page3) for item in page["events"]]
        self.assertEqual(len(all_ids), 4)
        self.assertEqual(len(set(all_ids)), 4)
        # Instance-filtered pages include global controller events (instance_id=null).
        self.assertEqual(len(read_event_page(events, limit=10, instance_id=1)["events"]), 4)
        blocker = folder / "not_a_directory"
        blocker.write_text("synthetic blocker", encoding="utf-8")
        unavailable = EventWriter(blocker / "events", "r1", "controller")
        self.assertTrue(unavailable.telemetry_gap)
        self.assertIsNone(unavailable.emit(status="accepted", source="controller"))
        corrupt = folder / "corrupt_events"
        corrupt.mkdir()
        (corrupt / "controller.seq.json").write_text("broken", encoding="utf-8")
        unrecoverable = EventWriter(corrupt, "r1", "controller")
        self.assertIsNone(unrecoverable.emit(status="accepted", source="controller"))
        self.assertTrue(unrecoverable.telemetry_gap)
        clock_events = folder / "clock_shift_events"
        clock_writer = EventWriter(clock_events, "r1", "worker_1", 1)
        with patch("src.control.event_store.utc_now", side_effect=[
                "2026-10-09T00:00:02Z", "2026-10-09T00:00:01Z"]):
            older_time = clock_writer.emit(status="first", source="worker")
            newer_time = clock_writer.emit(status="second", source="worker")
        first_page = read_event_page(clock_events, limit=1)
        second_page = read_event_page(clock_events, cursor=first_page["cursor"], limit=1)
        self.assertEqual(first_page["events"][0]["event_id"], older_time["event_id"])
        self.assertEqual(second_page["events"][0]["event_id"], newer_time["event_id"])
        rotated = folder / "rotated_events"
        rotated_writer = EventWriter(rotated, "r1", "worker_2", 2)
        with patch("src.control.event_store.EVENT_SHARD_BYTES", 1):
            rotated_events = [rotated_writer.emit(status=f"s{index}", source="worker")
                              for index in range(3)]
        self.assertEqual(len(list(rotated.glob("worker_2_*.jsonl"))), 3)
        page = read_event_page(rotated, limit=2)
        tail = read_event_page(rotated, cursor=page["cursor"], limit=2)
        self.assertEqual([row["event_id"] for row in page["events"] + tail["events"]],
                         [row["event_id"] for row in rotated_events])

    def test_20_worker_crash_reconciles_pending_operation_and_instances_are_isolated(self):
        folder = self.folder("worker_crash")
        store = OperationStore(folder / "study")
        first_sequences = []
        for instance_id in range(1, 6):
            request, _ = store.create_request(run_id="five-instance-run", instance_id=instance_id,
                operation_id=f"pause-{instance_id}", operation="pause", desired_state=True)
            first_sequences.append(request["command_seq"])
        self.assertEqual(first_sequences, [1, 1, 1, 1, 1])

        config = self.config(folder)
        (Path(config["study_root"]) / "_config" / "urls_1.txt").write_text(
            "https://tbzb.taobao.com/live?liveId=123\n", encoding="utf-8")
        manager = Manager(config, control=folder / "control")
        class Child:
            pid = 22345
        with patch.object(manager, "preflight", return_value={"ok": True}), \
                patch("src.control.server.subprocess.Popen", return_value=Child()), \
                patch("src.control.server.process_birth", return_value="fake-birth"):
            manager.start()
            pending = manager.set_paused(1, True, "worker-will-crash")
            self.assertEqual(pending["status"], "pending")
        with patch("src.control.server.process_birth", return_value=None):
            manager.state()
            rows = manager.operation_list(manager.run["run_id"])
        crashed = next(row for row in rows if row["operation_id"] == "worker-will-crash")
        self.assertEqual(crashed["status"], "failed")
        self.assertIn("exited", crashed.get("detail", ""))

    def test_21_worker_receipts_require_matching_run_instance_operation_and_sequence(self):
        folder = self.folder("receipt_identity")
        command_path = folder / "command.json"
        receipt_dir = folder / "receipts"
        receipt_dir.mkdir()
        command_lock = folder / "command_lock"
        pause_path = folder / "pause.json"
        state_path = folder / "worker_state.json"
        for index, (field, wrong_value) in enumerate((
                ("run_id", "wrong-run"), ("instance_id", 2),
                ("operation_id", "other-op"), ("command_seq", 99)), 1):
            operation_id = f"receipt-{index}"
            command = {"run_id": "right-run", "instance_id": 1, "operation_id": operation_id,
                "command_seq": index, "operation": "pause", "desired_state": True}
            atomic_json(command_path, command)
            wrong_receipt = {**command, "status": "applied"}
            wrong_receipt[field] = wrong_value
            atomic_json(receipt_dir / f"{operation_id}.json", wrong_receipt)
            acknowledgments = []
            sequence, applied = apply_control_command(command_path, command_lock, None, pause_path,
                receipt_dir, state_path, run_id="right-run", instance_id=1, processed_seq=0,
                acknowledge=lambda current, status, details: acknowledgments.append((current, status)) or True)
            self.assertEqual(sequence, index)
            self.assertEqual(applied["operation_id"], operation_id)
            self.assertEqual(acknowledgments, [(command, "applied")])

        config = self.config(folder)
        (Path(config["study_root"]) / "_config" / "urls_1.txt").write_text(
            "https://tbzb.taobao.com/live?liveId=123\n", encoding="utf-8")
        manager = Manager(config, control=folder / "manager_control")
        class Child:
            pid = 32345
        with patch.object(manager, "preflight", return_value={"ok": True}), \
                patch("src.control.server.subprocess.Popen", return_value=Child()), \
                patch("src.control.server.process_birth", return_value="fake-birth"):
            manager.start()
            request = manager.set_paused(1, True, "wrong-server-receipt")
            receipt_path = manager._operation_store().receipt_path(
                manager._run_dir(), 1, "wrong-server-receipt")
            atomic_json(receipt_path, {"run_id": "wrong-run", "instance_id": 1,
                "operation_id": "wrong-server-receipt", "command_seq": request["command_seq"],
                "status": "applied"})
            listing = manager.operation_list(manager.run["run_id"])
        observed = next(row for row in listing if row["operation_id"] == "wrong-server-receipt")
        self.assertEqual(observed["status"], "pending")
        self.assertIsNone(observed["worker_ack_at"])

    def test_22_newer_resume_supersedes_pause_before_worker_checkpoint(self):
        folder = self.folder("pause_resume_supersede")
        config = self.config(folder)
        (Path(config["study_root"]) / "_config" / "urls_1.txt").write_text(
            "https://tbzb.taobao.com/live?liveId=123\n", encoding="utf-8")
        manager = Manager(config, control=folder / "control")
        class Child:
            pid = 42345
        with patch.object(manager, "preflight", return_value={"ok": True}), \
                patch("src.control.server.subprocess.Popen", return_value=Child()), \
                patch("src.control.server.process_birth", return_value="fake-birth"):
            run = manager.start()
            pause = manager.set_paused(1, True, "quick-pause")
            pause_receipt = manager._operation_store().receipt_path(
                manager._run_dir(), 1, "quick-pause")
            atomic_json(pause_receipt, {"run_id": "wrong-run", "instance_id": 1,
                "operation_id": "quick-pause", "command_seq": pause["command_seq"], "status": "applied"})
            resume = manager.set_paused(1, False, "quick-resume")
            rows = manager.operation_list(run["run_id"])
            by_id = {row["operation_id"]: row for row in rows}
            self.assertEqual(by_id["quick-pause"]["status"], "superseded")
            self.assertEqual(by_id["quick-resume"]["status"], "pending")
            job = run["jobs"][0]
            receipt_dir = Path(job["receipt_dir"])
            command_file = Path(job["command_file"])
            self.assertEqual(json.loads(command_file.read_text(encoding="utf-8"))["operation_id"], "quick-resume")
            sequence, command = apply_control_command(command_file, Path(run["run_dir"]) / "commands_lock_1",
                job["stop_file"], job["pause_file"], receipt_dir, receipt_dir / "state.json",
                run_id=run["run_id"], instance_id=1, processed_seq=0,
                acknowledge=lambda current, status, details: _test_ack(receipt_dir, current, status, details))
            self.assertEqual(sequence, resume["command_seq"])
            self.assertEqual(command["operation_id"], "quick-resume")
            self.assertFalse(pause_requested(job["pause_file"]))
            self.assertEqual(manager._operation_view(by_id["quick-pause"], manager._operation_store(),
                manager._run_dir(), job)["status"], "superseded")
            self.assertEqual(manager._operation_view(by_id["quick-resume"], manager._operation_store(),
                manager._run_dir(), job)["status"], "applied")

    def test_17_count_correction_audit_ignores_stale_progress_and_keeps_run_snapshot(self):
        folder = self.folder("count_correction")
        config = self.config(folder)
        source = Path(config["study_root"]) / "_config" / "urls_1.txt"
        source.write_text("https://tbzb.taobao.com/live?liveId=123,已录制1/3\n", encoding="utf-8")
        room = self.make_room(Path(config["study_root"]) / "_staging" / "browser_9223", 123, segments=(1,))
        with patch.object(parse_data, "SESSIONS", str(Path(config["study_root"]) / "sessions")), \
                patch.object(parse_data, "probe_video", return_value=600), contextlib.redirect_stdout(io.StringIO()):
            parse_data.process_room(str(room))
        manager = Manager(config, control=folder / "control")
        snapshot = None

        class Child:
            pid = 12345

        with patch.object(manager, "preflight", return_value={"ok": True}), \
                patch("src.control.server.subprocess.Popen", return_value=Child()), \
                patch("src.control.server.process_birth", return_value="fake-birth"), \
                patch("src.utils.segment_evidence.probe_video", return_value=600):
            run = manager.start()
            snapshot = Path(run["jobs"][0]["urls_file"])
            original_snapshot = snapshot.read_bytes()
            document = manager.url_document(1)
            progress_path = Path(config["study_root"]) / "_control" / f"progress_{config['instances'][0]['port']}.json"
            atomic_json(progress_path, {"schema_version": 2, "legacy_counts": {"123": 3},
                                        "rooms": {"123": {"count": 3, "updated_t": 100,
                                                            "segments": []}}})
            self.assertEqual(manager.url_document(1)["rows"][0]["count"], 3)
            result = manager.correct_url_count({"instance_id": 1, "live_id": "123", "count": 1,
                "reason": "合成测试：逐段核对后修正", "confirmed": True, "revision": document["revision"]})
            self.assertTrue(result["correction_id"])
            self.assertIn("已录制1/3", source.read_text(encoding="utf-8"))
            self.assertEqual(snapshot.read_bytes(), original_snapshot)
            # Old schema-v2 room count and legacy_counts cannot restore the prior 3.
            self.assertEqual(manager.url_rows(config["instances"][0])[0]["count"], 1)
            after_correction = manager.url_document(1)
            with self.assertRaisesRegex(ValueError, "不能低于已核验的 1 段"):
                manager.correct_url_count({"instance_id": 1, "live_id": "123", "count": 0,
                    "reason": "不应低于证据", "confirmed": True, "revision": after_correction["revision"]})
            audit = Path(config["study_root"]) / "_control" / "url_count_corrections.jsonl"
            events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["event"] for event in events], ["requested", "completed"])
            self.assertEqual(events[0]["before_count"], 3)
            self.assertEqual(events[1]["after_count"], 1)
            # A progress update after correction is accepted as a later recording.
            atomic_json(progress_path, {"schema_version": 2, "legacy_counts": {"123": 3},
                                        "rooms": {"123": {"count": 2,
                                                           "updated_t": result["corrected_t"] + 1,
                                                           "segments": []}}})
            self.assertEqual(manager.url_rows(config["instances"][0])[0]["count"], 2)
            after_progress = manager.url_document(1)
            manager.save_urls({"instance_id": 1, "text": "123\n456",
                               "revision": after_progress["revision"], "mode": "replace"})
            self.assertEqual(snapshot.read_bytes(), original_snapshot)

    def test_11_pause_wait_resume_and_stop(self):
        folder = self.folder("pause_wait")
        path = folder / "pause.json"
        phases = []
        atomic_json(path, {"paused": True})
        def resume(_seconds):
            atomic_json(path, {"paused": False}, backup=True)
        self.assertTrue(wait_while_paused(path, stop_requested=lambda: False,
                                         publish_status=lambda phase, **fields: phases.append(phase),
                                         log=lambda text: None, sleep=resume))
        self.assertEqual(phases, ["paused"])
        atomic_json(path, {"paused": True})
        self.assertFalse(wait_while_paused(path, stop_requested=lambda: True,
                                          publish_status=lambda *args, **fields: None, log=lambda text: None))
        path.write_text("invalid control", encoding="utf-8")
        self.assertTrue(pause_requested(path))

    def test_12_real_crawler_loop_pause_and_one_room_archive(self):
        folder = self.folder("loop")
        tree = ast.parse((ROOT / "src" / "crawler" / "taobao_crawler.py").read_text(encoding="utf-8"))
        main_loop = next(node for node in tree.body if isinstance(node, ast.While))
        for pause_during_record, threshold in ((True, 1), (False, 1), (False, 2)):
            with self.subTest(pause=pause_during_record, threshold=threshold):
                path = folder / f"pause_{pause_during_record}_{threshold}.json"
                atomic_json(path, {"paused": False})
                events = []
                stopped = [False]
                rows = [("https://test", "123", 0, 1)]
                def record(*args):
                    events.append("record_finished")
                    if pause_during_record:
                        atomic_json(path, {"paused": True})
                    return True
                def mark(*args):
                    events.append("marked")
                    rows[0] = ("https://test", "123", 1, 1)
                def archive(*args):
                    events.append("archived")
                    return True
                def pause_sleep(_seconds):
                    events.append("paused")
                    self.assertIn("marked", events)
                    stopped[0] = True
                def wait(path, **kwargs):
                    return wait_while_paused(path, sleep=pause_sleep, **kwargs)
                import random
                namespace = {"wait_while_paused": wait, "pause_requested": pause_requested, "PAUSE_FILE": str(path),
                             "stop_requested": lambda: stopped[0], "publish_status": lambda *args, **fields: None,
                             "process_pending_command": lambda: None,
                             "log": lambda text: None, "pending_finalize": [], "batch_ready": False,
                             "time": time, "url_pool": rows,
                             "last_record": {}, "COOLDOWN_SEC": 0, "random": random, "SEG_NAMES": ["第一段"],
                             "scan_room": lambda *args: True, "record_room": record, "mark_recorded": mark,
                             "MAX_MIN": 0, "BATCH_ROOMS": threshold, "OUTDIR": str(folder), "os": os,
                             "state": {"stream_url": {"url": "synthetic://stream"}}, "read_urls": lambda: rows,
                             "finalize_room": archive}
                exec(compile(ast.Module(body=[main_loop], type_ignores=[]), "synthetic_main_loop", "exec"), namespace)
                self.assertEqual(events.count("record_finished"), 1)
                self.assertIn("marked", events)
                self.assertEqual("archived" in events, threshold == 1 and not pause_during_record)
                self.assertEqual("paused" in events, pause_during_record)

    def test_13_progress_caps_and_runtime_threshold(self):
        from src.control.server import summarize_rows
        self.assertEqual(summarize_rows([])["progress_percent"], 0)
        summary = summarize_rows([{"count": 4, "target": 1, "valid_count": 1, "archived_count": 1}])
        self.assertEqual(summary["recorded"], 4)
        self.assertEqual(summary["completed_segments"], 1)
        self.assertEqual(summary["valid_segments"], 1)
        self.assertEqual(summary["archived_segments"], 1)
        self.assertEqual(summary["progress_percent"], 100)
        unknown = summarize_rows([{"count": 3, "target": 3, "valid_count": None,
                                   "archived_count": 0}])
        self.assertIsNone(unknown["completed_segments"])
        self.assertIsNone(unknown["valid_segments"])
        self.assertEqual(unknown["unverified_rooms"], 1)
        mixed = summarize_rows([{"count": 3, "target": 3, "valid_count": 1,
                                 "archived_count": 0},
                                {"count": 3, "target": 3, "valid_count": None,
                                 "archived_count": 0}])
        self.assertEqual(mixed["completed_segments"], 1)
        self.assertEqual(mixed["progress_percent"], 16.7)
        self.assertEqual(mixed["unverified_rooms"], 1)
        import importlib
        from src.utils import config as runtime_config
        try:
            with patch.dict(os.environ, {"LIVE_BATCH_ROOMS": "1"}):
                self.assertEqual(importlib.reload(runtime_config).BATCH_ROOMS, 1)
            for value in ("0", "-1"):
                with patch.dict(os.environ, {"LIVE_BATCH_ROOMS": value}):
                    with self.assertRaises(ValueError):
                        importlib.reload(runtime_config)
        finally:
            importlib.reload(runtime_config)

    def test_15_progress_receipts_archives_and_legacy_counts(self):
        folder = self.folder('verified_progress')
        study = folder / 'study'
        staging = study / '_staging' / 'browser_9223' / 'room_123' / '第一段' / 'attempt'
        staging.mkdir(parents=True)
        video = staging / 'video.flv'
        video.write_bytes(b'synthetic video')
        final_path = staging / 'segment_final.json'
        final_payload = {'recording_id': 'rec-1', 'segment_index': 1,
                         'recorded_files': [str(video.resolve())], 'video_duration_seconds': 60.0,
                         'max_minutes': 1,
                         'technical_validation': video_validation(video, {'max_minutes': 1}, 'synthetic',
                                                                   probe=lambda *_: 60.0)}
        final_path.write_text(json.dumps(final_payload), encoding='utf-8')
        final_stat, video_stat = final_path.stat(), video.stat()
        progress = {'schema_version': 2, 'legacy_counts': {'123': 3}, 'rooms': {'123': {
            'count': 3, 'segments': [{'recording_id': 'rec-1', 'segment_index': 1,
                'final_json': str(final_path.resolve()), 'final_size': final_stat.st_size,
                'final_mtime_ns': final_stat.st_mtime_ns, 'video_path': str(video.resolve()),
                'video_size': video_stat.st_size, 'video_mtime_ns': video_stat.st_mtime_ns,
                'video_duration_seconds': 60.0, 'validation_schema_version': 1,
                'final_sha256': digest(final_path), 'video_sha256': digest(video)}]}}}
        valid, legacy = verified_progress_counts(progress, study)
        self.assertEqual(valid, {'123': 1})
        self.assertEqual(legacy, {'123': 3})
        records, _, states = verified_progress_records(progress, study)
        self.assertEqual(records, {'123': {'rec-1'}})
        self.assertEqual(states, {'123': 'verified'})
        video.write_bytes(b'changed synthetic video')
        valid, _ = verified_progress_counts(progress, study)
        self.assertEqual(valid, {'123': 0})
        _, _, states = verified_progress_records(progress, study)
        self.assertEqual(states, {'123': 'invalid'})
        valid, legacy = verified_progress_counts({'123': {'count': 4}}, study)
        self.assertEqual(valid, {})
        self.assertEqual(legacy, {'123': 4})
        valid, legacy = verified_progress_counts({'schema_version': 2, 'legacy_counts': ['bad'],
                         'rooms': {'123': {'count': 3, 'segments': []}}}, study)
        self.assertEqual(valid, {})
        self.assertEqual(legacy, {'123': 3})

        sessions = study / 'sessions'
        for dirname in ('session_a', 'session_b'):
            archived_video = sessions / dirname / 'video.flv'
            archived_video.parent.mkdir(parents=True, exist_ok=True)
            archived_video.write_bytes(b'archived')
            manifest = {'room_id': '123', 'source_preserved': True, 'entries': [
                {'recording_id': 'rec-archived', 'destination': str(archived_video.resolve()),
                 'bytes': archived_video.stat().st_size},
                {'recording_id': 'missing', 'destination': str((sessions / 'absent.flv').resolve()), 'bytes': 4},
            ]}
            (archived_video.parent / 'archive_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.assertEqual(archived_segment_counts(study), {'123': 0})

    def test_16_crawler_validation_receipt_links_final_and_video(self):
        folder = self.folder('receipt_builder')
        room = folder / 'room_123'
        segment = room / '第一段' / 'attempt'
        segment.mkdir(parents=True)
        video = segment / 'video.flv'
        video.write_bytes(b'synthetic')
        final = segment / 'capture_final.json'
        final.write_text(json.dumps({'recording_id': 'rec-123', 'segment_index': 1,
                         'recorded_files': [str(video.resolve())], 'video_duration_seconds': 60.0,
                         'max_minutes': 1, 'technical_validation': video_validation(video, {'max_minutes': 1},
                             'synthetic', probe=lambda *_: 60.0)}), encoding='utf-8')
        tree = ast.parse((ROOT / 'src' / 'crawler' / 'taobao_crawler.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'validation_receipt')
        namespace = {'Path': Path, 'json': json, 'digest': digest, 'validation_matches': validation_matches}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'synthetic_receipt_builder', 'exec'), namespace)
        receipt = namespace['validation_receipt'](room, 1)
        self.assertEqual(receipt['recording_id'], 'rec-123')
        self.assertEqual(receipt['video_size'], video.stat().st_size)
        self.assertEqual(receipt['final_json'], str(final.resolve()))
        self.assertIsNone(namespace['validation_receipt'](room, 2))

    def test_14_readonly_cookie_flag(self):
        folder = self.folder("cookie_guard")
        path = folder / "cookies.json"
        path.write_text("original synthetic cookie", encoding="utf-8")
        tree = ast.parse((ROOT / "src" / "crawler" / "taobao_crawler.py").read_text(encoding="utf-8"))
        save_block = next(node for node in tree.body if isinstance(node, ast.If)
                          and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                                  and child.func.id == "atomic_json" for child in ast.walk(node)))
        namespace = {"os": os, "logged_in": lambda: True, "COOKIE_JSON": str(path)}
        with patch.dict(os.environ, {"LIVE_SAVE_COOKIES": "0"}), \
                patch("src.utils.safe_io.atomic_json") as write:
            namespace["atomic_json"] = write
            exec(compile(ast.Module(body=[save_block], type_ignores=[]), "cookie_guard", "exec"), namespace)
            write.assert_not_called()
        self.assertEqual(path.read_text(encoding="utf-8"), "original synthetic cookie")


def _test_ack(receipt_dir, command, status, details):
    atomic_json(Path(receipt_dir) / f"{command['operation_id']}.json", {
        "run_id": command["run_id"], "instance_id": command["instance_id"],
        "operation_id": command["operation_id"], "command_seq": command["command_seq"],
        "status": status, "ack_at": "synthetic", "details": details})
    return True


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Checks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    atomic_json(ARTIFACTS / "result.json", {"passed": result.wasSuccessful(), "tests": result.testsRun,
                                           "failures": len(result.failures), "errors": len(result.errors),
                                           "synthetic_only": True, "files_retained": True})
    print("Synthetic artifacts retained:", ARTIFACTS)
    raise SystemExit(0 if result.wasSuccessful() else 1)
