"""Timestamped product observations from captured Taobao responses.

The collector records evidence; it does not run a language model and never
claims that a listed item is the item being spoken about. Every event keeps its
source API, response sequence and attribution grade so later analysis can join
video/ASR without guessing.

Only structures actually observed in captured responses are parsed:
- ``mtop.tblive.live.item.callback.query`` -> ``data.curItemList``
- ``mtop.roomstudio.live.detail.get`` -> ``data.curItemList``
- ``mtop.tblive.live.item.getvideodetailitemlistwithpagination``
  -> ``data.itemListv1[].liveItemDO``
"""
import json

from src.crawler.response_attribution import (attribution_basis, attribution_status,
                                               is_high_confidence_status,
                                               normalize_attribution_status)
from src.utils.api_names import PRODUCT_APIS, api_name, product_item_lists
from src.utils.digital_flags import find_values_by_key, parse_json_body

SCHEMA_VERSION = 1
CURRENT_ROLES = ("current_confirmed", "current_candidate")


def _text(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _digits(value):
    text = _text(value)
    if text is None or not text.isdigit():
        return None
    return text


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _json_object(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    parsed = parse_json_body(value)
    return parsed if isinstance(parsed, dict) else {}


def _price_observation(role, raw_value, *, unit, unit_basis, evidence_path, desc=None):
    """Keep the raw amount and the unit conversion basis next to each other."""
    value = _finite(raw_value)
    if value is None:
        return None
    if unit_basis == "live_item_price_in_cents":
        value = value / 100
    if value < 0:
        return None
    observation = {"role": role, "raw_value": str(raw_value), "value": round(value, 2),
                   "unit": unit, "unit_basis": unit_basis, "evidence_path": evidence_path}
    if desc:
        observation["desc"] = desc
    return observation


def _event_grade(response, parsed, context_live_id):
    """Trust a persisted grade; only derive one for legacy rows without it."""
    if "attribution_status" in response:
        return normalize_attribution_status(response.get("attribution_status"))
    ids = [str(item) for item in (response.get("live_ids") or []) if item is not None]
    if not ids:
        ids = [str(item) for item in find_values_by_key(parsed, "liveId") if item is not None]
    observed = response.get("observed_live_id")
    if observed is None and len(set(ids)) == 1:
        observed = ids[0]
    return attribution_status(ids, observed if observed is not None else context_live_id,
                              response.get("navigation_id"))


def _item_prices(item, evidence_path):
    observations = []
    item_price = _price_observation("item_price", item.get("itemPrice"), unit="CNY",
                                    unit_basis="item_price_as_yuan",
                                    evidence_path=evidence_path + ".itemPrice")
    if item_price:
        observations.append(item_price)
    live_price = item.get("liveItemPrice") if isinstance(item.get("liveItemPrice"), dict) else {}
    desc = _text(live_price.get("promotionPriceDesc"))
    for field, role in (("promotionPrice", "promotion_price"),
                        ("publicPromotionPrice", "public_promotion_price"),
                        ("singlePromotionPrice", "single_promotion_price")):
        observation = _price_observation(
            role, live_price.get(field), unit="CNY", unit_basis="live_item_price_in_cents",
            evidence_path=evidence_path + ".liveItemPrice." + field, desc=desc)
        if observation:
            observations.append(observation)
    return observations


def _item_event(item, *, context, api, role, role_evidence, evidence_path, index, grade,
                source_sequence):
    if not isinstance(item, dict):
        return None
    item_id = _digits(item.get("itemId"))
    item_name = _text(item.get("itemName"))
    live_price = item.get("liveItemPrice") if isinstance(item.get("liveItemPrice"), dict) else {}
    trade = _json_object((item.get("extendVal") or {}).get("tradeParams")
                         if isinstance(item.get("extendVal"), dict) else None)
    sku_id = _digits(live_price.get("skuId")) or _digits(trade.get("skuId"))
    if item_id is None and item_name is None and sku_id is None:
        return None
    prices = _item_prices(item, evidence_path)
    display = next((item_ for item_ in prices if item_["role"] == "item_price"), None)
    promotion = next((item_ for item_ in prices if item_["role"] == "promotion_price"), None)
    received_at = _finite(context.get("received_at"))
    start_t = _finite(context.get("record_start_t"))
    relative = (round(received_at - start_t, 3)
                if received_at is not None and start_t is not None else None)
    observed = _text(context.get("live_id"))
    live_id_basis = ("matched_response" if grade == "matched"
                     else "navigation_context" if grade == "context_only" else "unattributed")
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": f"{context.get('recording_id') or 'recording'}:"
                    f"{source_sequence if source_sequence is not None else 'untimed'}:"
                    f"{api}:{evidence_path}:{index}",
        "run_id": context.get("run_id"),
        "recording_id": context.get("recording_id"),
        "live_id": observed if live_id_basis != "unattributed" else None,
        "live_id_basis": live_id_basis,
        "navigation_id": context.get("navigation_id"),
        "response_sequence": source_sequence,
        "source_api": api,
        "received_at": received_at,
        "event_relative_seconds": relative,
        "item_id": item_id,
        "sku_id": sku_id,
        "item_name": item_name,
        "item_role": role,
        "role_evidence": role_evidence,
        "display_price": display["value"] if display else None,
        "original_price": None,
        "promotion_price": promotion["value"] if promotion else None,
        "promotion_price_desc": (promotion.get("desc") if promotion else None),
        "price_unit": display["unit"] if display else (promotion["unit"] if promotion else None),
        "price_observations": prices,
        "attribution_status": grade,
        "attribution_basis": attribution_basis(grade, context.get("navigation_id")),
        "observed_live_id": _text(context.get("observed_live_id")),
        "evidence_path": evidence_path,
        "raw_reference": (f"response:{source_sequence}" if source_sequence is not None
                          else "response:untimed"),
        "first_observed_at": received_at,
        "last_observed_at": received_at,
        "observation_source": f"{api}@{evidence_path}",
    }


def _dedupe(events):
    """Drop only byte-level duplicate observations inside the same response."""
    unique, seen = [], set()
    for event in events:
        key = (event.get("response_sequence"), event.get("source_api"),
               event.get("evidence_path"), event.get("item_id"), event.get("sku_id"),
               event.get("item_name"), event.get("item_role"),
               tuple((item.get("role"), item.get("raw_value"))
                     for item in event.get("price_observations", [])))
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def extract_product_events(response, context):
    """Return product observation events for one captured response."""
    if not isinstance(response, dict):
        return []
    api = api_name(response.get("url"))
    if api not in PRODUCT_APIS:
        return []
    parsed = parse_json_body(response.get("body"))
    if not isinstance(parsed, dict):
        return []
    grade = _event_grade(response, parsed, context.get("live_id"))
    if grade not in ("matched", "context_only"):
        # Conflict and unknown rows stay in the raw journal; they are not
        # silently assigned to the current room.
        return []
    events = []
    for base_path, base_role, role_evidence, items in product_item_lists(api, parsed):
        unique_current = len(items) == 1
        for index, item in enumerate(items):
            role, evidence = base_role, role_evidence
            if api.endswith("callback.query"):
                marker = str(((item.get("itemExtData") or {})
                              .get("smallCardItemType") or "")).lower()
                if "speak" not in marker:
                    role, evidence = "unknown", "callback_without_speaking_marker"
            if role == "current_candidate" and not unique_current:
                # Several current-item entries in one response are not a unique
                # "what is on screen now" observation.
                role, evidence = "unknown", role_evidence + "_ambiguous_multiple_items"
            event = _item_event(
                item, context=dict(context, navigation_id=(response.get("navigation_id")
                                                           or context.get("navigation_id")),
                                   received_at=response.get("received_at", response.get("t"))),
                api=api, role=role, role_evidence=evidence,
                evidence_path=f"{base_path}[{index}]", index=index, grade=grade,
                source_sequence=response.get("response_sequence"))
            if event:
                events.append(event)
    return _dedupe(events)


def product_intervals(events):
    """Infer intervals only between two observed current-item boundaries.

    A single observation never becomes a fabricated display duration, and the
    final open-ended run is deliberately not extended to the recording end.
    """
    usable = [event for event in events
              if event.get("attribution_status") == "matched"
              and event.get("item_role") in CURRENT_ROLES
              and event.get("event_relative_seconds") is not None]
    usable.sort(key=lambda item: (item.get("event_relative_seconds"),
                                  item.get("response_sequence") or 0))
    intervals = []
    run_start = None
    run_event = None
    for event in usable:
        identity = (event.get("item_id"), event.get("sku_id"), event.get("item_name"))
        if run_event is None:
            run_start, run_event = event["event_relative_seconds"], event
            continue
        previous_identity = (run_event.get("item_id"), run_event.get("sku_id"),
                             run_event.get("item_name"))
        if identity == previous_identity:
            continue
        if event["event_relative_seconds"] > run_start:
            intervals.append({
                "schema_version": SCHEMA_VERSION,
                "recording_id": event.get("recording_id"),
                "live_id": event.get("live_id"),
                "item_id": run_event.get("item_id"),
                "sku_id": run_event.get("sku_id"),
                "item_name": run_event.get("item_name"),
                "item_role": run_event.get("item_role"),
                "start_seconds": run_start,
                "end_seconds": event["event_relative_seconds"],
                "start_event_id": run_event.get("event_id"),
                "end_event_id": event.get("event_id"),
                "attribution_status": "matched",
                "basis": "consecutive_current_item_observations",
                "note": "区间由前后两次当前商品观测界定；不代表屏幕每一秒都已核验",
            })
        run_start, run_event = event["event_relative_seconds"], event
    return intervals


def summarize_product_events(events, intervals=()):
    events = list(events)
    intervals = list(intervals)
    by_role, by_status, by_api = {}, {}, {}
    item_ids, sku_ids, price_roles = set(), set(), {}
    with_price = with_promotion = with_item_id = with_sku = multiple_prices = 0
    for event in events:
        by_role[event.get("item_role") or "unknown"] = by_role.get(event.get("item_role") or "unknown", 0) + 1
        by_status[event.get("attribution_status") or "unknown"] = by_status.get(event.get("attribution_status") or "unknown", 0) + 1
        by_api[event.get("source_api") or ""] = by_api.get(event.get("source_api") or "", 0) + 1
        if event.get("item_id"):
            item_ids.add(str(event["item_id"]))
            with_item_id += 1
        if event.get("sku_id"):
            sku_ids.add(str(event["sku_id"]))
            with_sku += 1
        observations = event.get("price_observations") or []
        if observations:
            with_price += 1
        if len(observations) > 1:
            multiple_prices += 1
        if event.get("promotion_price") is not None:
            with_promotion += 1
        for observation in observations:
            role = observation.get("role") or "unknown"
            price_roles[role] = price_roles.get(role, 0) + 1
    relative = [event["event_relative_seconds"] for event in events
                if isinstance(event.get("event_relative_seconds"), (int, float))]
    return {
        "schema_version": SCHEMA_VERSION,
        "event_count": len(events),
        "interval_count": len(intervals),
        "distinct_item_ids": len(item_ids),
        "distinct_sku_ids": len(sku_ids),
        "events_with_item_id": with_item_id,
        "events_with_sku_id": with_sku,
        "events_with_price": with_price,
        "events_with_promotion_price": with_promotion,
        "events_with_multiple_price_observations": multiple_prices,
        "matched_event_count": by_status.get("matched", 0),
        "context_only_event_count": by_status.get("context_only", 0),
        "by_role": by_role,
        "by_attribution_status": by_status,
        "by_api": by_api,
        "by_price_role": price_roles,
        "first_event_relative_seconds": min(relative) if relative else None,
        "last_event_relative_seconds": max(relative) if relative else None,
        "note": "事件为接口观测；区间为由相邻观测推断的候选，不默认列表第一项为当前商品",
    }


def iter_product_events(responses, context):
    for response in responses or ():
        for event in extract_product_events(response, context):
            yield event


def is_product_response(response):
    return isinstance(response, dict) and api_name(response.get("url")) in PRODUCT_APIS


def latest_current_candidate(events):
    """Latest high-confidence current-item observation, never a listed fallback."""
    candidates = [event for event in events
                  if is_high_confidence_status(event.get("attribution_status"))
                  and event.get("item_role") in CURRENT_ROLES]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.get("response_sequence") or 0,
                                             item.get("received_at") or 0))
