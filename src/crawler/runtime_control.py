"""Pause between segments using a persistent control file; never stop a recorder."""
import json
from pathlib import Path
import time


def pause_requested(path):
    if not path:
        return False
    try:
        with Path(path).open(encoding="utf-8") as stream:
            payload = json.load(stream)
        return payload.get("paused") is not False
    except FileNotFoundError:
        return False
    except (OSError, ValueError, AttributeError):
        # An unreadable control must not accidentally resume a paused task.
        return True


def wait_while_paused(path, *, stop_requested, publish_status, log, sleep=time.sleep):
    if not pause_requested(path):
        return not stop_requested()
    log("实例已暂停，当前段原始文件保留；点击继续后恢复")
    while pause_requested(path) and not stop_requested():
        publish_status("paused", elapsed_seconds=0, remaining_seconds=None)
        sleep(1)
    if not stop_requested():
        log("实例继续采集，沿用原段数和房间冷却时间")
    return not stop_requested()
