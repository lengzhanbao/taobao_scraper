# -*- coding: utf-8 -*-
"""
淘宝直播 Edge — 参数化多实例版
用法: python taobao_run_edge.py urls_1.txt 9223
- 每个实例读自己的 URL 文件、自己的端口、自己的输出目录
- 随机抽 → 录20分 → 冷却2h → 每房间3轮
"""
import os, sys, time, json, re, subprocess, threading, datetime, random

if len(sys.argv) < 3:
    print("用法: python taobao_run_edge.py <urls文件名> <端口> [启动延迟秒数]")
    print("例如: python taobao_run_edge.py urls_1.txt 9223 30")
    sys.exit(1)

# 控制台编码兜底：输出重定向到文件时 Python 会退回 GBK，emoji 日志会崩（必须在任何 print 之前）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DELAY = int(sys.argv[3]) if len(sys.argv) > 3 else 0

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.utils.config import (
    STUDY_ROOT, FFMPEG, FFPROBE, EDGE_PATH,
    MAX_MIN, MAX_ROUND, COOLDOWN_SEC, PRODUCT_MIN_SEC,
    MAX_COLLECTED, SEG_NAMES, USER_AGENT, MAX_ROUND_OVERRIDE, BATCH_ROOMS,
)
from pathlib import Path
from src.utils.safe_io import atomic_json, probe_video, PortLock, digest
from src.control.event_store import EventWriter
from src.utils.segment_evidence import validation_matches
from src.utils.runtime_options import read_runtime_options
from src.crawler.response_attribution import attribution_status, detail_live_urls
from src.crawler.retry_state import RetryState, RetryStateError
from src.crawler.recovery import reconcile_room
from src.utils.digital_flags import (
    find_values_by_key, parse_json_body,
    summarize_digital_flags, title_keywords_in_values,
)
from src.crawler.runtime_control import pause_requested, wait_while_paused, apply_control_command
URLS_FILE = os.environ.get("LIVE_URLS_FILE", os.path.join(STUDY_ROOT, "_config", sys.argv[1]))
PORT = int(sys.argv[2])
COOKIE_JSON = os.environ.get(
    "LIVE_COOKIE_JSON", os.path.join(STUDY_ROOT, "_config", "taobao_cookies.json")
)
OUTDIR = os.path.join(STUDY_ROOT, "_staging", f"browser_{PORT}")
OUTDIR = os.environ.get("LIVE_STAGING_ROOT", OUTDIR)
COOKIE_TXT = os.path.join(STUDY_ROOT, "_config", f"taobao_cookies_{PORT}.txt")
UA = USER_AGENT

os.makedirs(OUTDIR, exist_ok=True)
port_lock = PortLock(os.path.join(STUDY_ROOT, "_control", f"port_{PORT}.lock"))
STATUS_FILE = os.environ.get("LIVE_STATUS_FILE")
STOP_FILE = os.environ.get("LIVE_STOP_FILE")
PAUSE_FILE = os.environ.get("LIVE_PAUSE_FILE")
RUN_ID = os.environ.get("LIVE_RUN_ID", "manual")
COMMAND_FILE = os.environ.get("LIVE_COMMAND_FILE")
COMMAND_LOCK = os.environ.get("LIVE_COMMAND_LOCK")
RECEIPT_DIR = os.environ.get("LIVE_OPERATION_RECEIPT_DIR")
EVENT_DIR = os.environ.get("LIVE_EVENT_DIR")
INSTANCE_ID = PORT - 9222
try:
    EVENTS = EventWriter(EVENT_DIR, RUN_ID, f"worker_{INSTANCE_ID}", INSTANCE_ID) if EVENT_DIR else None
except OSError:
    EVENTS = None
telemetry_gap = bool(EVENT_DIR) and (EVENTS is None or EVENTS.telemetry_gap)
RUNTIME = read_runtime_options()
status_data = {"pid": os.getpid(), "port": PORT, "run_id": RUN_ID,
               "latest_response_t": None, "last_response_sequence": 0,
               "unattributed_response_count": 0, "journal_error": False,
               "last_persisted_sequence": 0, "last_persisted_t": None}
processed_state_path = (Path(STATUS_FILE).parent / f"worker_state_{INSTANCE_ID}.json"
                        if STATUS_FILE else None)
try:
    processed_seq = int(json.loads(processed_state_path.read_text(encoding="utf-8")).get("last_command_seq", 0)) if processed_state_path and processed_state_path.exists() else 0
except (OSError, ValueError, TypeError, AttributeError):
    processed_seq = 0
control_local_lock = threading.Lock()


def _emit_worker_event(status, *, operation_id=None, details=None):
    global telemetry_gap
    if EVENTS:
        EVENTS.emit(status=status, source="worker", operation_id=operation_id,
                    live_id=status_data.get("live_id"),
                    segment_index=status_data.get("segment_index"),
                    recording_id=status_data.get("recording_id"), details=details)
        telemetry_gap = EVENTS.telemetry_gap


def _write_operation_receipt(command, status, details=None):
    global telemetry_gap
    if not RECEIPT_DIR:
        telemetry_gap = True
        return False
    operation_id = command.get("operation_id")
    try:
        path = Path(RECEIPT_DIR) / f"{operation_id}.json"
        atomic_json(path, {"run_id": RUN_ID, "instance_id": INSTANCE_ID,
                           "operation_id": operation_id, "command_seq": command.get("command_seq"),
                           "operation": command.get("operation"), "status": status,
                           "ack_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                           "details": details or {}})
        _emit_worker_event(status=status, operation_id=operation_id, details=details)
        return True
    except OSError:
        telemetry_gap = True
        print("操作确认回执写入失败，事件监控存在缺口", flush=True)
        return False


def _load_json_file(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def process_pending_command():
    """Apply only newest pause/resume at a safe checkpoint; stop remains separate."""
    global processed_seq, telemetry_gap
    if not COMMAND_FILE or not COMMAND_LOCK or not Path(COMMAND_FILE).is_file():
        return
    with control_local_lock:
        try:
            if not RECEIPT_DIR or not processed_state_path or not PAUSE_FILE:
                telemetry_gap = True
                return
            processed_seq, command = apply_control_command(
                COMMAND_FILE, COMMAND_LOCK, STOP_FILE, PAUSE_FILE, RECEIPT_DIR,
                processed_state_path, run_id=RUN_ID, instance_id=INSTANCE_ID,
                processed_seq=processed_seq, acknowledge=_write_operation_receipt)
            if command:
                receipt = _load_json_file(Path(RECEIPT_DIR) / f"{command.get('operation_id')}.json")
                publish_status(status_data.get("phase", "starting"),
                               last_operation_id=command.get("operation_id"),
                               last_operation_status=receipt.get("status", "pending"),
                               last_command_seq=command.get("command_seq"))
        except (OSError, TimeoutError, ValueError) as error:
            telemetry_gap = True
            print(f"控制命令读取/锁定失败，事件监控存在缺口: {error}", flush=True)


def publish_status(phase, **fields):
    status_data.update(fields)
    status_data.update(phase=phase, updated_t=time.time())
    previous_phase = status_data.get("_published_phase")
    if previous_phase != phase:
        _emit_worker_event(phase, details={key: status_data.get(key) for key in
                          ("reason", "elapsed_seconds", "remaining_seconds", "video_duration_seconds")
                          if status_data.get(key) is not None})
        status_data["_published_phase"] = phase
    status_data["telemetry_gap"] = bool(telemetry_gap or (EVENTS and EVENTS.telemetry_gap))
    if STATUS_FILE:
        try:
            atomic_json(STATUS_FILE, {key: value for key, value in status_data.items()
                                      if not key.startswith("_")})
        except OSError as error:
            print(f"状态写入失败: {error}", flush=True)


retry_state_path = Path(STUDY_ROOT) / "_control" / f"retry_state_instance_{INSTANCE_ID}.json"
retry_state_error = None
try:
    retry_state = RetryState(retry_state_path, RUN_ID,
                             cooldown_seconds=RUNTIME["failure_cooldown_minutes"] * 60,
                             max_failures=RUNTIME["max_room_failures"])
except RetryStateError as error:
    retry_state = None
    retry_state_error = error


def stop_requested():
    if not STOP_FILE or not os.path.isfile(STOP_FILE):
        return False
    command = _load_json_file(STOP_FILE)
    if (command.get("run_id") == RUN_ID and command.get("instance_id") == INSTANCE_ID
            and command.get("operation") == "stop" and type(command.get("command_seq")) is int):
        receipt = _load_json_file(Path(RECEIPT_DIR) / f"{command.get('operation_id')}.json") if RECEIPT_DIR else {}
        receipt_matches = (receipt.get("run_id") == RUN_ID
                           and receipt.get("instance_id") == INSTANCE_ID
                           and receipt.get("operation_id") == command.get("operation_id")
                           and receipt.get("command_seq") == command.get("command_seq"))
        if not receipt_matches or receipt.get("status") != "applied":
            _write_operation_receipt(command, "applied", {"message": "worker 已收到停止意图；完成当前段后停止"})
            status_data["last_operation_id"] = command.get("operation_id")
            status_data["last_operation_status"] = "applied"
            status_data["last_command_seq"] = command.get("command_seq")
            status_data["updated_t"] = time.time()
            status_data["telemetry_gap"] = bool(telemetry_gap or (EVENTS and EVENTS.telemetry_gap))
            if STATUS_FILE:
                try:
                    atomic_json(STATUS_FILE, {key: value for key, value in status_data.items()
                                              if not key.startswith("_")})
                except OSError as error:
                    print(f"状态写入失败: {error}", flush=True)
    return True


def interruptible_wait(seconds):
    deadline = time.monotonic() + max(0, seconds)
    while time.monotonic() < deadline:
        process_pending_command()
        if stop_requested() or pause_requested(PAUSE_FILE):
            break
        time.sleep(min(1, max(0, deadline - time.monotonic())))

# 启动时只报告未完成目录，保留所有原文件。
def clean_staging():
    """Report interrupted attempts; preserve every original path and file."""
    incomplete = 0
    for root, dirs, files in os.walk(OUTDIR):
        if any(f.endswith(".flv") for f in files) and not any(f.endswith("_final.json") for f in files):
            incomplete += 1
    if incomplete:
        log(f"发现 {incomplete} 个未完成录制目录，全部保留，不自动清理")

state = {"collected": [], "stream_url": {"url": None}, "sequence": 0, "journal": None,
         "live_id": None, "navigation_id": None, "scan_journal_path": None,
         "journal_status": {"error": False, "navigation_id": None}}
capture_lock = threading.Lock()
sink_lock = threading.Lock()
state["sink_lock"] = sink_lock
BODY_PREVIEW_CHARS = 800000


# 控制台编码兜底：输出重定向到文件时 Python 会退回 GBK，emoji 日志会崩
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def log(m):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}]| {m}", flush=True)

def get_cookie_header():
    try:
        tb = [c for c in page.cookies() if "taobao.com" in (c.get("domain") or "")]
        return "; ".join(f"{c['name']}={c['value']}" for c in tb)
    except:
        return ""

def wait_for_human_captcha(page, timeout=180):
    """Wait for user to finish captcha; stop request can interrupt wait."""
    try:
        if not page.ele('#nc_1_wrapper', timeout=1):
            return True
        publish_status("captcha_required", reason="请在 Edge 页面手动完成验证码")
        log("  页面需要人工完成验证码；等待最多 180 秒")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            process_pending_command()
            if stop_requested():
                return False
            if not page.ele('#nc_1_wrapper', timeout=1):
                publish_status("scanning", reason="")
                log("  人工验证码已完成，恢复扫描")
                return True
            time.sleep(1)
        publish_status("waiting", reason="人工验证码等待超时")
        log("  人工验证码等待超时")
        return False
    except Exception:
        return False

REAL_LOGIN = {"unb", "sgcookie", "thb"}
def logged_in():
    try: return bool(REAL_LOGIN & {c.get("name") for c in page.cookies()})
    except: return False


def login_expired():
    """扫描最近捕获的响应，判断登录态是否失效（SESSION_EXPIRED 等特征）"""
    try:
        with capture_lock:
            responses = list(state["collected"][-80:])
        for c in responses:
            b = c.get("body") or ""
            if isinstance(b, str) and any(k in b for k in ("SESSION_EXPIRED", "登录已失效", "needLogin", "NOT_LOGIN")):
                return True
    except Exception:
        pass
    return False


def refresh_login():
    """登录失效时重新注入已保存 Cookie 并刷新页面；仅能恢复部分失效，完全过期需手动登录"""
    try:
        if not os.path.exists(COOKIE_JSON):
            log("  ❌ 无保存的 Cookie 文件，请手动登录")
            return False
        saved = json.load(open(COOKIE_JSON, encoding="utf-8"))
        page.set.cookies(saved)
        page.refresh()
        time.sleep(3)
        if logged_in():
            log("  ✅ Cookie 重新注入成功")
            return True
        log("  ❌ Cookie 重新注入后仍未登录，请手动登录")
    except Exception as e:
        log(f"  refresh_login 异常: {e}")
    return False

def listen_loop():
    while True:
        try:
            resp = page.listen.wait(timeout=RUNTIME["listen_timeout_seconds"])
        except:
            time.sleep(0.3)
            continue
        if resp is None or resp is False:
            continue
        received_at = time.time()
        url = (resp.url or "").lower()
        if (".ts" in url) or (".m3u8" in url) or not any(
                k in url for k in ["mtop", "comment", "item", "viewer", "like", "fans", "gmv", "interact", "detail"]):
            continue
        if url.endswith(".js") or "data:" in url or "woff" in url or "mtop.js" in url:
            continue
        body = None
        parsed = None
        try:
            body = resp.response.body
            parsed = parse_json_body(body)
        except Exception:
            pass
        response_ids = [str(v) for v in find_values_by_key(parsed, "liveId") if v is not None]
        try:
            if isinstance(body, dict):
                full_body = json.dumps(body, ensure_ascii=False)
            elif isinstance(body, str):
                full_body = body
            else:
                full_body = str(body)
            snippet = full_body[:BODY_PREVIEW_CHARS]
        except Exception:
            full_body = snippet = None
        with capture_lock:
            if state.get("capture_closed"):
                continue
            current_live_id = state.get("live_id")
            navigation_id = state.get("navigation_id")
            status = attribution_status(response_ids, current_live_id, navigation_id)
            state["sequence"] += 1
            preview_truncated = bool(full_body is not None and len(full_body) > BODY_PREVIEW_CHARS)
            response = {"t": round(received_at, 1), "received_at": received_at, "url": url, "body": snippet,
                        "response_sequence": state["sequence"], "body_truncated": preview_truncated,
                        "body_original_chars": len(full_body) if full_body is not None else None,
                        "live_ids": response_ids,
                        "navigation_id": navigation_id,
                        "observed_live_id": response_ids[0] if len(set(response_ids)) == 1 else None,
                        "attribution_status": status,
                        "attribution_basis": ("response_live_id_match" if status == "matched"
                                              else "receive_navigation_context_only" if navigation_id
                                              else "no_live_id_evidence"),
                        "digital_flags": find_values_by_key(parsed, "isDigitalAnchorLive"),
                        "titles": (find_values_by_key(parsed, "liveTitle") or find_values_by_key(parsed, "title")),
                        "products": []}
            if ("live.detail.get" in url and status == "matched" and isinstance(full_body, str)):
                item = re.search(r'"itemId"\s*:\s*"?(\d+)"?', full_body)
                if item:
                    name = re.search(r'"itemName"\s*:\s*"([^"]+)"', full_body)
                    price = re.search(r'"itemPrice"\s*:\s*"?([\d.]+)"?', full_body)
                    response["products"] = [{"itemId": item.group(1),
                                             "name": name.group(1) if name else "",
                                             "price": price.group(1) if price else ""}]
            state["collected"].append(response)
            complete_response = {**response, "body": full_body, "body_truncated": False,
                                 "preview_truncated": preview_truncated}
            spool_path = state.get("scan_journal_path")
            sink_status = state.get("journal_status")
            status_data["latest_response_t"] = response["t"]
            status_data["last_response_sequence"] = response["response_sequence"]
            if response["attribution_status"] != "matched":
                status_data["unattributed_response_count"] = int(
                    status_data.get("unattributed_response_count", 0)) + 1
            if len(state["collected"]) > RUNTIME["max_collected"]:
                del state["collected"][:len(state["collected"]) - RUNTIME["max_collected"]]
            # Keep ambiguous and late responses in the journal; only exact object-paired URL can start stream.
            if "live.detail.get" in url and status == "matched":
                pairs = detail_live_urls(parsed)
                matching_urls = {item_url for item_id, item_url in pairs
                                 if item_id == str(current_live_id)}
                if len(matching_urls) == 1:
                    state["stream_url"].update(url=next(iter(matching_urls)), capture_time=received_at)
            journal_ref = state.get("journal")
            sink_lock.acquire()
        try:
            encoded = json.dumps(complete_response, ensure_ascii=False) + "\n"
            persisted = False
            if spool_path:
                with open(spool_path, "a", encoding="utf-8") as spool:
                    spool.write(encoded)
                    spool.flush()
                persisted = True
            if journal_ref:
                journal_ref.write(encoded)
                journal_ref.flush()
                persisted = True
            if persisted:
                status_data["last_persisted_sequence"] = response["response_sequence"]
                status_data["last_persisted_t"] = received_at
        except Exception:
            if sink_status is not None:
                sink_status["error"] = True
            status_data["journal_error"] = True
        finally:
            sink_lock.release()
def read_urls():
    """读 URL 文件，返回 [(url, lid, 已录次数, 目标次数)]"""
    if not os.path.exists(URLS_FILE):
        return []
    out = []
    for line in open(URLS_FILE, encoding="utf-8"):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        m = re.search(r"liveId=(\d+)", raw)
        if not m:
            continue
        lid = m.group(1)
        clean = f"https://tbzb.taobao.com/live?liveId={lid}"
        m2 = re.search(r"已录制(\d+)/(\d+)", raw)
        if m2:
            count = int(m2.group(1))
            total = MAX_ROUND if MAX_ROUND_OVERRIDE else int(m2.group(2))
        else:
            count = 0
            total = MAX_ROUND
        out.append((clean, lid, count, total))
    return out


def _finalize_pending_rooms(room_ids, finalize, stop_check):
    """Try unique rooms once; preserve failed and not-yet-started IDs."""
    unique_ids = list(dict.fromkeys(room_ids))
    pending = []
    for index, live_id in enumerate(unique_ids):
        if stop_check():
            pending.extend(unique_ids[index:])
            break
        if not finalize(live_id):
            pending.append(live_id)
    return pending


def _wait_for_room_switch(live_id, previous_live_id, gap_seconds, wait_fn):
    """Wait after a different room, then return room ID to remember before scanning."""
    if previous_live_id is not None and str(previous_live_id) != str(live_id) and gap_seconds:
        wait_fn(gap_seconds)
    return str(live_id)


def scan_room(url, live_id):
    nav_t = time.time()
    navigation_id = f"{RUN_ID}:{live_id}:{time.time_ns()}"
    scan_journal = Path(OUTDIR) / f"scan_room_{live_id}_{time.time_ns()}.jsonl"
    with capture_lock:
        sink_lock.acquire()
        state["live_id"] = str(live_id)
        state["navigation_id"] = navigation_id
        state["collected"] = []
        state["stream_url"] = {"url": None}
        state["scan_journal_path"] = str(scan_journal)
        state["journal_error"] = False
        state["journal_status"] = {"error": False, "navigation_id": navigation_id}
        status_data["journal_error"] = False
        sink_lock.release()
    try:
        page.get(url, timeout=RUNTIME["scan_timeout_seconds"])
    except Exception as error:
        status_data["last_scan_result"] = "network_or_navigation_error"
        status_data["last_scan_reason"] = type(error).__name__
        with capture_lock:
            state["scan_journal_path"] = None
        return False
    time.sleep(min(3, RUNTIME["scan_timeout_seconds"]))  # 页面加载缓冲
    if not wait_for_human_captcha(page, timeout=RUNTIME["captcha_timeout_seconds"]):
        status_data["last_scan_result"] = "captcha_timeout_or_stop"
        status_data["last_scan_reason"] = "captcha_timeout_or_stop"
        with capture_lock:
            state["scan_journal_path"] = None
        return False
    clicked = False
    # 策略1: 文字按钮
    for txt in ["进入直播间", "进入直播", "点击进入", "立即观看", "观看直播", "正在直播"]:
        try:
            btn = page.ele(f"text={txt}", timeout=2)
            if btn:
                btn.click()
                log(f"  点击「{txt}」")
                time.sleep(5)
                clicked = True
                break
        except:
            pass
    # 策略2: 查找任意可点击进入的元素
    if not clicked:
        for sel in ["a[href*='live']", ".enter-btn", ".live-entry", "[class*='enter']"]:
            try:
                btn = page.ele(sel, timeout=2)
                if btn:
                    btn.click()
                    log(f"  点击 {sel}")
                    time.sleep(5)
                    clicked = True
                    break
            except:
                pass
    dl = time.time() + RUNTIME["connect_timeout_seconds"]  # 验证码人工等待不计入流等待时限
    while time.time() < dl:
        if stop_requested():
            with capture_lock:
                state["scan_journal_path"] = None
            return False
        with capture_lock:
            stream_snapshot = dict(state["stream_url"])
        if stream_snapshot.get("url") and stream_snapshot.get("capture_time", 0) >= nav_t:
            break
        time.sleep(min(1, RUNTIME["listen_timeout_seconds"]))
    with capture_lock:
        stream_snapshot = dict(state["stream_url"])
        response_snapshot = list(state["collected"])
    if not stream_snapshot.get("url"):
        has_conflict = any(item.get("attribution_status") == "conflict"
                           for item in response_snapshot)
        write_failed = bool(state.get("journal_status", {}).get("error"))
        status_data["last_scan_result"] = ("journal_write_error" if write_failed else
                                            "ambiguous_live_detail_response" if has_conflict
                                            else "missing_stream_or_unattributed_response")
        status_data["last_scan_reason"] = ("journal persistence failed; raw evidence retained" if write_failed else
                                            "multiple_or_conflicting_live_ids; retained in journal"
                                            if has_conflict else "missing_stream_or_unattributed_response")
        with capture_lock:
            state["scan_journal_path"] = None
        return False
    # Preserve true/false/missing as separate states. A false segment is not a
    # confirmed digital segment under the study's strict platform-flag rule.
    digital_values = []
    title_values = []
    for c in response_snapshot:
        if "live.detail.get" not in (c.get("url") or ""):
            continue
        response_ids = c.get("live_ids") or []
        parsed = None
        if not response_ids:
            parsed = parse_json_body(c.get("body", ""))
            if parsed is not None:
                response_ids = [str(value) for value in find_values_by_key(parsed, "liveId")
                                if value is not None]
        if c.get("attribution_status") != "matched" or str(live_id) not in response_ids:
            continue
        digital_values.extend(c.get("digital_flags", []))
        response_titles = c.get("titles", [])
        if not response_titles and parsed is not None:
            response_titles = find_values_by_key(parsed, "title")
        title_values.extend(response_titles)

    flag_summary = summarize_digital_flags(digital_values)
    title_matches = title_keywords_in_values(title_values)
    if title_matches:
        status_data["last_scan_result"] = "success_title_rule"
        status_data["last_scan_reason"] = "title_keyword_match"
        log(
            f"  数字人标记：标题命中={'、'.join(title_matches)}；"
            f"平台={flag_summary['raw_label']}（按标题规则保留）"
        )
        return True
    if flag_summary["all_true"]:
        status_data["last_scan_result"] = "success_platform_flag"
        status_data["last_scan_reason"] = "all_captured_flags_true"
        log("  数字人标记：该次响应全 true（按平台标记保留）")
        return True
    if flag_summary["has_false"]:
        status_data["last_scan_result"] = "non_digital"
        status_data["last_scan_reason"] = "platform_flag_false"
        log(
            f"  数字人标记：含 false（平台={flag_summary['raw_label']}），"
            "按数字人样本口径跳过"
        )
    else:
        status_data["last_scan_result"] = "missing_digital_flag"
        status_data["last_scan_reason"] = "digital_flag_missing_or_incomplete"
        log(
            f"  数字人标记：缺失或不完整（平台={flag_summary['raw_label']}），"
            "待核查并跳过"
        )
    with capture_lock:
        state["scan_journal_path"] = None
    return False

def record_room(url, live_id, room_dir, surl, seg_name, segment_index):
    from src.crawler.recording import record_segment
    return record_segment(
        url=url, live_id=live_id, room_dir=room_dir, stream_url=surl,
        segment_index=segment_index, seg_name=seg_name, ffmpeg=FFMPEG,
        ffprobe=FFPROBE, max_minutes=MAX_MIN, user_agent=UA,
        cookie_header=get_cookie_header(), state=state, lock=capture_lock,
        page=page, log=log, publish_status=publish_status, run_id=RUN_ID,
    )

def validation_receipt(room_dir, segment_index):
    """Return file identity metadata for a segment accepted by record_segment."""
    root = Path(room_dir).resolve()
    candidates = []
    for final_path in root.rglob("*_final.json"):
        try:
            with final_path.open(encoding="utf-8") as stream:
                final = json.load(stream)
            if not isinstance(final, dict) or not validation_matches(final):
                continue
            if int(final.get("segment_index", -1)) != int(segment_index):
                continue
            video_path = Path(final["recorded_files"][0]).resolve()
            if not video_path.is_relative_to(root) or not video_path.is_file():
                continue
            final_stat, video_stat = final_path.stat(), video_path.stat()
            candidates.append({
                "segment_index": int(segment_index), "recording_id": str(final["recording_id"]),
                "final_json": str(final_path.resolve()), "final_size": final_stat.st_size,
                "final_mtime_ns": final_stat.st_mtime_ns, "video_path": str(video_path),
                "video_size": video_stat.st_size, "video_mtime_ns": video_stat.st_mtime_ns,
                "video_duration_seconds": float(final["video_duration_seconds"]),
                "validation_schema_version": 1,
                "final_sha256": digest(final_path),
                "video_sha256": final["technical_validation"]["video_sha256"],
            })
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
    return max(candidates, key=lambda item: item["final_mtime_ns"]) if candidates else None


def mark_recorded(lid, count, total, room_dir=None):
    """更新 urls 计数：先每日备份一次，再原子写入（临时文件+os.replace），防崩溃损坏"""
    path = URLS_FILE
    lines = open(path, encoding="utf-8").readlines()
    tag = f"已录制{count}/{total}"
    for i, line in enumerate(lines):
        match = re.search(r"liveId=(\d+)\b", line)
        if match and match.group(1) == str(lid):
            if "待录制" in line:
                lines[i] = line.replace("待录制", tag)
            elif "已录制" in line:
                lines[i] = line.rsplit(",", 1)[0] + f",{tag}\n"
            else:
                lines[i] = line.rstrip("\r\n") + f",{tag}\n"
            break
    bak = path + ".bak_" + time.strftime("%Y%m%d")
    if not os.path.exists(bak):
        try:
            import shutil
            shutil.copy2(path, bak)
        except Exception:
            pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, path)
    progress_path = os.environ.get("LIVE_PROGRESS_FILE")
    if progress_path:
        progress = {}
        if os.path.isfile(progress_path):
            with open(progress_path, encoding="utf-8") as progress_file:
                progress = json.load(progress_file)
        if progress.get("schema_version") == 2 and isinstance(progress.get("rooms"), dict):
            rooms = progress["rooms"]
            legacy_counts = progress.get("legacy_counts", {})
        else:
            rooms = {}
            legacy_counts = {key: value.get("count", 0) for key, value in progress.items()
                             if isinstance(value, dict) and type(value.get("count")) is int}
        previous = rooms.get(str(lid), {})
        segments = {item.get("recording_id"): item for item in previous.get("segments", [])
                    if isinstance(item, dict) and item.get("recording_id")}
        receipt = validation_receipt(room_dir, count) if room_dir else None
        if receipt:
            segments[receipt["recording_id"]] = receipt
        rooms[str(lid)] = {"count": count, "target": total, "updated_t": time.time(),
                           "segments": list(segments.values())}
        atomic_json(progress_path, {"schema_version": 2, "rooms": rooms,
                                    "legacy_counts": legacy_counts}, backup=False)

def finalize_room(lid):
    """Copy and verify a full room as part of a batch. Keep all staging sources."""
    room_dir = os.path.join(OUTDIR, f"room_{lid}")
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script = os.path.join(project_root, "scripts", "parse_data.py")
    publish_status("archiving", live_id=lid)
    try:
        result = subprocess.run([sys.executable, script, room_dir], capture_output=True,
                                timeout=600, cwd=project_root,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output = result.stdout.decode("utf-8", errors="replace")
        if result.returncode != 0 or "ARCHIVE_COPY_OK" not in output:
            log(f"  归档未通过校验，留在队列: {output[-300:]}")
            return False
        log(f"  room_{lid} 归档校验完成；staging 原视频与 JSON 保留")
        return True
    except Exception as error:
        log(f"  归档失败，全部源文件保留: {error}")
        return False

# ========== 主流程 ==========
log(f"ffmpeg={os.path.exists(FFMPEG)}")
publish_status("starting")
if retry_state_error:
    publish_status("failed", reason="retry_state_corrupt", detail=str(retry_state_error),
                   instance_id=INSTANCE_ID)
    _emit_worker_event("retry_state_corrupt", details={"instance_id": INSTANCE_ID})
    sys.exit(4)
url_pool = None
progress_path = os.environ.get("LIVE_PROGRESS_FILE")
if STATUS_FILE and progress_path:
    run_control = Path(STATUS_FILE).parent.resolve()
    expected_progress = (Path(STUDY_ROOT) / "_control" / f"progress_{PORT}.json").resolve()
    if (not Path(URLS_FILE).resolve().is_relative_to(run_control)
            or Path(progress_path).resolve() != expected_progress):
        publish_status("failed", reason="unsafe_reconciliation_paths", instance_id=INSTANCE_ID)
        _emit_worker_event("unsafe_reconciliation_paths", details={"instance_id": INSTANCE_ID})
        sys.exit(5)
    clean_staging()
    url_pool = read_urls()
    recovery_report = run_control / f"recovery_instance_{INSTANCE_ID}.jsonl"
    try:
        for url, lid, count, total in url_pool:
            result = reconcile_room(urls_path=URLS_FILE, progress_path=progress_path,
                                    staging_root=OUTDIR, report_path=recovery_report,
                                    run_id=RUN_ID, instance_id=INSTANCE_ID, live_id=lid,
                                    target=total)
            if result.get("status") in ("reconciled", "already_reconciled"):
                log(f"启动对账已恢复/确认 room_{lid} 第 {result['segment_index']} 段计数")
                retry_state.succeeded(lid)
    except Exception as error:
        publish_status("failed", reason="startup_reconciliation_failed",
                       detail=type(error).__name__, instance_id=INSTANCE_ID)
        _emit_worker_event("startup_reconciliation_failed", details={"instance_id": INSTANCE_ID})
        sys.exit(5)
    url_pool = read_urls()
if DELAY:
    log(f"延迟 {DELAY} 秒启动浏览器")
    interruptible_wait(DELAY)
if stop_requested():
    publish_status("stopped")
    sys.exit(0)
if not wait_while_paused(PAUSE_FILE, stop_requested=stop_requested,
                         publish_status=publish_status, log=log,
                         process_commands=process_pending_command):
    publish_status("stopped")
    sys.exit(0)

from DrissionPage import ChromiumPage, ChromiumOptions

# 预创建用户数据，跳过 Edge 首次向导
user_data = os.path.abspath(os.path.join(STUDY_ROOT, f"../.edge_{PORT}", "User Data"))
os.makedirs(user_data, exist_ok=True)
# 从共享配置(.edge_data)复制登录态，避免每次都要重新扫码
seed_dir = os.path.abspath(os.path.join(STUDY_ROOT, "../.edge_data"))
if os.path.isdir(seed_dir) and not os.path.isfile(os.path.join(user_data, "Default", "Network", "Cookies")):
    import shutil
    for rel in ["Local State", "Default/Login Data", "Default/Network/Cookies", "Default/Preferences"]:
        src = os.path.join(seed_dir, *rel.split("/"))
        dst = os.path.join(user_data, *rel.split("/"))
        if os.path.isfile(src) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            log(f"  📋 从共享配置复制: {rel}")
open(os.path.join(user_data, "First Run"), "w").close()
try:
    pref_dir = os.path.join(user_data, "Default")
    os.makedirs(pref_dir, exist_ok=True)
    import json as _j
    pref_path = os.path.join(pref_dir, "Preferences")
    if not os.path.exists(pref_path):
        atomic_json(pref_path, {"browser": {"has_seen_welcome_page": True}})
except: pass

co = ChromiumOptions()
co.headless(False)
co.set_argument("--no-sandbox")
co.set_argument("--disable-gpu")
co.set_argument("--disable-blink-features=AutomationControlled")
co.set_argument("--window-size=1440,900")
co.set_argument("--window-position=50,50")
co.set_argument("--lang=zh-CN")
co.set_argument("--disable-extensions")
co.set_argument("--disable-background-mode")
co.set_argument("--disable-plugins")
co.set_argument("--disable-sync")
co.set_argument("--no-first-run")
co.set_argument("--no-default-browser-check")
co.set_argument("--disable-features=TranslateUI,msWelcomePage,msEdgeSync")
co.set_argument("--disable-background-networking")
co.set_argument("--disable-component-update")
if os.path.exists(EDGE_PATH):
    co.set_browser_path(EDGE_PATH)
else:
    log(f"  ⚠️ Edge 未找到({EDGE_PATH})，使用默认浏览器")
co.set_local_port(PORT)
co.set_user_data_path(user_data)
log(f"Edge (端口 {PORT})")
log(f"URL 文件: {sys.argv[1]}")

try: page = ChromiumPage(co)
except Exception as e: log(f"启动失败: {e}"); sys.exit(2)

page.get("https://www.taobao.com", timeout=20); time.sleep(2)
if logged_in():
    log("已有登录态")
else:
    if os.path.isfile(COOKIE_JSON):
        try:
            with open(COOKIE_JSON, encoding="utf-8") as saved_file:
                page.set.cookies(json.load(saved_file))
            page.refresh()
            time.sleep(3)
        except Exception as error:
            log(f"Cookie 读取失败: {type(error).__name__}")
    if not logged_in():
        log("请在新开的 Edge 窗口登录，程序最多等候 5 分钟")
        publish_status("login_required")
        deadline = time.monotonic() + 300
        while not logged_in() and not stop_requested() and time.monotonic() < deadline:
            time.sleep(2)
        if not logged_in():
            publish_status("stopped" if stop_requested() else "login_timeout")
            sys.exit(3)
if logged_in() and os.environ.get("LIVE_SAVE_COOKIES", "1") != "0":
    try:
        atomic_json(COOKIE_JSON, list(page.cookies()), backup=True)
    except Exception as error:
        log(f"登录态保存失败: {type(error).__name__}")
publish_status("ready")

page.listen.start("")
threading.Thread(target=listen_loop, daemon=True).start()

# ---- 主循环：随机抽 + 冷却 ----
if url_pool is None:
    clean_staging()
    url_pool = read_urls()
active = sum(1 for _, _, c, t in url_pool if c < t)
log(f"共 {len(url_pool)} 个 URL，{active} 个活跃")
last_record = {}
# 从已有 _final.json 恢复录制时间（FIFO 需要）
for url, lid, count, total in url_pool:
    room_dir = os.path.join(OUTDIR, f"room_{lid}")
    if os.path.isdir(room_dir):
        latest_t = 0
        for root, dirs, files in os.walk(room_dir):
            for f in files:
                if f.endswith("_final.json"):
                    try:
                        data = json.load(open(os.path.join(root, f)))
                        t = data.get("record_start_t", 0)
                        if t > latest_t:
                            latest_t = t
                    except: pass
        if latest_t > 0:
            last_record[lid] = latest_t
log(f"恢复 {len(last_record)} 个房间的录制时间")
pending_finalize = []  # 批量归档队列
attempted_finalize = set()


def finalize_room_once(live_id):
    key = str(live_id)
    if key in attempted_finalize:
        return False
    attempted_finalize.add(key)
    return finalize_room(live_id)


suspect_finalize = []  # staging 有未归档段但 session 已存在 → 仅告警，不自动 parse（防重编号损坏）
# 启动时检查已有满段房间（跳过 session 已完整归档的）
sess_root = os.path.join(STUDY_ROOT, "sessions")
for url, lid, count, total in url_pool:
    if count < total:
        continue
    n_sess = 0
    found_sess = False
    try:
        for name in os.listdir(sess_root):
            if name.endswith("_" + lid):
                found_sess = True
                rd = os.path.join(sess_root, name, "raw")
                if os.path.isdir(rd):
                    n_sess = len([f for f in os.listdir(rd) if f.endswith(".json")])
                break
    except Exception:
        pass
    if not found_sess:
        room_path = os.path.join(OUTDIR, f"room_{lid}")
        finals = sum(f.endswith("_final.json") for _, _, fs in os.walk(room_path) for f in fs)
        if finals >= total:
            pending_finalize.append(lid)
        else:
            log(f"room_{lid} 清单已满段，但 staging 仅 {finals}/{total} 个 final；不自动解析")
    elif n_sess < count:
        suspect_finalize.append(lid)   # 有额外未归档段 → 人工确认
if pending_finalize:
    log(f"📋 启动发现 {len(pending_finalize)} 间已满段，待归档（已跳过 session 已归档房间）")
    if len(pending_finalize) >= BATCH_ROOMS and not stop_requested():
        log(f"\n📦 批量归档 {len(pending_finalize)} 间...")
        pending_finalize = _finalize_pending_rooms(pending_finalize, finalize_room_once, stop_requested)
if suspect_finalize:
    log(f"⚠️ {len(suspect_finalize)} 间 staging 有未归档段且 session 已存在（不自动 parse，需人工确认）: {suspect_finalize}")

batch_ready = False
natural_finished = False
while True:
    if not wait_while_paused(PAUSE_FILE, stop_requested=stop_requested,
                             publish_status=publish_status, log=log,
                             process_commands=process_pending_command):
        break
    publish_status("waiting", pending_rooms=len(pending_finalize))
    if stop_requested():
        log("收到停止请求，当前段已完成，原数据保留")
        break
    if batch_ready and len(pending_finalize) >= BATCH_ROOMS and not stop_requested():
        log(f"\n📦 批量归档 {len(pending_finalize)} 间...")
        pending_finalize = _finalize_pending_rooms(pending_finalize, finalize_room_once, stop_requested)
        log(f"   批量归档结束，{len(pending_finalize)} 间未通过校验留在队列\n")
        publish_status("waiting", pending_rooms=len(pending_finalize))
        batch_ready = False
    now = time.time()
    # 优先挑已录过+冷却完的，其次新房
    hot = []   # 已录过且冷却完了（需要第2/3段）
    cold = []  # 全新的（需要第1段）
    for url, lid, count, total in url_pool:
        if count >= total:
            continue
        retry_ok, retry_wait = retry_state.eligible(lid, now)
        if not retry_ok:
            entry = retry_state.entry(lid)
            if entry.get("retry_exhausted"):
                if lid not in status_data.get("retry_exhausted_rooms", []):
                    status_data["retry_exhausted_rooms"] = list(status_data.get("retry_exhausted_rooms", [])) + [lid]
                    _emit_worker_event("retry_exhausted", details={"failure_count": entry.get("failure_count")})
            continue
        last = last_record.get(lid, 0)
        if now - last < COOLDOWN_SEC:
            continue
        if count > 0:
            hot.append((url, lid, count, total))
        else:
            cold.append((url, lid, count, total))
    # FIFO: 已录房间按录制时间排序，最早录的先（先进先出）
    hot.sort(key=lambda x: last_record.get(x[1], 0))
    candidates = hot if hot else cold
    if len(hot) > 0:
        log(f"  优先 {len(hot)}个已录房间冷却完成")

    if not candidates:
        wait = 60
        times = [last_record.get(lid, 0) for _, lid, c, t in url_pool if c < t]
        if times:
            earliest = min(times) + COOLDOWN_SEC - now
            if earliest > 0:
                wait = min(earliest, 60)
        # 检查是否都满了
        if not times:
            remaining = sum(1 for _, _, c, t in url_pool if c < t)
            if remaining == 0:
                log("🎉 本实例所有房间已录满")
                natural_finished = True
                break
        interruptible_wait(wait)
        continue

    url, lid, count, total = random.choice(candidates)
    previous_lid = status_data.get("previous_room_id")
    room_switch = previous_lid is not None and str(previous_lid) != str(lid)
    if room_switch and RUNTIME["room_gap_seconds"]:
        log(f"  房间切换间隔 {RUNTIME['room_gap_seconds']} 秒")
    previous_lid = _wait_for_room_switch(lid, previous_lid, RUNTIME["room_gap_seconds"], interruptible_wait)
    if stop_requested():
        break
    status_data["previous_room_id"] = previous_lid
    seg_name = SEG_NAMES[count] if count < len(SEG_NAMES) else f"第{count+1}段"
    log(f"\n🎯 {seg_name} room_{lid}")
    publish_status("scanning", live_id=lid, segment_index=count + 1,
                   planned_duration_seconds=MAX_MIN * 60, elapsed_seconds=0,
                   remaining_seconds=MAX_MIN * 60, reason="", last_scan_result="in_progress")

    # 扫码
    try: ok_scan = scan_room(url, lid)
    except:
        with capture_lock:
            state["scan_journal_path"] = None
        log("  扫码异常"); last_record[lid] = now
        entry = retry_state.failed(lid, "scan_exception")
        if entry["retry_exhausted"]:
            status_data["retry_exhausted_rooms"] = list(dict.fromkeys(
                status_data.get("retry_exhausted_rooms", []) + [lid]))
            _emit_worker_event("retry_exhausted", details={"failure_count": entry["failure_count"]})
        time.sleep(5); continue
    if stop_requested():
        with capture_lock:
            state["scan_journal_path"] = None
        break
    if not ok_scan:
        log("  不可录"); last_record[lid] = now
        entry = retry_state.failed(lid, "scan_unavailable_or_filtered")
        if entry["retry_exhausted"]:
            status_data["retry_exhausted_rooms"] = list(dict.fromkeys(
                status_data.get("retry_exhausted_rooms", []) + [lid]))
            _emit_worker_event("retry_exhausted", details={"failure_count": entry["failure_count"]})
        publish_status("waiting", reason=status_data.get("reason") or "未取得目标直播流或未通过数字人筛选",
                       next_retry_t=entry["last_failure_t"] + retry_state.cooldown_seconds,
                       retry_exhausted=entry["retry_exhausted"])
        if login_expired():
            log("  ⚠️ 检测到登录失效，尝试重新注入 Cookie...")
            refresh_login()
        time.sleep(5); continue

    # 录制
    if stop_requested():
        with capture_lock:
            state["scan_journal_path"] = None
        break
    if pause_requested(PAUSE_FILE):
        with capture_lock:
            state["scan_journal_path"] = None
        continue
    room_dir = os.path.join(OUTDIR, f"room_{lid}")
    with capture_lock:
        surl = state["stream_url"].get("url", "")
    rec_start_t = time.time()
    try:
        ok = record_room(url, lid, room_dir, surl, seg_name, count + 1)
    except Exception as e:
        log(f"  录制异常: {e}")
        last_record[lid] = time.time()
        entry = retry_state.failed(lid, type(e).__name__)
        if entry["retry_exhausted"]:
            status_data["retry_exhausted_rooms"] = list(dict.fromkeys(
                status_data.get("retry_exhausted_rooms", []) + [lid]))
            _emit_worker_event("retry_exhausted", details={"failure_count": entry["failure_count"]})
        continue

    if ok:
        new_count = count + 1
        mark_recorded(lid, new_count, total, room_dir)
        elapsed = time.time() - rec_start_t
        if elapsed < MAX_MIN * 60:
            pad = MAX_MIN * 60 - elapsed
            log(f"  ⏳ 填满 {MAX_MIN}分槽位，等 {pad:.0f}s")
            interruptible_wait(pad)
        log(f"📀 room_{lid} {new_count}/{total} 轮")
        if new_count >= total:
            if lid not in pending_finalize:
                pending_finalize.append(lid)
            batch_ready = len(pending_finalize) >= BATCH_ROOMS
            log(f"  📋 加入归档队列 ({len(pending_finalize)}/{BATCH_ROOMS})")
        last_record[lid] = rec_start_t
        status_data["previous_room_id"] = str(lid)
        retry_state.succeeded(lid)
        status_data["retry_exhausted_rooms"] = [item for item in
                                                  status_data.get("retry_exhausted_rooms", [])
                                                  if str(item) != str(lid)]
        url_pool = read_urls()  # 重载
    else:
        log(f"  room_{lid} 录制失败")
        if login_expired():
            log("  ⚠️ 检测到登录失效，尝试重新注入 Cookie...")
            refresh_login()
        last_record[lid] = now
        status_data["previous_room_id"] = str(lid)
        entry = retry_state.failed(lid, "record_segment_failed")
        if entry["retry_exhausted"]:
            status_data["retry_exhausted_rooms"] = list(dict.fromkeys(
                status_data.get("retry_exhausted_rooms", []) + [lid]))
            _emit_worker_event("retry_exhausted", details={"failure_count": entry["failure_count"]})

if natural_finished and pending_finalize and not stop_requested():
    log(f"\n📦 自然结束归档尾批 {len(pending_finalize)} 间（配置阈值 {BATCH_ROOMS}）...")
    pending_finalize = _finalize_pending_rooms(pending_finalize, finalize_room_once, stop_requested)
    log(f"   尾批归档完成；{len(pending_finalize)} 间失败/待归档，原始数据保留")
with capture_lock:
    sink_lock.acquire()
    state["capture_closed"] = True
    state["scan_journal_path"] = None
    sink_lock.release()
publish_status("stopped" if stop_requested() else "finished", pending_rooms=len(pending_finalize),
               pending_archive_rooms=list(pending_finalize),
               pending_archive_reason=("主动停止，等待下次明确处理" if stop_requested() and pending_finalize
                                       else "归档失败/校验未通过，保留原始数据" if pending_finalize else ""))
