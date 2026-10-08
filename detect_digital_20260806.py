# -*- coding: utf-8 -*-
"""Compatibility entry point for the shared detector.

All replies and all flags in the captured detection window are summarized.
The result describes that window only, not an entire subsequent recording.
"""
from src.detector.detect_digital import (
    DEFAULT_IDS as ID_LIST, DEFAULT_TXT as TXT_PATH,
    parse_live_response, aggregate_detail_responses, check_one,
    find_keys, as_bool, main,
)

if __name__ == "__main__":
    main()
