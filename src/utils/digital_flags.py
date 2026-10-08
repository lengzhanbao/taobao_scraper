# -*- coding: utf-8 -*-
"""Shared tri-state helpers for Taobao digital-human flags."""
import json


DIGITAL_TITLE_KEYWORDS = ("虚拟主播", "智能主播")


def normalize_digital_flag(value):
    """Return True/False for recognized values; None when absent or ambiguous."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return value == 1
    if isinstance(value, float) and value in (0.0, 1.0):
        return value == 1.0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1", "yes"):
            return True
        if normalized in ("false", "0", "no"):
            return False
    return None


def find_values_by_key(obj, key):
    """Collect all values for exact key in nested dict/list data."""
    found = []
    if isinstance(obj, dict):
        for item_key, value in obj.items():
            if item_key == key:
                found.append(value)
            found.extend(find_values_by_key(value, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_values_by_key(item, key))
    return found


def parse_json_body(body):
    """Parse JSON or JSONP bodies captured from Taobao API responses."""
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if not isinstance(body, str):
        return None

    text = body.strip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (TypeError, ValueError):
        return None


def title_keywords(title):
    """Return explicit title words that mark a room as digital by study rule."""
    return title_keywords_in_values((title,))


def title_keywords_in_values(values):
    """Match title rules across all captured title values for one room/segment."""
    texts = [str(value or "") for value in values or ()]
    return tuple(
        keyword
        for keyword in DIGITAL_TITLE_KEYWORDS
        if any(keyword in text for text in texts)
    )


def first_text(values):
    for value in values or []:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() != "none":
            return text
    return ""


def summarize_digital_flags(values):
    """Summarize raw values without treating missing/invalid values as false."""
    normalized = [normalize_digital_flag(value) for value in values]
    true_count = sum(value is True for value in normalized)
    false_count = sum(value is False for value in normalized)
    unknown_count = len(normalized) - true_count - false_count
    has_true = true_count > 0
    has_false = false_count > 0
    all_true = bool(normalized) and true_count == len(normalized)
    all_false = bool(normalized) and false_count == len(normalized)

    if has_true and has_false:
        status = "mixed"
        raw_label = "true|false"
    elif all_false:
        status = "all_false"
        raw_label = "false"
    elif has_false:
        status = "contains_false"
        raw_label = "false" if unknown_count == 0 else "false|unknown"
    elif all_true:
        status = "all_true"
        raw_label = "true"
    else:
        status = "unknown"
        raw_label = "unknown" if normalized else "missing"

    return {
        "status": status,
        "raw_label": raw_label,
        "true_count": true_count,
        "false_count": false_count,
        "unknown_count": unknown_count,
        "has_true": has_true,
        "has_false": has_false,
        "all_true": all_true,
        "all_false": all_false,
        "has_missing": not normalized or unknown_count > 0,
    }


def classify_digital_segment(flag_summary, matched_title_keywords=()):
    """Return export labels for one segment without changing its raw flag."""
    if matched_title_keywords or flag_summary["all_true"]:
        return {"digital_label": "是", "sample_handling": "保留"}
    if flag_summary["has_false"]:
        return {
            "digital_label": "否",
            "sample_handling": "排除：含平台 false",
        }
    return {
        "digital_label": "待核查",
        "sample_handling": "待核查：标记缺失或不完整",
    }


def summarize_room_digital_flags(segment_summaries, segment_title_matches=()):
    """Summarize all captured segments for one crawler room/liveId."""
    summaries = list(segment_summaries)
    titles = list(segment_title_matches)
    room_all_true = bool(summaries) and all(
        summary["all_true"] for summary in summaries
    )
    room_has_false = any(summary["has_false"] for summary in summaries)
    room_has_missing = any(summary["has_missing"] for summary in summaries)
    room_title_matches = tuple(
        keyword
        for keyword in DIGITAL_TITLE_KEYWORDS
        if any(keyword in matches for matches in titles)
    )
    true_segment_count = sum(summary["has_true"] for summary in summaries)
    false_segment_count = sum(summary["has_false"] for summary in summaries)
    missing_segment_count = sum(summary["has_missing"] for summary in summaries)

    if room_all_true:
        room_flag_label = "全段true"
    elif room_has_false and true_segment_count:
        room_flag_label = "含true且含false"
    elif room_has_false:
        room_flag_label = "含false"
    else:
        room_flag_label = "含缺失或不完整标记"

    if room_title_matches:
        room_classification = "数字人（标题规则）"
    elif room_all_true:
        room_classification = "数字人（平台全true）"
    elif room_has_false:
        room_classification = "含false（排除对应段）"
    else:
        room_classification = "待核查（标记缺失或不完整）"

    return {
        "all_true": room_all_true,
        "has_false": room_has_false,
        "has_missing": room_has_missing,
        "title_matches": room_title_matches,
        "true_segment_count": true_segment_count,
        "false_segment_count": false_segment_count,
        "missing_segment_count": missing_segment_count,
        "flag_label": room_flag_label,
        "classification": room_classification,
    }
