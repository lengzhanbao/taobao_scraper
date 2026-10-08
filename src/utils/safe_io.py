"""Small data-preserving helpers. No helper removes source recordings."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid


def atomic_json(path, payload, backup=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak_" + uuid.uuid4().hex))
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def verified_copy(source, destination):
    """Never replace an existing different file; verify copied bytes and SHA256."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = digest(source)
    if destination.exists():
        if source.stat().st_size != destination.stat().st_size or digest(destination) != source_hash:
            raise ValueError("归档目标已存在且内容不同，保留两端文件：" + str(destination))
    else:
        temp = destination.with_name(destination.name + ".copy_" + uuid.uuid4().hex + ".tmp")
        shutil.copy2(source, temp)
        if source.stat().st_size != temp.stat().st_size or digest(temp) != source_hash:
            raise ValueError("复制校验失败，原文件和临时副本均保留：" + str(source))
        # Caller owns the archive port lock; there is no concurrent parser writer.
        os.replace(temp, destination)
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
    info = json.loads(result.stdout)
    duration = float(info.get("format", {}).get("duration", 0))
    if duration <= 0 or not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
        raise ValueError("视频没有有效时长或视频轨道")
    return duration


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
