#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quick Setup Script for Taobao Live Scraper
This script helps you set up the project quickly
"""
import os
import sys
import subprocess

def print_step(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print('='*60)

def run_command(cmd, check=True):
    """Run shell command"""
    try:
        result = subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e}")
        if e.stderr:
            print(e.stderr)
        return False

def create_directories():
    """Create necessary directories"""
    dirs = [
        "直播研究数据/_config",
        "直播研究数据/_staging",
        "直播研究数据/sessions",
        "DouyinLiveRecorder_v4.0.7/ffmpeg",
        "_logs"
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    print("✓ Directories created")

def create_url_files():
    """Create URL template files"""
    for i in range(1, 6):
        max_rounds = 4 if i == 4 else 3
        filepath = f"直播研究数据/_config/urls_{i}.txt"
        if not os.path.exists(filepath):
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"# URL配置文件 {i}\n")
                f.write(f"# 格式: https://tbzb.taobao.com/live?liveId=XXXXX,店铺名,已录制0/{max_rounds}\n\n")
    print("✓ URL template files created")

def check_python():
    """Check Python version"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 9):
        print(f"ERROR: Python 3.9+ required, found {version.major}.{version.minor}")
        return False
    print(f"✓ Python {version.major}.{version.minor}.{version.micro}")
    return True

def install_dependencies():
    """Install Python dependencies"""
    print("\nInstalling dependencies...")
    return run_command(f"{sys.executable} -m pip install -r requirements.txt")

def check_ffmpeg():
    """Check FFmpeg availability"""
    if run_command("ffmpeg -version", check=False):
        print("✓ FFmpeg found")
        return True
    else:
        print("⚠ FFmpeg not found. Please install FFmpeg manually.")
        print("  Windows: Download from https://ffmpeg.org/download.html")
        print("  Linux: sudo apt install ffmpeg")
        print("  macOS: brew install ffmpeg")
        return False

def main():
    print_step("Taobao Live Scraper - Quick Setup")
    
    # Check Python version
    print_step("Step 1: Checking Python version")
    if not check_python():
        sys.exit(1)
    
    # Create directories
    print_step("Step 2: Creating directories")
    create_directories()
    create_url_files()
    
    # Install dependencies
    print_step("Step 3: Installing dependencies")
    if not install_dependencies():
        print("\n⚠ Warning: Some dependencies failed to install")
        print("  You may need to install them manually")
    
    # Check FFmpeg
    print_step("Step 4: Checking FFmpeg")
    check_ffmpeg()
    
    # Final instructions
    print_step("Setup Complete!")
    print("\nNext steps:")
    print("1. Configure URLs in: 直播研究数据/_config/urls_1.txt ~ urls_5.txt")
    print("2. Login and save cookies:")
    print("   python scripts/crawler_instance_1.py")
    print("3. Start all crawlers:")
    print("   powershell -ExecutionPolicy Bypass -File scripts\\start_all_crawlers.ps1")
    print("\nFor more information, see README.md")

if __name__ == '__main__':
    main()
