# -*- coding: utf-8 -*-
"""本地仪表盘服务：实时采集 5 实例状态 -> http://127.0.0.1:8765"""
import json, os, subprocess, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "_logs")
STAGING = os.path.join(ROOT, "直播研究数据", "_staging")
SESSIONS = os.path.join(ROOT, "直播研究数据", "sessions")
PORTS = [9223, 9224, 9225, 9226, 9227]
HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
_last_flv = {}

def get_python_cmds():
    out = {}
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }"],
            capture_output=True, text=True, timeout=15)
        for line in ps.stdout.splitlines():
            if "|" in line:
                pid, cmd = line.split("|", 1)
                out[pid] = cmd
    except Exception:
        pass
    return out

def cdp_alive(port):
    """用 CDP 探测实例是否真正在线（浏览器起来了才算活）"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False

def get_ffmpeg_count():
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process ffmpeg -ErrorAction SilentlyContinue).Count"],
            capture_output=True, text=True, timeout=15)
        return int(ps.stdout.strip() or 0)
    except Exception:
        return -1

def latest_flv(port):
    latest = None
    base = os.path.join(STAGING, f"browser_{port}")
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            for f in files:
                if f.endswith(".flv"):
                    fp = os.path.join(root, f)
                    try:
                        st = os.stat(fp)
                        if latest is None or st.st_mtime > latest[1]:
                            latest = (fp, st.st_mtime, st.st_size)
                    except Exception:
                        pass
    return latest

def finals_count(port):
    n = 0
    base = os.path.join(STAGING, f"browser_{port}")
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            n += sum(1 for f in files if f.endswith("_final.json"))
    return n

def log_tail(port, n=10):
    import glob
    cands = [p for p in glob.glob(os.path.join(LOGS, f"edge*{port}*.log"))
             if os.path.getsize(p) > 0]
    if not cands:
        return ""
    p = max(cands, key=os.path.getmtime)
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception:
        return ""

def events_tail(n=14):
    p = os.path.join(LOGS, "watch_events.log")
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception:
        return ""

def collect():
    cmds = get_python_cmds()
    insts = []
    for port in PORTS:
        n = PORTS.index(port) + 1
        key = f"taobao_run_edge_{n}.py"
        alive = cdp_alive(port)
        flv = latest_flv(port)
        now = time.time()
        size = flv[2] if flv else 0
        age = (now - flv[1]) / 60 if flv else None
        prev = _last_flv.get(port)
        rate = None
        if flv:
            if prev and now - prev[0] > 0:
                rate = (size - prev[2]) / (now - prev[0])
            _last_flv[port] = (now, flv[1], size)
        seg_dir = os.path.basename(os.path.dirname(flv[0])) if flv else ""
        room_dir = os.path.basename(os.path.dirname(os.path.dirname(flv[0]))) if flv else ""
        insts.append({
            "port": port, "n": n, "alive": alive,
            "room": room_dir,
            "seg": seg_dir,
            "flv_name": os.path.basename(flv[0]) if flv else "",
            "flv_mb": round(size / 1048576, 1) if flv else 0,
            "flv_age_min": round(age, 1) if age is not None else None,
            "rate_kbs": round(rate / 1024) if rate else None,
            "finals": finals_count(port),
            "log": log_tail(port),
        })
    return {
        "time": time.strftime("%H:%M:%S"),
        "sessions": len([d for d in os.listdir(SESSIONS) if os.path.isdir(os.path.join(SESSIONS, d))]) if os.path.isdir(SESSIONS) else 0,
        "ffmpeg": get_ffmpeg_count(),
        "instances": insts,
        "events": events_tail(),
    }

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            body = json.dumps(collect(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            try:
                with open(HTML_PATH, "rb") as f:
                    body = f.read()
            except Exception:
                body = b"dashboard.html not found"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    print("dashboard on http://127.0.0.1:8765", flush=True)
    HTTPServer(("127.0.0.1", 8765), H).serve_forever()
