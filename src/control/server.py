"""Loopback-only, standard-library dashboard. Crawlers start only on explicit clicks."""
import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import uuid
import webbrowser

from src.control.settings import ROOT, defaults, validate, migrate_settings, read_url_list, build_environment
from src.utils.safe_io import atomic_json, digest
from src.utils.segment_evidence import (EvidenceCache, validation_matches, digital_observation,
                                       observation_counts, inspect_archive)
from src.crawler.runtime_control import pause_requested
from src.control.url_lists import inspect as inspect_urls, save_list, correct_count

CONTROL = ROOT / "_control"
VERSION = "2.6-local-panel"
TERMINAL_PHASES = {"stopped", "finished", "login_timeout", "failed"}


def summarize_rows(rows):
    target = sum(row["target"] for row in rows)
    completed = sum(min(row["count"], row["target"]) for row in rows)
    known = [row["valid_count"] for row in rows if row.get("valid_count") is not None]
    unknown = sum(row.get("valid_count") is None for row in rows)
    verified = sum(known)
    archived = sum(row.get("archived_count", 0) for row in rows)
    verified_completed = sum(min(row["valid_count"], row["target"])
                             for row in rows if row.get("valid_count") is not None)
    progress_percent = (round(verified_completed * 100 / target, 1) if target else 0)
    if not known and unknown:
        verified = verified_completed = progress_percent = None
    observations = {(row.get("live_id"), key): value for row in rows
                    for key, value in row.get("digital_observations", {}).items()}
    return {"digital_markers": observation_counts(observations),
            "rooms": len(rows), "complete": sum(r["count"] >= r["target"] for r in rows),
            "verified_complete": sum(r.get("valid_count") is not None and
                                     r["valid_count"] >= r["target"] for r in rows),
            "recorded": sum(r["count"] for r in rows), "remaining": target - completed,
            "valid_segments": verified, "archived_segments": archived,
            "unverified_rooms": unknown, "target_segments": target,
            "completed_segments": verified_completed, "progress_percent": progress_percent,
            "progress_state": "unverified" if not known and unknown else
                              "partial" if unknown else "verified"}


def verified_progress_records(progress, study_root, observations=None, cache=None):
    """Return receipt identities, historical counts and per-room receipt state."""
    if not isinstance(progress, dict):
        return {}, {}, {}
    if progress.get("schema_version") != 2 or not isinstance(progress.get("rooms"), dict):
        legacy = {key: value.get("count", 0) for key, value in progress.items()
                  if isinstance(value, dict) and type(value.get("count")) is int}
        return {}, legacy, {}
    staging = (Path(study_root) / "_staging").resolve()
    cache = cache or EvidenceCache()
    verified = {}
    receipt_states = {}
    raw_legacy = progress.get("legacy_counts", {})
    legacy = ({str(key): value for key, value in raw_legacy.items()
               if type(value) is int and value >= 0} if isinstance(raw_legacy, dict) else {})
    for live_id, room in progress["rooms"].items():
        if not isinstance(room, dict):
            continue
        if type(room.get("count")) is int and room["count"] >= 0:
            legacy[live_id] = max(legacy.get(live_id, 0), room["count"])
        receipts = room.get("segments", [])
        if not isinstance(receipts, list):
            receipts = []
        if receipts:
            receipt_states[live_id] = "invalid"
        identities = set()
        for receipt in receipts:
            try:
                final_path = Path(receipt["final_json"]).resolve()
                video_path = Path(receipt["video_path"]).resolve()
                if not final_path.is_relative_to(staging) or not video_path.is_relative_to(staging):
                    continue
                final_stat, video_stat = final_path.stat(), video_path.stat()
                if ((final_stat.st_size, final_stat.st_mtime_ns) !=
                        (receipt["final_size"], receipt["final_mtime_ns"]) or
                        (video_stat.st_size, video_stat.st_mtime_ns) !=
                        (receipt["video_size"], receipt["video_mtime_ns"])):
                    continue
                with final_path.open(encoding="utf-8") as stream:
                    final = json.load(stream)
                if (not isinstance(final, dict)
                        or not isinstance(final.get("recorded_files"), list)):
                    continue
                if receipt.get("validation_schema_version") != 1 or not final.get("technical_validation"):
                    receipt_states[live_id] = "unverified"
                    continue
                if (not validation_matches(final)
                        or cache.get(final_path, "sha256", digest) != receipt.get("final_sha256")
                        or cache.get(video_path, "sha256", digest) != receipt.get("video_sha256")
                        or receipt.get("video_sha256") != final["technical_validation"]["video_sha256"]):
                    continue
                if (str(final.get("recording_id")) != str(receipt["recording_id"])
                        or int(final.get("segment_index", -1)) != int(receipt["segment_index"])
                        or video_path not in [Path(item).resolve() for item in final.get("recorded_files", [])]
                        or float(final.get("video_duration_seconds", 0)) != float(receipt["video_duration_seconds"])):
                    continue
                identities.add(str(receipt["recording_id"]))
                if observations is not None:
                    observations.setdefault(live_id, {})[str(receipt["recording_id"])] = digital_observation(final, live_id)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        if receipts:
            if identities or receipt_states.get(live_id) != "unverified":
                verified[live_id] = identities
            if identities:
                receipt_states[live_id] = "verified"
    return verified, legacy if isinstance(legacy, dict) else {}, receipt_states


def verified_progress_counts(progress, study_root):
    records, legacy, _ = verified_progress_records(progress, study_root)
    return {live_id: len(items) for live_id, items in records.items()}, legacy


def archived_segment_records(study_root, ffprobe=None, observations=None, diagnostics=None, cache=None):
    """Count only complete JSON/video/comments/summary sets with actual media duration."""
    if ffprobe is None:
        from src.utils.config import FFPROBE
        ffprobe = FFPROBE
    sessions = (Path(study_root) / "sessions").resolve()
    recordings_by_room = {}
    if not sessions.is_dir():
        return recordings_by_room
    for manifest_path in sessions.glob("*/archive_manifest.json"):
        try:
            live_id, records, flags, issues = inspect_archive(manifest_path, ffprobe, cache)
            recordings_by_room.setdefault(live_id, set()).update(records)
            if observations is not None:
                observations.setdefault(live_id, {}).update(flags)
            if diagnostics is not None:
                diagnostics.setdefault(live_id, []).extend(issues)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            if diagnostics is not None:
                live_id = manifest_path.parent.name.rsplit("_", 1)[-1]
                diagnostics.setdefault(live_id, []).append("归档清单无法核验：" + str(error))
    return recordings_by_room


def archived_segment_counts(study_root):
    return {live_id: len(records) for live_id, records in archived_segment_records(study_root).items()}


def read_count_corrections(study_root):
    """Read retained correction requests; malformed lines do not hide earlier entries."""
    path = Path(study_root) / "_control" / "url_count_corrections.jsonl"
    events = {}
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        continue
                    correction_id = event.get("correction_id")
                    if isinstance(correction_id, str) and correction_id:
                        events.setdefault(correction_id, {}).update(event)
                except (ValueError, TypeError):
                    continue
    except OSError:
        pass
    latest = {}
    for event in events.values():
        if event.get("event") not in ("requested", "completed"):
            continue
        instance_id = event.get("instance_id")
        live_id = event.get("live_id")
        if (type(instance_id) is not int or not 1 <= instance_id <= 5
                or not isinstance(live_id, str) or not live_id.isdigit()
                or type(event.get("after_count")) is not int):
            continue
        timestamp = event.get("corrected_t", event.get("requested_t", 0))
        if type(timestamp) not in (int, float):
            continue
        key = (instance_id, live_id)
        if key not in latest or timestamp >= latest[key].get("corrected_t", 0):
            latest[key] = {**event, "corrected_t": timestamp}
    return latest


def append_count_correction(study_root, event):
    path = Path(study_root) / "_control" / "url_count_corrections.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_json(path, fallback):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return fallback


def process_birth(pid):
    """Windows creation time identifies the same process even after a panel restart."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return str(pid)
        except OSError:
            return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.c_void_p] * 4
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        exit_code = ctypes.c_uint32()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
            return None
        times = [ctypes.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return None
        return str(times[0].value)
    finally:
        kernel.CloseHandle(handle)


def port_free(port):
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def compatible_existing_service(state, expected_root, expected_version):
    return (isinstance(state, dict) and state.get("code_root") == str(expected_root)
            and state.get("version") == expected_version)


def existing_crawler_pids():
    """Read-only check includes legacy/delayed Python workers, before Edge opens."""
    if os.name != "nt":
        return []
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| Where-Object { $_.CommandLine -match 'taobao_run_edge_[1-5]\\.py|crawler_instance_[1-5]\\.py|taobao_crawler\\.py' } "
        "| Select-Object -ExpandProperty ProcessId | ConvertTo-Json -Compress"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            capture_output=True, timeout=20,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("无法查询已有采集进程，请稍后重试")
    if not result.stdout.strip():
        return []
    pids = json.loads(result.stdout)
    return pids if isinstance(pids, list) else [pids]


class Manager:
    def __init__(self, initial=None, control=CONTROL):
        self.control = Path(control)
        self.control.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.control / "settings.json"
        base = validate(initial or defaults())
        if self.settings_path.exists():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ValueError("设置文件无法读取；已保留原文件，请先检查或恢复备份") from error
            self.settings = migrate_settings(saved, base)
            if self.settings != saved:
                atomic_json(self.settings_path, self.settings, backup=True)
        else:
            self.settings = base
        self.lock = threading.RLock()
        self.children = {}
        self.run = load_json(self.control / "active_run.json", {})
        self.check_cache = None
        self._archive_cache = ("", 0, {})
        self.evidence_cache = EvidenceCache()

    def save(self, config):
        with self.lock:
            self.settings = validate(config)
            atomic_json(self.settings_path, self.settings, backup=True)
            self.check_cache = None
            return self.settings

    def url_document(self, instance_id):
        if type(instance_id) is not int or not 1 <= instance_id <= 5:
            raise ValueError('实例编号必须是 1—5')
        with self.lock:
            instance = self.settings['instances'][instance_id - 1]
            path = Path(self.settings['study_root']) / '_config' / instance['urls_file']
            result = inspect_urls(path, instance['segments'])
            result['rows'] = self.url_rows(instance)
            result['instance_id'] = instance_id
            result['active_text'] = '\n'.join(line for line in result['text'].splitlines()
                                            if line.strip() and not line.lstrip().startswith('#'))
            result['running'] = any(job['alive'] for job in self.jobs())
            return result

    def save_urls(self, body):
        with self.lock:
            document = self.url_document(body.get('instance_id'))
            saved = save_list(document['path'], document['target'], body.get('text'),
                              body.get('revision'), body.get('mode', 'replace'))
            self.check_cache = None
            result = self.url_document(document['instance_id'])
            result['backup'] = saved['backup']
            result['message'] = '网址清单已保存；正在运行的任务使用启动时的清单，新清单下次启动生效。'
            return result

    def correct_url_count(self, body):
        with self.lock:
            instance_id = body.get('instance_id')
            live_id = str(body.get('live_id', '')).strip()
            count = body.get('count')
            reason = body.get('reason', '').strip() if isinstance(body.get('reason'), str) else ''
            if type(instance_id) is not int or not 1 <= instance_id <= 5:
                raise ValueError('实例编号必须是 1—5 的整数')
            if type(count) is not int or not 0 <= count <= 10000:
                raise ValueError('更正段数必须是 0—10000 的整数')
            if not reason or len(reason) > 200:
                raise ValueError('请填写 1—200 字的更正原因')
            if body.get('confirmed') is not True:
                raise ValueError('请先确认已对照有效视频和 final JSON 核对段数')
            document = self.url_document(instance_id)
            if body.get('revision') != document['revision']:
                raise ValueError('网址文件已被其他操作修改。请重新加载后再更正。')
            row = next((item for item in document['rows'] if item['live_id'] == live_id), None)
            if row is None:
                raise ValueError('当前实例清单中找不到这个直播 ID')
            progress = load_json(Path(self.settings['study_root']) / '_control' /
                                 f"progress_{self.settings['instances'][instance_id - 1]['port']}.json", {})
            receipt_records, _, _ = verified_progress_records(progress, self.settings['study_root'])
            archive_ids = archived_segment_records(self.settings['study_root'], self.settings['ffprobe'],
                                                  cache=self.evidence_cache).get(live_id, set())
            evidence_floor = len(receipt_records.get(live_id, set()) | archive_ids)
            if count < evidence_floor:
                raise ValueError(f'更正值不能低于已核验的 {evidence_floor} 段')

            correction_id = uuid.uuid4().hex
            requested_t = time.time()
            source_row = next((item for item in read_url_list(document['path'], document['target'])
                               if item['live_id'] == live_id), row)
            event = {"correction_id": correction_id, "instance_id": instance_id,
                     "live_id": live_id, "before_count": row['count'],
                     "source_count": source_row['count'], "after_count": count,
                     "valid_count": row['valid_count'], "archived_count": row['archived_count'],
                     "reason": reason, "confirmed": True, "requested_t": requested_t,
                     "expected_revision": document['revision'],
                     "event": "requested"}
            append_count_correction(self.settings['study_root'], event)
            try:
                saved = correct_count(document['path'], document['target'], live_id, count,
                                      body.get('revision'))
            except Exception as error:
                try:
                    append_count_correction(self.settings['study_root'], {
                        **event, "event": "failed", "failure_type": type(error).__name__,
                        "failed_t": time.time()})
                except OSError:
                    pass
                raise
            append_count_correction(self.settings['study_root'], {
                **event, "event": "completed", "corrected_t": requested_t,
                "revision": saved['revision']})
            self._archive_cache = ("", 0, {})
            self.check_cache = None
            result = self.url_document(instance_id)
            result['backup'] = saved['backup']
            result['correction_id'] = correction_id
            result['corrected_t'] = requested_t
            result['message'] = ('历史计数已更正并写入审计日志。运行中的任务仍使用启动时的网址快照；'
                                 '更正值从下一次启动生效。')
            return result

    def url_rows(self, instance, config=None, snapshot=None):
        config = config or self.settings
        source = snapshot or Path(config["study_root"]) / "_config" / instance["urls_file"]
        rows = read_url_list(source, instance["segments"])
        progress = load_json(Path(config["study_root"]) / "_control" /
                             f"progress_{instance['port']}.json", {})
        observations = {}
        valid_records, legacy_counts, receipt_states = verified_progress_records(
            progress, config["study_root"], observations, self.evidence_cache)
        archive_bundle = self.archive_bundle(config["study_root"], config["ffprobe"])
        archive_records = archive_bundle["records"]
        corrections = read_count_corrections(config["study_root"]) if snapshot is None else {}
        for row in rows:
            live_id = row["live_id"]
            progress_entry = progress.get("rooms", {}).get(live_id, {}) if isinstance(progress, dict) else {}
            saved_count = progress_entry.get("count", 0) if isinstance(progress_entry, dict) else 0
            archive_ids = archive_records.get(live_id, set())
            receipt_ids = valid_records.get(live_id, set())
            all_valid_ids = receipt_ids | archive_ids
            correction = corrections.get((instance["id"], live_id))
            if correction and correction['event'] == 'requested':
                correction = correction if row['count'] == correction.get('after_count') else None
            if correction:
                updated_t = progress_entry.get('updated_t', 0) if isinstance(progress_entry, dict) else 0
                post_correction_count = (saved_count if isinstance(updated_t, (int, float))
                                         and updated_t > correction['corrected_t'] else 0)
                row["count"] = max(row["count"], correction['after_count'], post_correction_count,
                                   len(archive_ids))
            else:
                row["count"] = max(row["count"], legacy_counts.get(live_id, 0), saved_count)
            row["valid_count"] = (len(all_valid_ids) if all_valid_ids or
                                  receipt_states.get(live_id) == "invalid" else None)
            row["valid_record_ids"] = sorted(all_valid_ids)
            row["archived_count"] = len(archive_ids)
            row["digital_observations"] = {**archive_bundle["observations"].get(live_id, {}),
                                            **observations.get(live_id, {})}
            row["digital_markers"] = observation_counts(row["digital_observations"])
            row["archive_issues"] = archive_bundle["issues"].get(live_id, [])
            if row["valid_count"] is None:
                row['validation_state'] = '未核验'
            elif row["valid_count"] == 0 and receipt_states.get(live_id) == 'invalid':
                row['validation_state'] = '凭据失效'
            elif row['valid_count'] < row['count']:
                row['validation_state'] = '部分核验'
            else:
                row['validation_state'] = '已核验'
        return rows

    def archive_records(self, study_root):
        return self.archive_bundle(study_root, self.settings["ffprobe"])["records"]

    def archive_bundle(self, study_root, ffprobe):
        now = time.monotonic()
        root = str(Path(study_root).resolve()) + "|" + str(ffprobe)
        if self._archive_cache[0] == root and now - self._archive_cache[1] < 30:
            return self._archive_cache[2]
        observations, issues = {}, {}
        records = archived_segment_records(study_root, ffprobe, observations, issues, self.evidence_cache)
        bundle = {"records": records, "observations": observations, "issues": issues}
        self._archive_cache = (root, now, bundle)
        return bundle

    def archive_counts(self, study_root):
        return {live_id: len(records) for live_id, records in self.archive_records(study_root).items()}

    def jobs(self):
        jobs = []
        for job in self.run.get("jobs", []):
            status = load_json(job["status_file"], {})
            alive = bool(job.get("birth") and process_birth(job["pid"]) == job["birth"])
            phase = status.get("phase", "starting")
            if not alive and phase not in TERMINAL_PHASES:
                phase = "exited"
            jobs.append(dict(job, status=status, phase=phase, alive=alive,
                             pause_supported=bool(job.get("pause_file")),
                             pause_requested=pause_requested(job.get("pause_file")),
                             stop_requested=Path(job["stop_file"]).is_file()))
        return jobs

    def active(self):
        return any(job["alive"] for job in self.jobs())

    def preflight(self):
        with self.lock:
            config = self.settings
            checks = []
            def check(name, ok, detail, required=True):
                checks.append({"name": name, "ok": bool(ok), "detail": detail, "required": required})
            for key, name in (("python", "Python"), ("ffmpeg", "FFmpeg"),
                              ("ffprobe", "FFprobe"), ("edge", "Edge")):
                check(name, Path(config[key]).is_file(), config[key])
            if Path(config["python"]).is_file():
                try:
                    result = subprocess.run([config["python"], "-B", "-c",
                                             "import importlib.util; print(bool(importlib.util.find_spec('DrissionPage')))"],
                                            capture_output=True, timeout=15,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    check("DrissionPage", result.returncode == 0 and result.stdout.strip() == b"True",
                          "所选 Python 必须安装 DrissionPage")
                except (OSError, subprocess.TimeoutExpired):
                    check("DrissionPage", False, "无法检查所选 Python")
            root = Path(config["study_root"])
            check("数据目录", root.is_dir(), str(root))
            try:
                disk = shutil.disk_usage(root if root.is_dir() else root.parent)
                check("剩余空间", disk.free > 2 * 1024**3, f"{disk.free / 1024**3:.1f} GB（保留原视频会增加占用）")
            except OSError:
                check("剩余空间", False, "目录所在磁盘不可读")
            check("登录态", (root / "_config" / "taobao_cookies.json").is_file(),
                  "没有 Cookie 时，启动后在 Edge 登录；最多等待 5 分钟", required=False)
            used_ids = {}
            duplicate_ids = set()
            remaining = 0
            for instance in config["instances"]:
                if not instance["enabled"]:
                    continue
                rows = self.url_rows(instance)
                check(f"实例 {instance['id']} 链接", bool(rows),
                      f"{instance['urls_file']}：{len(rows)} 个去重直播间")
                check(f"端口 {instance['port']}", port_free(instance["port"]), "启动前检查端口占用")
                for row in rows:
                    if row["count"] >= row["target"]:
                        continue
                    remaining += 1
                    if row["live_id"] in used_ids:
                        duplicate_ids.add(row["live_id"])
                    used_ids[row["live_id"]] = instance["id"]
            check("实例间链接重复", not duplicate_ids,
                  f"{len(duplicate_ids)} 个待录直播 ID 重复；调整链接文件后再启动")
            check("待录房间", remaining > 0, f"{remaining} 个房间未达到设置段数")
            check("已有采集任务", not self.active(), "运行中任务请等待完成或录完当前段后停止")
            try:
                existing = existing_crawler_pids()
                check("旧版 / 新版采集进程", not existing,
                      "已运行 PID：" + ", ".join(str(pid) for pid in existing) if existing else "没有已运行的采集进程")
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                check("采集进程查询", False, str(error))
            result = {"checks": checks, "ok": all(c["ok"] for c in checks if c["required"]), "checked_t": time.time()}
            self.check_cache = result
            return result

    def start(self):
        with self.lock:
            result = self.preflight()
            if not result["ok"]:
                raise ValueError("启动条件未满足，请查看环境检查结果")
            config = self.settings
            run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
            run_dir = self.control / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            atomic_json(run_dir / "settings_snapshot.json", config)
            run = {"run_id": run_id, "started_t": time.time(), "settings": config, "jobs": []}
            # Save every successfully spawned child immediately, including partial launch failures.
            self.run = run
            atomic_json(self.control / "active_run.json", run, backup=True)
            for index, instance in enumerate(i for i in config["instances"] if i["enabled"]):
                rows = self.url_rows(instance)
                if not any(row["count"] < row["target"] for row in rows):
                    continue
                snapshot = run_dir / instance["urls_file"]
                snapshot.write_text("".join(f"{r['url']},已录制{r['count']}/{r['target']}\n" for r in rows), encoding="utf-8")
                status_path = run_dir / f"status_{instance['id']}.json"
                stop_path = run_dir / f"stop_{instance['id']}.request"
                pause_path = run_dir / f"pause_{instance['id']}.json"
                atomic_json(pause_path, {"paused": False})
                log_path = run_dir / f"crawler_{instance['id']}.log"
                env = dict(os.environ)
                env.update(build_environment(config, instance, snapshot, status_path, stop_path, run_id, pause_path))
                delay = index * config["launch_gap_seconds"]
                command = [config["python"], "-B", str(ROOT / "src" / "crawler" / "taobao_crawler.py"),
                           instance["urls_file"], str(instance["port"]), str(delay)]
                with log_path.open("xb") as log:
                    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                               stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                               start_new_session=os.name != "nt")
                job = {"instance_id": instance["id"], "port": instance["port"], "pid": process.pid,
                       "birth": process_birth(process.pid), "status_file": str(status_path),
                       "stop_file": str(stop_path), "pause_file": str(pause_path),
                       "log_file": str(log_path), "urls_file": str(snapshot)}
                self.children[process.pid] = process
                run["jobs"].append(job)
                atomic_json(self.control / "active_run.json", run, backup=True)
            return run

    def stop(self):
        with self.lock:
            count = 0
            for job in self.jobs():
                if job["alive"]:
                    Path(job["stop_file"]).write_text("finish_current_segment\n", encoding="utf-8")
                    count += 1
            return {"requested": count, "message": "已请求录完当前段后停止"}

    def set_paused(self, instance_id, paused):
        with self.lock:
            if type(instance_id) is not int or not 1 <= instance_id <= 5:
                raise ValueError("实例编号必须是 1—5 的整数")
            job = next((j for j in self.jobs() if j["instance_id"] == instance_id and j["alive"]), None)
            if not job:
                raise ValueError("该实例没有正在运行的任务")
            if job["stop_requested"]:
                raise ValueError("该实例已请求停止，请等待退出后再启动")
            if not job["pause_supported"]:
                raise ValueError("该任务由旧版本启动，暂停功能在下次启动时生效")
            path = Path(job["pause_file"])
            if not path.resolve().is_relative_to(self.control.resolve()):
                raise ValueError("暂停控制路径无效")
            atomic_json(path, {"paused": paused, "updated_t": time.time()}, backup=True)
            return {"instance_id": instance_id, "paused": paused,
                    "message": f"实例 {instance_id} " + ("已请求暂停，录完当前段后生效" if paused else "已请求继续采集")}

    def state(self):
        with self.lock:
            totals = {"rooms": 0, "completed_rooms": 0, "recorded_segments": 0,
                      "remaining_segments": 0, "valid_segments": 0, "archived_segments": 0}
            instances = []
            distinct = {}
            jobs = self.jobs()
            for instance in self.settings["instances"]:
                job = next((j for j in jobs if j["instance_id"] == instance["id"] and j["alive"]), None)
                if job:
                    config = self.run["settings"]
                    running_instance = next(i for i in config["instances"] if i["id"] == instance["id"])
                    rows = self.url_rows(running_instance, config, job["urls_file"])
                else:
                    rows = self.url_rows(instance)
                instances.append(dict(instance, **summarize_rows(rows),
                                      progress_source="本次运行" if job else "当前设置"))
                if instance["enabled"] or job:
                    for row in rows:
                        previous = distinct.get(row["live_id"], {"count": 0, "target": 0,
                                                                   "valid_count": None, "archived_count": 0})
                        previous_valid = previous["valid_count"]
                        row_valid = row["valid_count"]
                        merged_ids = set(previous.get("valid_record_ids", [])) | set(row["valid_record_ids"])
                        distinct[row["live_id"]] = {
                            "count": max(previous["count"], row["count"]),
                            "target": max(previous["target"], row["target"]),
                            "valid_count": len(merged_ids) if previous_valid is not None or row_valid is not None else None,
                            "valid_record_ids": sorted(merged_ids),
                            "digital_observations": {**previous.get("digital_observations", {}),
                                                     **row["digital_observations"]},
                            "archived_count": max(previous["archived_count"], row["archived_count"]),
                        }
            totals["rooms"] = len(distinct)
            totals["completed_rooms"] = sum(r["count"] >= r["target"] for r in distinct.values())
            known_counts = [r["valid_count"] for r in distinct.values() if r["valid_count"] is not None]
            unverified_rooms = sum(r["valid_count"] is None for r in distinct.values())
            totals["verified_rooms"] = sum(r["valid_count"] is not None and
                                            r["valid_count"] >= r["target"] for r in distinct.values())
            totals["recorded_segments"] = sum(r["count"] for r in distinct.values())
            totals["valid_segments"] = sum(known_counts) if known_counts or not unverified_rooms else None
            totals["archived_segments"] = sum(r["archived_count"] for r in distinct.values())
            totals["digital_markers"] = observation_counts({(lid, key): value
                for lid, row in distinct.items() for key, value in row["digital_observations"].items()})
            totals["remaining_segments"] = sum(max(0, r["target"] - r["count"]) for r in distinct.values())
            totals["target_segments"] = sum(r["target"] for r in distinct.values())
            completed_valid = sum(min(r["valid_count"], r["target"]) for r in distinct.values()
                                  if r["valid_count"] is not None)
            totals["completed_segments"] = (completed_valid if known_counts or not unverified_rooms else None)
            totals["unverified_rooms"] = unverified_rooms
            totals["progress_percent"] = (round(completed_valid * 100 / totals["target_segments"], 1)
                                          if totals["target_segments"] and
                                          (known_counts or not unverified_rooms) else None)
            return {"settings": self.settings, "instances": instances, "totals": totals,
                    "jobs": jobs, "preflight": self.check_cache,
                    "version": VERSION, "code_root": str(ROOT),
                    "server_pid": os.getpid(),
                    "run_id": self.run.get("run_id"), "server_time": time.time()}

    def log_tail(self, instance_id):
        job = next((j for j in self.jobs() if j["instance_id"] == instance_id), None)
        if not job:
            return "本次没有这个实例的运行日志。"
        path = Path(job["log_file"])
        if not path.is_relative_to(self.control):
            raise ValueError("日志路径无效")
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 24000))
            content = stream.read().decode("utf-8", errors="replace")
        return re.sub(r"(?i)(cookie:|authorization:)[^\r\n]*", r"\1 [hidden]", content)


def handler_for(manager, token, expected_origin):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, payload, kind="application/json; charset=utf-8"):
            data = json.dumps(payload, ensure_ascii=False).encode() if kind.startswith("application/json") else payload
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("Host") != urlparse(expected_origin).netloc:
                return self.reply(403, {"error": "只允许本机面板地址"})
            url = urlparse(self.path)
            try:
                if url.path == "/api/state":
                    state = manager.state()
                    state["token"] = token
                    return self.reply(200, state)
                if url.path == "/api/log":
                    return self.reply(200, {"text": manager.log_tail(int(parse_qs(url.query).get("instance", [1])[0]))})
                if url.path == "/api/urls":
                    return self.reply(200, manager.url_document(int(parse_qs(url.query).get('instance', [1])[0])))
                static = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
                if url.path in static:
                    path = ROOT / "src" / "control" / "web" / static[url.path]
                    types = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
                             ".css": "text/css; charset=utf-8"}
                    return self.reply(200, path.read_bytes(), types[path.suffix])
                return self.reply(404, {"error": "页面不存在"})
            except Exception as error:
                return self.reply(400, {"error": str(error)})

        def do_POST(self):
            if (self.headers.get("Host") != urlparse(expected_origin).netloc
                    or self.headers.get("Origin") != expected_origin
                    or not secrets.compare_digest(self.headers.get("X-Control-Token", ""), token)):
                return self.reply(403, {"error": "请求来源无效"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 64000:
                    raise ValueError("请求大小无效")
                body = json.loads(self.rfile.read(length))
                actions = {"/api/settings": lambda: manager.save(body),
                           "/api/check": manager.preflight, "/api/start": manager.start,
                           "/api/stop": manager.stop}
                actions["/api/pause"] = lambda: manager.set_paused(body.get("instance_id"), True)
                actions["/api/resume"] = lambda: manager.set_paused(body.get("instance_id"), False)
                actions['/api/urls'] = lambda: manager.save_urls(body)
                actions['/api/urls/correct-count'] = lambda: manager.correct_url_count(body)
                if self.path not in actions:
                    return self.reply(404, {"error": "操作不存在"})
                return self.reply(200, actions[self.path]())
            except Exception as error:
                return self.reply(400, {"error": str(error)})

        def log_message(self, message, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description="Taobao crawler local control panel")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--study-root")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--python")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    config = defaults(args.study_root)
    for key in ("ffmpeg", "ffprobe", "python"):
        if getattr(args, key):
            config[key] = getattr(args, key)
    manager = Manager(config)
    origin = f"http://127.0.0.1:{args.port}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(manager, secrets.token_hex(32), origin))
    except OSError as error:
        print(f"面板端口 {args.port} 无法启动: {error}")
        # Open an existing server only after verifying its version and code root.
        from urllib.request import urlopen
        try:
            with urlopen(origin + "/api/state", timeout=2) as response:
                state = json.load(response)
            server_pid = state.get("server_pid", load_json(CONTROL / "panel_server.json", {}).get("pid", "未知"))
            if compatible_existing_service(state, ROOT, VERSION) and args.open:
                webbrowser.open(origin)
            elif args.open:
                notice = (f"端口已有不同版本控制台：PID {server_pid}，版本 {state.get('version', '未知')}。\n"
                          f"本次需要 {VERSION}。请先在旧面板请求停止并确认采集任务退出，再关闭旧控制台后重试。\n"
                          "未连接旧服务，也未停止任何任务。")
                if os.name == "nt":
                    ctypes.windll.user32.MessageBoxW(None, notice, "采集控制台版本不匹配", 0x30)
                try:
                    print(notice)
                except (AttributeError, OSError):
                    pass
        except Exception:
            pass
        return
    atomic_json(CONTROL / "panel_server.json", {"pid": os.getpid(), "birth": process_birth(os.getpid()), "url": origin})
    if args.open:
        webbrowser.open(origin)
    print("本地控制台: " + origin, flush=True)
    server.serve_forever()
