# -*- coding: utf-8 -*-
"""
Taobao Live Crawler - Instance 1
Usage: python crawler_instance_1.py
"""
import os
import runpy
import sys

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Run crawler with instance 1 config: urls_1.txt, port 9223, no start delay
sys.argv = [sys.argv[0], "urls_1.txt", "9223", "0"]
crawler = os.path.join(os.path.dirname(__file__), "..", "src", "crawler", "taobao_crawler.py")
runpy.run_path(os.path.abspath(crawler), run_name="__main__")
