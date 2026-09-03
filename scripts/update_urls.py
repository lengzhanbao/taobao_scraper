# -*- coding: utf-8 -*-
"""
Update URLs Status
Updates recording status based on completed segments
Usage: python update_urls.py [--apply]
"""
import sys
import os
import runpy
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
target = os.path.join(os.path.dirname(__file__), "..", "src", "utils", "update_urls.py")
runpy.run_path(os.path.abspath(target), run_name="__main__")
