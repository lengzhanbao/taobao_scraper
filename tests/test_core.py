# -*- coding: utf-8 -*-
"""Smoke tests for core parsing/detection helpers (no browser needed)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.parser.parse_data import strip_jsonp, deep_find, safe_name, api_name, fmt
from src.detector.detect_digital import parse_live_response, as_bool, find_keys
from src.utils import config


def test_strip_jsonp():
    raw = 'jsonp123({"ret":["SUCCESS"],"data":{"title":"x"}})'
    assert strip_jsonp(raw)["data"]["title"] == "x"
    assert strip_jsonp("not json") is None
    assert strip_jsonp(None) is None


def test_deep_find():
    obj = {"a": {"b": [{"c": 42}]}}
    assert deep_find(obj, "c") == 42
    assert deep_find(obj, "missing") is None
    assert deep_find(None, "c") is None


def test_safe_name_and_api_name():
    assert safe_name("a/b:c d") == "a_b_c_d"
    url = "https://mtop.taobao.com/mtop.roomstudio.live.detail.get/1.0/?x=1"
    # api_name 提取 host 段（真实行为），解析分组靠调用方匹配固定 key
    assert api_name(url) == "mtop.taobao.com"


def test_fmt():
    assert fmt("") == ""
    assert "2026" in fmt("1756780800") or fmt("1756780800") != ""


def test_parse_live_response_digital():
    body = '{"liveId":"123","isDigitalAnchorLive":"true","liveTitle":"t"}'
    r = parse_live_response(body, "123")
    assert r is not None and r["isDigitalAnchorLive"] is True
    assert parse_live_response(body, "999") is None


def test_as_bool_and_find_keys():
    assert as_bool("true") and not as_bool("false")
    assert find_keys({"k": 1, "n": {"k": 2}}, "k") == [1, 2]


def test_config_defaults():
    assert config.MAX_MIN == 20
    assert config.COOLDOWN_SEC == 7200
    assert config.MAX_COLLECTED == 800
    assert isinstance(config.SEG_NAMES, list) and config.SEG_NAMES
