"""Recording evidence and observations; never changes source recordings."""
from collections import OrderedDict
import copy
import csv
import datetime
import json
import math
from pathlib import Path
import subprocess
import threading
import time

from src.utils.digital_flags import (find_values_by_key, parse_json_body,
                                    summarize_digital_flags, title_keywords_in_values)
from src.utils.safe_io import digest, iter_jsonl, probe_media, probe_video


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


TARGET_DURATION_RATIO = 0.9


def minimum_duration(data):
    minutes = number(data.get("max_minutes"))
    return min(420.0, minutes * 60 * 0.9) if minutes is not None and 1 <= minutes <= 120 else 420.0


def target_duration(data):
    """Planned media duration, only when the configured segment length is known."""
    minutes = number(data.get("max_minutes"))
    return minutes * 60 if minutes is not None and 1 <= minutes <= 120 else None


def video_validation(video, data, ffprobe, probe=None, media_probe=None):
    """Measure media duration; wall clock and file size cannot establish duration.

    ``probe`` keeps the existing duration-only contract for callers and tests.
    ``media_probe`` optionally adds audio/codec/first-PTS facts. Its failure is
    recorded as unknown media facts instead of changing the validity decision.
    """
    before = Path(video).stat()
    media = None
    if media_probe is probe_media and probe in (None, probe_video):
        media = probe_media(video, ffprobe)
        duration = number(media.get("duration_seconds")) if isinstance(media, dict) else None
        has_video = bool(media.get("has_video")) if isinstance(media, dict) else False
        if duration is None or duration <= 0 or not has_video:
            raise ValueError("视频没有有效时长或视频轨道")
    else:
        duration = (probe or probe_video)(video, ffprobe)
    minimum = minimum_duration(data)
    if not math.isfinite(duration) or duration < minimum:
        raise ValueError(f"实际视频时长 {duration:.1f} 秒，少于最低 {minimum:.1f} 秒")
    checksum = digest(video)
    after = Path(video).stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("核验期间视频仍在变化；保留文件并稍后核验")
    result = {"schema_version": 1, "valid": True, "duration_source": "ffprobe",
              "duration_seconds": duration, "minimum_duration_seconds": minimum,
              "video_sha256": checksum, "validated_t": time.time()}
    if media_probe is not None:
        if media is None:
            try:
                media = media_probe(video, ffprobe)
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                media = None
        if isinstance(media, dict):
            result["media"] = media
        final = Path(video).stat()
        if (before.st_size, before.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            raise ValueError("核验期间视频仍在变化；保留文件并稍后核验")
    return result


def recording_quality(data):
    """Separate technical validity, planned-duration achievement and media facts.

    The threshold is an observation of the plan, not a claim that the segment is
    usable for every research question. Missing facts stay ``None``; a 7-minute
    file can remain technically valid while ``target_duration_met`` is false.
    """
    proof = data.get("technical_validation")
    proof = proof if isinstance(proof, dict) else {}
    media = proof.get("media") if isinstance(proof.get("media"), dict) else data.get("media_facts")
    media = media if isinstance(media, dict) else {}
    actual = number(data.get("video_duration_seconds"))
    if actual is None:
        actual = number(proof.get("duration_seconds"))
    target = target_duration(data)
    ratio = (actual / target) if actual is not None and target else None
    met = (ratio >= TARGET_DURATION_RATIO) if ratio is not None else None
    technical_valid = validation_matches(data) if proof else False
    has_video = media.get("has_video")
    has_video = bool(has_video) if isinstance(has_video, bool) else (True if technical_valid else None)
    if technical_valid and met is True:
        state = "technical_valid_target_met"
    elif technical_valid and met is False:
        state = "technical_valid_target_not_met"
    elif technical_valid:
        state = "technical_valid_target_unknown"
    else:
        state = "technically_invalid"
    return {
        "schema_version": 1,
        "quality_state": state,
        "technical_valid": technical_valid,
        "duration_source": "ffprobe" if actual is not None else None,
        "technical_minimum_seconds": minimum_duration(data),
        "target_duration_seconds": target,
        "actual_duration_seconds": actual,
        "duration_completion_ratio": round(ratio, 4) if ratio is not None else None,
        "target_duration_met": met,
        "target_duration_threshold": TARGET_DURATION_RATIO,
        "has_video": has_video,
        "has_audio": media.get("has_audio") if isinstance(media.get("has_audio"), bool) else None,
        "video_codec": media.get("video_codec"),
        "audio_codec": media.get("audio_codec"),
        "video_duration_seconds": (number(media.get("video_duration_seconds"))
                                   if media.get("video_duration_seconds") is not None else actual),
        "audio_duration_seconds": number(media.get("audio_duration_seconds")),
        "video_width": media.get("video_width"),
        "video_height": media.get("video_height"),
        "video_fps": number(media.get("video_fps")),
        "note": ("技术有效、目标时长达成与科研可用性是三个独立判断；"
                 "缺少音轨或编码事实时保持 unknown，不推断可用性"),
    }


def timing_evidence(data, media=None, events=None):
    """Keep wall-clock, media and response-received time bases explicitly apart."""
    media = media if isinstance(media, dict) else (
        data.get("technical_validation", {}).get("media")
        if isinstance(data.get("technical_validation"), dict)
        and isinstance(data.get("technical_validation", {}).get("media"), dict) else {})
    start = number(data.get("record_start_t"))
    end = number(data.get("record_end_t"))
    first_data = number(data.get("first_video_data_t"))
    duration = number(data.get("video_duration_seconds"))
    window = (end - start) if start is not None and end is not None and end > start else None
    first_video_pts = number(media.get("first_video_pts"))
    first_audio_pts = number(media.get("first_audio_pts"))
    if start is not None and first_data is not None and duration is not None:
        alignment = "approximate"
    else:
        alignment = "unresolved"
    def iso(epoch):
        return (datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
                .isoformat(timespec="milliseconds").replace("+00:00", "Z")
                if epoch is not None else None)
    return {
        "schema_version": 1,
        "recording_id": data.get("recording_id"),
        "live_id": data.get("live_id") or data.get("observed_live_id"),
        "record_start_wall_time": start,
        "record_start_wall_time_iso": iso(start),
        "record_end_wall_time": end,
        "record_end_wall_time_iso": iso(end),
        "first_video_data_wall_time": first_data,
        "first_video_data_wall_time_iso": iso(first_data),
        "video_duration_seconds": duration,
        "audio_duration_seconds": number(media.get("audio_duration_seconds")),
        "first_video_pts": first_video_pts,
        "first_audio_pts": first_audio_pts,
        "time_basis": "wall_clock_from_ffmpeg_launch",
        "media_time_basis": "ffprobe_pts_seconds",
        "alignment_status": alignment,
        "alignment_uncertainty_ms": None,
        "wall_clock_media_window_delta_seconds": (round(window - duration, 3)
                                                  if window is not None and duration is not None else None),
        "mapping": {"formula": "t_media ≈ t_wall - record_start_wall_time - delta",
                    "reference_wall_time": start, "delta_seconds": None,
                    "delta_source": "not_estimated", "note": "未估计时延时不把 delta 默认为零而宣称精确对齐"},
        "response_received_time_basis": "capture_listener_wall_clock",
        "event_time_basis": "derived_from_response_received_time",
        "events": list(events or []),
    }


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


class DigitalObservationCollector:
    """Single-pass accumulator so journals need not be held in memory twice."""

    def __init__(self, live_id, start_t=None, end_t=None, media_duration=None):
        self.live_id = str(live_id) if live_id is not None else None
        self.start, self.end = start_t, end_t
        self.media_duration = media_duration
        self.flags, self.titles, self.times = [], [], []
        self.detail_count = self.matched = self.missing = 0
        self.ambiguous = self.outside = self.untimed = 0

    def observe(self, response):
        if not isinstance(response, dict) or "live.detail.get" not in str(response.get("url", "")):
            return
        self.detail_count += 1
        parsed = parse_json_body(response.get("body"))
        ids = {str(item) for item in find_values_by_key(parsed, "liveId") if item is not None}
        if ids != {self.live_id}:
            self.ambiguous += 1
            return
        self.matched += 1
        values = find_values_by_key(parsed, "isDigitalAnchorLive")
        self.flags.extend(values)
        if not values:
            self.missing += 1
        self.titles.extend(find_values_by_key(parsed, "liveTitle") or find_values_by_key(parsed, "title"))
        captured = number(response.get("t"))
        if captured is None or self.start is None or self.end is None or self.end <= self.start:
            self.untimed += 1
        elif self.start <= captured <= self.end:
            if values:
                self.times.append(captured)
        else:
            self.outside += 1

    def result(self):
        summary = summarize_digital_flags(self.flags)
        observed = summary["all_true"] if summary["true_count"] + summary["false_count"] else None
        window = (self.end - self.start if self.start is not None and self.end is not None
                  and self.end > self.start else None)
        times = sorted(set(self.times))
        max_gap = None
        if window is not None:
            points = [self.start, *times, self.end]
            max_gap = max(b - a for a, b in zip(points, points[1:]))
        return {"all_true": observed, "flag_summary": summary,
                "title_keywords": list(title_keywords_in_values(self.titles)),
                "detail_response_count": self.detail_count, "matched_response_count": self.matched,
                "missing_flag_response_count": self.missing, "unlinked_response_count": self.ambiguous,
                "outside_recording_response_count": self.outside,
                "untimed_response_count": self.untimed,
                "in_recording_flagged_response_count": len(times),
                "first_flag_t": times[0] if times else None,
                "last_flag_t": times[-1] if times else None,
                "maximum_observation_gap_seconds": max_gap,
                "recording_window_seconds": window,
                "media_duration_seconds": self.media_duration,
                "wall_clock_media_delta_seconds": (window - self.media_duration
                    if window is not None and self.media_duration is not None else None),
                "observation_basis": "captured_matching_detail_responses_only",
                "coverage_note": "仅表示已捕获标记；未证明整个录制期间始终全true，无覆盖阈值分类"}


def digital_observation(data, live_id):
    """Record all captured, unambiguously linked detail flags, with no sample rule."""
    collector = DigitalObservationCollector(
        live_id, number(data.get("record_start_t")), number(data.get("record_end_t")),
        number(data.get("video_duration_seconds")))
    responses = data.get("responses", [])
    if isinstance(responses, list):
        for response in responses:
            collector.observe(response)
    return collector.result()


def observation_counts(records):
    observations = list(records.values())
    return {"all_true": sum(item.get("all_true") is True for item in observations),
            "not_all_true": sum(item.get("all_true") is False for item in observations),
            "missing": sum(item.get("all_true") is None for item in observations)}


def file_evidence(path):
    path = Path(path).resolve()
    return {"destination": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}


class EvidenceCache:
    """Thread-safe LRU cache avoiding redundant reads of unchanged large files."""
    def __init__(self, max_entries=4096):
        self.values = OrderedDict()
        self.max_entries = max(64, int(max_entries))
        self._lock = threading.Lock()

    def get(self, path, kind, reader):
        path = Path(path).resolve()
        stat = path.stat()
        signature = (str(path), kind, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        with self._lock:
            if signature in self.values:
                self.values.move_to_end(signature)
                return self.values[signature]
        result = reader(path)
        after = path.stat()
        if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("核验期间文件发生变化")
        with self._lock:
            self.values[signature] = result
            self.values.move_to_end(signature)
            while len(self.values) > self.max_entries:
                self.values.popitem(last=False)
        return result


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
    analysis_entries = manifest.get("recording_analysis")
    analysis_by_id = {}
    if analysis_entries is not None:
        if not isinstance(analysis_entries, list):
            return room_id, records, observations, ["逐录制分析索引格式无效"]
        for entry in analysis_entries:
            if not isinstance(entry, dict) or not entry.get("recording_id"):
                return room_id, records, observations, ["逐录制分析索引缺少录制标识"]
            key = str(entry["recording_id"])
            if key in analysis_by_id:
                return room_id, records, observations, ["逐录制分析索引存在重复录制标识"]
            analysis_by_id[key] = entry
        if set(analysis_by_id) != {str(item) for item in segment_ids}:
            return room_id, records, observations, ["逐录制分析索引与归档段不一致"]

    def verify_analysis(entry, segment_item, recording_id, raw, video):
        manifest_path = checked_file(entry.get("manifest"))
        with manifest_path.open(encoding="utf-8") as stream:
            analysis = json.load(stream)
        if not isinstance(analysis, dict) or analysis.get("schema_version") != 1:
            raise ValueError("逐录制分析清单格式无效")
        if str(analysis.get("recording_id")) != recording_id:
            raise ValueError("逐录制分析清单标识与录制段不一致")
        if analysis.get("segment_index") != segment_item.get("segment_index"):
            raise ValueError("逐录制分析清单段号与录制段不一致")
        if str(analysis.get("live_id")) != str(room_id):
            raise ValueError("逐录制分析清单直播间标识不一致")
        files = analysis.get("files")
        required = {"raw_json", "video", "product_events", "product_events_strict",
                    "product_events_candidate", "product_intervals",
                    "recording_quality", "timing_evidence"}
        if not isinstance(files, dict) or not required.issubset(files):
            raise ValueError("逐录制分析清单缺少必要文件")
        checked = {name: checked_file(evidence) for name, evidence in files.items()}
        for name, expected in (("raw_json", segment_item.get("raw_json")),
                               ("video", segment_item.get("video"))):
            evidence = files.get(name) or {}
            if (Path(evidence.get("destination", "")).resolve() != Path(expected.get("destination", "")).resolve()
                    or evidence.get("sha256") != expected.get("sha256")
                    or evidence.get("bytes") != expected.get("bytes")):
                raise ValueError("逐录制分析清单与归档 JSON/视频凭据不一致")
        quality = json.loads(checked["recording_quality"].read_text(encoding="utf-8"))
        if str(quality.get("recording_id")) != recording_id:
            raise ValueError("录制质量文件与录制标识不一致")
        timing = json.loads(checked["timing_evidence"].read_text(encoding="utf-8"))
        if str(timing.get("recording_id")) != recording_id:
            raise ValueError("时间证据文件与录制标识不一致")
        expected_statuses = {
            "product_events_strict": "matched",
            "product_events_candidate": "context_only",
        }
        for name in ("product_events", "product_events_strict",
                     "product_events_candidate", "product_intervals"):
            expected_status = expected_statuses.get(name)
            for row in iter_jsonl(checked[name]):
                if str(row.get("recording_id")) != recording_id:
                    raise ValueError(f"{name} 含其他录制的记录")
                if row.get("live_id") is not None and str(row.get("live_id")) != str(room_id):
                    raise ValueError(f"{name} 含其他直播间的记录")
                if expected_status is not None and row.get("attribution_status") != expected_status:
                    raise ValueError(f"{name} 归属等级不匹配")
        return True

    legacy_analysis_files = manifest.get("analysis_files")
    if legacy_analysis_files is not None:
        try:
            if not isinstance(legacy_analysis_files, list):
                raise ValueError("分析文件凭据格式无效")
            for entry in legacy_analysis_files:
                checked_file(entry)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return room_id, records, observations, [str(error)]
    for item in segments:
        recording_id = str(item["recording_id"])
        try:
            raw = checked_file(item.get("raw_json"))
            video = checked_file(item.get("video"))
            comments = checked_file(item.get("comments"))
            def _load_raw_summary(path):
                with path.open(encoding="utf-8") as stream:
                    raw_data = json.load(stream)
                if not isinstance(raw_data, dict):
                    raise ValueError("原始 JSON 格式无效")
                return {
                    "recording_id": raw_data.get("recording_id"),
                    "segment_index": raw_data.get("segment_index"),
                    "recorded_files": raw_data.get("recorded_files", []),
                    "max_minutes": raw_data.get("max_minutes"),
                    "digital_observation": digital_observation(raw_data, room_id),
                }
            data = cache.get(raw, f"archive_raw_summary:{room_id}", _load_raw_summary)
            expected_id = str(data.get("recording_id") or raw.name.removeprefix("data_").removesuffix("_final.json"))
            raw_segment_index = data.get("segment_index")
            if raw_segment_index is None:
                raw_segment_index = item["segment_index"]
            if expected_id != recording_id or raw_segment_index != item["segment_index"]:
                raise ValueError("JSON 与视频录制标识/段号不对应")
            source_files = data.get("recorded_files", [])
            if (not isinstance(source_files, list) or len(source_files) != 1
                    or Path(source_files[0]).resolve() != Path(item["video"]["source"]).resolve()):
                raise ValueError("原始 JSON 未明确对应归档视频来源")
            if analysis_entries is not None:
                verify_analysis(analysis_by_id[recording_id], item, recording_id, raw, video)
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
            observations[recording_id] = copy.deepcopy(data["digital_observation"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
            errors.append(f"{recording_id}: {error}")
    return room_id, records, observations, errors
