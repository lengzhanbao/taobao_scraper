# -*- coding: utf-8 -*-
"""Read-only historical media audit; recovered candidates stay outside staging.

Default: report only. --create-finals writes candidate JSON into a new report
folder. Never overwrites sources, changes recording timestamps, updates URL
counts, or treats elapsed wall clock as video duration.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import subprocess
import time
import uuid

from src.utils.config import STUDY_ROOT, FFPROBE
from src.utils.safe_io import atomic_json, digest
from src.utils.segment_evidence import video_validation, digital_observation, number

MIN_SEC = 420
ROOT = Path(__file__).resolve().parent


def assess(data_path, staging, ffprobe):
    data_path, staging = Path(data_path).resolve(), Path(staging).resolve()
    if not data_path.is_relative_to(staging):
        raise ValueError("原始 JSON 路径超出 staging")
    before = data_path.stat()
    with data_path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or not isinstance(data.get("responses"), list):
        raise ValueError("JSON 缺少有效的 responses 列表")
    source_files = data.get("recorded_files", [])
    if not source_files:
        source_files = [str(path) for path in data_path.parent.glob("*.flv") if "待删除" not in path.name]
    if not isinstance(source_files, list) or len(source_files) != 1:
        raise ValueError("视频对应关系不明确；需要恰好一个 recorded_files 视频")
    video = Path(source_files[0]).resolve()
    if not video.is_relative_to(staging) or video.parent != data_path.parent or not video.is_file():
        raise ValueError("对应视频不存在或不在同一个 staging 段/attempt 目录")
    proof = video_validation(video, data, ffprobe)
    duration = proof["duration_seconds"]
    if duration < MIN_SEC:
        raise ValueError(f"FFprobe 实测 {duration:.1f} 秒，不足历史段要求的 420 秒")
    responses = data["responses"]
    complete_capture = False
    journal_name = data.get("responses_journal")
    if journal_name:
        journal = Path(journal_name).resolve()
        if not journal.is_relative_to(staging) or journal.parent != data_path.parent:
            raise ValueError("响应日志路径不属于这个段/attempt")
        journal_before = journal.stat()
        with journal.open(encoding="utf-8") as stream:
            responses = [json.loads(line) for line in stream if line.strip()]
        if any(not isinstance(item, dict) for item in responses):
            raise ValueError("响应日志存在非对象行")
        if (journal_before.st_size, journal_before.st_mtime_ns) != (journal.stat().st_size, journal.stat().st_mtime_ns):
            raise ValueError("响应日志仍在写入，暂不生成候选")
        complete_capture = True
    elif type(data.get("captured_count")) is int and data["captured_count"] == len(responses):
        complete_capture = True
    after = data_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("原始 JSON 仍在变化，暂不生成候选")
    live_id = next((part[5:] for part in data_path.parts if re.fullmatch(r"room_\d+", part)), "")
    candidate = dict(data)
    candidate.update(recorded_files=[str(video)], responses=responses, captured_count=len(responses),
                     video_duration_seconds=duration, technical_validation=proof,
                     manual_finalize=True, recovered_at_t=time.time(), recovery_source=str(data_path),
                     recovery_source_sha256=digest(data_path))
    candidate.setdefault("recording_id", "recovered-" + digest(data_path)[:16] + "-" + proof["video_sha256"][:16])
    candidate["digital_observation"] = digital_observation(candidate, live_id)
    candidate_eligible = (complete_capture and number(data.get("record_start_t")) is not None
                          and type(data.get("segment_index")) is int and data["segment_index"] > 0)
    return {"source_json": str(data_path), "video": str(video), "live_id": live_id,
            "actual_duration_seconds": duration, "technical_valid": True,
            "capture_completeness_recorded": complete_capture, "candidate_eligible": candidate_eligible,
            "all_true": candidate["digital_observation"]["all_true"],
            "note": "" if candidate_eligible else "录制起点、段号或完整响应日志凭据不足；只报告，不生成候选"}, candidate


def run(study_root, ffprobe, report_dir, create_finals=False):
    staging = (Path(study_root) / "_staging").resolve()
    if not staging.is_dir():
        raise ValueError("staging 目录不存在")
    report_dir = Path(report_dir).resolve()
    sessions = (Path(study_root) / "sessions").resolve()
    if (report_dir.is_relative_to(staging) or staging.is_relative_to(report_dir)
            or report_dir.is_relative_to(sessions) or sessions.is_relative_to(report_dir)):
        raise ValueError("报告必须写入与 staging/sessions 分开的新目录")
    report_dir.mkdir(parents=True, exist_ok=False)
    results = []
    # Finalized historical segments are audited too; sessions presence never skips a room.
    sources = sorted(staging.rglob("data_*.json"))
    for source in sources:
        if source.name.endswith("_final.json"):
            already_finalized = True
        else:
            already_finalized = source.with_name(source.stem + "_final.json").exists()
            if already_finalized:
                continue
        try:
            result, candidate = assess(source, staging, ffprobe)
            result["already_finalized"] = already_finalized
            if create_finals and result["candidate_eligible"] and not already_finalized:
                destination = report_dir / "candidates" / source.relative_to(staging)
                destination = destination.with_name(source.stem + "_final.json")
                atomic_json(destination, candidate)
                result["candidate_json"] = str(destination)
            results.append(result)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            results.append({"source_json": str(source), "technical_valid": False, "error": str(error)})
    atomic_json(report_dir / "audit.json", {"minimum_seconds": MIN_SEC, "source_files_unchanged": True,
                "timestamps_unchanged": True, "results": results})
    return results


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-root", default=STUDY_ROOT)
    parser.add_argument("--ffprobe", default=FFPROBE)
    parser.add_argument("--report-dir", default=str(ROOT / "_control" / "recovery_reports" /
                        (time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8])))
    parser.add_argument("--create-finals", action="store_true", help="write eligible candidate JSON only into the report folder")
    args = parser.parse_args(argv)
    results = run(args.study_root, args.ffprobe, args.report_dir, args.create_finals)
    print(f"检查 {len(results)} 段；实测合格 {sum(row['technical_valid'] for row in results)} 段")
    print(f"报告：{args.report_dir}；staging、录制时间戳、网址计数均未修改")


if __name__ == "__main__":
    main()
