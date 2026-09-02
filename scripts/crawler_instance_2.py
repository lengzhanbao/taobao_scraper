# -*- coding: utf-8 -*-
"""
Taobao Live Crawler - Instance 2
Usage: python crawler_instance_2.py
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import and run crawler with instance 2 config
sys.argv = [sys.argv[0], "urls_2.txt", "9224", "45"]
exec(open(os.path.join(os.path.dirname(__file__), "..", "src", "crawler", "taobao_crawler.py"), encoding="utf-8").read())
