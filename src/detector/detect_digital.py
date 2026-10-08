# -*- coding: utf-8 -*-
"""
Detect Taobao live rooms flagged as digital-human live streams.

Reads the live detail API response from the browser and extracts
isDigitalAnchorLive. This script does not delete, move, or modify any
existing user data; it only writes one txt file in the project root.
"""

import datetime
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

try:
    from DrissionPage import ChromiumPage, ChromiumOptions
except Exception:
    ChromiumPage = None
    ChromiumOptions = None


import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.utils.config import STUDY_ROOT as CFG_STUDY_ROOT, EDGE_PATH as CFG_EDGE_PATH
from src.utils.digital_flags import (
    normalize_digital_flag, parse_json_body, summarize_digital_flags,
    title_keywords_in_values,
)

ROOT = Path(__file__).resolve().parent.parent.parent
STUDY_ROOT = Path(os.environ.get("LIVE_STUDY_ROOT", str(CFG_STUDY_ROOT)))
COOKIE_JSON = STUDY_ROOT / "_config" / "taobao_cookies.json"
DEFAULT_TXT = STUDY_ROOT / "sessions" / "数字人确认.txt"
EDGE_PATH = CFG_EDGE_PATH

DEFAULT_IDS = [
    "4185708607630442",
    "2159201216030444",
    "3802945795113668",
    "1798606982673197",
    "4440671415937176",
    "2835520390595837",
    "3662375446845606",
    "906353825743304",
    "2320599129799487",
    "3877462002379074",
    "1906390834281132",
    "3953145595953056",
    "2772046376030968",
    "4414154203819745",
    "3194494387131868",
    "2966900568804956",
    "3266226158167777",
    "3968170402713334",
    "3203763468795195",
    "2779840023470123",
]
# 兼容旧引用
ID_LIST = DEFAULT_IDS


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def find_keys(obj, key):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(find_keys(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_keys(item, key))
    return found


def as_bool(value):
    return normalize_digital_flag(value)


def first_text(values):
    for value in values or []:
        if value is None:
            continue
        text = str(value).strip()
        if text and text != "None":
            return text
    return ""


def normalize_body(body):
    if body is None:
        return None, ""
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    elif isinstance(body, (dict, list)):
        text = json.dumps(body, ensure_ascii=False)
    else:
        text = str(body)
    return parse_json_body(body), text


def parse_live_response(body, expected_lid):
    parsed, text = normalize_body(body)
    if expected_lid not in text:
        return None

    result = {}
    title_values = []
    if parsed is not None:
        digital_values = find_keys(parsed, "isDigitalAnchorLive")
        flag_summary = summarize_digital_flags(digital_values)
        result["isDigitalAnchorLive"] = (
            True if flag_summary["all_true"]
            else False if flag_summary["all_false"]
            else None
        )
        result["isDigitalAnchorLiveValues"] = [
            "true" if as_bool(value) is True
            else "false" if as_bool(value) is False
            else "unknown"
            for value in digital_values
        ]
        result["digitalFlagStatus"] = flag_summary["status"]
        result["platformAllTrue"] = flag_summary["all_true"]
        result["platformHasFalse"] = flag_summary["has_false"]
        result["platformHasMissing"] = flag_summary["has_missing"]
        result["liveId"] = first_text(find_keys(parsed, "liveId"))
        title_values = find_keys(parsed, "liveTitle")
        if not title_values:
            title_values = find_keys(parsed, "title")
        result["liveTitle"] = first_text(title_values)
        result["anchorName"] = first_text(
            find_keys(parsed, "nickName")
            or find_keys(parsed, "anchorName")
            or find_keys(parsed, "userName")
        )
        result["liveStatus"] = first_text(
            find_keys(parsed, "liveStatus")
            or find_keys(parsed, "status")
            or find_keys(parsed, "isLive")
        )
    else:
        raw_flags = re.findall(
            r'"isDigitalAnchorLive"\s*:\s*("(?:true|false)"|true|false)',
            text,
        )
        if raw_flags:
            flag_values = [value.strip('"').lower() for value in raw_flags]
            flag_summary = summarize_digital_flags(flag_values)
            result["isDigitalAnchorLive"] = (
                True if flag_summary["all_true"]
                else False if flag_summary["all_false"]
                else None
            )
            result["isDigitalAnchorLiveValues"] = flag_values
            result["digitalFlagStatus"] = flag_summary["status"]
            result["platformAllTrue"] = flag_summary["all_true"]
            result["platformHasFalse"] = flag_summary["has_false"]
            result["platformHasMissing"] = flag_summary["has_missing"]
        for key in ("liveId", "liveTitle", "title", "nickName", "anchorName", "userName", "liveStatus", "status", "isLive"):
            mm = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
            if mm:
                result[key] = mm.group(1)
                if key in ("liveTitle", "title"):
                    title_values.append(mm.group(1))

    if result.get("liveId") and result["liveId"] != expected_lid:
        return None
    if result.get("liveId") is None:
        result["liveId"] = expected_lid
    result.setdefault("isDigitalAnchorLive", None)
    result.setdefault("isDigitalAnchorLiveValues", [])
    result.setdefault("digitalFlagStatus", "unknown")
    result.setdefault("platformAllTrue", False)
    result.setdefault("platformHasFalse", False)
    result.setdefault("platformHasMissing", True)
    result["titleRuleOverride"] = list(
        title_keywords_in_values(title_values or [result.get("liveTitle")])
    )
    result["raw_snippet"] = text[:1500]
    return result


def collect_detail_responses(page, wait_sec):
    collected = []
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        try:
            resp = page.listen.wait(timeout=remaining)
        except Exception:
            continue
        if resp is None or resp is False:
            continue
        try:
            url = resp.url or ""
        except Exception:
            url = ""
        if "live.detail.get" not in url:
            continue
        try:
            body = resp.response.body
        except Exception:
            body = None
        collected.append({"url": url, "body": body})
    return collected


def make_browser_options(profile_dir):
    co = ChromiumOptions()
    co.headless(True)
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--lang=zh-CN")
    co.set_argument("--disable-extensions")
    co.set_argument("--disable-background-mode")
    co.set_argument("--disable-sync")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")
    co.set_argument("--disable-background-networking")
    co.set_argument("--disable-component-update")
    if os.path.exists(EDGE_PATH):
        co.set_browser_path(EDGE_PATH)
    co.set_user_data_path(str(profile_dir))
    return co


def load_cookies():
    if not COOKIE_JSON.exists():
        return []
    with open(COOKIE_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def check_one(page, live_id):
    url = f"https://tbzb.taobao.com/live?liveId={live_id}"
    page.listen.start("live.detail.get")
    try:
        page.get(url, timeout=30)
    except Exception as exc:
        log(f"  load failed for {live_id}: {exc}")

    responses = collect_detail_responses(page, 8)
    result = None
    for item in responses:
        parsed = parse_live_response(item["body"], live_id)
        if parsed:
            result = parsed
            break

    if result is None:
        try:
            page.refresh()
        except Exception:
            pass
        responses.extend(collect_detail_responses(page, 6))
        for item in responses:
            parsed = parse_live_response(item["body"], live_id)
            if parsed:
                result = parsed
                break

    if result is None:
        return {
            "liveId": live_id,
            "status": "no_api",
            "isDigitalAnchorLive": None,
            "isDigitalAnchorLiveValues": [],
            "digitalFlagStatus": "missing",
            "platformAllTrue": False,
            "platformHasFalse": False,
            "platformHasMissing": True,
            "titleRuleOverride": [],
            "note": "live.detail.get not captured; may be offline, login expired, or risk control",
        }

    title_override = bool(result.get("titleRuleOverride"))
    flag_status = result.get("digitalFlagStatus")
    if title_override or flag_status == "all_true":
        result["status"] = "digital"
    elif flag_status == "mixed":
        result["status"] = "mixed"
    elif flag_status == "all_false":
        result["status"] = "platform_false"
    elif result.get("platformHasFalse"):
        result["status"] = "review"
    else:
        result["status"] = "unknown"
    result["digitalBasis"] = (
        "title_keyword" if title_override else flag_status
    )
    return result


def main(argv=None):
    """用法: python detect_digital.py [liveId] [--ids a,b] [--ids-file x.txt] [--out out.txt]"""
    if ChromiumPage is None or ChromiumOptions is None:
        log("DrissionPage not installed")
        sys.exit(1)

    import argparse
    ap = argparse.ArgumentParser(description="Detect digital-human live rooms")
    ap.add_argument("live_id", nargs="?", default=None, help="single liveId to check")
    ap.add_argument("--ids", default="", help="comma-separated liveIds")
    ap.add_argument("--ids-file", default="", help="file with one liveId per line")
    ap.add_argument("--out", default=str(DEFAULT_TXT), help="output txt path")
    args = ap.parse_args(argv)

    ids = []
    if args.live_id:
        ids = [args.live_id]
    elif args.ids:
        ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    elif args.ids_file:
        ids = [l.strip() for l in open(args.ids_file, encoding="utf-8")
               if l.strip() and not l.startswith("#")]
    else:
        ids = list(DEFAULT_IDS)
    txt_path = Path(args.out)

    txt_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir = ROOT / f".digital_check_profile_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    profile_dir.mkdir(parents=True, exist_ok=True)

    co = make_browser_options(profile_dir)
    page = ChromiumPage(co)
    try:
        page.get("https://www.taobao.com", timeout=20)
        saved_cookies = load_cookies()
        if saved_cookies:
            try:
                page.set.cookies(saved_cookies)
                page.refresh()
                time.sleep(2)
            except Exception as exc:
                log(f"cookie inject warning: {exc}")

        results = []
        for index, live_id in enumerate(ids, 1):
            log(f"[{index}/{len(ids)}] check {live_id}")
            result = check_one(page, live_id)
            result["checked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            result["url"] = f"https://tbzb.taobao.com/live?liveId={live_id}"
            results.append(result)
            log(
                f"  -> {result.get('status')} "
                f"digital={result.get('isDigitalAnchorLive')} "
                f"flags={result.get('isDigitalAnchorLiveValues')} "
                f"title_rule={result.get('titleRuleOverride')} "
                f"title={result.get('liveTitle') or ''}"
            )
            if index < len(ids):
                time.sleep(2)

        digital_ids = [r["liveId"] for r in results if r.get("status") == "digital"]
        platform_false_ids = [
            r["liveId"] for r in results if r.get("status") == "platform_false"
        ]
        mixed_ids = [r["liveId"] for r in results if r.get("status") == "mixed"]
        review_ids = [r["liveId"] for r in results if r.get("status") == "review"]
        unknown_ids = [r["liveId"] for r in results if r.get("status") == "unknown"]
        log(
            f"digital={len(digital_ids)} platform_false={len(platform_false_ids)} "
            f"mixed={len(mixed_ids)} review={len(review_ids)} unknown={len(unknown_ids)}"
        )
        log("digital ids: " + ", ".join(digital_ids))

        digital_lines = [
            f"liveId={live_id},https://tbzb.taobao.com/live?liveId={live_id}"
            for live_id in digital_ids
        ]
        if digital_lines:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("\n".join(digital_lines) + "\n")
            log(f"txt saved: {txt_path}")
        else:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write("未确认到数字人直播间\n")
            log(f"txt saved (no digital rooms): {txt_path}")
    except Exception:
        traceback.print_exc()
    finally:
        try:
            page.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
