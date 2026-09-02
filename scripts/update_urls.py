# -*- coding: utf-8 -*-
"""
Update URLs Status
Updates recording status based on completed segments
Usage: python update_urls.py [--apply]
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
exec(open(os.path.join(os.path.dirname(__file__), "..", "src", "utils", "update_urls.py"), encoding="utf-8").read())
