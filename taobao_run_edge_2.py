# -*- coding: utf-8 -*-
"""
淘宝直播 Edge — 参数化多实例版
用法: python taobao_run_edge.py urls_2.txt 9223
- 每个实例读自己的 URL 文件、自己的端口、自己的输出目录
- 随机抽 → 录20分 → 冷却2h → 每房间3轮
"""
import os, sys, time, json, re, subprocess, threading, datetime, random

if len(sys.argv) < 3:
    print("用法: python taobao_run_edge.py <urls文件名> <端口> [启动延迟秒数]")
    print("例如: python taobao_run_edge.py urls_2.txt 9223 30")
    sys.exit(1)

# 控制台编码兜底：输出重定向到文件时 Python 会退回 GBK，emoji 日志会崩（必须在任何 print 之前）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DELAY = int(sys.argv[3]) if len(sys.argv) > 3 else 0
if DELAY:
    print(f"⏳ 延迟 {DELAY}s 启动..."); time.sleep(DELAY)

from config import STUDY_ROOT, FFMPEG
URLS_FILE = os.path.join(STUDY_ROOT, "_config", sys.argv[1])
PORT = int(sys.argv[2])
COOKIE_JSON = os.path.join(STUDY_ROOT, "_config", "taobao_cookies.json")
OUTDIR = os.path.join(STUDY_ROOT, "_staging", f"browser_{PORT}")
MAX_MIN = 20
MAX_ROUND = 3
PRODUCT_MIN_SEC = 0  # 0=商品切换只记录不早停
COOKIE_TXT = os.path.join(STUDY_ROOT, "_config", f"taobao_cookies_{PORT}.txt")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

os.makedirs(OUTDIR, exist_ok=True)

# 启动时清理残留垃圾（无 _final.json 的目录全删）
def clean_staging():
    if os.path.isdir(OUTDIR):
        for d in os.listdir(OUTDIR):
            dpath = os.path.join(OUTDIR, d)
            if not os.path.isdir(dpath) or not d.startswith("room_"):
                continue
            for seg in os.listdir(dpath):
                sp = os.path.join(dpath, seg)
                if not os.path.isdir(sp):
                    continue
                if any(f.endswith("_final.json") for f in os.listdir(sp)):
                    continue
                # seg-level: flv >= 50MB (~7min) valid -> rename keep; else delete whole seg
                valid_flv = False
                any_data = False
                for f in os.listdir(sp):
                    fp = os.path.join(sp, f)
                    if f.lower().endswith(".flv") and "待删除" not in f:
                        any_data = True
                        try:
                            if os.path.getsize(fp) >= 50 * 1024 * 1024:
                                valid_flv = True
                        except OSError:
                            pass
                    if f.startswith("data_"):
                        any_data = True
                if valid_flv:
                    try:
                        os.rename(sp, sp + ".待清理")
                        log("[clean] keep valid incomplete seg: " + seg)
                    except Exception:
                        pass
                elif any_data:
                    import shutil
                    shutil.rmtree(sp, ignore_errors=True)
                    log("[clean] remove <7min seg: " + seg)

state = {"collected": [], "stream_url": {"url": None}}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
SEG_NAMES = ["第一段", "第二段", "第三段"]


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

def solve_slider(page):
    """auto solve taobao SLIDE captcha: drag handle to right with human-like track"""
    try:
        w = page.ele('#nc_1_wrapper', timeout=1)
        if not w:
            return False
        btn = page.ele('#nc_1_n1z', timeout=2)
        if not btn:
            return False
        log("  [captcha] slider found, drag")
        page.actions.hold(btn)
        time.sleep(0.25)
        steps = 20
        moved = 0
        for i in range(1, steps + 1):
            t = i / steps
            ease = 1 - (1 - t) ** 3
            target = int(258 * ease)
            dx = target - moved
            dy = random.randint(-1, 1)
            if dx > 0:
                page.actions.move(dx, dy, duration=random.uniform(0.02, 0.05))
                moved = target
            time.sleep(random.uniform(0.005, 0.02))
        time.sleep(0.15)
        page.actions.release()
        time.sleep(2)
        w2 = page.ele('#nc_1_wrapper', timeout=2)
        if w2:
            log("  [captcha] still there, retry")
            return False
        log("  [captcha] passed")
        return True
    except Exception:
        return False

REAL_LOGIN = {"unb", "sgcookie", "thb"}
def logged_in():
    try: return bool(REAL_LOGIN & {c.get("name") for c in page.cookies()})
    except: return False


def login_expired():
    """扫描最近捕获的响应，判断登录态是否失效（SESSION_EXPIRED 等特征）"""
    try:
        for c in state["collected"][-80:]:
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
            resp = page.listen.wait(timeout=2)
        except:
            continue
        if resp is None or resp is False:
            continue
        url = (resp.url or "").lower()
        if "live.detail.get" in url:
            try:
                body = resp.response.body
                if isinstance(body, str):
                    m = re.search(r'"liveUrl"\s*:\s*"([^"]+)"', body)
                    if m:
                        state["stream_url"]["url"] = m.group(1)
                        state["stream_url"]["capture_time"] = time.time()
            except:
                pass
        if (".ts" not in url) and (".m3u8" not in url) and any(k in url for k in ["mtop","comment","item","viewer","like","fans","gmv","interact","detail"]):
            if url.endswith(".js") or "data:" in url or "woff" in url or "mtop.js" in url:
                continue
            try:
                body = resp.response.body
                if isinstance(body, dict):
                    snippet = json.dumps(body, ensure_ascii=False)[:800000]
                elif isinstance(body, str):
                    snippet = body[:800000]
                else:
                    snippet = str(body)[:800000]
            except:
                snippet = None
            state["collected"].append({"t": round(time.time(),1), "url": url, "body": snippet})

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
            total = int(m2.group(2))
        else:
            count = 0
            total = MAX_ROUND
        out.append((clean, lid, count, total))
    return out

def scan_room(url, live_id):
    state["collected"] = []
    state["stream_url"]["url"] = None
    try:
        page.get(url, timeout=30)
    except:
        return False
    time.sleep(3)  # 多等一会页面加载
    solve_slider(page)
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
    nav_t = time.time()
    dl = nav_t + 30  # 多等一会
    while state["stream_url"]["url"] is None and time.time() < dl:
        if state["stream_url"]["url"] and state["stream_url"].get("capture_time",0) < nav_t:
            state["stream_url"]["url"] = None
        if state["stream_url"]["url"]:
            valid = False
            for c in state["collected"]:
                if "live.detail.get" in (c.get("url") or "") and live_id in (c.get("body") or ""):
                    valid = True; break
            if not valid:
                state["stream_url"]["url"] = None
        time.sleep(1)
    if not state["stream_url"]["url"]:
        return False
    # 数字人确认
    dig = None
    for c in state["collected"]:
        if "live.detail.get" in (c.get("url") or ""):
            try:
                body = c.get("body","")
                if isinstance(body, str):
                    m = re.search(r'"isDigitalAnchorLive"\s*:\s*"(true|false)"', body)
                    if m:
                        dig = (m.group(1)=="true"); break
            except:
                pass
    if dig is not True:
        return False
    return True

def record_room(url, live_id, room_dir, surl, seg_name):
    """录制，录完存到 seg_name（第一段/第二段...）"""
    seg_dir = os.path.join(room_dir, seg_name)
    # 如果这个段已存在但没有 final → 是上次失败的垃圾，清掉
    if os.path.isdir(seg_dir):
        has_final = any(f.endswith("_final.json") for f in os.listdir(seg_dir))
        if not has_final:
            for f in os.listdir(seg_dir):
                try: os.remove(os.path.join(seg_dir, f))
                except: pass
    os.makedirs(seg_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # 提取首产品
    current_pid = None
    current_name = current_price = current_promo_price = current_category = ""
    current_view = current_like = current_fans = ""
    for c in state["collected"]:
        if "live.detail.get" in (c.get("url") or ""):
            try:
                body = c.get("body","")
                if isinstance(body, str) and live_id in body:
                    m = re.search(r'"itemId"\s*:\s*"(\d+)"', body)
                    if m:
                        current_pid = m.group(1)
                        mn = re.search(r'"itemName"\s*:\s*"([^"]+)"', body)
                        current_name = mn.group(1)[:60] if mn else ""
                        mp = re.search(r'"itemPrice"\s*:\s*"([\d.]+)"', body)
                        current_price = mp.group(1) if mp else ""
                        mpromo = re.search(r'"liveItemPrice"\s*:\s*\{[^}]*"promotionPrice"\s*:\s*"(\d+)"', body)
                        current_promo_price = mpromo.group(1) if mpromo else ""
                        mcat = re.search(r'"categoryLevelOneName"\s*:\s*"([^"]+)"', body)
                        current_category = mcat.group(1) if mcat else ""
                        mview = re.search(r'"viewCount"\s*:\s*"?(\d+)"?', body)
                        current_view = mview.group(1) if mview else ""
                        break
            except:
                pass
    if not current_pid:
        current_pid = "pending"
        current_name = "待检测"
    log(f"  {seg_name} 首商品: {current_name[:30]}")

    rec_path = os.path.join(seg_dir, f"record_{ts}.flv")
    record_start = time.time()

    ck = get_cookie_header()
    headers = f"Referer: {url}\r\nUser-Agent: {UA}\r\n"
    if ck: headers += f"Cookie: {ck}\r\n"
    cmd = [FFMPEG, "-y",
           "-reconnect", "1", "-reconnect_streamed", "1",
           "-reconnect_on_network_error", "1",
           "-reconnect_delay_max", "10", "-reconnect_at_eof", "1",
           "-rw_timeout", "10000000",
           "-headers", headers, "-i", surl, "-c", "copy",
           "-t", str(MAX_MIN * 60 + 60), rec_path]
    log(f"  录制: {surl.split('/')[2]}")
    # CDN 重试：有时连上但没数据，重试 2 次
    ok, ffproc, ferr = False, None, None
    for attempt in range(3):
        ferr = open(os.path.join(seg_dir, f"ffmpeg_{ts}.log"), "w", encoding="utf-8")
        ffproc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=ferr)
        # 等 15 秒看 FLV 有没有生成
        waited = 0
        while waited < 30:
            time.sleep(5)
            waited += 5
            if os.path.exists(rec_path) and os.path.getsize(rec_path) > 1024:
                ok = True
                break
        if ok:
            log(f"  流已接通（尝试 {attempt+1}）")
            break
        log(f"  尝试 {attempt+1}: FLV 未生成，重试...")
        ffproc.kill(); ffproc.wait(timeout=5)
        time.sleep(3)
    if not ok:
        log(f"  视频未生成（CDN 失败）")
        return False
    current_start = 0.0
    prev_len = len(state["collected"])
    next_reload = time.time() + 300
    product_timeline = []
    ok = True

    try:
        last_flv_size = 0
        last_flv_check = time.time()
        while True:
            time.sleep(5)
            solve_slider(page)
            now = time.time()
            elapsed = now - record_start
            if elapsed > 30 and not (os.path.exists(rec_path) and os.path.getsize(rec_path) > 0):
                log(f"  视频未生成"); ok = False; break
            if elapsed >= MAX_MIN * 60:
                log(f"  达 {MAX_MIN} 分上限"); break
            # CDN 掉线检测：30 秒没涨就杀 ffmpeg 重连
            if now - last_flv_check >= 30:
                cur_size = os.path.getsize(rec_path) if os.path.exists(rec_path) else 0
                if cur_size > 0 and cur_size == last_flv_size and elapsed > 60:
                    log(f"  ⚠️ FLV 停止增长 at {elapsed:.0f}s，尝试重连...")
                    ffproc.kill(); ffproc.wait(timeout=5)
                    time.sleep(3)
                    ferr = open(os.path.join(seg_dir, f"ffmpeg_{ts}.log"), "a", encoding="utf-8")
                    ffproc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=ferr)
                last_flv_size = cur_size
                last_flv_check = now
            if now >= next_reload:
                try: page.get(url, timeout=30); time.sleep(4)
                except: pass
                next_reload = now + 300
            # 增量存档
            data_path = os.path.join(seg_dir, f"data_{ts}.json")
            try:
                json.dump({"live_url": url, "stream_url": surl,
                    "recorded_files": [rec_path] if os.path.exists(rec_path) else [],
                    "record_start_t": record_start, "record_end_t": now,
                    "product_timeline": product_timeline,
                    "captured_count": len(state["collected"]),
                    "responses": state["collected"],
                }, open(data_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            except: pass
            # 产品检测
            new_resps = state["collected"][prev_len:]
            prev_len = len(state["collected"])
            for c in new_resps:
                if "live.detail.get" in (c.get("url") or ""):
                    try:
                        body = c.get("body","")
                        if isinstance(body, str) and live_id in body:
                            m = re.search(r'"itemId"\s*:\s*"(\d+)"', body)
                            if not m: continue
                            pid = m.group(1)
                            if current_pid == "pending" and pid != "pending":
                                current_pid = pid
                                mn = re.search(r'"itemName"\s*:\s*"([^"]+)"', body)
                                if mn: current_name = mn.group(1)[:60]
                                mp = re.search(r'"itemPrice"\s*:\s*"([\d.]+)"', body)
                                if mp: current_price = mp.group(1)
                                current_start = elapsed
                                log(f"  检测到商品: {current_name[:30]} ¥{current_price}")
                                continue
                            if pid != current_pid and elapsed >= PRODUCT_MIN_SEC:
                                product_timeline.append({
                                    "itemId": current_pid, "name": current_name,
                                    "price": current_price,
                                    "start_sec": round(current_start,1), "end_sec": round(elapsed,1),
                                })
                                log(f"  切产品 at {elapsed:.0f}s（记录，继续录）")
                                # 切换到新商品继续录制，不早停
                                current_pid = pid
                                mn = re.search(r'"itemName"\s*:\s*"([^"]+)"', body)
                                if mn: current_name = mn.group(1)[:60]
                                mp = re.search(r'"itemPrice"\s*:\s*"([\d.]+)"', body)
                                if mp: current_price = mp.group(1)
                                current_start = elapsed
                                continue
                    except: pass

    finally:
        try: ffproc.kill(); ffproc.wait(timeout=10)
        except: pass
        try: ferr.close()
        except: pass

    if ok and os.path.exists(rec_path) and os.path.getsize(rec_path) > 0:
        # 正常完成 → 写 final
        try:
            json.dump({
                "live_url": url, "stream_url": surl,
                "recorded_files": [rec_path],
                "record_start_t": record_start, "record_end_t": time.time(),
                "product_timeline": product_timeline,
                "captured_count": len(state["collected"]),
                "responses": state["collected"],
            }, open(os.path.join(seg_dir, f"data_{ts}_final.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
            log(f"  ✅ {seg_name} 完成: {os.path.getsize(rec_path)/1024/1024:.0f}MB")
        except: pass
        return True
    else:
        # 录制异常但超过7分钟且FLV有数据 → 保留算有效
        elapsed = time.time() - record_start
        if elapsed >= 420 and os.path.exists(rec_path) and os.path.getsize(rec_path) > 0:
            log(f"  💾 {elapsed:.0f}s 中断但保留（>=7分钟）")
            try:
                json.dump({
                    "live_url": url, "stream_url": surl,
                    "recorded_files": [rec_path],
                    "record_start_t": record_start, "record_end_t": time.time(),
                    "product_timeline": product_timeline,
                    "captured_count": len(state["collected"]),
                    "responses": state["collected"],
                }, open(os.path.join(seg_dir, f"data_{ts}_final.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
                return True
            except: pass
        # 录制失败 → 清空这一段的所有垃圾文件
        log(f"  🧹 清理失败段: {seg_name}")
        for f in list(os.listdir(seg_dir)):
            try: os.remove(os.path.join(seg_dir, f))
            except: pass
        try: os.rmdir(seg_dir)
        except: pass
        return False

def mark_recorded(lid, count, total):
    """更新 urls 计数：先每日备份一次，再原子写入（临时文件+os.replace），防崩溃损坏"""
    path = URLS_FILE
    lines = open(path, encoding="utf-8").readlines()
    tag = f"已录制{count}/{total}"
    for i, line in enumerate(lines):
        if f"liveId={lid}" in line:
            if "待录制" in line:
                lines[i] = line.replace("待录制", tag)
            elif "已录制" in line:
                lines[i] = line.rsplit(",", 1)[0] + f",{tag}\n"
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

def finalize_room(lid):
    """房间录满 → 调 parse(复制JSON+FLV到sessions, 生成CSV) → 删staging FLV"""
    room_dir = os.path.join(OUTDIR, f"room_{lid}")

    # 调用 parse_taobao_data.py（会复制FLV到sessions/video）
    parse_script = os.path.join(os.path.dirname(STUDY_ROOT), "parse_taobao_data.py")
    parse_ok = False
    if os.path.exists(parse_script):
        try:
            result = subprocess.run(
                [sys.executable, parse_script, room_dir],
                capture_output=True, timeout=600, cwd=STUDY_ROOT
            )
            out = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            log(f"  📊 parse: {out[-150:] if out else 'OK'}")
            if "VIDEO_COPY_OK" in out:  # 全部FLV复制成功才删staging
                parse_ok = True
        except Exception as e:
            log(f"  ⚠️ parse 失败: {e}")

    # 确认FLV复制成功后才删staging中的FLV（保留JSON）
    if parse_ok:
        deleted = 0
        for root, dirs, files in os.walk(room_dir):
            for f in files:
                if f.endswith(".flv"):
                    try:
                        os.remove(os.path.join(root, f))
                        deleted += 1
                    except: pass
        log(f"  🧹 清理staging FLV: {deleted}个 (JSON保留)")
    log(f"  🏁 room_{lid} 完成")

# ========== 主流程 ==========
log(f"ffmpeg={os.path.exists(FFMPEG)}")

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
    _j.dump({"browser":{"has_seen_welcome_page":True,"suppress_first_run_default_browser_prompt":True}},
            open(os.path.join(pref_dir, "Preferences"), "w"))
except: pass

co = ChromiumOptions()
co.headless(False)
co.set_argument("--no-sandbox")
co.set_argument("--disable-gpu")
co.set_argument("--disable-blink-features=AutomationControlled")
co.set_argument("--window-size=1440,900")
co.set_argument("--window-position=-32000,-32000")
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
co.set_browser_path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
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
    saved = json.load(open(COOKIE_JSON, encoding="utf-8"))
    page.set.cookies(saved); page.refresh(); time.sleep(3)
    if logged_in(): log("Cookie 注入成功")
    else:
        log("请手动登录后按回车..."); input()

page.listen.start("")
threading.Thread(target=listen_loop, daemon=True).start()

# ---- 主循环：随机抽 + 冷却 ----
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
        pending_finalize.append(lid)   # 全新满段 → 正常批量归档
    elif n_sess < count:
        suspect_finalize.append(lid)   # 有额外未归档段 → 人工确认
if pending_finalize:
    log(f"📋 启动发现 {len(pending_finalize)} 间已满段，待归档（已跳过 session 已归档房间）")
    if len(pending_finalize) >= 6:
        log(f"\n📦 批量归档 {len(pending_finalize)} 间...")
        for flid in pending_finalize:
            finalize_room(flid)
        pending_finalize.clear()
if suspect_finalize:
    log(f"⚠️ {len(suspect_finalize)} 间 staging 有未归档段且 session 已存在（不自动 parse，需人工确认）: {suspect_finalize}")

while True:
    now = time.time()
    # 优先挑已录过+冷却完的，其次新房
    hot = []   # 已录过且冷却完了（需要第2/3段）
    cold = []  # 全新的（需要第1段）
    for url, lid, count, total in url_pool:
        if count >= total:
            continue
        last = last_record.get(lid, 0)
        if now - last < 120 * 60:
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
            earliest = min(times) + 120 * 60 - now
            if earliest > 0:
                wait = min(earliest, 60)
        # 检查是否都满了
        if not times:
            remaining = sum(1 for _, _, c, t in url_pool if c < t)
            if remaining == 0:
                log("🎉 本实例所有房间已录满")
                break
        time.sleep(wait)
        continue

    url, lid, count, total = random.choice(candidates)
    seg_name = SEG_NAMES[count] if count < len(SEG_NAMES) else f"第{count+1}段"
    log(f"\n🎯 {seg_name} room_{lid}")

    # 扫码
    try: ok_scan = scan_room(url, lid)
    except:
        log("  扫码异常"); last_record[lid] = now; time.sleep(5); continue
    if not ok_scan:
        log("  不可录"); last_record[lid] = now
        if login_expired():
            log("  ⚠️ 检测到登录失效，尝试重新注入 Cookie...")
            refresh_login()
        time.sleep(5); continue

    # 录制
    room_dir = os.path.join(OUTDIR, f"room_{lid}")
    surl = state["stream_url"].get("url", "")
    rec_start_t = time.time()
    try:
        ok = record_room(url, lid, room_dir, surl, seg_name)
    except Exception as e:
        log(f"  录制异常: {e}")
        continue

    if ok:
        new_count = count + 1
        mark_recorded(lid, new_count, total)
        elapsed = time.time() - rec_start_t
        if elapsed < MAX_MIN * 60:
            pad = MAX_MIN * 60 - elapsed
            log(f"  ⏳ 填满 {MAX_MIN}分槽位，等 {pad:.0f}s")
            time.sleep(pad)
        log(f"📀 room_{lid} {new_count}/{total} 轮")
        if new_count >= total:
            pending_finalize.append(lid)
            log(f"  📋 加入归档队列 ({len(pending_finalize)}/6)")
            if len(pending_finalize) >= 6:
                log(f"\n📦 批量归档 {len(pending_finalize)} 间...")
                for flid in pending_finalize:
                    finalize_room(flid)
                pending_finalize.clear()
                log(f"   ✅ 批量归档完成\n")
        last_record[lid] = rec_start_t
        url_pool = read_urls()  # 重载
    else:
        log(f"  room_{lid} 录制失败")
        if login_expired():
            log("  ⚠️ 检测到登录失效，尝试重新注入 Cookie...")
            refresh_login()
        # 清理空房间目录
        try:
            if os.path.isdir(room_dir) and not os.listdir(room_dir):
                os.rmdir(room_dir)
        except: pass
        last_record[lid] = now
