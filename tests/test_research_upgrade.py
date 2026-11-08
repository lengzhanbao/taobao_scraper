"""Regression tests for the 2.8.0 research-data upgrade.

All media and responses here are synthetic. No browser, Taobao account or real
research directory is used.
"""
import csv
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.crawler.attempt_log import CollectionAttemptWriter, classify_scan_result
from src.crawler.response_attribution import (attribution_status, normalize_attribution_status,
                                               summarize_attribution)
from src.parser import parse_data
from src.parser.product_events import (extract_product_events, latest_current_candidate,
                                       product_intervals, summarize_product_events)
from src.utils.safe_io import atomic_json, atomic_json_with_raw_array, digest, probe_media
from src.utils.segment_evidence import (recording_quality, target_duration, timing_evidence,
                                        validation_matches, video_validation)

LIVE_ID = "123456789"


def proof(duration=600.0, minimum=420.0, media=None, max_minutes=20):
    payload = {"schema_version": 1, "valid": True, "duration_source": "ffprobe",
               "duration_seconds": duration, "minimum_duration_seconds": minimum,
               "video_sha256": "a" * 64, "validated_t": time.time()}
    if media is not None:
        payload["media"] = media
    return {"max_minutes": max_minutes, "video_duration_seconds": duration,
            "technical_validation": payload}


def product_response(api, items, *, sequence=10, received_at=1010.0, status="matched",
                     live_id=LIVE_ID):
    body = {"data": {}}
    if api.endswith("getvideodetailitemlistwithpagination"):
        body["data"]["itemListv1"] = [{"liveItemDO": item} for item in items]
    else:
        body["data"]["curItemList"] = items
    return {"url": f"https://h5api.m.taobao.com/h5/{api}/1.0/?x=1",
            "body": json.dumps(body, ensure_ascii=False),
            "response_sequence": sequence, "received_at": received_at,
            "observed_live_id": live_id, "live_ids": [live_id],
            "navigation_id": "nav", "attribution_status": status}


def item(item_id="987654321", **extra):
    value = {"itemId": item_id, "itemName": f"商品{item_id}", "itemPrice": "239",
             "liveItemPrice": {"promotionPrice": "18327", "publicPromotionPrice": "18327",
                               "singlePromotionPrice": "23900", "skuId": "5994544129612",
                               "promotionPriceDesc": "入会价"},
             "itemExtData": {"smallCardItemType": "speakIngItem"},
             "liveId": LIVE_ID}
    value.update(extra)
    return value


CONTEXT = {"run_id": "run-1", "recording_id": "rec-1", "live_id": LIVE_ID,
           "segment_index": 1, "record_start_t": 1000.0}


def test_target_duration_achievement_is_separate_from_technical_validity():
    assert target_duration({"max_minutes": 20}) == 1200
    seven_minutes = recording_quality(proof(duration=420.0))
    assert seven_minutes["technical_valid"] is True
    assert seven_minutes["target_duration_seconds"] == 1200
    assert seven_minutes["duration_completion_ratio"] == 0.35
    assert seven_minutes["target_duration_met"] is False
    assert seven_minutes["quality_state"] == "technical_valid_target_not_met"

    almost_full = recording_quality(proof(duration=1140.0))
    assert almost_full["target_duration_met"] is True
    assert almost_full["duration_completion_ratio"] == 0.95

    unknown_target = recording_quality({"video_duration_seconds": 600.0,
                                        "technical_validation": proof(media=None)["technical_validation"]})
    assert unknown_target["target_duration_seconds"] is None
    assert unknown_target["target_duration_met"] is None
    assert unknown_target["quality_state"] == "technical_valid_target_unknown"
    assert validation_matches(proof())


def test_audio_facts_are_unknown_when_not_measured():
    no_audio = recording_quality(proof(media={"has_video": True, "has_audio": False,
                                              "video_codec": "h264", "audio_codec": None,
                                              "video_width": 720, "video_height": 1280,
                                              "video_fps": 25.0}))
    assert no_audio["has_video"] is True
    assert no_audio["has_audio"] is False
    assert no_audio["video_codec"] == "h264"
    assert no_audio["audio_codec"] is None
    unknown = recording_quality(proof(media=None))
    assert unknown["has_audio"] is None
    assert unknown["video_codec"] is None
    assert unknown["video_fps"] is None


def test_timing_evidence_keeps_wall_clock_and_media_time_apart():
    data = {"recording_id": "rec-1", "live_id": LIVE_ID, "record_start_t": 1000.0,
            "record_end_t": 1600.0, "first_video_data_t": 1002.0,
            "video_duration_seconds": 600.0,
            "technical_validation": proof(media={"first_video_pts": 0.0,
                                                 "first_audio_pts": 0.04,
                                                 "audio_duration_seconds": 599.8})["technical_validation"]}
    timing = timing_evidence(data, events=[{"kind": "page_refresh", "wall_time": 1100.0}])
    assert timing["record_start_wall_time"] == 1000.0
    assert timing["first_video_pts"] == 0.0
    assert timing["first_audio_pts"] == 0.04
    assert timing["alignment_status"] == "approximate"
    assert timing["alignment_uncertainty_ms"] is None
    assert timing["mapping"]["delta_seconds"] is None
    assert timing["wall_clock_media_window_delta_seconds"] == 0.0
    unresolved = timing_evidence({"record_start_t": 1000.0, "record_end_t": 1600.0,
                                  "video_duration_seconds": 600.0})
    assert unresolved["alignment_status"] == "unresolved"
    assert unresolved["first_video_pts"] is None


def test_attribution_grades_and_legacy_unscoped_mapping():
    assert attribution_status([LIVE_ID], LIVE_ID, "nav") == "matched"
    assert attribution_status([], LIVE_ID, "nav") == "context_only"
    assert attribution_status([], LIVE_ID, None) == "unknown"
    assert attribution_status(["old"], LIVE_ID, "nav") == "conflict"
    assert attribution_status([LIVE_ID, "other"], LIVE_ID, "nav") == "conflict"
    assert normalize_attribution_status("unscoped") == "context_only"
    assert normalize_attribution_status("bogus") == "unknown"
    summary = summarize_attribution([
        {"url": "https://x/mtop.a/1.0/", "attribution_status": "matched"},
        {"url": "https://x/mtop.b/1.0/", "attribution_status": "unscoped"},
        {"url": "https://x/mtop.a/1.0/", "attribution_status": "unknown"},
    ])
    assert summary["by_status"] == {"matched": 1, "context_only": 1, "conflict": 0, "unknown": 1}
    assert summary["high_confidence"] == 1


def test_product_events_keep_multiple_items_and_price_roles():
    response = product_response("mtop.tblive.live.item.callback.query",
                                [item(), item("222", itemName="第二件")])
    events = extract_product_events(response, CONTEXT)
    assert len(events) == 2
    assert {event["item_id"] for event in events} == {"987654321", "222"}
    # Two current-item entries in one response are ambiguous, not a unique current item.
    assert {event["item_role"] for event in events} == {"unknown"}
    assert events[0]["attribution_status"] == "matched"
    assert events[0]["response_sequence"] == 10
    assert events[0]["event_relative_seconds"] == 10.0
    assert events[0]["evidence_path"] == "data.curItemList[0]"

    single = extract_product_events(
        product_response("mtop.tblive.live.item.callback.query", [item()]), CONTEXT)
    assert single[0]["item_role"] == "current_candidate"
    assert single[0]["item_id"] == "987654321"
    assert single[0]["sku_id"] == "5994544129612"
    assert single[0]["display_price"] == pytest.approx(239.0)
    assert single[0]["promotion_price"] == pytest.approx(183.27)
    assert single[0]["promotion_price_desc"] == "入会价"
    roles = {row["role"]: row for row in single[0]["price_observations"]}
    assert roles["item_price"]["value"] == pytest.approx(239.0)
    assert roles["promotion_price"]["value"] == pytest.approx(183.27)
    assert roles["public_promotion_price"]["value"] == pytest.approx(183.27)
    assert roles["single_promotion_price"]["value"] == pytest.approx(239.0)
    assert single[0]["original_price"] is None
    assert latest_current_candidate(single)["item_id"] == "987654321"


def test_callback_publish_item_is_not_a_current_item_candidate():
    publish_item = item(itemExtData={"smallCardItemType": "publishItem"})
    response = product_response("mtop.tblive.live.item.callback.query", [publish_item])
    event = extract_product_events(response, CONTEXT)[0]
    assert event["item_role"] == "unknown"
    assert event["role_evidence"] == "callback_without_speaking_marker"


def test_callback_request_can_supply_speaking_evidence_without_item_marker():
    no_marker = item()
    no_marker.pop("itemExtData")
    plain = product_response("mtop.tblive.live.item.callback.query", [no_marker])
    assert extract_product_events(plain, CONTEXT)[0]["item_role"] == "unknown"
    requested = product_response("mtop.tblive.live.item.callback.query", [no_marker])
    requested["url"] += "&data=%7B%22smallcarditemtype%22%3A%22speakingitem%22%7D"
    event = extract_product_events(requested, CONTEXT)[0]
    assert event["item_role"] == "current_candidate"
    assert event["role_evidence"] == "callback_request_speaking_item"


def test_product_artifacts_from_another_recording_are_not_merged(tmp_path):
    event_path = tmp_path / "product_events.jsonl"
    event_path.write_text(
        "\n".join(json.dumps(row) for row in [
            {"recording_id": "other", "live_id": LIVE_ID, "item_id": "1"},
            {"recording_id": "rec-1", "live_id": LIVE_ID, "item_id": "2"},
            {"recording_id": "rec-1", "live_id": "other-room", "item_id": "3"},
            {"recording_id": "rec-1", "live_id": LIVE_ID, "item_id": "4"},
        ]) + "\n", encoding="utf-8")
    interval_path = tmp_path / "product_intervals.jsonl"
    interval_path.write_text(
        "\n".join(json.dumps(row) for row in [
            {"recording_id": "other", "live_id": LIVE_ID, "item_id": "1"},
            {"recording_id": "rec-1", "live_id": LIVE_ID, "item_id": "2"},
        ]) + "\n", encoding="utf-8")
    data = {"product_events_journal": str(event_path),
            "product_intervals_journal": str(interval_path)}
    events, intervals, summary = parse_data.segment_product_evidence(
        data, LIVE_ID, "rec-1", 1, "run-1", [])
    assert [event["item_id"] for event in events] == ["2", "4"]
    assert [item["item_id"] for item in intervals] == ["2"]
    assert summary["event_count"] == 2


def test_scan_phase_product_events_do_not_enter_the_recording_timeline():
    before = product_response("mtop.tblive.live.item.callback.query", [item("111")],
                              sequence=1, received_at=990.0)
    during = product_response("mtop.tblive.live.item.callback.query", [item("222")],
                              sequence=2, received_at=1010.0)
    data = {"record_start_t": 1000.0}
    events, intervals, summary = parse_data.segment_product_evidence(
        data, LIVE_ID, "rec-1", 1, "run-1", [before, during])
    assert [event["item_id"] for event in events] == ["222"]
    assert summary["event_count"] == 1
    assert product_intervals([
        {**event, "event_relative_seconds": -5.0, "attribution_status": "matched",
         "item_role": "current_candidate"} for event in events]) == []


def test_product_events_cover_listed_items_missing_ids_and_sources():
    listed = product_response(
        "mtop.tblive.live.item.getvideodetailitemlistwithpagination",
        [{"itemName": "仅有名称"}, {"itemId": "333", "itemPrice": "9.9"}], sequence=20)
    events = extract_product_events(listed, CONTEXT)
    assert len(events) == 2
    assert events[0]["item_role"] == events[1]["item_role"] == "listed"
    assert events[0]["item_id"] is None and events[0]["item_name"] == "仅有名称"
    assert events[1]["item_id"] == "333"
    assert events[0]["source_api"].endswith("getvideodetailitemlistwithpagination")
    assert events[0]["raw_reference"] == "response:20"

    detail = product_response("mtop.roomstudio.live.detail.get", [item()], sequence=30)
    detail_event = extract_product_events(detail, CONTEXT)[0]
    assert detail_event["item_role"] == "current_candidate"
    assert detail_event["role_evidence"] == "detail_cur_item_list"

    unknown = product_response("mtop.tblive.live.item.callback.query", [item()],
                               status="unknown")
    assert extract_product_events(unknown, CONTEXT) == []
    context_only = product_response("mtop.tblive.live.item.callback.query", [item()],
                                    status="context_only")
    context_event = extract_product_events(context_only, CONTEXT)[0]
    assert context_event["attribution_status"] == "context_only"
    assert context_event["live_id_basis"] == "navigation_context"


def test_product_intervals_need_two_observed_boundaries():
    first = extract_product_events(
        product_response("mtop.tblive.live.item.callback.query", [item()],
                         sequence=1, received_at=1010.0), CONTEXT)
    assert product_intervals(first) == []
    second = extract_product_events(
        product_response("mtop.tblive.live.item.callback.query", [item("222")],
                         sequence=2, received_at=1060.0), CONTEXT)
    intervals = product_intervals(first + second)
    assert len(intervals) == 1
    assert intervals[0]["item_id"] == "987654321"
    assert intervals[0]["start_seconds"] == 10.0
    assert intervals[0]["end_seconds"] == 60.0
    assert intervals[0]["basis"] == "consecutive_current_item_observations"
    same = extract_product_events(
        product_response("mtop.tblive.live.item.callback.query", [item()],
                         sequence=3, received_at=1090.0), CONTEXT)
    assert len(first + same) == 2
    assert product_intervals(first + same) == []
    summary = summarize_product_events(first + second, intervals)
    assert summary["event_count"] == 2 and summary["interval_count"] == 1
    assert summary["distinct_item_ids"] == 2


def test_response_filter_excludes_new_unknown_but_keeps_legacy_rows():
    def response(status=None, ids=None, observed=None):
        value = {"body": "{}", "live_ids": ids or [], "observed_live_id": observed}
        if status is not None:
            value["attribution_status"] = status
        return value
    assert parse_data.response_for_room_analysis(response("matched", [LIVE_ID], LIVE_ID), LIVE_ID)
    assert not parse_data.response_for_room_analysis(response("context_only", [], None), LIVE_ID)
    assert not parse_data.response_for_room_analysis(response("unknown", [], None), LIVE_ID)
    assert not parse_data.response_for_room_analysis(response("conflict", ["old"], "old"), LIVE_ID)
    # Legacy rows without a grade keep their previous conservative treatment.
    assert parse_data.response_for_room_analysis(response(None, [LIVE_ID], LIVE_ID), LIVE_ID)
    assert parse_data.response_for_room_analysis(response(None, [], None), LIVE_ID)
    assert not parse_data.response_for_room_analysis(response(None, ["old"], "old"), LIVE_ID)


def test_derived_jsonl_preserves_differing_prior_content(tmp_path):
    path = tmp_path / "product_events.jsonl"
    path.write_text('{"old": 1}\n', encoding="utf-8")
    parse_data.write_derived_jsonl(path, [{"new": 2}])
    assert path.read_text(encoding="utf-8") == '{"new": 2}\n'
    backups = list(tmp_path.glob("product_events.jsonl.bak_*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"old": 1}\n'
    parse_data.write_derived_jsonl(path, [{"new": 2}])
    assert len(list(tmp_path.glob("product_events.jsonl.bak_*"))) == 1


def test_console_counts_target_duration_and_audio_separately():
    from src.control.server import summarize_rows
    rows = [{"count": 3, "target": 3, "valid_count": 3, "archived_count": 0,
             "quality_records": {
                 "a": {"target_duration_met": True, "has_audio": True},
                 "b": {"target_duration_met": False, "has_audio": False},
                 "c": {"target_duration_met": None, "has_audio": None},
             }}]
    summary = summarize_rows(rows)
    assert summary["valid_segments"] == 3
    assert summary["target_duration_met_segments"] == 1
    assert summary["target_duration_not_met_segments"] == 1
    assert summary["audio_missing_segments"] == 1
    assert summary["quality_unknown_segments"] == 1


def test_recent_attempts_are_sorted_and_humanized(tmp_path):
    from src.control.server import read_recent_attempts
    path = tmp_path / "collection_attempts_instance_1.jsonl"
    rows = [
        {"run_id": "run-1", "instance_id": 1, "live_id": "111", "stage": "scan",
         "decision": "failed_scan", "failure_reason": "network_or_navigation_error",
         "attempt_end_t": 100.0, "detail": {"error": "synthetic network"}},
        {"run_id": "run-1", "instance_id": 1, "live_id": "222", "stage": "recording",
         "decision": "recorded", "failure_reason": None, "attempt_end_t": 300.0,
         "recording_id": "rec-2", "recording_duration_seconds": 600.0,
         "target_duration_met": False, "detail": {"has_audio": True}},
        {"run_id": "run-1", "instance_id": 1, "live_id": "333", "stage": "scan",
         "decision": "skipped_not_eligible", "failure_reason": "platform_flag_false",
         "attempt_end_t": 200.0, "detail": {}},
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                    encoding="utf-8")
    recent = read_recent_attempts([path], limit=2)
    assert [row["live_id"] for row in recent] == ["222", "333"]
    assert recent[0]["decision_text"] == "已录制"
    assert recent[1]["decision_text"] == "按规则跳过"
    assert recent[1]["reason_text"] == "平台标记 false，按样本规则跳过"
    assert recent[0]["detail"] == {"has_audio": True}
    full = read_recent_attempts([path], limit=20)
    assert full[2]["reason_text"] == "网络或页面导航失败"
    assert full[2]["detail"]["error"] == "synthetic network"


def test_collection_attempt_writer_writes_ledger(tmp_path):
    path = tmp_path / "collection_attempts_instance_1.jsonl"
    writer = CollectionAttemptWriter(path, run_id="run-1", instance_id=1)
    writer.record(live_id=LIVE_ID, stage="scan", decision="failed_scan",
                  failure_reason="network_or_navigation_error", attempt_start_t=1000.0,
                  attempt_end_t=1002.0, retry_count=2, segment_index=1,
                  detail={"scan_result": "network_or_navigation_error"})
    writer.record(live_id=LIVE_ID, stage="recording", decision="recorded",
                  attempt_start_t=1010.0, attempt_end_t=1610.0, retry_count=0,
                  recording_id="rec-1", segment_index=1,
                  recording_duration_seconds=600.0, target_duration_met=True,
                  quality_state="technical_valid_target_met")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["failure_reason"] == "network_or_navigation_error"
    assert rows[0]["attempt_start_time"].endswith("Z")
    assert rows[1]["recording_id"] == "rec-1"
    assert rows[1]["target_duration_met"] is True
    assert classify_scan_result("non_digital") == ("skipped_not_eligible", "platform_flag_false")
    assert classify_scan_result("network_or_navigation_error")[0] == "failed_scan"
    assert classify_scan_result("weird") == ("failed_scan", "weird")


def test_streaming_final_json_keeps_embedded_responses(tmp_path):
    journal = tmp_path / "responses.jsonl"
    journal.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    output = tmp_path / "final.json"
    fields = {"schema_version": 2, "captured_count": 2, "responses_storage": "jsonl"}
    raw = [line for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
    atomic_json_with_raw_array(output, fields, "responses", raw)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["responses"] == [{"a": 1}, {"b": 2}]
    assert data["responses_storage"] == "jsonl"


def test_parse_data_writes_research_artifacts_and_keeps_old_json_readable(tmp_path):
    study = tmp_path / "study"
    room = study / "_staging" / "browser_9223" / ("room_" + LIVE_ID)
    attempt = room / "第一段" / "attempt_1"
    attempt.mkdir(parents=True)
    video = attempt / "video.flv"
    video.write_bytes(b"synthetic video")
    media = {"has_video": True, "has_audio": True, "video_codec": "h264",
             "audio_codec": "aac", "video_duration_seconds": 60.0,
             "audio_duration_seconds": 60.0, "video_width": 720, "video_height": 1280,
             "video_fps": 25.0, "first_video_pts": 0.0, "first_audio_pts": 0.0}
    responses = [
        {"t": 1001.0, "received_at": 1001.0, "url": "https://h5api.m.taobao.com/h5/mtop.roomstudio.live.detail.get/1.0/",
         "body": json.dumps({"data": {"liveId": LIVE_ID, "isDigitalAnchorLive": "true",
                                      "liveTitle": "测试直播", "accountName": "测试店",
                                      "shopId": "shop-1", "accountId": "acc-1",
                                      "bizCode": "TMALL"}}),
         "response_sequence": 1, "live_ids": [LIVE_ID], "observed_live_id": LIVE_ID,
         "navigation_id": "nav", "attribution_status": "matched"},
        product_response("mtop.tblive.live.item.callback.query", [item()], sequence=2,
                         received_at=1010.0),
        {"t": 1002.0, "received_at": 1002.0, "url": "https://h5api.m.taobao.com/h5/mtop.taobao.iliad.comment.query.latest/1.0/",
         "body": json.dumps({"ret": ["SUCCESS::调用成功"],
                             "data": {"comments": [{"publisherNick": "u", "content": "hi",
                                                    "timestamp": "1002"}]}}),
         "response_sequence": 3, "live_ids": [LIVE_ID], "observed_live_id": LIVE_ID,
         "navigation_id": "nav", "attribution_status": "matched"},
    ]
    technical = {"schema_version": 1, "valid": True, "duration_source": "ffprobe",
                 "duration_seconds": 60.0, "minimum_duration_seconds": 54.0,
                 "video_sha256": digest(video), "validated_t": time.time(), "media": media}
    final = {"schema_version": 2, "recording_id": "rec-1", "segment_index": 1,
             "run_id": "run-1", "live_url": f"https://tbzb.taobao.com/live?liveId={LIVE_ID}",
             "record_start_t": 1000.0, "record_end_t": 1060.0, "first_video_data_t": 1001.0,
             "max_minutes": 1, "video_duration_seconds": 60.0,
             "recorded_files": [str(video.resolve())], "responses": responses,
             "captured_count": len(responses), "technical_validation": technical}
    atomic_json(attempt / "data_20261008_120000_final.json", final)
    with patch.object(parse_data, "SESSIONS", str(study / "sessions")), \
            patch.object(parse_data, "probe_video", return_value=60.0), \
            patch.object(parse_data, "probe_media", return_value=media):
        assert parse_data.process_room(str(room))
    session = next((study / "sessions").glob("*_" + LIVE_ID))
    events = [json.loads(line) for line in (session / "product_events.jsonl")
              .read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(events) == 1 and events[0]["item_id"] == "987654321"
    quality = json.loads((session / "recording_quality.json").read_text(encoding="utf-8"))
    assert quality["summary"]["target_duration_met_count"] == 1
    assert quality["summary"]["audio_present_count"] == 1
    timing = json.loads((session / "timing_evidence.json").read_text(encoding="utf-8"))
    assert timing["recordings"][0]["alignment_status"] == "approximate"
    manifest = json.loads((session / "archive_manifest.json").read_text(encoding="utf-8"))
    assert manifest["segments"][0]["recording_quality"]["target_duration_met"] is True
    assert any(item["destination"].endswith("recording_quality.json")
               for item in manifest["analysis_files"])
    summary_csv = next((session / "crawler").glob("lives_summary_*.csv"))
    with summary_csv.open(encoding="utf-8-sig", newline="") as stream:
        summary_rows = list(csv.DictReader(stream))
    header = summary_csv.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "目标时长达成" in header and "商品事件数" in header
    assert summary_rows[0]["平台shopId"] == "shop-1"
    assert summary_rows[0]["平台accountId"] == "acc-1"
    assert summary_rows[0]["平台bizCode"] == "TMALL"

    # A pre-2.8 final JSON without quality/timing/product fields still parses.
    old_final = dict(final)
    for key in ("recording_quality", "timing_evidence", "response_attribution_summary",
                "product_event_summary", "product_events_journal"):
        old_final.pop(key, None)
    old_final["recorded_files"] = [str(video.resolve())]
    old_final["technical_validation"] = technical
    legacy_room = study / "_staging" / "browser_9224" / ("room_" + LIVE_ID)
    legacy_attempt = legacy_room / "第一段" / "attempt_1"
    legacy_attempt.mkdir(parents=True)
    legacy_video = legacy_attempt / "video.flv"
    legacy_video.write_bytes(b"legacy synthetic video")
    legacy_data = dict(old_final, recording_id="rec-old", segment_index=1,
                       recorded_files=[str(legacy_video.resolve())],
                       technical_validation={**technical,
                                             "video_sha256": digest(legacy_video)})
    atomic_json(legacy_attempt / "data_20261008_120100_final.json", legacy_data)
    with patch.object(parse_data, "SESSIONS", str(study / "legacy_sessions")), \
            patch.object(parse_data, "probe_video", return_value=60.0), \
            patch.object(parse_data, "probe_media", return_value=media):
        assert parse_data.process_room(str(legacy_room))
    legacy_session = next((study / "legacy_sessions").glob("*_" + LIVE_ID))
    assert (legacy_session / "recording_quality.json").is_file()
    assert (legacy_session / "product_events.jsonl").is_file()
