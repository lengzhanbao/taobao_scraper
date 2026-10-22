"""Conservative response-to-navigation attribution helpers."""


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
    ids = {str(item) for item in live_ids if item is not None}
    if not ids:
        return "unknown"
    if len(ids) > 1:
        return "conflict"
    if observed_live_id is None:
        return "unscoped"
    return "matched" if str(observed_live_id) in ids else "conflict"
