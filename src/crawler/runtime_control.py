"""Pause between segments using a persistent control file; never stop a recorder."""
import json
from pathlib import Path
import time

from src.control.url_lists import url_file_lock
from src.utils.safe_io import atomic_json


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


def wait_while_paused(path, *, stop_requested, publish_status, log, sleep=time.sleep,
                      process_commands=None):
    if process_commands:
        process_commands()
    if not pause_requested(path):
        return not stop_requested()
    log("实例已暂停，当前段原始文件保留；点击继续后恢复")
    while pause_requested(path) and not stop_requested():
        if process_commands:
            process_commands()
            if not pause_requested(path) or stop_requested():
                break
        publish_status("paused", elapsed_seconds=0, remaining_seconds=None)
        sleep(1)
    if not stop_requested():
        log("实例继续采集，沿用原段数和房间冷却时间")
    return not stop_requested()


def apply_control_command(command_path, command_lock, stop_path, pause_path, receipt_dir,
                          state_path, *, run_id, instance_id, processed_seq,
                          acknowledge):
    """Apply only latest pause/resume at a safe checkpoint; return new worker sequence."""
    command_path = Path(command_path)
    if not command_path.is_file():
        return processed_seq, None
    with url_file_lock(command_lock):
        if stop_path and Path(stop_path).exists():
            return processed_seq, None
        try:
            with command_path.open(encoding="utf-8") as stream:
                command = json.load(stream)
        except (OSError, ValueError):
            return processed_seq, None
        if not isinstance(command, dict):
            return processed_seq, None
        sequence = command.get("command_seq")
        operation_id = command.get("operation_id")
        if (command.get("run_id") != run_id or command.get("instance_id") != instance_id
                or command.get("operation") not in ("pause", "resume")
                or type(sequence) is not int or sequence <= processed_seq
                or not isinstance(operation_id, str) or not operation_id):
            return processed_seq, None
        if command.get("desired_state") is not (command.get("operation") == "pause"):
            acknowledge(command, "failed", {"message": "desired_state 与 operation 不匹配"})
            return sequence, command
        receipt_path = Path(receipt_dir) / f"{operation_id}.json"
        try:
            with receipt_path.open(encoding="utf-8") as stream:
                receipt = json.load(stream)
        except (OSError, ValueError):
            receipt = {}
        receipt_matches = (receipt.get("run_id") == run_id
                           and receipt.get("instance_id") == instance_id
                           and receipt.get("operation_id") == operation_id
                           and receipt.get("command_seq") == sequence)
        if receipt_matches and receipt.get("status") in ("applied", "superseded"):
            return sequence, command
        paused = command["desired_state"] is True
        atomic_json(pause_path, {"paused": paused, "updated_t": time.time(),
                                 "operation_id": operation_id, "command_seq": sequence,
                                 "run_id": run_id})
        if not acknowledge(command, "applied", {"desired_state": paused}):
            return processed_seq, None
        atomic_json(state_path, {"run_id": run_id, "instance_id": instance_id,
                                 "last_command_seq": sequence})
        return sequence, command
