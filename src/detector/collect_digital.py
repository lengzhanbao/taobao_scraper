# -*- coding: utf-8 -*-
"""
淘宝直播数字人 URL 收集器 v2
策略：发现页批量提取 liveId → 逐个加载检查 isDigitalAnchorLive
"""

import os, sys, time, json, re, random

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.utils.config import STUDY_ROOT, EDGE_PATH, USER_AGENT
from src.utils.digital_flags import (
    find_values_by_key, parse_json_body,
    summarize_digital_flags, title_keywords_in_values,
)
OUTDIR = os.path.join(STUDY_ROOT, "_staging")
URLS_FILE = os.path.join(STUDY_ROOT, "_config", "live_urls.txt")
COOKIE_JSON = os.path.join(STUDY_ROOT, "_config", "taobao_cookies.json")
COOKIE_TXT = os.path.join(STUDY_ROOT, "_config", "taobao_cookies.txt")
QR_PNG = os.path.join(OUTDIR, "qr.png")

CHECK_TIMEOUT = 8
MIN_COLLECT = 200               # 总共收集200个数字人
MAX_CHECK = 800                 # 最多检查800个liveId
LOGIN_WAIT = 300
MIN_DELAY = 60                 # 每次检查间隔最少1分钟
MAX_DELAY = 120                # 最多2分钟
EXISTING_COUNT = 34             # 已有34个，需新收集166个

UA = USER_AGENT
os.makedirs(OUTDIR, exist_ok=True)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---- 清理上次异常退出遗留的浏览器/ffmpeg 进程 ----
import subprocess as _sp
try:
    _sp.run(["taskkill", "/F", "/IM", "ffmpeg.exe"], capture_output=True, timeout=5)
except Exception: pass
try:
    _sp.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True, timeout=5)
    log("已清理遗留浏览器进程，5秒后启动...")
    time.sleep(5)
except Exception: pass

from DrissionPage import ChromiumPage, ChromiumOptions

co = ChromiumOptions()
co.headless(False)
co.set_argument("--no-sandbox")
co.set_argument("--disable-gpu")
co.set_argument("--disable-blink-features=AutomationControlled")
co.set_user_agent(UA)
if os.path.exists(EDGE_PATH):
    co.set_browser_path(EDGE_PATH)
    log("使用 Edge 浏览器（防指纹检测）")
else:
    log("Edge 未找到，回退到系统默认浏览器")

log("启动浏览器...")
page = ChromiumPage(co)
page.listen.start()

def drain_responses(wait_sec=6):
    deadline = time.time() + wait_sec
    out = []
    while time.time() < deadline:
        try:
            for resp in page.listen.steps(timeout=2):
                url = resp.url or ""
                if resp.response:
                    body = resp.response.body
                    if isinstance(body, dict):
                        body = json.dumps(body, ensure_ascii=False)
                    out.append({"url": url, "body": str(body)[:800000]})
        except Exception:
            time.sleep(0.3)
    return out

def is_logged_in():
    try:
        return bool({c.get("name") for c in page.cookies()} & {"unb", "sgcookie"})
    except:
        return False

def get_cookie_header():
    try:
        return "; ".join([f"{c['name']}={c['value']}" for c in page.cookies() if c.get("name") and c.get("value")])
    except:
        return ""

# ==== 登录 ====
login_ok = False
if os.path.exists(COOKIE_JSON):
    try:
        saved = json.load(open(COOKIE_JSON, encoding="utf-8"))
        page.get("https://www.taobao.com", timeout=20)
        page.set.cookies(saved)
        page.refresh()
        time.sleep(3)
        if is_logged_in():
            login_ok = True
            log("Cookie 登录成功！")
        else:
            log("Cookie 过期，改走二维码")
    except Exception as e:
        log(f"复用异常: {e}")

if not login_ok:
    log("打开登录页，请扫码...")
    page.get("https://login.taobao.com/", timeout=30)
    for sel in ['text=二维码登录', 'text=扫码登录']:
        try:
            if page.ele(sel, timeout=1):
                page.ele(sel).click()
                time.sleep(2)
                break
        except:
            pass
    try:
        page.get_screenshot(QR_PNG)
        log(f"二维码: {QR_PNG}")
    except:
        pass
    deadline = time.time() + LOGIN_WAIT
    while time.time() < deadline:
        if is_logged_in():
            login_ok = True
            log("登录成功！")
            break
        time.sleep(3)
    if not login_ok:
        log("登录超时"); page.quit(); sys.exit(1)

try:
    cks = page.cookies()
    with open(COOKIE_JSON, "w", encoding="utf-8") as f:
        json.dump(cks, f, ensure_ascii=False, indent=2)
    with open(COOKIE_TXT, "w", encoding="utf-8") as f:
        f.write(get_cookie_header())
    log("Cookie 已保存")
except:
    pass

# ==== 主流程 ====
DISCOVERY = "https://tbzb.taobao.com/"
digital_urls = []
# 启动时读回已有 URL，防止重跑后覆盖
if os.path.exists(URLS_FILE):
    with open(URLS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("https://"):
                digital_urls.append(line)
    log(f"已加载 {len(digital_urls)} 个已有数字人 URL")
checked_ids = set()

def save_urls():
    """追加写入：保留原文件内容，新 URL 附加到末尾（含 ,数字人,待录制 格式）"""
    existing_lines = []
    existing_urls = set()
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line_r = line.rstrip("\n")
                existing_lines.append(line_r)
                if "liveId=" in line_r:
                    existing_urls.add(line_r)
    new_lines = []
    for u in digital_urls:
        line = f"{u},数字人,待录制"
        if line not in existing_urls and u not in {l.split(",")[0] for l in existing_urls if "liveId=" in l}:
            new_lines.append(line)
            existing_urls.add(line)
    if new_lines:
        with open(URLS_FILE, "a", encoding="utf-8") as f:
            for line in new_lines:
                f.write(line + "\n")
        log(f"  追加 {len(new_lines)} 个新 URL")

for round_n in range(20):
    if len(digital_urls) >= MIN_COLLECT or len(checked_ids) >= MAX_CHECK:
        break

    log(f"--- 第{round_n+1}轮：发现页提取 liveId ---")
    page.get(DISCOVERY, timeout=30)
    time.sleep(4)
    for s in range(3):
        try:
            page.run_js("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
        except:
            pass

    try:
        html = page.html or ""
    except Exception as e:
        log(f"  发现页读取失败({e})，刷新重试")
        try:
            page.get(DISCOVERY, timeout=30)
            time.sleep(4)
            html = page.html or ""
        except:
            log("  跳过本轮")
            continue
    found = re.findall(r'liveId=(\d+)', html)
    new_ids = [lid for lid in found if lid not in checked_ids]
    log(f"  发现 {len(new_ids)} 个未检查的 liveId")

    for lid in new_ids:
        if len(digital_urls) >= MIN_COLLECT or len(checked_ids) >= MAX_CHECK:
            break

        # 随机延迟 180-300 秒，防止频率过高封号
        delay = random.randint(MIN_DELAY, MAX_DELAY)
        log(f"  等待 {delay}s 后检查...")
        time.sleep(delay)

        try:
            page.get(f"https://tbzb.taobao.com/live?liveId={lid}", timeout=20)
        except Exception as e:
            log(f"  页面加载失败({e})，尝试重连...")
            try:
                page = ChromiumPage(co)
                page.listen.start()
                page.get(f"https://tbzb.taobao.com/live?liveId={lid}", timeout=20)
            except:
                checked_ids.add(lid)
                continue
        digital_values = []
        title_values = []
        matched_detail_response = False
        for c in drain_responses(CHECK_TIMEOUT):
            if "live.detail.get" in c.get("url","") and "mtop" in c.get("url",""):
                body = c.get("body","")
                parsed = parse_json_body(body)
                if parsed is None:
                    continue
                # Require the current liveId; ignore a stale response from prior room.
                response_ids = [
                    str(value) for value in find_values_by_key(parsed, "liveId")
                    if value is not None
                ]
                if lid not in response_ids:
                    continue
                matched_detail_response = True
                digital_values.extend(
                    find_values_by_key(parsed, "isDigitalAnchorLive")
                )
                response_titles = find_values_by_key(parsed, "liveTitle")
                if not response_titles:
                    response_titles = find_values_by_key(parsed, "title")
                title_values.extend(response_titles)

        if matched_detail_response:
            checked_ids.add(lid)
        flag_summary = summarize_digital_flags(digital_values)
        title_matches = title_keywords_in_values(title_values)
        if title_matches or flag_summary["all_true"]:
            digital_urls.append(f"https://tbzb.taobao.com/live?liveId={lid}")
            save_urls()  # 立刻写盘，中断不丢
            basis = (
                f"标题命中{'、'.join(title_matches)}"
                if title_matches else "平台标记全 true"
            )
            log(
                f"  [{len(checked_ids)}] ✅ 数字人({basis}) "
                f"平台={flag_summary['raw_label']} {lid[:12]} 累计 {len(digital_urls)}"
            )
        elif flag_summary["has_false"]:
            log(
                f"  [{len(checked_ids)}] 含 false，按数字人样本口径排除 "
                f"平台={flag_summary['raw_label']} {lid[:12]}"
            )
        else:
            log(
                f"  [{len(checked_ids)}] ⚠️ 未确认，平台="
                f"{flag_summary['raw_label']} {lid[:12]}"
            )

if digital_urls:
    save_urls()
    log(f"✅ 共收集 {len(digital_urls)} 个数字人 URL → {URLS_FILE}")
else:
    log("⚠️ 未找到数字人直播间")

page.quit()
log("完成")
