# -*- coding: utf-8 -*-
"""
Taobao Live Crawler - Instance 4
Usage: python crawler_instance_4.py
"""
import sys
import os
import runpy

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Run crawler with instance 4 config
sys.argv = [sys.argv[0], "urls_4.txt", "9226", "135"]
crawler = os.path.join(os.path.dirname(__file__), "..", "src", "crawler", "taobao_crawler.py")
runpy.run_path(os.path.abspath(crawler), run_name="__main__")
