"""Validated settings and URL snapshots; no writes to original URL lists."""
from pathlib import Path
import os
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]


def default_edge_path():
    if os.name == "nt":
        return r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    for executable in ("microsoft-edge", "msedge", "google-chrome", "chromium", "chromium-browser"):
        candidate = shutil.which(executable)
        if candidate:
            return candidate
    if sys.platform == "darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return "/usr/bin/chromium"


def defaults(study_root=None):
    python_path = Path(sys.executable)
    if python_path.name.lower() == "pythonw.exe" and python_path.with_name("python.exe").is_file():
        python_path = python_path.with_name("python.exe")
    return {
        "study_root": str(Path(study_root or ROOT / "直播研究数据").resolve()),
        "python": str(python_path),
        "ffmpeg": str(ROOT / "DouyinLiveRecorder_v4.0.7" / "ffmpeg" / "ffmpeg.exe"),
        "ffprobe": str(ROOT / "DouyinLiveRecorder_v4.0.7" / "ffmpeg" / "ffprobe.exe"),
        "edge": default_edge_path(),
        "max_minutes": 20, "cooldown_minutes": 120, "batch_rooms": 6,
        "launch_gap_seconds": 45,
        "instances": [{"id": i, "enabled": True, "port": 9222 + i,
                       "segments": 4 if i == 4 else 3, "urls_file": f"urls_{i}.txt"}
                      for i in range(1, 6)],
    }


def validate(config):
    if not isinstance(config, dict):
        raise ValueError("参数必须是对象")
    required = defaults()
    if set(config) != set(required):
        raise ValueError("设置字段不完整或包含未知字段")
    out = dict(config)
    for key in ("study_root", "python", "ffmpeg", "ffprobe", "edge"):
        value = config[key]
        if not isinstance(value, str) or not value.strip() or any(c in value for c in "\r\n\x00"):
            raise ValueError(key + " 路径无效")
        if not Path(value).is_absolute():
            raise ValueError(key + " 请填写完整路径")
        out[key] = str(Path(value).resolve())
    for key, low, high in (("max_minutes", 1, 120), ("cooldown_minutes", 1, 1440),
                           ("launch_gap_seconds", 0, 300)):
        value = config[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} 必须是 {low}—{high} 的整数")
    if type(config["batch_rooms"]) is not int or config["batch_rooms"] <= 0:
        raise ValueError("batch_rooms 必须是正整数（至少 1 间）")
    if not isinstance(config["instances"], list) or len(config["instances"]) != 5:
        raise ValueError("需要 5 个实例设置")
    rows = []
    for i, row in enumerate(config["instances"], 1):
        if not isinstance(row, dict) or set(row) != {"id", "enabled", "port", "segments", "urls_file"}:
            raise ValueError("实例设置不完整")
        if type(row["id"]) is not int or row["id"] != i or row["port"] != 9222 + i:
            raise ValueError("实例编号和端口不能修改")
        if type(row["enabled"]) is not bool or row["urls_file"] != f"urls_{i}.txt":
            raise ValueError("实例开关或 URL 文件名无效")
        if type(row["segments"]) is not int or not 1 <= row["segments"] <= 20:
            raise ValueError("段数必须是 1—20 的整数")
        rows.append(dict(row))
    if not any(row["enabled"] for row in rows):
        raise ValueError("至少启用一个实例")
    out["instances"] = rows
    return out


def read_url_list(path, target):
    """Deduplicate by liveId. Only canonical live URLs leave this function."""
    found = {}
    if not Path(path).is_file():
        return []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = re.search(r"liveId=(\d+)\b", line)
        if not match:
            continue
        live_id = match.group(1)
        count_match = re.search(r"已录制(\d+)/(\d+)", line)
        count = int(count_match.group(1)) if count_match else 0
        previous = found.get(live_id, {}).get("count", 0)
        found[live_id] = {"live_id": live_id, "url": f"https://tbzb.taobao.com/live?liveId={live_id}",
                          "count": max(count, previous), "target": target}
    return list(found.values())


def build_environment(config, instance, url_snapshot, status_path, stop_path, run_id, pause_path=None):
    return {
        "LIVE_STUDY_ROOT": config["study_root"], "LIVE_FFMPEG": config["ffmpeg"],
        "LIVE_FFPROBE": config["ffprobe"], "LIVE_EDGE_PATH": config["edge"],
        "LIVE_MAX_MIN": str(config["max_minutes"]), "LIVE_MAX_ROUND": str(instance["segments"]),
        "LIVE_MAX_ROUND_OVERRIDE": "1", "LIVE_COOLDOWN_SEC": str(config["cooldown_minutes"] * 60),
        "LIVE_BATCH_ROOMS": str(config["batch_rooms"]), "LIVE_URLS_FILE": str(url_snapshot),
        "LIVE_STATUS_FILE": str(status_path), "LIVE_STOP_FILE": str(stop_path),
        "LIVE_PAUSE_FILE": str(pause_path) if pause_path else "",
        "LIVE_PROGRESS_FILE": str(Path(config["study_root"]) / "_control" / f"progress_{instance['port']}.json"),
        "LIVE_RUN_ID": run_id, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(ROOT / "_control" / "pycache"),
    }
