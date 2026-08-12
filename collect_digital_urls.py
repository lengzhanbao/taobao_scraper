# -*- coding: utf-8 -*-
"""
淘宝直播数字人 URL 收集器 v2
策略：发现页批量提取 liveId → 逐个加载检查 isDigitalAnchorLive
"""

import os, sys, time, json, re, random

from config import STUDY_ROOT
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

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
os.makedirs(OUTDIR, exist_ok=True)

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

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

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
        is_digital = None
        for c in drain_responses(CHECK_TIMEOUT):
            if "live.detail.get" in c.get("url","") and "mtop" in c.get("url",""):
                body = c.get("body","")
                # ⚠️ 关键修复：必须 liveId 匹配，防止读到上一个房间的残留响应
                m_lid = re.search(r'"liveId"\s*:\s*"(\d+)"', body)
                if not m_lid or m_lid.group(1) != lid:
                    continue
                m = re.search(r'"isDigitalAnchorLive"\s*:\s*"(true|false)"', body)
                if m:
                    is_digital = (m.group(1) == "true")
                    checked_ids.add(lid)
                    break
        if is_digital is True:
            digital_urls.append(f"https://tbzb.taobao.com/live?liveId={lid}")
            save_urls()  # 立刻写盘，中断不丢
            log(f"  [{len(checked_ids)}] ✅ 数字人 {lid[:12]} 累计 {len(digital_urls)}")
        elif is_digital is False:
            log(f"  [{len(checked_ids)}] ❌ 真人")
        else:
            log(f"  [{len(checked_ids)}] ⚠️ 超时")

if digital_urls:
    save_urls()
    log(f"✅ 共收集 {len(digital_urls)} 个数字人 URL → {URLS_FILE}")
else:
    log("⚠️ 未找到数字人直播间")

page.quit()
log("完成")
