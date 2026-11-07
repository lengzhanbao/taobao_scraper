"""Shared API-name extraction and product endpoint recognition."""
import re

PRODUCT_APIS = (
    "mtop.tblive.live.item.callback.query",
    "mtop.tblive.live.item.getvideodetailitemlistwithpagination",
    "mtop.roomstudio.live.detail.get",
)
CURRENT_ITEM_APIS = (
    "mtop.tblive.live.item.callback.query",
    "mtop.roomstudio.live.detail.get",
)


def api_name(url):
    """Extract the mtop API segment used by existing grouping code."""
    url_clean = re.sub(r"_(\d{6,})\b", "", str(url or ""))
    match = re.search(r"mtop\.[\w.]+?(?=[/\?])", url_clean)
    return match.group(0) if match else ""


def product_item_lists(api, parsed):
    """Yield (path, item_role, role_evidence, items) for known product payloads.

    Only structures observed in captured Taobao responses are listed. The
    callback and detail payloads expose ``data.curItemList``; the pagination API
    exposes ``data.itemListv1[].liveItemDO``. Missing structures yield nothing
    instead of falling back to arbitrary first elements.
    """
    if not isinstance(parsed, dict):
        return
    data = parsed.get("data")
    if not isinstance(data, dict):
        return
    if api in CURRENT_ITEM_APIS:
        items = data.get("curItemList")
        if isinstance(items, list):
            yield ("data.curItemList", "current_candidate",
                   "callback_speaking_item_marker" if api.endswith("callback.query")
                   else "detail_cur_item_list", items)
    if api == "mtop.tblive.live.item.getvideodetailitemlistwithpagination":
        entries = data.get("itemListv1")
        if isinstance(entries, list):
            items = [entry.get("liveItemDO") for entry in entries
                     if isinstance(entry, dict) and isinstance(entry.get("liveItemDO"), dict)]
            if items:
                yield ("data.itemListv1[].liveItemDO", "listed",
                       "videodetail_item_list", items)
