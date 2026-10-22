# -*- coding: utf-8 -*-
"""Smoke tests for core parsing/detection helpers (no browser needed)."""
import os
import sys
import ast
import json
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.parser.parse_data import strip_jsonp, deep_find, safe_name, api_name, fmt
from src.detector.detect_digital import parse_live_response, as_bool, find_keys
from src.utils import config
from src.crawler.recording import copy_scan_journal, product_in, record_segment
from src.crawler.response_attribution import attribution_status, detail_live_urls
from src.utils.digital_flags import find_values_by_key, parse_json_body, summarize_digital_flags, title_keywords_in_values
from src.parser.parse_data import response_for_room_analysis


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


def test_scan_journal_keeps_full_body_and_preview_metadata(tmp_path):
    body = '{"liveId":"123","payload":"' + ("x" * 800010) + '"}'
    source = tmp_path / "scan.jsonl"
    target = tmp_path / "segment.jsonl"
    response = {"url": "mtop.roomstudio.live.detail.get", "body": body,
                "body_truncated": False, "preview_truncated": True,
                "body_original_chars": len(body)}
    source.write_text(json.dumps(response, ensure_ascii=False) + "\n", encoding="utf-8")
    with target.open("w", encoding="utf-8") as output:
        assert copy_scan_journal(source, output)
    copied = json.loads(target.read_text(encoding="utf-8"))
    assert copied["body"] == body
    assert copied["body_truncated"] is False
    assert copied["preview_truncated"] is True
    assert copied["body_original_chars"] == len(body)


def test_product_metadata_survives_bounded_preview():
    response = {"url": "mtop.roomstudio.live.detail.get", "body": "{" + ("x" * 800000),
                "live_ids": ["123"],
                "products": [{"itemId": "987", "name": "商品", "price": "12.5"}]}
    assert product_in(response, "123") == {"itemId": "987", "name": "商品", "price": "12.5"}


def _crawler_capture_namespace(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src" / "crawler" / "taobao_crawler.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"wait_for_human_captcha", "listen_loop", "scan_room"}
    funcs = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    ns = {"time": time, "threading": threading, "json": json, "re": __import__("re"),
          "os": os, "Path": Path, "capture_lock": threading.Lock(),
          "BODY_PREVIEW_CHARS": 800000, "MAX_COLLECTED": 800,
          "RUNTIME": {"max_collected": 800, "listen_timeout_seconds": 2,
                      "scan_timeout_seconds": 30, "connect_timeout_seconds": 30,
                      "captcha_timeout_seconds": 180},
          "OUTDIR": str(tmp_path), "state": {"collected": [], "stream_url": {"url": None},
                  "sequence": 0, "journal": None, "live_id": None, "navigation_id": None,
                  "scan_journal_path": None, "journal_error": False},
          "sink_lock": threading.Lock(), "status_data": {}, "RUN_ID": "test",
          "parse_json_body": parse_json_body, "find_values_by_key": find_values_by_key,
          "summarize_digital_flags": summarize_digital_flags,
          "title_keywords_in_values": title_keywords_in_values,
          "attribution_status": attribution_status, "detail_live_urls": detail_live_urls,
          "publish_status": lambda *args, **kwargs: None, "process_pending_command": lambda: None,
          "stop_requested": lambda: False, "log": lambda *args: None,
          "sys": sys}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(source), "exec"), ns)
    return ns


class _CaptureEnd(BaseException):
    pass


class _FakeResponse:
    def __init__(self, url, body=None, stop_after=False):
        self.url = url
        self.response = self
        self._body = body
        self.stop_after = stop_after

    @property
    def body(self):
        if self.stop_after:
            raise _CaptureEnd()
        return self._body


class _FakeListen:
    def __init__(self, responses):
        self.responses = iter(responses)

    def wait(self, timeout=2):
        return next(self.responses)


def _run_listener_once(ns, response):
    ns["page"] = type("Page", (), {"listen": _FakeListen([response, _FakeResponse("mtop", stop_after=True)])})()
    try:
        ns["listen_loop"]()
    except _CaptureEnd:
        return
    raise AssertionError("listener did not stop at synthetic sentinel")


def test_listener_keeps_large_body_and_ignores_media_and_stale_room(tmp_path):
    ns = _crawler_capture_namespace(tmp_path)
    ns["state"].update(live_id="123", scan_journal_path=str(tmp_path / "capture.jsonl"))
    body = ('{"liveUrl":"https://stream","padding":"' + ("x" * 800005) +
            '","liveId":"123","isDigitalAnchorLive":true}')
    _run_listener_once(ns, _FakeResponse("https://mtop/live.detail.get", body))
    row = ns["state"]["collected"][0]
    assert row["body_truncated"] is True and row["body_original_chars"] == len(body)
    assert row["digital_flags"] == [True]
    assert ns["state"]["stream_url"]["url"] == "https://stream"
    saved = json.loads((tmp_path / "capture.jsonl").read_text(encoding="utf-8"))
    assert saved["body"] == body and saved["body_truncated"] is False
    assert saved["preview_truncated"] is True

    ns = _crawler_capture_namespace(tmp_path)
    ns["state"].update(live_id="222", scan_journal_path=str(tmp_path / "stale.jsonl"))
    _run_listener_once(ns, _FakeResponse("https://mtop/live.detail.get", '{"liveId":"111","liveUrl":"old"}'))
    assert len(ns["state"]["collected"]) == 1
    assert ns["state"]["collected"][0]["attribution_status"] == "conflict"
    assert ns["state"]["stream_url"]["url"] is None


def test_listener_filters_media_before_reading_body(tmp_path):
    ns = _crawler_capture_namespace(tmp_path)
    ns["state"].update(live_id="123", scan_journal_path=str(tmp_path / "media.jsonl"))
    media = _FakeResponse("https://cdn.example/live.ts", stop_after=True)
    _run_listener_once(ns, media)
    assert ns["state"]["collected"] == []


def test_scan_to_record_journal_switch_waits_for_inflight_writer(tmp_path):
    ns = _crawler_capture_namespace(tmp_path)
    scan_path = tmp_path / "scan.jsonl"
    target_path = tmp_path / "segment.jsonl"
    scan_path.write_text("", encoding="utf-8")
    ns["state"].update(live_id="123", navigation_id="nav-123",
                       scan_journal_path=str(scan_path))
    writing, release, switching = threading.Event(), threading.Event(), threading.Event()
    real_open = open

    def controlled_open(path, mode="r", *args, **kwargs):
        if str(path) == str(scan_path) and mode == "a":
            writing.set()
            assert release.wait(2)
        return real_open(path, mode, *args, **kwargs)

    ns["open"] = controlled_open
    response = _FakeResponse("https://mtop/live.detail.get",
                             '{"liveId":"123","liveUrl":"synthetic://stream"}')
    listener = threading.Thread(target=_run_listener_once, args=(ns, response), daemon=True)
    listener.start()
    assert writing.wait(2)

    def transition():
        switching.set()
        with ns["capture_lock"]:
            ns["sink_lock"].acquire()
            with target_path.open("x", encoding="utf-8") as target:
                copy_scan_journal(scan_path, target)
                ns["state"]["scan_journal_path"] = None
                ns["state"]["journal"] = target
            ns["sink_lock"].release()

    switcher = threading.Thread(target=transition, daemon=True)
    switcher.start()
    assert switching.wait(2)
    release.set()
    listener.join(2)
    switcher.join(2)
    rows = [json.loads(line) for line in target_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["navigation_id"] == "nav-123"


def test_scan_room_accepts_early_stream_without_captcha(tmp_path):
    ns = _crawler_capture_namespace(tmp_path)
    ns["stop_requested"] = lambda: False
    body = ('{"padding":"' + ("x" * 800005) +
            '","liveId":"123","liveUrl":"synthetic://stream","isDigitalAnchorLive":true}')
    response = _FakeResponse("https://mtop/live.detail.get", body)
    completed = threading.Event()
    release = threading.Event()

    class Listen:
        def wait(self, timeout=2):
            if not completed.is_set():
                completed.set()
                return response
            release.wait(1)
            raise _CaptureEnd()

    page = type("Page", (), {})()
    page.listen = Listen()
    threads = []
    def get(url, timeout=30):
        def run_listener():
            try:
                ns["listen_loop"]()
            except _CaptureEnd:
                pass
        thread = threading.Thread(target=run_listener, daemon=True)
        threads.append(thread)
        thread.start()
        assert completed.wait(1)
        deadline = time.monotonic() + 1
        while ns["state"]["stream_url"].get("url") is None and time.monotonic() < deadline:
            threading.Event().wait(0.005)
    page.get = get
    page.ele = lambda *args, **kwargs: None
    page.refresh = lambda: None
    ns.update(page=page, time=type("Clock", (), {"time": staticmethod(time.time),
               "time_ns": staticmethod(time.time_ns),
               "monotonic": staticmethod(time.monotonic), "sleep": staticmethod(lambda _: None)})())
    try:
        assert ns["scan_room"]("synthetic://room", "123") is True
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=1)


def test_live_detail_url_must_belong_to_matching_object_and_response_stays_attributed():
    body = {"data": [{"liveId": "123", "liveUrl": "good"},
                     {"liveId": "456", "liveUrl": "other"}]}
    assert detail_live_urls(body) == [("123", "good"), ("456", "other")]
    assert attribution_status(["123", "456"], "123", "nav-1") == "conflict"
    assert attribution_status([], "123", "nav-1") == "unknown"


def test_parser_excludes_conflicting_room_comments_but_keeps_legacy_unknown():
    old_room = {"url": "mtop.taobao.iliad.comment.query.latest", "live_ids": ["old"],
                "attribution_status": "conflict", "body": "private old-room comment"}
    unknown_legacy = {"url": "mtop.taobao.iliad.comment.query.latest", "body": "legacy comment"}
    assert not response_for_room_analysis(old_room, "current")
    assert response_for_room_analysis(unknown_legacy, "current")


def test_natural_tail_finalizes_singleton_and_keeps_failed_or_stopped_rooms():
    source = Path(__file__).resolve().parents[1] / "src" / "crawler" / "taobao_crawler.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == "_finalize_pending_rooms")
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), "exec"), ns)
    calls = []
    finalize = lambda room: calls.append(room) or True
    assert ns["_finalize_pending_rooms"](["one"], finalize, lambda: False) == []
    assert calls == ["one"]
    calls.clear()
    finalize = lambda room: calls.append(room) or room != "one"
    assert ns["_finalize_pending_rooms"](["one", "two", "one"], finalize,
                                         lambda: False) == ["one"]
    assert calls == ["one", "two"]
    calls.clear()
    stop_calls = iter([False, True])
    assert ns["_finalize_pending_rooms"](["one", "two", "three"], lambda room: calls.append(room) or True,
                                         lambda: next(stop_calls)) == ["two", "three"]
    assert calls == ["one"]


def test_listener_health_status_keys_exist_before_concurrent_updates():
    source = Path(__file__).resolve().parents[1] / "src" / "crawler" / "taobao_crawler.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "status_data"
                              for target in node.targets))
    initialized = {key.value for key in assignment.value.keys
                   if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    listener = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "listen_loop")
    written = {node.targets[0].slice.value for node in ast.walk(listener)
               if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript)
               and isinstance(node.targets[0].value, ast.Name)
               and node.targets[0].value.id == "status_data"
               and isinstance(node.targets[0].slice, ast.Constant)}
    health_keys = {"latest_response_t", "last_response_sequence", "unattributed_response_count",
                   "journal_error", "last_persisted_sequence", "last_persisted_t"}
    assert health_keys <= initialized
    assert health_keys <= written
    status = {key: None for key in initialized}
    keys_before = set(status)
    status.update({key: 1 for key in health_keys})
    assert set(status) == keys_before


def test_manual_captcha_wait_success_timeout_and_stop(tmp_path):
    ns = _crawler_capture_namespace(tmp_path)
    class Page:
        def __init__(self, values): self.values = iter(values)
        def ele(self, *args, **kwargs): return next(self.values)
    ns["page"] = Page([object(), None])
    assert ns["wait_for_human_captcha"](ns["page"], timeout=1) is True

    ticks = iter([0.0, 0.5, 1.1])
    ns["page"] = Page([object(), object()])
    ns["time"] = type("Clock", (), {"monotonic": staticmethod(lambda: next(ticks)),
               "sleep": staticmethod(lambda _: None)})()
    assert ns["wait_for_human_captcha"](ns["page"], timeout=1) is False

    ns["stop_requested"] = lambda: True
    ns["time"] = type("Clock", (), {"monotonic": staticmethod(lambda: 0.0),
               "sleep": staticmethod(lambda _: None)})()
    ns["page"] = Page([object()])
    assert ns["wait_for_human_captcha"](ns["page"], timeout=1) is False


def test_scan_journal_error_blocks_subprocess(tmp_path, monkeypatch):
    import src.crawler.recording as recording
    def forbidden_popen(*args, **kwargs): raise AssertionError("FFmpeg must not start")
    monkeypatch.setattr(recording.subprocess, "Popen", forbidden_popen)
    state = {"collected": [], "journal": None, "scan_journal_path": str(tmp_path / "missing.jsonl")}
    statuses = []
    result = recording.record_segment(
        url="synthetic://room", live_id="123", room_dir=tmp_path / "room",
        stream_url="synthetic://stream", segment_index=1, seg_name="segment",
        ffmpeg="ffmpeg", ffprobe="ffprobe", max_minutes=1, user_agent="synthetic",
        cookie_header="", state=state, lock=threading.Lock(), page=None,
        log=lambda *args: None, publish_status=lambda phase, **kwargs: statuses.append(phase))
    assert result is False
    assert "segment_failed" in statuses
