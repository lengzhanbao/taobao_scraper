"""Conservative startup reconciliation for one room in one explicit run."""
import json
import hashlib
import hashlib
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from src.utils.safe_io import PortLock, atomic_json, digest
from src.utils.segment_evidence import validation_matches


def _append_report(path, item):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = PortLock(path.with_suffix(path.suffix + ".lock"))
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        lock.close()


def _receipt(final_path, final, video_path):
    final_stat, video_stat = final_path.stat(), video_path.stat()
    return {"segment_index": int(final["segment_index"]),
            "recording_id": str(final["recording_id"]), "final_json": str(final_path.resolve()),
            "final_size": final_stat.st_size, "final_mtime_ns": final_stat.st_mtime_ns,
            "video_path": str(video_path.resolve()), "video_size": video_stat.st_size,
            "video_mtime_ns": video_stat.st_mtime_ns,
            "video_duration_seconds": float(final["video_duration_seconds"]),
            "validation_schema_version": 1, "final_sha256": digest(final_path),
            "video_sha256": digest(video_path)}


def _verified_backup(path, label):
    path = Path(path)
    backup = path.with_name(path.name + ".reconcile_bak_" + label + "_" + uuid.uuid4().hex)
    shutil.copy2(path, backup)
    if path.stat().st_size != backup.stat().st_size or digest(path) != digest(backup):
        raise OSError("reconciliation backup verification failed")
    return backup


def reconcile_room(*, urls_path, progress_path, staging_root, report_path, run_id,
                   instance_id, live_id, target):
    """Repair only a uniquely evidenced one-segment pre/post count discrepancy."""
    urls_path, progress_path = Path(urls_path), Path(progress_path)
    root = Path(staging_root).resolve()
    now = time.time()
    base = {"run_id": str(run_id), "instance_id": int(instance_id), "live_id": str(live_id),
            "at": now, "source": "startup_reconciliation"}

    def review(reason, **evidence):
        item = dict(base, status="needs_review", reason=reason, evidence=evidence)
        _append_report(report_path, item)
        return item

    try:
        if not urls_path.is_file():
            return review("run_url_snapshot_missing")
        run_control = Path(report_path).parent.resolve()
        if not urls_path.resolve().is_relative_to(run_control):
            return review("url_snapshot_outside_run_control")
        url_original = urls_path.read_bytes()
        lines = url_original.decode("utf-8").splitlines(keepends=True)
        matches = [i for i, line in enumerate(lines) if re.search(r"liveId=" + re.escape(str(live_id)) + r"\b", line)]
        if len(matches) != 1:
            return review("url_row_missing_or_duplicated", matches=len(matches))
        row = lines[matches[0]]
        count_matches = list(re.finditer(r"\u5df2\u5f55\u5236(\d+)/(\d+)", row))
        if len(count_matches) > 1 or ("\u5df2\u5f55\u5236" in row and not count_matches):
            return review("url_count_annotation_conflict")
        count_match = count_matches[0] if count_matches else None
        current_count = int(count_match.group(1)) if count_match else 0
        row_target = int(count_match.group(2)) if count_match else int(target)
        if row_target != int(target) or current_count < 0 or current_count > row_target:
            return review("url_count_unknown_or_complete", count=current_count, target=row_target)
        progress = {}
        progress_original = progress_path.read_bytes() if progress_path.exists() else None
        if progress_path.exists():
            progress = json.loads(progress_original.decode("utf-8"))
            if (not isinstance(progress, dict) or progress.get("schema_version") != 2
                    or not isinstance(progress.get("rooms", {}), dict)):
                return review("progress_schema_unknown")
        rooms = progress.setdefault("rooms", {})
        legacy = progress.setdefault("legacy_counts", {})
        if not isinstance(legacy, dict):
            return review("progress_legacy_counts_unknown")
        current_entry = rooms.get(str(live_id), {})
        if not isinstance(current_entry, dict):
            return review("progress_room_state_unknown")
        progress_count = current_entry.get("count", legacy.get(str(live_id), 0))
        if type(progress_count) is not int:
            return review("progress_count_unknown", progress_count=progress_count)
        existing = current_entry.get("segments", [])
        if not isinstance(existing, list):
            return review("progress_receipts_unknown")
        saved_ids = [item.get("recording_id") for item in existing if isinstance(item, dict)
                     and item.get("recording_id")]
        if len(saved_ids) != len(set(saved_ids)):
            return review("duplicate_progress_recording_ids")
        possible_indices = {current_count + 1} if current_count < row_target else set()
        if current_count > 0:
            possible_indices.add(current_count)
        room_dir = root / ("room_" + str(live_id))
        valid = []
        for final_path in room_dir.rglob("*_final.json") if room_dir.is_dir() else []:
            try:
                final_bytes = final_path.read_bytes()
                final = json.loads(final_bytes.decode("utf-8"))
                if (not isinstance(final, dict) or str(final.get("run_id")) != str(run_id)
                        or str(final.get("recording_id", "")) == ""
                        or int(final.get("segment_index", -1)) not in possible_indices
                        or not validation_matches(final)):
                    continue
                live_url = str(final.get("live_url", ""))
                if not re.search(r"[?&]liveId=" + re.escape(str(live_id)) + r"(?:\D|$)", live_url):
                    continue
                files = final.get("recorded_files")
                if not isinstance(files, list) or len(files) != 1:
                    continue
                video = Path(files[0]).resolve()
                if not video.is_relative_to(room_dir.resolve()) or not video.is_file():
                    continue
                proof_hash = final["technical_validation"].get("video_sha256")
                if digest(video) != proof_hash:
                    continue
                matching_saved = any(isinstance(item, dict)
                                    and item.get("recording_id") == final["recording_id"]
                                    for item in existing)
                if matching_saved and not (current_count == int(final["segment_index"])
                                           and progress_count == int(final["segment_index"])):
                    continue
                valid.append((final_path.resolve(), final, video,
                              hashlib.sha256(final_bytes).hexdigest()))
            except (OSError, ValueError, TypeError, KeyError, IndexError):
                continue
        if len(valid) != 1:
            forward = [item for item in valid if int(item[1]["segment_index"]) > current_count]
            if forward:
                valid = forward
        if len(valid) != 1:
            return review("valid_final_evidence_missing_or_ambiguous", candidates=len(valid),
                          expected_segment_indices=sorted(possible_indices))
        final_path, final, video, final_sha_at_read = valid[0]
        expected_index = int(final["segment_index"])
        evidence_receipt = _receipt(final_path, final, video)
        if (evidence_receipt["final_sha256"] != final_sha_at_read
                or digest(video) != final["technical_validation"].get("video_sha256")):
            return review("evidence_changed_during_reconciliation")

        if type(progress_count) is not int or progress_count not in (expected_index - 1, expected_index):
            return review("progress_count_conflict", progress_count=progress_count,
                          expected_before=expected_index - 1, expected_after=expected_index)
        same_index = [item for item in existing if isinstance(item, dict)
                      and item.get("segment_index") == expected_index]
        if same_index and not (len(same_index) == 1 and same_index[0].get("recording_id") == final["recording_id"]):
            return review("progress_receipt_conflict", count=len(same_index))
        matching_receipt = next((item for item in existing if isinstance(item, dict)
                                 and item.get("recording_id") == final["recording_id"]), None)
        if matching_receipt and matching_receipt != evidence_receipt:
            return review("progress_receipt_evidence_mismatch")

        url_after = current_count == expected_index
        progress_after = progress_count == expected_index and matching_receipt is not None
        if url_after and progress_after:
            item = dict(base, status="already_reconciled", recording_id=final["recording_id"],
                        segment_index=expected_index, evidence={"final": evidence_receipt})
            _append_report(report_path, item)
            return item
        # Only the run-specific snapshot is editable. Never derive or touch _config URLs here.
        if current_count not in (expected_index - 1, expected_index):
            return review("url_count_conflict", count=current_count, expected=expected_index)
        planned = dict(base, status="repair_planned", recording_id=final["recording_id"],
                       segment_index=expected_index,
                       evidence={"url_count": current_count, "progress_count": progress_count,
                                 "final": evidence_receipt})
        _append_report(report_path, planned)
        url_write = current_count == expected_index - 1
        progress_write = not matching_receipt or progress_count != expected_index
        if url_write:
            _verified_backup(urls_path, "urls")
        if progress_write and progress_path.exists():
            _verified_backup(progress_path, "progress")
        if urls_path.read_bytes() != url_original:
            return review("url_snapshot_changed_during_reconciliation")
        if progress_original is None:
            if progress_path.exists():
                return review("progress_changed_during_reconciliation")
        elif progress_path.read_bytes() != progress_original:
            return review("progress_changed_during_reconciliation")
        if url_write:
            replacement = f"\u5df2\u5f55\u5236{expected_index}/{row_target}"
            new_row = (row[:count_match.start()] + replacement + row[count_match.end():]
                       if count_match else row.rstrip("\r\n") + "," + replacement + "\n")
            lines[matches[0]] = new_row
            temp = urls_path.with_name(urls_path.name + "." + uuid.uuid4().hex + ".tmp")
            with temp.open("x", encoding="utf-8") as stream:
                stream.writelines(lines)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, urls_path)
        if progress_write:
            segments = list(existing) if matching_receipt else list(existing) + [evidence_receipt]
            rooms[str(live_id)] = {"count": expected_index, "target": row_target,
                                   "updated_t": now, "segments": segments}
            atomic_json(progress_path, {"schema_version": 2, "rooms": rooms,
                                        "legacy_counts": legacy}, backup=False)
        item = dict(base, status="reconciled", recording_id=final["recording_id"],
                    segment_index=expected_index, evidence={"final": evidence_receipt,
                                                            "url_count_after": expected_index,
                                                            "progress_count_after": expected_index})
        _append_report(report_path, item)
        return item
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return review("reconciliation_error", error=type(exc).__name__)
