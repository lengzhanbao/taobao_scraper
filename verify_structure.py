#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Project Structure Verification Script
Verifies that all required files and directories are present
"""
import os
import sys

def check_file(path, required=True):
    """Check if file exists"""
    exists = os.path.isfile(path)
    status = "✓" if exists else ("✗" if required else "○")
    req_str = "(required)" if required else "(optional)"
    print(f"  {status} {path} {req_str if not exists and required else ''}")
    return exists or not required

def check_dir(path, required=True):
    """Check if directory exists"""
    exists = os.path.isdir(path)
    status = "✓" if exists else ("✗" if required else "○")
    req_str = "(required)" if required else "(optional)"
    print(f"  {status} {path}/ {req_str if not exists and required else ''}")
    return exists or not required

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=" * 60)
    print("  Taobao Live Scraper - Project Structure Verification")
    print("=" * 60)
    
    all_ok = True
    
    # Check core directories
    print("\n[Core Directories]")
    all_ok &= check_dir("src")
    all_ok &= check_dir("src/crawler")
    all_ok &= check_dir("src/parser")
    all_ok &= check_dir("src/detector")
    all_ok &= check_dir("src/utils")
    all_ok &= check_dir("src/control")
    all_ok &= check_dir("src/control/web")
    all_ok &= check_dir("tests")
    all_ok &= check_dir("scripts")
    all_ok &= check_dir("config")
    all_ok &= check_dir("docs")
    all_ok &= check_dir("examples")
    all_ok &= check_dir(".github")
    
    # Check core Python files
    print("\n[Core Python Files]")
    all_ok &= check_file("src/__init__.py")
    all_ok &= check_file("src/crawler/__init__.py")
    all_ok &= check_file("src/crawler/taobao_crawler.py")
    all_ok &= check_file("src/parser/__init__.py")
    all_ok &= check_file("src/parser/parse_data.py")
    all_ok &= check_file("src/detector/__init__.py")
    all_ok &= check_file("src/detector/collect_digital.py")
    all_ok &= check_file("src/detector/detect_digital.py")
    all_ok &= check_file("src/utils/__init__.py")
    all_ok &= check_file("src/utils/config.py")
    all_ok &= check_file("src/utils/update_urls.py")
    for path in ("src/control/__init__.py", "src/control/server.py", "src/control/settings.py",
                 "src/control/url_lists.py", "src/control/web/index.html", "src/control/web/app.js",
                 "src/control/web/style.css", "src/utils/segment_evidence.py",
                 "tests/test_control_simulation.py", "tests/test_control_ui.js",
                 "tests/test_segment_evidence.py", "tests/test_url_lists.py"):
        all_ok &= check_file(path)
    
    # Check script files
    print("\n[Script Files]")
    all_ok &= check_file("scripts/crawler_instance_1.py")
    all_ok &= check_file("scripts/crawler_instance_2.py")
    all_ok &= check_file("scripts/crawler_instance_3.py")
    all_ok &= check_file("scripts/crawler_instance_4.py")
    all_ok &= check_file("scripts/crawler_instance_5.py")
    all_ok &= check_file("scripts/start_all_crawlers.ps1")
    all_ok &= check_file("scripts/stop_all_crawlers.ps1")
    all_ok &= check_file("scripts/parse_data.py")
    all_ok &= check_file("scripts/update_urls.py")
    all_ok &= check_file("scripts/collect_digital.py")
    all_ok &= check_file("scripts/control_panel.py")
    all_ok &= check_file("scripts/open_control_panel.vbs")
    
    # Check documentation
    print("\n[Documentation]")
    all_ok &= check_file("README.md")
    all_ok &= check_file("LICENSE")
    all_ok &= check_file("CHANGELOG.md")
    all_ok &= check_file("CONTRIBUTING.md")
    all_ok &= check_file("PROJECT_OVERVIEW.md")
    all_ok &= check_file("docs/QUICKSTART.md")
    all_ok &= check_file("docs/ARCHITECTURE.md")
    all_ok &= check_file("docs/TROUBLESHOOTING.md")
    all_ok &= check_file("examples/EXAMPLES.md")
    all_ok &= check_file("config/README.md")
    
    # Check config files
    print("\n[Configuration Files]")
    all_ok &= check_file("requirements.txt")
    all_ok &= check_file(".gitignore")
    all_ok &= check_file("setup.py")
    all_ok &= check_file("setup_quick.py")
    all_ok &= check_file("config/urls_template.txt")
    all_ok &= check_file("config/cookies_template.json")
    
    # Check GitHub Actions
    print("\n[GitHub Actions]")
    all_ok &= check_file(".github/workflows/python-package.yml")
    
    # Check data directories (optional, will be created at runtime)
    print("\n[Data Directories (Optional)]")
    check_dir("直播研究数据", required=False)
    check_dir("直播研究数据/_config", required=False)
    check_dir("直播研究数据/_staging", required=False)
    check_dir("直播研究数据/sessions", required=False)
    check_dir("DouyinLiveRecorder_v4.0.7", required=False)
    
    # Test imports
    print("\n[Testing Imports]")
    try:
        sys.path.insert(0, os.getcwd())
        from src.utils.config import STUDY_ROOT, FFMPEG
        print("  ✓ Config import successful")
        print(f"    - STUDY_ROOT: {STUDY_ROOT}")
        print(f"    - FFMPEG: {FFMPEG}")
    except Exception as e:
        print(f"  ✗ Config import failed: {e}")
        all_ok = False
    
    # Summary
    print("\n" + "=" * 60)
    if all_ok:
        print("  ✓ All required files and directories are present!")
        print("  Project structure verification: PASSED")
        print("\n  Next steps:")
        print("  1. Run: python setup_quick.py")
        print("  2. Configure URLs and login")
        print("  3. Start crawlers")
    else:
        print("  ✗ Some required files or directories are missing!")
        print("  Project structure verification: FAILED")
        print("\n  Please check the errors above and fix them.")
    print("=" * 60)
    
    return 0 if all_ok else 1

if __name__ == '__main__':
    sys.exit(main())
