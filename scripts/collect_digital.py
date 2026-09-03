# -*- coding: utf-8 -*-
"""
Collect Digital Live URLs
Discovers and collects digital human live room URLs
Usage: python collect_digital.py
"""
import sys
import os
import runpy
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
target = os.path.join(os.path.dirname(__file__), "..", "src", "detector", "collect_digital.py")
runpy.run_path(os.path.abspath(target), run_name="__main__")
