# -*- coding: utf-8 -*-
"""
Parse Taobao Live Data
Processes staged data and archives to sessions
Usage: python parse_data.py [room_dir]
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.parser.parse_data import main
if __name__ == "__main__":
    main()
