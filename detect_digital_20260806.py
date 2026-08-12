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


ROOT = Path(__file__).resolve().parent
STUDY_ROOT = ROOT / "直播研究数据"
COOKIE_JSON = STUDY_ROOT / "_config" / "taobao_cookies.json"
TXT_PATH = ROOT / "数字人确认_20260806.txt"
EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

ID_LIST = [
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
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in ("true", "1", "yes")


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
    if isinstance(body, dict):
        return body, json.dumps(body, ensure_ascii=False)
    if isinstance(body, str):
        text = body
        try:
            parsed = json.loads(text)
            return parsed, text
        except Exception:
            return None, text
    try:
        text = str(body)
        return None, text
    except Exception:
        return None, ""


def parse_live_response(body, expected_lid):
    parsed, text = normalize_body(body)
    if expected_lid not in text:
        return None

    result = {}
    if parsed is not None:
        digital_values = find_keys(parsed, "isDigitalAnchorLive")
        if digital_values:
            result["isDigitalAnchorLive"] = as_bool(digital_values[0])
        result["liveId"] = first_text(find_keys(parsed, "liveId"))
        result["liveTitle"] = first_text(
            find_keys(parsed, "liveTitle") or find_keys(parsed, "title")
        )
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
        m = re.search(
            r'"isDigitalAnchorLive"\s*:\s*("(?:true|false)"|true|false)', text
        )
        if m:
            result["isDigitalAnchorLive"] = m.group(1).strip('"').lower() == "true"
        for key in ("liveId", "liveTitle", "title", "nickName", "anchorName", "userName", "liveStatus", "status", "isLive"):
            mm = re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
            if mm:
                result[key] = mm.group(1)

    if result.get("liveId") and result["liveId"] != expected_lid:
        return None
    if result.get("liveId") is None:
        result["liveId"] = expected_lid
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
            "note": "live.detail.get not captured; may be offline, login expired, or risk control",
        }

    digital = result.get("isDigitalAnchorLive")
    result["status"] = "digital" if digital is True else "human" if digital is False else "unknown"
    return result


def main():
    if ChromiumPage is None or ChromiumOptions is None:
        log("DrissionPage not installed")
        sys.exit(1)

    only = None
    if len(sys.argv) > 1:
        only = sys.argv[1]
    ids = [only] if only else ID_LIST

    TXT_PATH.parent.mkdir(parents=True, exist_ok=True)
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
                f"title={result.get('liveTitle') or ''}"
            )
            if index < len(ids):
                time.sleep(2)

        digital_ids = [r["liveId"] for r in results if r.get("status") == "digital"]
        human_ids = [r["liveId"] for r in results if r.get("status") == "human"]
        unknown_ids = [r["liveId"] for r in results if r.get("status") != "digital" and r.get("status") != "human"]
        log(f"digital={len(digital_ids)} human={len(human_ids)} unknown={len(unknown_ids)}")
        log("digital ids: " + ", ".join(digital_ids))

        digital_lines = [
            f"liveId={live_id},https://tbzb.taobao.com/live?liveId={live_id}"
            for live_id in digital_ids
        ]
        if digital_lines:
            with open(TXT_PATH, "w", encoding="utf-8") as f:
                f.write("\n".join(digital_lines) + "\n")
            log(f"txt saved: {TXT_PATH}")
        else:
            with open(TXT_PATH, "w", encoding="utf-8") as f:
                f.write("未确认到数字人直播间\n")
            log(f"txt saved (no digital rooms): {TXT_PATH}")
    except Exception:
        traceback.print_exc()
    finally:
        try:
            page.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
