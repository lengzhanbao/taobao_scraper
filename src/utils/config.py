# -*- coding: utf-8 -*-
"""
Configuration module for Taobao Live Scraper
Handles all path and environment variable configurations
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 兼容旧引用：BASE_DIR 即项目根目录
PROJECT_ROOT = BASE_DIR


def _get_env_path(env_name, default_path):
    """Get path from environment variable or use default"""
    return os.environ.get(env_name, default_path)


def _get_env_int(env_name, default):
    """Get int from environment variable or use default"""
    try:
        return int(os.environ.get(env_name, str(default)))
    except (TypeError, ValueError):
        return default


# Data root directory
STUDY_ROOT = _get_env_path(
    "LIVE_STUDY_ROOT",
    os.path.join(BASE_DIR, "直播研究数据")
)

# FFmpeg executable path
FFMPEG = _get_env_path(
    "LIVE_FFMPEG",
    os.path.join(BASE_DIR, "DouyinLiveRecorder_v4.0.7", "ffmpeg", "ffmpeg.exe")
)

# Python executable
PYTHON = _get_env_path("LIVE_PYTHON", "python")

# Edge browser path (Windows default)
EDGE_PATH = _get_env_path(
    "LIVE_EDGE_PATH",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)

# Playwright core path for digital detection
PLAYWRIGHT_CORE_PATH = os.environ.get("LIVE_PLAYWRIGHT_CORE_PATH", None)

# Crawler tuning (override via environment variables)
MAX_MIN = _get_env_int("LIVE_MAX_MIN", 20)              # minutes recorded per segment
MAX_ROUND = _get_env_int("LIVE_MAX_ROUND", 3)           # segments per room (urls_4 uses 4 in file)
COOLDOWN_SEC = _get_env_int("LIVE_COOLDOWN_SEC", 120 * 60)
PRODUCT_MIN_SEC = _get_env_int("LIVE_PRODUCT_MIN_SEC", 0)
MAX_COLLECTED = _get_env_int("LIVE_MAX_COLLECTED", 800)
SEG_NAMES = ["第一段", "第二段", "第三段"]

# Browser user agent
USER_AGENT = os.environ.get(
    "LIVE_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
