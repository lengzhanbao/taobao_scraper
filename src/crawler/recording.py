"""Recording attempts retain every source file, including failed attempts."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from src.utils.safe_io import atomic_json, probe_video
from src.utils.segment_evidence import video_validation, digital_observation


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


def record_segment(*, url, live_id, room_dir, stream_url, segment_index, seg_name,
                   ffmpeg, ffprobe, max_minutes, user_agent, cookie_header,
                   state, lock, page, log, publish_status, run_id=None):
    recording_id = uuid.uuid4().hex
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    attempt_dir = Path(room_dir) / seg_name / ("attempt_" + ts + "_" + recording_id[:8])
    attempt_dir.mkdir(parents=True, exist_ok=False)
    journal_path = attempt_dir / f"data_{ts}_responses.jsonl"
    with lock:
        initial = list(state["collected"])
        journal = journal_path.open("x", encoding="utf-8")
        for response in initial:
            journal.write(json.dumps(response, ensure_ascii=False) + "\n")
        journal.flush()
        state["journal_error"] = False
        state["journal"] = journal
    start_t = time.time()
    process = None
    video = None
    first_data_t = None
    failure = None
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
        for attempt in range(1, 4):
            video = attempt_dir / f"record_{ts}_attempt{attempt}.flv"
            cmd = [str(ffmpeg), "-n", "-reconnect", "1", "-reconnect_streamed", "1",
                   "-reconnect_on_network_error", "1", "-reconnect_delay_max", "10",
                   "-rw_timeout", "10000000", "-headers", headers, "-i", stream_url,
                   "-c", "copy", "-t", str(max_minutes * 60), str(video)]
            start_t = time.time()
            with (attempt_dir / f"ffmpeg_attempt{attempt}.log").open("x", encoding="utf-8") as ferr:
                process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=ferr, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                publish_status("connecting", live_id=live_id, segment_index=segment_index,
                               ffmpeg_pid=process.pid, recording_id=recording_id, record_start_t=start_t,
                               planned_duration_seconds=max_minutes * 60, elapsed_seconds=0,
                               remaining_seconds=max_minutes * 60)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and process.poll() is None:
                    if video.is_file() and video.stat().st_size > 1024:
                        first_data_t = time.time()
                        break
                    time.sleep(1)
                if first_data_t is None:
                    stop_recorder(process)
                    log(f"  接流尝试 {attempt} 未成功，文件保留")
                    continue
                log(f"  {seg_name} 开始录制，时长上限 {max_minutes} 分钟")
                last_size = video.stat().st_size
                last_growth = time.monotonic()
                next_refresh = time.monotonic() + 300
                next_checkpoint = time.monotonic() + 30
                while process.poll() is None:
                    elapsed = time.time() - start_t
                    publish_status("recording", elapsed_seconds=round(elapsed),
                                   remaining_seconds=max(0, round(max_minutes * 60 - elapsed)))
                    if elapsed >= max_minutes * 60:
                        break
                    size = video.stat().st_size if video.exists() else 0
                    if size > last_size:
                        last_size, last_growth = size, time.monotonic()
                    elif time.monotonic() - last_growth > 60:
                        failure = "stream_stalled"
                        log("  视频 60 秒未增长，结束本次录制并保留文件")
                        break
                    with lock:
                        recent = [r for r in state["collected"] if r.get("response_sequence", 0) > last_sequence]
                    for response in recent:
                        last_sequence = max(last_sequence, response.get("response_sequence", 0))
                        product = product_in(response, live_id)
                        if product and (current is None or product["itemId"] != current["itemId"]):
                            if current:
                                timeline.append(dict(current, start_sec=round(current_start, 1), end_sec=round(elapsed, 1)))
                            current, current_start = product, elapsed
                    if time.monotonic() >= next_refresh:
                        try:
                            page.refresh()
                        except Exception:
                            pass
                        next_refresh = time.monotonic() + 300
                    if time.monotonic() >= next_checkpoint:
                        atomic_json(attempt_dir / f"data_{ts}.json", {
                            "run_id": run_id, "recording_id": recording_id, "segment_index": segment_index,
                            "live_url": url, "record_start_t": start_t, "record_end_t": time.time(),
                            "recorded_files": [str(video)], "responses_journal": str(journal_path),
                            "responses": list(state["collected"]), "product_timeline": timeline,
                        })
                        next_checkpoint = time.monotonic() + 30
                    time.sleep(2)
                stop_recorder(process)
                break
    except Exception as error:
        failure = type(error).__name__ + ": " + str(error)
        log(f"  录制异常，源文件保留: {failure}")
    finally:
        if process is not None:
            stop_recorder(process)
        with lock:
            state["journal"] = None
            journal.flush()
            os.fsync(journal.fileno())
            journal.close()
            journal_error = state["journal_error"]
    end_t = time.time()
    if current:
        timeline.append(dict(current, start_sec=round(current_start, 1), end_sec=round(end_t - start_t, 1)))
    try:
        publish_status("validating", elapsed_seconds=round(end_t - start_t), remaining_seconds=0)
        if first_data_t is None or failure is not None or journal_error:
            raise ValueError(failure or "接流失败或响应日志写入失败")
        validation = video_validation(video, {"max_minutes": max_minutes}, ffprobe, probe=probe_video)
        duration = validation["duration_seconds"]
        with journal_path.open(encoding="utf-8") as stream:
            responses = [json.loads(line) for line in stream if line.strip()]
        payload = {
            "run_id": run_id, "recording_id": recording_id, "segment_index": segment_index,
            "live_url": url, "recorded_files": [str(video)],
            "record_start_t": start_t, "record_end_t": end_t, "first_video_data_t": first_data_t,
            "video_duration_seconds": duration, "timing_basis": "wall_clock_from_ffmpeg_launch",
            "max_minutes": max_minutes, "product_timeline": timeline,
            "captured_count": len(responses), "responses": responses,
            "technical_validation": validation,
        }
        payload["digital_observation"] = digital_observation(payload, live_id)
        atomic_json(attempt_dir / f"data_{ts}_final.json", payload)
        publish_status("segment_completed", video_duration_seconds=duration)
        log(f"  ✅ {seg_name} 完成，ffprobe 时长 {duration:.1f} 秒；原始视频保留")
        return True
    except Exception as error:
        try:
            with journal_path.open(encoding="utf-8") as stream:
                failed_responses = [json.loads(line) for line in stream if line.strip()]
        except (OSError, ValueError):
            failed_responses = list(state["collected"])
        atomic_json(attempt_dir / "attempt_status.json", {
            "run_id": run_id, "recording_id": recording_id, "segment_index": segment_index,
            "valid": False, "error": str(error), "record_start_t": start_t, "record_end_t": end_t,
            "digital_observation": digital_observation({"record_start_t": start_t,
                "record_end_t": end_t, "responses": failed_responses}, live_id),
        })
        log(f"  本段未计入有效段，全部文件保留: {error}")
        publish_status("segment_failed", reason=str(error))
        return False
