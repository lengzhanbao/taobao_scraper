"""Persistent command sequence and idempotency receipts scoped to a data root."""
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import uuid

from src.control.url_lists import url_file_lock
from src.utils.safe_io import atomic_json

OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class OperationStore:
    def __init__(self, study_root):
        self.root = Path(study_root) / "_control" / "panel_operations_v1"
        self.requests = self.root / "requests"
        self.sequences = self.root / "sequences"

    @staticmethod
    def normalize_id(operation_id):
        if operation_id is None:
            return uuid.uuid4().hex
        value = str(operation_id)
        if not OPERATION_ID_RE.fullmatch(value):
            raise ValueError("operation_id 格式无效")
        return value

    def request_path(self, instance_id, operation_id):
        operation_id = self.normalize_id(operation_id)
        return self.requests / f"instance_{instance_id}" / f"{operation_id}.json"

    def receipt_path(self, run_dir, instance_id, operation_id):
        return Path(run_dir) / "operation_receipts" / f"instance_{instance_id}" / f"{self.normalize_id(operation_id)}.json"

    def command_path(self, run_dir, instance_id):
        return Path(run_dir) / f"command_{instance_id}.json"

    def sequence_path(self, instance_id):
        return self.sequences / f"instance_{instance_id}.json"

    def get_request(self, instance_id, operation_id):
        path = self.request_path(instance_id, operation_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def create_request(self, *, run_id, instance_id, operation_id, operation, desired_state):
        operation_id = self.normalize_id(operation_id)
        request_path = self.request_path(instance_id, operation_id)
        self.root.mkdir(parents=True, exist_ok=True)
        with url_file_lock(self.root / "sequence_lock"):
            if request_path.exists():
                previous = self.get_request(instance_id, operation_id)
                if not previous:
                    raise ValueError("已有 operation_id 凭据损坏，拒绝复用")
                if (previous.get("run_id") != run_id or previous.get("operation") != operation
                        or previous.get("desired_state") != desired_state):
                    raise ValueError("operation_id 已用于不同操作")
                return previous, False
            sequence_path = self.sequence_path(instance_id)
            if not sequence_path.exists():
                sequence = 0
            else:
                try:
                    sequence_data = json.loads(sequence_path.read_text(encoding="utf-8"))
                    sequence = sequence_data.get("last_seq")
                except (OSError, ValueError, TypeError, AttributeError) as error:
                    raise ValueError("命令序号文件损坏，拒绝回退序号") from error
                if type(sequence) is not int or sequence < 0:
                    raise ValueError("命令序号文件无效，拒绝回退序号")
            sequence += 1
            atomic_json(sequence_path, {"instance_id": instance_id, "last_seq": sequence,
                                        "updated_at": utc_now()})
            now = utc_now()
            request = {"run_id": str(run_id), "instance_id": int(instance_id),
                       "operation_id": operation_id, "operation": operation,
                       "desired_state": desired_state, "command_seq": sequence,
                       "status": "accepted", "created_at": now, "updated_at": now,
                       "status_history": [{"status": "accepted", "time_utc": now}]}
            atomic_json(request_path, request)
            return request, True

    def update_request(self, instance_id, operation_id, status, *, detail=None):
        path = self.request_path(instance_id, operation_id)
        with url_file_lock(self.root / "sequence_lock"):
            request = self.get_request(instance_id, operation_id)
            if not request:
                return None
            if request.get("status") == status:
                return request
            now = utc_now()
            request["status"] = status
            request["updated_at"] = now
            if detail:
                request["detail"] = str(detail)
            request.setdefault("status_history", []).append({"status": status, "time_utc": now})
            atomic_json(path, request)
            return request

    def get_worker_receipt(self, run_dir, instance_id, operation_id):
        path = self.receipt_path(run_dir, instance_id, operation_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def list_requests(self, run_id):
        results = []
        if not self.requests.exists():
            return results
        for path in self.requests.glob("instance_*/*.json"):
            try:
                request = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(request, dict) and request.get("run_id") == run_id:
                results.append(request)
        return sorted(results, key=lambda row: (row.get("created_at", ""), row.get("command_seq", 0)))

