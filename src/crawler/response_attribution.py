"""Conservative response-to-navigation attribution helpers."""
from src.utils.api_names import api_name

STATUSES = ("matched", "context_only", "conflict", "unknown")
LEGACY_STATUSES = {"unscoped": "context_only"}


def normalize_attribution_status(value):
    """Map persisted grades to the current vocabulary, including legacy ``unscoped``."""
    if not isinstance(value, str):
        return "unknown"
    value = value.strip().lower()
    value = LEGACY_STATUSES.get(value, value)
    return value if value in STATUSES else "unknown"


def is_high_confidence_status(value):
    """Only an explicit current-room liveId match may feed strict room statistics."""
    return normalize_attribution_status(value) == "matched"


def detail_live_urls(value):
    """Return (live_id, url) pairs only when both fields share one object."""
    pairs = []

    def walk(node, inherited_live_id=None):
        if isinstance(node, dict):
            live_id = node.get("liveId", inherited_live_id)
            live_url = node.get("liveUrl")
            if live_id is not None and isinstance(live_url, str) and live_url:
                pairs.append((str(live_id), live_url))
            for child in node.values():
                walk(child, str(live_id) if live_id is not None else inherited_live_id)
        elif isinstance(node, list):
            for child in node:
                walk(child, inherited_live_id)

    walk(value)
    return pairs


def attribution_status(live_ids, observed_live_id, navigation_id=None):
    """Grade a response without treating page context as room identity.

    matched: the response carries the current room's liveId.
    context_only: no liveId evidence, but a navigation context exists.
    conflict: multiple liveIds, or a liveId that contradicts the current room.
    unknown: no liveId and no navigation context.
    """
    ids = {str(item) for item in live_ids if item is not None}
    if len(ids) > 1:
        return "conflict"
    if not ids:
        return "context_only" if navigation_id else "unknown"
    if observed_live_id is None:
        return "context_only" if navigation_id else "unknown"
    return "matched" if str(observed_live_id) in ids else "conflict"


def attribution_basis(status, navigation_id=None):
    status = normalize_attribution_status(status)
    if status == "matched":
        return "response_live_id_match"
    if status == "context_only":
        return "receive_navigation_context_only" if navigation_id else "no_live_id_evidence"
    if status == "conflict":
        return "response_live_id_conflict"
    return "no_live_id_evidence"


def summarize_attribution(responses):
    """Count grades and API sources without dropping or reinterpreting raw rows."""
    by_status = {name: 0 for name in STATUSES}
    by_api = {}
    total = 0
    for response in responses or ():
        if not isinstance(response, dict):
            continue
        total += 1
        status = normalize_attribution_status(response.get("attribution_status"))
        by_status[status] = by_status.get(status, 0) + 1
        url = str(response.get("url", ""))
        api = api_name(url)
        if api:
            by_api[api] = by_api.get(api, 0) + 1
    return {"schema_version": 1, "total": total, "by_status": by_status,
            "high_confidence": by_status["matched"], "by_api": by_api,
            "policy": "only matched rows enter strict room statistics; raw rows stay in the journal"}
