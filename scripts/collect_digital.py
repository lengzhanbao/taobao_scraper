# -*- coding: utf-8 -*-
"""
Collect Digital Live URLs
Discovers and collects digital human live room URLs
Usage: python collect_digital.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
exec(open(os.path.join(os.path.dirname(__file__), "..", "src", "detector", "collect_digital.py"), encoding="utf-8").read())
