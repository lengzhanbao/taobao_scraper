"""Small data-preserving helpers. No helper removes source recordings."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


def _replace_with_retry(temp, path, retries=6):
    """Retry transient Windows sharing violations during atomic file replacement."""
    last_error = None
    for attempt in range(retries):
        try:
            os.replace(temp, path)
            return
        except PermissionError as error:
            last_error = error
            if os.name != "nt" or attempt + 1 >= retries:
                break
            time.sleep(0.02 * (attempt + 1))
    raise last_error


def _cleanup_temp(temp):
    try:
        if temp.exists():
            temp.unlink()
    except OSError:
        pass


def _finite_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _rate(value):
    """Parse ffprobe r_frame_rate such as 25/1 or 30000/1001."""
    if not isinstance(value, str) or "/" not in value:
        return _finite_number(value)
    numerator, denominator = value.split("/", 1)
    numerator, denominator = _finite_number(numerator), _finite_number(denominator)
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def atomic_json(path, payload, backup=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak_" + uuid.uuid4().hex))
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(temp, path)
    except Exception:
        _cleanup_temp(temp)
        raise


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def verified_copy(source, destination, *, source_sha256=None):
    """Never replace an existing different file; verify copied bytes and SHA256."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = source_sha256 or digest(source)
    if destination.exists():
        if source.stat().st_size != destination.stat().st_size or digest(destination) != source_hash:
            raise ValueError("归档目标已存在且内容不同，保留两端文件：" + str(destination))
    else:
        temp = destination.with_name(destination.name + ".copy_" + uuid.uuid4().hex + ".tmp")
        shutil.copy2(source, temp)
        if source.stat().st_size != temp.stat().st_size or digest(temp) != source_hash:
            raise ValueError("复制校验失败，原文件和临时副本均保留：" + str(source))
        # Caller owns the archive port lock; there is no concurrent parser writer.
        _replace_with_retry(temp, destination)
    return {"source": str(source), "destination": str(destination),
            "bytes": source.stat().st_size, "sha256": source_hash}


def probe_video(path, ffprobe):
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
        capture_output=True, timeout=40,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("ffprobe 无法读取视频")
    try:
        info = json.loads(result.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        streams = info.get("streams", [])
        has_video = isinstance(streams, list) and any(isinstance(item, dict) and
                    item.get("codec_type") == "video" for item in streams)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("ffprobe 返回的媒体信息不完整")
    if not math.isfinite(duration) or duration <= 0 or not has_video:
        raise ValueError("视频没有有效时长或视频轨道")
    return duration


def probe_first_packet(path, ffprobe, selector):
    """Return the first packet PTS in seconds, or None when unavailable.

    This is media time, not wall clock. A missing value is left unknown rather
    than guessed from the container duration.
    """
    try:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-select_streams", selector,
             "-show_entries", "packet=pts_time,dts_time", "-read_intervals", "%+#1",
             "-of", "json", str(path)],
            capture_output=True, timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            return None
        packets = json.loads(result.stdout).get("packets", [])
        if not isinstance(packets, list) or not packets:
            return None
        packet = packets[0]
        if not isinstance(packet, dict):
            return None
        value = _finite_number(packet.get("pts_time"))
        return value if value is not None else _finite_number(packet.get("dts_time"))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None


def probe_media(path, ffprobe):
    """Read factual media metadata; absent audio never becomes a guessed value."""
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,codec_name,width,height,"
         "r_frame_rate,avg_frame_rate,start_time,duration", "-of", "json", str(path)],
        capture_output=True, timeout=40,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("ffprobe 无法读取视频")
    try:
        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        duration = _finite_number(info.get("format", {}).get("duration"))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("ffprobe 返回的媒体信息不完整")
    if not isinstance(streams, list):
        streams = []
    video = next((item for item in streams if isinstance(item, dict)
                  and item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if isinstance(item, dict)
                  and item.get("codec_type") == "audio"), None)
    video_duration = _finite_number(video.get("duration")) if video else None
    audio_duration = _finite_number(audio.get("duration")) if audio else None
    return {
        "duration_seconds": duration,
        "has_video": video is not None,
        "has_audio": audio is not None,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "video_duration_seconds": video_duration if video_duration is not None else duration,
        "audio_duration_seconds": (audio_duration if audio_duration is not None
                                   else duration if audio else None),
        "video_width": int(video["width"]) if video and type(video.get("width")) is int else None,
        "video_height": int(video["height"]) if video and type(video.get("height")) is int else None,
        "video_fps": (_rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
                      if video else None),
        "video_start_time": _finite_number(video.get("start_time")) if video else None,
        "audio_start_time": _finite_number(audio.get("start_time")) if audio else None,
        "first_video_pts": probe_first_packet(path, ffprobe, "v:0") if video else None,
        "first_audio_pts": probe_first_packet(path, ffprobe, "a:0") if audio else None,
    }


def iter_jsonl(path):
    """Yield parsed JSON lines without silently skipping malformed records."""
    with Path(path).open(encoding="utf-8") as stream:
        for number_, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError as error:
                raise ValueError(f"JSONL 第 {number_} 行不是有效 JSON") from error


def atomic_jsonl(path, rows, backup=False):
    """Write one JSON object per line atomically; optionally preserve prior bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak_" + uuid.uuid4().hex))
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(temp, path)
    except Exception:
        _cleanup_temp(temp)
        raise


def atomic_json_with_raw_array(path, fields, array_key, raw_items, backup=False):
    """Write JSON whose array items are already-encoded JSON text.

    Long response journals are streamed straight into the final artifact, so the
    complete evidence is kept without holding every parsed response in memory.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak_" + uuid.uuid4().hex))
    if not isinstance(fields, dict) or array_key in fields:
        raise ValueError("数组字段必须独立于普通字段")
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            encoded = json.dumps(fields, ensure_ascii=False, indent=2).rstrip()
            if not encoded.endswith("}"):
                raise ValueError("字段序列化结果无效")
            body = encoded[:-1].rstrip()
            stream.write(body)
            if body.strip() != "{":
                stream.write(",\n  ")
            else:
                stream.write("\n  ")
            stream.write(json.dumps(array_key, ensure_ascii=False) + ": [")
            first = True
            for raw in raw_items:
                text = str(raw).strip()
                if not text:
                    continue
                stream.write("\n    " if first else ",\n    ")
                stream.write(text)
                first = False
            stream.write("\n  ]\n}\n")
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(temp, path)
    except Exception:
        _cleanup_temp(temp)
        raise


class PortLock:
    """OS lock released when the owning process exits, including abnormal exit."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        if path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError("实例端口已被新版爬虫占用")

    def close(self):
        self.stream.close()
