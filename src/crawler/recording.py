"""Recording attempts retain every source file, including failed attempts."""
import datetime
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from src.utils.api_names import api_name
from src.utils.safe_io import (atomic_json, atomic_json_with_raw_array, atomic_jsonl,
                               probe_video, probe_media)
from src.utils.segment_evidence import (DigitalObservationCollector, digital_observation,
                                        recording_quality, timing_evidence, video_validation)
from src.utils.runtime_options import read_runtime_options
from src.crawler.response_attribution import normalize_attribution_status
from src.parser.product_events import (CURRENT_ROLES, extract_product_events,
                                       product_intervals, summarize_product_events)


_SECRET_QUERY = re.compile(r"(?i)([?&](?:token|access_token|auth[_-]?key|sign|signature|secret|sid|key|session|ticket)=)[^&#\s]+")
_SECRET_HEADER = re.compile(r"(?i)\b(cookie|authorization|proxy-authorization)\s*:\s*[^\r\n]*")


def _stderr_summary(path, limit=8192):
    try:
        with Path(path).open("rb") as stream:
            stream.seek(max(0, Path(path).stat().st_size - limit))
            raw = stream.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""
    raw = _SECRET_HEADER.sub(lambda match: match.group(1) + ": [REDACTED]", raw)
    raw = _SECRET_QUERY.sub(r"\1[REDACTED]", raw)
    return raw[-1200:]


def _save_attempt_diagnostic(attempt_dir, attempt, *, run_id, recording_id, live_id,
                             log_path, returncode, failure_stage, warning=None):
    try:
        atomic_json(Path(attempt_dir) / f"ffmpeg_attempt{attempt}_diagnostic.json", {
            "run_id": run_id, "recording_id": recording_id, "live_id": str(live_id),
            "attempt": attempt, "returncode": returncode, "failure_stage": failure_stage,
            "stderr_log": str(Path(log_path).resolve()),
            "stderr_summary": _stderr_summary(log_path), "warning": warning,
        })
        return True
    except OSError:
        # Diagnostics are best-effort and never decide media validity.
        return False


def _attempt_outcome(returncode, *, failure=None, duration_limit=False):
    stage = failure or ("duration_limit_reached" if duration_limit else
                        "ffmpeg_exit" if returncode not in (None, 0) else "completed")
    warning = ("ffmpeg_nonzero_exit_but_media_will_be_independently_validated"
               if returncode not in (None, 0) else None)
    return stage, warning


def stop_recorder(process):
    if process.poll() is not None:
        return
    try:
        process.communicate(input=b"q\n", timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def product_in(response, live_id):
    if "live.detail.get" not in (response.get("url") or ""):
        return None
    response_ids = response.get("live_ids") or []
    if response_ids and str(live_id) not in [str(value) for value in response_ids]:
        return None
    for product in response.get("products", []):
        metadata_live_id = product.get("live_id") if isinstance(product, dict) else None
        if (isinstance(product, dict) and product.get("itemId")
                and (str(live_id) in [str(value) for value in response_ids]
                     or str(metadata_live_id) == str(live_id))):
            return {"itemId": str(product["itemId"]), "name": str(product.get("name") or ""),
                    "price": str(product.get("price") or "")}
    body = response.get("body")
    if not isinstance(body, str) or str(live_id) not in body:
        return None
    match = re.search(r'"itemId"\s*:\s*"?(\d+)"?', body)
    if not match:
        return None
    name = re.search(r'"itemName"\s*:\s*"([^"]+)"', body)
    price = re.search(r'"itemPrice"\s*:\s*"?([\d.]+)"?', body)
    return {"itemId": match.group(1), "name": name.group(1) if name else "",
            "price": price.group(1) if price else ""}


def copy_scan_journal(scan_journal_path, target):
    """Copy complete, valid scan records into active segment journal."""
    if not scan_journal_path:
        return False
    copied = False
    with Path(scan_journal_path).open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            response = json.loads(line)
            target.write(json.dumps(response, ensure_ascii=False) + "\n")
            copied = True
    return copied


def _timeline_from_responses(responses, live_id, start_t, end_t):
    """Rebuild product intervals from complete ordered journal, not bounded preview."""
    timeline, current, current_start = [], None, 0.0
    for response in sorted(responses, key=lambda item: item.get("response_sequence", 0)):
        product = product_in(response, live_id)
        if not product or (current and product["itemId"] == current["itemId"]):
            continue
        event_t = response.get("received_at", response.get("t", start_t))
        try:
            event_sec = max(0.0, float(event_t) - start_t)
        except (TypeError, ValueError):
            continue
        if current:
            timeline.append(dict(current, start_sec=round(current_start, 1), end_sec=round(event_sec, 1)))
        current, current_start = product, event_sec
    if current:
        timeline.append(dict(current, start_sec=round(current_start, 1),
                             end_sec=round(max(0.0, end_t - start_t), 1)))
    return timeline


def _journal_evidence(path, *, live_id, run_id, recording_id, segment_index, start_t, end_t,
                      media_duration=None):
    """One bounded-memory pass over the journal for every derived fact."""
    collector = DigitalObservationCollector(live_id, start_t, end_t, media_duration)
    events, legacy_timeline = [], []
    status_counts = {"matched": 0, "context_only": 0, "conflict": 0, "unknown": 0}
    api_counts, missing_status = {}, 0
    captured, checksum = 0, hashlib.sha256()
    legacy_current, legacy_start = None, 0.0
    with Path(path).open("rb") as stream:
        for raw_line in stream:
            checksum.update(raw_line)
            if not raw_line.strip():
                continue
            response = json.loads(raw_line.decode("utf-8"))
            captured += 1
            collector.observe(response)
            status = normalize_attribution_status(response.get("attribution_status"))
            if response.get("attribution_status") is None:
                missing_status += 1
            status_counts[status] = status_counts.get(status, 0) + 1
            api = api_name(response.get("url"))
            if api:
                api_counts[api] = api_counts.get(api, 0) + 1
            context = {"run_id": run_id, "recording_id": recording_id,
                       "live_id": live_id, "segment_index": segment_index,
                       "navigation_id": response.get("navigation_id"),
                       "record_start_t": start_t,
                       "observed_live_id": response.get("observed_live_id")}
            events.extend(extract_product_events(response, context))
            # Legacy detail-only timeline remains available for older payloads.
            product = product_in(response, live_id)
            if product and (legacy_current is None
                            or product["itemId"] != legacy_current["itemId"]):
                event_t = response.get("received_at", response.get("t", start_t))
                try:
                    event_sec = max(0.0, float(event_t) - start_t)
                except (TypeError, ValueError):
                    event_sec = None
                if event_sec is not None:
                    if legacy_current:
                        legacy_timeline.append(dict(legacy_current,
                                                    start_sec=round(legacy_start, 1),
                                                    end_sec=round(event_sec, 1)))
                    legacy_current, legacy_start = product, event_sec
    if legacy_current:
        legacy_timeline.append(dict(legacy_current, start_sec=round(legacy_start, 1),
                                    end_sec=round(max(0.0, end_t - start_t), 1)))
    intervals = product_intervals(events)
    current_events = [event for event in events
                      if event.get("attribution_status") == "matched"
                      and event.get("item_role") in CURRENT_ROLES]
    if current_events:
        # Price evidence stays at event level in product_events.jsonl; an
        # interval only proves the observed current-item boundaries.
        timeline = [{"itemId": interval.get("item_id") or "",
                     "name": interval.get("item_name") or "",
                     "start_sec": interval["start_seconds"],
                     "end_sec": interval["end_seconds"],
                     "item_id": interval.get("item_id"),
                     "item_name": interval.get("item_name"),
                     "sku_id": interval.get("sku_id"),
                     "basis": "product_event_interval"}
                    for interval in intervals]
    else:
        timeline = legacy_timeline
    return {
        "captured_count": captured,
        "journal_sha256": checksum.hexdigest(),
        "digital_observation": collector.result(),
        "product_events": events,
        "product_intervals": intervals,
        "product_timeline": timeline,
        "product_event_summary": summarize_product_events(events, intervals),
        "response_attribution_summary": {
            "schema_version": 1, "total": captured, "by_status": status_counts,
            "by_api": api_counts, "missing_status_count": missing_status,
            "high_confidence": status_counts.get("matched", 0),
            "policy": "only matched rows enter strict room statistics; raw rows stay in the journal",
        },
    }


def _raw_json_lines(path):
    """Yield validated JSON lines without retaining the journal in memory."""
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                json.loads(line)
                yield line


def _write_research_artifacts(attempt_dir, *, quality, timing, evidence):
    """Write small, citable artifacts next to the authoritative response journal."""
    evidence = evidence or {"product_events": [], "product_intervals": []}
    atomic_jsonl(Path(attempt_dir) / "product_events.jsonl", evidence.get("product_events", []))
    atomic_jsonl(Path(attempt_dir) / "product_intervals.jsonl", evidence.get("product_intervals", []))
    atomic_json(Path(attempt_dir) / "recording_quality.json", quality)
    atomic_json(Path(attempt_dir) / "timing_evidence.json", timing)
    return {"product_events": str(Path(attempt_dir) / "product_events.jsonl"),
            "product_intervals": str(Path(attempt_dir) / "product_intervals.jsonl"),
            "recording_quality": str(Path(attempt_dir) / "recording_quality.json"),
            "timing_evidence": str(Path(attempt_dir) / "timing_evidence.json")}


def record_segment(*, url, live_id, room_dir, stream_url, segment_index, seg_name,
                   ffmpeg, ffprobe, max_minutes, user_agent, cookie_header,
                   state, lock, page, log, publish_status, run_id=None):
    recording_id = uuid.uuid4().hex
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    attempt_dir = Path(room_dir) / seg_name / ("attempt_" + ts + "_" + recording_id[:8])
    attempt_dir.mkdir(parents=True, exist_ok=False)
    journal_path = attempt_dir / f"data_{ts}_responses.jsonl"
    with lock:
        sink_lock = state.get("sink_lock")
        with (sink_lock if sink_lock else nullcontext()):
            initial = list(state["collected"])
            state.setdefault("journal_error", False)
            scan_status = state.get("journal_status")
            scan_error = bool(scan_status and scan_status.get("error"))
            journal = None
            try:
                journal = journal_path.open("x", encoding="utf-8")
                scan_journal_path = state.get("scan_journal_path")
                if scan_journal_path:
                    try:
                        copy_scan_journal(scan_journal_path, journal)
                    except (OSError, ValueError, TypeError):
                        state["journal_error"] = True
                        if scan_status is not None:
                            scan_status["error"] = True
                    state["scan_journal_path"] = None
                else:
                    for response in initial:
                        journal.write(json.dumps(response, ensure_ascii=False) + "\n")
                journal.flush()
            except Exception:
                state["journal_error"] = True
                if scan_status is not None:
                    scan_status["error"] = True
                if journal is not None:
                    try:
                        journal.close()
                    except Exception:
                        pass
                raise
            if state["journal_error"] or scan_error:
                state["journal"] = None
                journal.close()
                state["last_record_outcome"] = {
                    "ok": False, "recording_id": recording_id,
                    "failure_reason": "journal_write_error",
                    "failure_stage": "journal_setup", "duration_seconds": None,
                    "target_duration_met": None, "quality_state": "technically_invalid",
                    "has_audio": None, "attempt_count": 0, "product_event_count": 0,
                }
                log("  扫描响应日志写入失败，本段不启动录制；原始日志保留")
                publish_status("segment_failed", reason="扫描响应日志写入失败")
                return False
            state["journal"] = journal
            state["journal_status"] = {"error": False, "navigation_id": state.get("navigation_id")}
    start_t = time.time()
    process = None
    ferr = None
    video = None
    first_data_t = None
    failure = None
    ffmpeg_attempts = []
    record_events = []
    timeline = []
    last_sequence = 0
    current = None
    current_start = 0.0
    for response in initial:
        product = product_in(response, live_id)
        if product:
            current = product
    headers = f"Referer: {url}\r\nUser-Agent: {user_agent}\r\n"
    if cookie_header:
        headers += f"Cookie: {cookie_header}\r\n"
    try:
        options = read_runtime_options()
        for attempt in range(1, options["max_record_attempts"] + 1):
            video = attempt_dir / f"record_{ts}_attempt{attempt}.flv"
            stderr_path = attempt_dir / f"ffmpeg_attempt{attempt}.log"
            cmd = [str(ffmpeg), "-n", "-reconnect", "1", "-reconnect_streamed", "1",
                   "-reconnect_on_network_error", "1", "-reconnect_delay_max", "10",
                   "-rw_timeout", "10000000", "-headers", headers, "-i", stream_url,
                   "-c", "copy", "-t", str(max_minutes * 60), str(video)]
            start_t = time.time()
            record_events.append({"kind": "ffmpeg_attempt_started", "attempt": attempt,
                                  "wall_time": start_t})
            _save_attempt_diagnostic(attempt_dir, attempt, run_id=run_id, recording_id=recording_id,
                                     live_id=live_id, log_path=stderr_path, returncode=None,
                                     failure_stage="running")
            failure_stage = "connect_timeout"
            ferr = stderr_path.open("x", encoding="utf-8")
            try:
                process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=ferr, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                ferr.close()
                failure_stage = "spawn_error"
                _save_attempt_diagnostic(attempt_dir, attempt, run_id=run_id, recording_id=recording_id,
                                         live_id=live_id, log_path=stderr_path, returncode=None,
                                         failure_stage=failure_stage)
                ffmpeg_attempts.append({"attempt": attempt, "returncode": None,
                                        "failure_stage": failure_stage,
                                        "stderr_log": str(stderr_path.resolve())})
                record_events.append({"kind": "ffmpeg_attempt_finished", "attempt": attempt,
                                      "wall_time": time.time(), "returncode": None,
                                      "failure_stage": failure_stage})
                raise
            failure_stage = "connect_timeout"
            publish_status("connecting", live_id=live_id, segment_index=segment_index,
                           ffmpeg_pid=process.pid, recording_id=recording_id, record_start_t=start_t,
                           planned_duration_seconds=max_minutes * 60, elapsed_seconds=0,
                           remaining_seconds=max_minutes * 60)
            deadline = time.monotonic() + options["connect_timeout_seconds"]
            while time.monotonic() < deadline and process.poll() is None:
                if video.is_file() and video.stat().st_size > 1024:
                    first_data_t = time.time()
                    record_events.append({"kind": "first_video_data", "attempt": attempt,
                                          "wall_time": first_data_t,
                                          "record_relative_seconds": round(first_data_t - start_t, 3)})
                    break
                time.sleep(1)
            if first_data_t is None:
                stop_recorder(process)
                ferr.close()
                returncode = process.poll()
                _save_attempt_diagnostic(attempt_dir, attempt, run_id=run_id,
                                         recording_id=recording_id, live_id=live_id,
                                         log_path=stderr_path, returncode=returncode,
                                         failure_stage="connect_timeout")
                ffmpeg_attempts.append({"attempt": attempt, "returncode": returncode,
                                        "failure_stage": "connect_timeout",
                                        "stderr_log": str(stderr_path.resolve())})
                record_events.append({"kind": "ffmpeg_attempt_finished", "attempt": attempt,
                                      "wall_time": time.time(), "returncode": returncode,
                                      "failure_stage": "connect_timeout"})
                log(f"  接流尝试 {attempt} 未成功，文件保留")
                continue
            log(f"  {seg_name} 开始录制，时长上限 {max_minutes} 分钟")
            last_size = video.stat().st_size
            last_growth = time.monotonic()
            next_refresh = time.monotonic() + options["page_refresh_seconds"]
            next_checkpoint = time.monotonic() + options["checkpoint_seconds"]
            while process.poll() is None:
                elapsed = time.time() - start_t
                publish_status("recording", elapsed_seconds=round(elapsed),
                               remaining_seconds=max(0, round(max_minutes * 60 - elapsed)))
                if elapsed >= max_minutes * 60:
                    failure_stage = "duration_limit_reached"
                    break
                size = video.stat().st_size if video.exists() else 0
                if size > last_size:
                    last_size, last_growth = size, time.monotonic()
                elif time.monotonic() - last_growth > options["stall_timeout_seconds"]:
                    failure = "stream_stalled"
                    log(f"  视频 {options['stall_timeout_seconds']} 秒未增长，结束本次录制并保留文件")
                    break
                with lock:
                    recent = [r for r in state["collected"] if r.get("response_sequence", 0) > last_sequence]
                for response in recent:
                    last_sequence = max(last_sequence, response.get("response_sequence", 0))
                    product = product_in(response, live_id)
                    if product and (current is None or product["itemId"] != current["itemId"]):
                        if current:
                            event_sec = max(0.0, float(response.get("t", time.time())) - start_t)
                            timeline.append(dict(current, start_sec=round(current_start, 1), end_sec=round(event_sec, 1)))
                        current = product
                        current_start = max(0.0, float(response.get("t", time.time())) - start_t)
                if time.monotonic() >= next_refresh:
                    requested_t = time.time()
                    try:
                        page.refresh()
                        record_events.append({"kind": "page_refresh", "wall_time": requested_t,
                                              "record_relative_seconds": round(requested_t - start_t, 3)})
                    except Exception as error:
                        record_events.append({"kind": "page_refresh_failed", "wall_time": requested_t,
                                              "record_relative_seconds": round(requested_t - start_t, 3),
                                              "error": type(error).__name__})
                    next_refresh = time.monotonic() + options["page_refresh_seconds"]
                if time.monotonic() >= next_checkpoint:
                    with lock:
                        collected_snapshot = list(state["collected"])
                    atomic_json(attempt_dir / f"data_{ts}.json", {
                        "run_id": run_id, "recording_id": recording_id, "segment_index": segment_index,
                        "live_url": url, "record_start_t": start_t, "record_end_t": time.time(),
                        "recorded_files": [str(video)], "responses_journal": str(journal_path),
                        "responses": collected_snapshot, "product_timeline": timeline,
                    })
                    next_checkpoint = time.monotonic() + options["checkpoint_seconds"]
                time.sleep(2)  # status_refresh_seconds is UI-only
            stop_recorder(process)
            ferr.close()
            returncode = process.poll()
            stage, warning = _attempt_outcome(returncode, failure=failure,
                                              duration_limit=failure_stage == "duration_limit_reached")
            _save_attempt_diagnostic(attempt_dir, attempt, run_id=run_id,
                                     recording_id=recording_id, live_id=live_id,
                                     log_path=stderr_path, returncode=returncode,
                                     failure_stage=stage, warning=warning)
            ffmpeg_attempts.append({"attempt": attempt, "returncode": returncode,
                                    "failure_stage": stage, "warning": warning,
                                    "stderr_log": str(stderr_path.resolve())})
            record_events.append({"kind": "ffmpeg_attempt_finished", "attempt": attempt,
                                  "wall_time": time.time(), "returncode": returncode,
                                  "failure_stage": stage, "warning": warning})
            break
    except Exception as error:
        failure = type(error).__name__ + ": " + str(error)
        log(f"  录制异常，源文件保留: {failure}")
    finally:
        if process is not None:
            stop_recorder(process)
        if ferr is not None and not ferr.closed:
            ferr.close()
        with lock:
            sink_lock = state.get("sink_lock")
            if sink_lock:
                sink_lock.acquire()
            try:
                state["journal"] = None
                journal.flush()
                os.fsync(journal.fileno())
                journal.close()
                journal_status = state.get("journal_status")
                journal_error = bool(state["journal_error"] or
                                     (journal_status and journal_status.get("error")))
            except Exception:
                state["journal_error"] = True
                journal_error = True
                try:
                    journal.close()
                except Exception:
                    pass
            finally:
                if sink_lock:
                    sink_lock.release()
    end_t = time.time()
    validation = None
    duration = None
    evidence = None
    quality = None
    timing = None
    artifacts = {}
    final_path = attempt_dir / f"data_{ts}_final.json"
    base = {"run_id": run_id, "recording_id": recording_id, "live_id": str(live_id),
            "segment_index": segment_index, "live_url": url,
            "record_start_t": start_t, "record_end_t": end_t,
            "first_video_data_t": first_data_t, "max_minutes": max_minutes,
            "ffmpeg_attempts": ffmpeg_attempts, "record_events": record_events}
    try:
        publish_status("validating", elapsed_seconds=round(end_t - start_t), remaining_seconds=0)
        if first_data_t is None or failure is not None or journal_error:
            raise ValueError(failure or "接流失败或响应日志写入失败")
        validation = video_validation(video, {"max_minutes": max_minutes}, ffprobe,
                                      probe=probe_video, media_probe=probe_media)
        duration = validation["duration_seconds"]
        base.update(video_duration_seconds=duration, technical_validation=validation)
        evidence = _journal_evidence(journal_path, live_id=live_id, run_id=run_id,
                                     recording_id=recording_id, segment_index=segment_index,
                                     start_t=start_t, end_t=end_t, media_duration=duration)
        quality = recording_quality(base)
        timing = timing_evidence(base, events=record_events)
        artifacts = _write_research_artifacts(attempt_dir, quality=quality, timing=timing,
                                              evidence=evidence)
        payload = {
            **base,
            "schema_version": 2,
            "recorded_files": [str(video)],
            "timing_basis": "wall_clock_from_ffmpeg_launch",
            "product_timeline": evidence["product_timeline"],
            "captured_count": evidence["captured_count"],
            "responses_storage": "jsonl",
            "responses_journal": str(journal_path),
            "responses_journal_relative": journal_path.name,
            "journal_sha256": evidence["journal_sha256"],
            "responses_embedded": True,
            "digital_observation": evidence["digital_observation"],
            "response_attribution_summary": evidence["response_attribution_summary"],
            "product_event_summary": evidence["product_event_summary"],
            "product_events_journal": artifacts["product_events"],
            "product_intervals_journal": artifacts["product_intervals"],
            "recording_quality": quality,
            "timing_evidence": timing,
        }
        atomic_json_with_raw_array(final_path, payload, "responses",
                                   _raw_json_lines(journal_path))
        publish_status("segment_completed", video_duration_seconds=duration,
                       target_duration_met=quality["target_duration_met"],
                       has_audio=quality["has_audio"], quality_state=quality["quality_state"])
        state["last_record_outcome"] = {
            "ok": True, "recording_id": recording_id, "failure_reason": None,
            "failure_stage": None, "duration_seconds": duration,
            "target_duration_met": quality["target_duration_met"],
            "quality_state": quality["quality_state"], "has_audio": quality["has_audio"],
            "attempt_count": len(ffmpeg_attempts),
            "product_event_count": evidence["product_event_summary"]["event_count"],
        }
        log(f"  ✅ {seg_name} 完成，ffprobe 时长 {duration:.1f} 秒；原始视频保留")
        return True
    except Exception as error:
        failure_detail = str(error)
        if failure:
            failure_reason = str(failure)
        elif "实际视频时长" in failure_detail:
            failure_reason = "technical_validation_failed"
        elif "接流失败或响应日志写入失败" in failure_detail:
            failure_reason = "connect_or_journal_failure"
        else:
            failure_reason = type(error).__name__
        if evidence is None and journal_path.is_file():
            try:
                evidence = _journal_evidence(journal_path, live_id=live_id, run_id=run_id,
                                             recording_id=recording_id,
                                             segment_index=segment_index,
                                             start_t=start_t, end_t=end_t,
                                             media_duration=duration)
            except (OSError, ValueError, TypeError):
                evidence = None
        failed_base = dict(base, video_duration_seconds=duration,
                           technical_validation=validation)
        quality = recording_quality(failed_base)
        media = validation.get("media") if isinstance(validation, dict) else None
        timing = timing_evidence(failed_base, media=media, events=record_events)
        try:
            artifacts = _write_research_artifacts(attempt_dir, quality=quality, timing=timing,
                                                  evidence=evidence)
            artifact_error = None
        except OSError as artifact_exception:
            artifact_error = type(artifact_exception).__name__
        status_payload = {
            **failed_base,
            "valid": False,
            "error": failure_detail,
            "failure_reason": failure_reason,
            "recording_quality": quality,
            "timing_evidence": timing,
            "captured_count": evidence["captured_count"] if evidence else None,
            "response_attribution_summary": (evidence["response_attribution_summary"]
                                             if evidence else None),
            "product_event_summary": (evidence["product_event_summary"]
                                      if evidence else None),
            "product_events_journal": artifacts.get("product_events"),
            "product_intervals_journal": artifacts.get("product_intervals"),
            "artifact_error": artifact_error,
        }
        try:
            atomic_json(attempt_dir / "attempt_status.json", status_payload)
        except OSError as write_error:
            log(f"  attempt_status 写入失败: {type(write_error).__name__}")
        state["last_record_outcome"] = {
            "ok": False, "recording_id": recording_id,
            "failure_reason": failure_reason, "failure_stage": failure_reason,
            "duration_seconds": duration,
            "target_duration_met": quality["target_duration_met"],
            "quality_state": quality["quality_state"], "has_audio": quality["has_audio"],
            "attempt_count": len(ffmpeg_attempts),
            "product_event_count": (evidence["product_event_summary"]["event_count"]
                                    if evidence else 0),
            "error": failure_detail[:500],
        }
        log(f"  本段未计入有效段，全部文件保留: {error}")
        publish_status("segment_failed", reason=str(error))
        return False
