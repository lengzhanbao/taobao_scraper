"""Retained, synthetic self-checks. Never opens a browser or real research data."""
import ast
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
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
from src.control.server import Manager, handler_for, process_birth, verified_progress_counts, archived_segment_counts, compatible_existing_service
from src.utils.safe_io import atomic_json, verified_copy, PortLock
from src.crawler.recording import record_segment
from src.crawler.runtime_control import pause_requested, wait_while_paused
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
                    pause.assert_called_with(2, expected)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def make_room(self, root, lid, flag=True):
        room = root / f"room_{lid}"
        for index in (1, 2, 3):
            segment = room / f"segment_{index}"
            segment.mkdir(parents=True)
            video = segment / "video.flv"
            video.write_bytes(f"synthetic room {lid} segment {index}".encode())
            body = {"liveId": str(lid), "liveTitle": "测试直播", "accountName": "测试店",
                    "isDigitalAnchorLive": flag, "viewCount": "12"}
            atomic_json(segment / f"data_20261008_12000{index}_final.json", {
                "live_url": f"https://tbzb.taobao.com/live?liveId={lid}", "recorded_files": [str(video)],
                "recording_id": f"{lid}-{index}", "segment_index": index,
                "record_start_t": 1780000000 + index * 8000, "record_end_t": 1780000600 + index * 8000,
                "responses": [{"t": 1780000000, "url": "https://h5api.m.taobao.com/h5/mtop.roomstudio.live.detail.get/1.0/",
                               "body": json.dumps(body, ensure_ascii=False)}],
            })
        return room

    def test_07_six_room_archive_and_conflict(self):
        folder = self.folder("archive")
        rooms = [self.make_room(folder / "staging", lid, flag=lid != 6) for lid in range(1, 7)]
        with patch.object(parse_data, "SESSIONS", str(folder / "sessions")), \
                patch.object(parse_data, "probe_video", return_value=600), contextlib.redirect_stdout(io.StringIO()):
            for room in rooms:
                self.assertTrue(parse_data.process_room(str(room)))
            self.assertTrue(parse_data.process_room(str(rooms[0])))
            first_session = next(p for p in (folder / "sessions").iterdir() if p.name.endswith("_1"))
            manifest = json.loads((first_session / "archive_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["entries"]), 6)
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
                                      log=lambda message: None, publish_status=lambda phase, **kwargs: phases.append(phase))
                self.assertIn("validating", phases)
                self.assertEqual(phases[-1], "segment_failed" if fail_final else "segment_completed")
                return result
        self.assertTrue(attempt(False))
        self.assertFalse(attempt(True))
        self.assertEqual(len(list(folder.rglob("*.flv"))), 2)
        self.assertEqual(len(list(folder.rglob("*_final.json"))), 1)

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
            manager.set_paused(1, True)
            self.assertTrue(pause_requested(run["jobs"][0]["pause_file"]))
            self.assertFalse(pause_requested(run["jobs"][1]["pause_file"]))
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
            self.assertEqual(state["totals"]["completed_segments"], 0)
            self.assertEqual(state["totals"]["valid_segments"], 0)
            self.assertEqual(state["totals"]["progress_percent"], 0)
            manager.set_paused(1, False)
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
                         'recorded_files': [str(video.resolve())], 'video_duration_seconds': 60.0}
        final_path.write_text(json.dumps(final_payload), encoding='utf-8')
        final_stat, video_stat = final_path.stat(), video.stat()
        progress = {'schema_version': 2, 'legacy_counts': {'123': 3}, 'rooms': {'123': {
            'count': 3, 'segments': [{'recording_id': 'rec-1', 'segment_index': 1,
                'final_json': str(final_path.resolve()), 'final_size': final_stat.st_size,
                'final_mtime_ns': final_stat.st_mtime_ns, 'video_path': str(video.resolve()),
                'video_size': video_stat.st_size, 'video_mtime_ns': video_stat.st_mtime_ns,
                'video_duration_seconds': 60.0}]}}}
        valid, legacy = verified_progress_counts(progress, study)
        self.assertEqual(valid, {'123': 1})
        self.assertEqual(legacy, {'123': 3})
        video.write_bytes(b'changed synthetic video')
        valid, _ = verified_progress_counts(progress, study)
        self.assertEqual(valid, {'123': 0})
        valid, legacy = verified_progress_counts({'123': {'count': 4}}, study)
        self.assertEqual(valid, {})
        self.assertEqual(legacy, {'123': 4})
        valid, legacy = verified_progress_counts({'schema_version': 2, 'legacy_counts': ['bad'],
                         'rooms': {'123': {'count': 3, 'segments': []}}}, study)
        self.assertEqual(valid, {'123': 0})
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
        self.assertEqual(archived_segment_counts(study), {'123': 1})

    def test_16_crawler_validation_receipt_links_final_and_video(self):
        folder = self.folder('receipt_builder')
        room = folder / 'room_123'
        segment = room / '第一段' / 'attempt'
        segment.mkdir(parents=True)
        video = segment / 'video.flv'
        video.write_bytes(b'synthetic')
        final = segment / 'capture_final.json'
        final.write_text(json.dumps({'recording_id': 'rec-123', 'segment_index': 1,
                         'recorded_files': [str(video.resolve())], 'video_duration_seconds': 42.5}), encoding='utf-8')
        tree = ast.parse((ROOT / 'src' / 'crawler' / 'taobao_crawler.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'validation_receipt')
        namespace = {'Path': Path, 'json': json}
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


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Checks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    atomic_json(ARTIFACTS / "result.json", {"passed": result.wasSuccessful(), "tests": result.testsRun,
                                           "failures": len(result.failures), "errors": len(result.errors),
                                           "synthetic_only": True, "files_retained": True})
    print("Synthetic artifacts retained:", ARTIFACTS)
    raise SystemExit(0 if result.wasSuccessful() else 1)
