"""Recording evidence and observations; never changes source recordings."""
import csv
import json
import math
from pathlib import Path
import subprocess
import time

from src.utils.digital_flags import (find_values_by_key, parse_json_body,
                                    summarize_digital_flags, title_keywords_in_values)
from src.utils.safe_io import digest, probe_video


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def minimum_duration(data):
    minutes = number(data.get("max_minutes"))
    return min(420.0, minutes * 60 * 0.9) if minutes is not None and 1 <= minutes <= 120 else 420.0


def video_validation(video, data, ffprobe, probe=None):
    """Measure media duration; wall clock and file size cannot establish duration."""
    before = Path(video).stat()
    duration = (probe or probe_video)(video, ffprobe)
    minimum = minimum_duration(data)
    if not math.isfinite(duration) or duration < minimum:
        raise ValueError(f"实际视频时长 {duration:.1f} 秒，少于最低 {minimum:.1f} 秒")
    checksum = digest(video)
    after = Path(video).stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("核验期间视频仍在变化；保留文件并稍后核验")
    return {"schema_version": 1, "valid": True, "duration_source": "ffprobe",
            "duration_seconds": duration, "minimum_duration_seconds": minimum,
            "video_sha256": checksum, "validated_t": time.time()}


def validation_matches(data):
    proof = data.get("technical_validation", {})
    if not isinstance(proof, dict):
        return False
    duration = number(data.get("video_duration_seconds"))
    return (proof.get("schema_version") == 1 and proof.get("valid") is True
            and proof.get("duration_source") == "ffprobe" and duration is not None
            and duration >= minimum_duration(data)
            and number(proof.get("duration_seconds")) == duration
            and number(proof.get("minimum_duration_seconds")) == minimum_duration(data)
            and isinstance(proof.get("video_sha256"), str)
            and len(proof["video_sha256"]) == 64)


def digital_observation(data, live_id):
    """Record all captured, unambiguously linked detail flags, with no sample rule."""
    flags, titles, times = [], [], []
    detail_count = matched = missing = ambiguous = outside = untimed = 0
    start, end = number(data.get("record_start_t")), number(data.get("record_end_t"))
    responses = data.get("responses", [])
    if not isinstance(responses, list):
        responses = []
    for response in responses:
        if not isinstance(response, dict) or "live.detail.get" not in str(response.get("url", "")):
            continue
        detail_count += 1
        parsed = parse_json_body(response.get("body"))
        ids = {str(item) for item in find_values_by_key(parsed, "liveId") if item is not None}
        if ids != {str(live_id)}:
            ambiguous += 1
            continue
        matched += 1
        values = find_values_by_key(parsed, "isDigitalAnchorLive")
        flags.extend(values)
        if not values:
            missing += 1
        titles.extend(find_values_by_key(parsed, "liveTitle") or find_values_by_key(parsed, "title"))
        captured = number(response.get("t"))
        if captured is None or start is None or end is None or end <= start:
            untimed += 1
        elif start <= captured <= end:
            if values:
                times.append(captured)
        else:
            outside += 1
    summary = summarize_digital_flags(flags)
    observed = summary["all_true"] if summary["true_count"] + summary["false_count"] else None
    window = end - start if start is not None and end is not None and end > start else None
    times = sorted(set(times))
    max_gap = None
    if window is not None:
        points = [start, *times, end]
        max_gap = max(b - a for a, b in zip(points, points[1:]))
    return {"all_true": observed, "flag_summary": summary,
            "title_keywords": list(title_keywords_in_values(titles)),
            "detail_response_count": detail_count, "matched_response_count": matched,
            "missing_flag_response_count": missing, "unlinked_response_count": ambiguous,
            "outside_recording_response_count": outside, "untimed_response_count": untimed,
            "in_recording_flagged_response_count": len(times),
            "first_flag_t": times[0] if times else None, "last_flag_t": times[-1] if times else None,
            "maximum_observation_gap_seconds": max_gap,
            "recording_window_seconds": window,
            "media_duration_seconds": number(data.get("video_duration_seconds")),
            "wall_clock_media_delta_seconds": (window - float(data["video_duration_seconds"])
                if window is not None and number(data.get("video_duration_seconds")) is not None else None),
            "observation_basis": "captured_matching_detail_responses_only",
            "coverage_note": "仅表示已捕获标记；未证明整个录制期间始终全true，无覆盖阈值分类"}


def observation_counts(records):
    observations = list(records.values())
    return {"all_true": sum(item.get("all_true") is True for item in observations),
            "not_all_true": sum(item.get("all_true") is False for item in observations),
            "missing": sum(item.get("all_true") is None for item in observations)}


def file_evidence(path):
    path = Path(path).resolve()
    return {"destination": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}


class EvidenceCache:
    """Avoid rereading unchanged large videos at each dashboard refresh."""
    def __init__(self):
        self.values = {}

    def get(self, path, kind, reader):
        path = Path(path).resolve()
        stat = path.stat()
        signature = (str(path), kind, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if signature not in self.values:
            result = reader(path)
            after = path.stat()
            if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("核验期间文件发生变化")
            if len(self.values) >= 4096:
                self.values.clear()
            self.values[signature] = result
        return self.values[signature]


def inspect_archive(manifest_path, ffprobe, cache=None):
    """Accept a segment only with matching JSON/video/comments/summary evidence."""
    cache = cache or EvidenceCache()
    records, observations, errors = set(), {}, []
    manifest_path = Path(manifest_path).resolve()
    session = manifest_path.parent
    with manifest_path.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    if not isinstance(manifest, dict) or manifest.get("source_preserved") is not True:
        raise ValueError("归档清单损坏或未确认源文件保留")
    room_id = str(manifest.get("room_id", ""))
    segments = manifest.get("segments")
    if manifest.get("schema_version") != 2 or not isinstance(segments, list):
        return room_id, records, observations, ["旧归档清单缺少成套核验凭据，待重新核验"]

    def checked_file(entry):
        if not isinstance(entry, dict):
            raise ValueError("缺少文件凭据")
        path = Path(entry["destination"]).resolve()
        if (not path.is_relative_to(session) or not path.is_file()
                or path.stat().st_size != entry.get("bytes")):
            raise ValueError("归档文件缺失、越界或大小改变")
        if cache.get(path, "sha256", digest) != entry.get("sha256"):
            raise ValueError("归档文件 SHA256 不匹配")
        return path

    try:
        summary_path = checked_file(manifest.get("summary"))
        with summary_path.open(encoding="utf-8-sig", newline="") as stream:
            summary_rows = list(csv.DictReader(stream))
        ids = [item.get("录制唯一ID") for item in summary_rows]
        if not ids or any(not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError("汇总缺少唯一录制标识或存在重复标识")
    except (OSError, ValueError, KeyError, TypeError) as error:
        return room_id, records, observations, [str(error)]
    segment_ids = [item.get("recording_id") for item in segments if isinstance(item, dict)]
    if (len(segment_ids) != len(segments) or any(not item for item in segment_ids)
            or len(segment_ids) != len(set(segment_ids))):
        return room_id, records, observations, ["归档段录制标识缺失或重复"]
    for item in segments:
        recording_id = str(item["recording_id"])
        try:
            raw = checked_file(item.get("raw_json"))
            video = checked_file(item.get("video"))
            comments = checked_file(item.get("comments"))
            with raw.open(encoding="utf-8") as stream:
                data = json.load(stream)
            expected_id = str(data.get("recording_id") or raw.name.removeprefix("data_").removesuffix("_final.json"))
            if expected_id != recording_id or data.get("segment_index", item["segment_index"]) != item["segment_index"]:
                raise ValueError("JSON 与视频录制标识/段号不对应")
            source_files = data.get("recorded_files", [])
            if (not isinstance(source_files, list) or len(source_files) != 1
                    or Path(source_files[0]).resolve() != Path(item["video"]["source"]).resolve()):
                raise ValueError("原始 JSON 未明确对应归档视频来源")
            duration = cache.get(video, "duration:" + str(ffprobe), lambda path: probe_video(path, ffprobe))
            if (not math.isfinite(duration) or duration < minimum_duration(data)
                    or number(item.get("duration_seconds")) is None
                    or abs(duration - float(item["duration_seconds"])) > 1):
                raise ValueError("视频实测时长不满足要求或与归档凭据不符")
            with comments.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not {"用户", "内容", "时间"}.issubset(reader.fieldnames or []):
                    raise ValueError("弹幕 CSV 表头不完整")
                comment_rows = list(reader)
            extraction = item.get("comment_extraction", {})
            if extraction.get("state") not in ("extracted", "empty_confirmed", "not_captured"):
                raise ValueError("弹幕提取失败或状态未核验")
            if extraction.get("row_count") != len(comment_rows):
                raise ValueError("弹幕 CSV 行数与提取凭据不符")
            if len([row for row in summary_rows if row.get("录制唯一ID") == recording_id]) != 1:
                raise ValueError("汇总中缺少对应录制段")
            records.add(recording_id)
            observations[recording_id] = digital_observation(data, room_id)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
            errors.append(f"{recording_id}: {error}")
    return room_id, records, observations, errors
