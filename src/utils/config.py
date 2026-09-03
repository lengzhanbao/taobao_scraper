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
