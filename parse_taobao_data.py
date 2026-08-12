# -*- coding: utf-8 -*-
"""淘宝直播数据解析 → sessions/<店铺>/crawler/ CSV"""
import json, re, os, glob, csv, subprocess, shutil, sys

# 控制台编码兜底：子进程管道输出默认 GBK，中文/emoji 会导致父进程 UTF-8 解码崩溃
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

STUDY_ROOT = r"E:\直播爬取\直播研究数据"
SESSIONS = os.path.join(STUDY_ROOT, "sessions")
os.makedirs(SESSIONS, exist_ok=True)

def fmt(ts):
    if not ts: return ""
    try:
        t = int(str(ts)[:10])
        import datetime
        return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    except: return str(ts)[:19]

def safe_name(s):
    return re.sub(r'[\\/*?:"<>|\s]', '_', s).strip('_')

def api_name(url):
    url_clean = re.sub(r'_(\d{6,})\b', '', url)
    m = re.search(r'mtop\.[\w.]+?(?=[/\?])', url_clean)
    return m.group(0) if m else ""

def strip_jsonp(s):
    if not isinstance(s, str): return None
    s = s.strip()
    i = s.find("{"); j = s.rfind("}")
    if i == -1 or j == -1: return None
    try: return json.loads(s[i:j+1])
    except: return None

def deep_find(obj, key):
    if obj is None: return None
    if isinstance(obj, dict):
        if key in obj: return obj[key]
        for v in obj.values():
            r = deep_find(v, key)
            if r is not None: return r
    elif isinstance(obj, list):
        for v in obj:
            r = deep_find(v, key)
            if r is not None: return r
    return None

def walk_find_list(obj, path):
    cur = [obj]
    for p in path:
        nxt = []
        for c in cur:
            if isinstance(c, dict):
                v = c.get(p)
                if isinstance(v, list): nxt.extend(v)
                elif isinstance(v, dict): nxt.append(v)
        cur = nxt
    return cur

def write_csv(path, rows, fieldnames, overwrite=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = []
    existing_tags = set()
    if not overwrite and os.path.exists(path):
        try:
            for r in csv.DictReader(open(path, encoding="utf-8-sig")):
                tag = r.get("录制编号", "")
                if not tag:
                    existing.append(r)  # 弹幕等无录制编号，直接保留
                elif tag not in existing_tags:
                    existing.append(r)
                    existing_tags.add(tag)
        except: pass
    # 新行去重（仅对有录制编号的summary行）
    new_tags = set()
    deduped = []
    for r in rows:
        tag = r.get("录制编号", "")
        if not tag:
            deduped.append(r)  # 弹幕行不参与去重
        elif tag not in existing_tags and tag not in new_tags:
            deduped.append(r)
            new_tags.add(tag)
    all_rows = existing + deduped
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})

def process_room(room_dir):
    """一个房间所有段 → 1个店铺文件夹 + 1汇总CSV + N弹幕CSV
    步骤: 1)复制JSON到sessions/raw 2)从副本解析"""
    files = []
    for root, dirs, filenames in os.walk(room_dir):
        for f in filenames:
            if f.startswith("data_") and f.endswith("_final.json"):
                files.append(os.path.join(root, f))
    files.sort(key=os.path.getmtime)
    if not files:
        print("  无 _final.json")
        return

    # —— 从第一个文件提取店铺名 ——
    first = json.load(open(files[0], encoding="utf-8"))
    groups_tmp = {}
    for c in first.get("responses", []):
        an = api_name(c.get("url", ""))
        if an: groups_tmp.setdefault(an, []).append(c)
    dc_tmp = groups_tmp.get("mtop.roomstudio.live.detail.get", [])
    live_tmp = {}
    for c in dc_tmp:
        o = strip_jsonp(c.get("body"))
        if not o: continue
        for key in ["title","liveTitle","accountName","anchorName"]:
            v = deep_find(o, key)
            if v is not None and not live_tmp.get(key): live_tmp[key] = v
    _title_tmp = live_tmp.get("title") or live_tmp.get("liveTitle") or ""
    _anchor_tmp = live_tmp.get("accountName") or live_tmp.get("anchorName") or ""
    live_id = os.path.basename(room_dir).replace("room_", "")
    base_name = safe_name(_title_tmp + "_" + _anchor_tmp) if (_title_tmp and _anchor_tmp) else "unknown"
    pair_str = f"{base_name}_{live_id}"

    # —— 复制 JSON 到 sessions/raw ——
    raw_dir = os.path.join(SESSIONS, pair_str, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    copied = []
    for fp in files:
        dst = os.path.join(raw_dir, os.path.basename(fp))
        if not os.path.exists(dst):
            shutil.copy2(fp, dst)
        copied.append(dst)
    print(f"  复制 {len(copied)} 个JSON到 {raw_dir}")

    # —— 从副本解析 ——
    rows = []
    for fi, fp in enumerate(copied):
        print(f"  段{fi+1}: {os.path.basename(fp)[:30]}...")
        data = json.load(open(fp, encoding="utf-8"))

        groups = {}
        for c in data.get("responses", []):
            an = api_name(c.get("url", ""))
            if an: groups.setdefault(an, []).append(c)

        dc = groups.get("mtop.roomstudio.live.detail.get", [])
        cc = groups.get("mtop.taobao.iliad.comment.query.latest", [])

        # 解析后的 detail.get 响应
        dc_parsed = [strip_jsonp(c.get("body")) for c in dc]
        dc_parsed = [o for o in dc_parsed if o]

        live = {}
        for o in dc_parsed:
            for key in ["title","liveTitle","accountName","anchorName","viewCount",
                       "praiseCount","fansNum","shopId","bizCode","accountId",
                       "isDigitalAnchorLive","categoryLevelOneName",
                       "headImg","coverImg","backgroundImageURL",
                       "liveIntroduction","curItemNum"]:
                v = deep_find(o, key)
                if v is not None and not live.get(key): live[key] = v

        _title = live.get("title") or live.get("liveTitle") or ""
        _anchor = live.get("accountName") or live.get("anchorName") or ""

        # 主播认证 / 代理店 / 回头客（anchornavigation）
        nav = groups.get("mtop.tblive.live.shopwindow.anchornavigation", [])
        anchor_cert = ""; returning_count = ""; agent_shop_flag = ""
        for c in nav:
            o = strip_jsonp(c.get("body"))
            if not o: continue
            ret = o.get("ret", [])
            if isinstance(ret, list) and ret and any("SESSION_EXPIRED" in str(r) for r in ret): continue
            certs = o["data"].get("anchorCertificationTags") if o.get("data") else None
            if not certs: certs = deep_find(o, "anchorCertificationTags") or []
            texts = []
            for it in certs:
                t = it.get("text") or it.get("certTitle") or ""
                if t: texts.append(t)
            if texts: anchor_cert = "；".join(texts)
            rc = deep_find(o, "returning")
            if rc is not None: returning_count = rc
            ag = o["data"].get("agentShop") if o.get("data") else None
            if ag is None: ag = deep_find(o, "agentShop")
            if isinstance(ag, str): agent_shop_flag = "是" if ag.lower() == "true" else "否"

        # 商品信息：callback.query → curItemList[0]，备用 videodetail → itemListv1[0].liveItemDO
        _item_name = ""; _item_price = ""; _live_promo_price = ""
        # 方式1: item.callback.query → curItemList
        cq = groups.get("mtop.tblive.live.item.callback.query", [])
        if cq:
            cq_parsed = [strip_jsonp(c.get("body","")) for c in cq if strip_jsonp(c.get("body",""))]
            if cq_parsed:
                cil = deep_find(cq_parsed[-1], "curItemList") or []
                if isinstance(cil, list) and cil:
                    _item_name = cil[0].get("itemName", "")
                    p = cil[0].get("itemPrice")
                    if p is not None: _item_price = p
                    lip = cil[0].get("liveItemPrice") or {}
                    pp = lip.get("promotionPrice") if isinstance(lip, dict) else None
                    if pp:
                        try: _live_promo_price = int(pp) / 100
                        except: _live_promo_price = pp
        # 方式2: item.getvideodetailitemlistwithpagination → itemListv1[0].liveItemDO
        if not _item_name:
            il = groups.get("mtop.tblive.live.item.getvideodetailitemlistwithpagination", [])
            if il:
                il_parsed = [strip_jsonp(c.get("body","")) for c in il if strip_jsonp(c.get("body",""))]
                if il_parsed:
                    ilv1 = deep_find(il_parsed[-1], "itemListv1") or []
                    if isinstance(ilv1, list) and ilv1:
                        ldo = ilv1[0].get("liveItemDO") if isinstance(ilv1[0], dict) else None
                        if ldo:
                            _item_name = ldo.get("itemName", "")
                            p2 = ldo.get("itemPrice")
                            if p2 is not None: _item_price = p2
                            lip2 = ldo.get("liveItemPrice") or {}
                            pp2 = lip2.get("promotionPrice") if isinstance(lip2, dict) else None
                            if pp2:
                                try: _live_promo_price = int(pp2) / 100
                                except: _live_promo_price = pp2

        # 粉丝（product_timeline优先，否则从解析后的detail.get取）
        start_fans = end_fans = None
        pt = data.get("product_timeline", [])
        if pt:
            try:
                start_fans = int(pt[0].get("fans", 0)) if pt[0].get("fans") else None
                end_fans = int(pt[-1].get("fans", 0)) if pt[-1].get("fans") else None
            except: pass
        if start_fans is None and dc_parsed:
            try:
                start_fans = int(deep_find(dc_parsed[0], "fansNum"))
                end_fans = int(deep_find(dc_parsed[-1], "fansNum"))
            except: pass
        new_fans = (end_fans - start_fans) if (isinstance(start_fans, int) and isinstance(end_fans, int)) else ""

        # 弹幕
        comments = []
        for c in cc:
            o = strip_jsonp(c.get("body"))
            if not o: continue
            # 直接取 data.comments（淘宝直播弹幕结构）
            data_part = o.get("data", {})
            cm_list = data_part.get("comments", [])
            if isinstance(cm_list, list):
                for it in cm_list:
                    comments.append({
                        "用户": it.get("publisherNick") or it.get("tbNick") or "",
                        "内容": it.get("content") or "",
                        "时间": fmt(it.get("timestamp") or it.get("createTime") or ""),
                    })
            # 备用：深层搜索
            if not cm_list:
                for lst in walk_find_list(o, ["content", "comment", "text"]):
                    for it in lst:
                        comments.append({
                            "用户": it.get("publisherNick") or it.get("tbNick") or "",
                            "内容": it.get("content") or it.get("comment") or "",
                            "时间": fmt(it.get("timestamp") or it.get("createTime") or ""),
                        })
        seen = set(); uniq_c = []
        for cm in comments:
            key = (cm["用户"], cm["内容"], cm["时间"])
            if key not in seen: seen.add(key); uniq_c.append(cm)

        ctimes = sorted(m["时间"] for m in uniq_c if m["时间"])

        tag = os.path.basename(fp).replace("data_", "").replace("_final.json", "")
        try:
            d8, t6 = tag.split("_")
            date_s = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
            cap_s = f"{t6[:2]}:{t6[2:4]}:{t6[4:]}"
        except: date_s = cap_s = ""
        rec_dur = round(data.get("record_end_t", 0) - data.get("record_start_t", 0))
        digital = "是" if str(live.get("isDigitalAnchorLive", "")).lower() == "true" else ""

        row = {
            "段": f"第{fi+1}段",
            "录制编号": tag,
            "标题": _title, "主播名": _anchor, "账号ID": live.get("accountId", ""),
            "日期": date_s, "录制时间": cap_s, "总录制时长(秒)": rec_dur,
            "录制开始时间戳": data.get("record_start_t", ""),
            "录制结束时间戳": data.get("record_end_t", ""),
            "直播间链接": data.get("live_url", ""),
            "弹幕最早": ctimes[0] if ctimes else "", "弹幕最晚": ctimes[-1] if ctimes else "",
            "弹幕数": len(uniq_c), "弹幕人数": len(set(m["用户"] for m in uniq_c if m["用户"])),
            "观看人数": live.get("viewCount", ""), "点赞数": live.get("praiseCount", ""),
            "当前商品名称": _item_name, "当前商品价格": _item_price,
            "直播专属价": _live_promo_price,
            "商品数量": live.get("curItemNum", ""),
            "直播简介": live.get("liveIntroduction", ""),
            "主播头像": live.get("headImg", ""),
            "背景图": live.get("coverImg") or live.get("backgroundImageURL") or "",
            "直播时长(分钟)": round(rec_dur / 60, 1) if rec_dur else "",
            "是否数字人": digital, "品类": live.get("categoryLevelOneName", ""),
            "店铺ID": live.get("shopId", "") or live.get("accountId", ""),
            "店铺类型": live.get("bizCode", ""),
            "主播认证": anchor_cert, "是否代理店": agent_shop_flag,
            "粉丝数(开始)": start_fans if start_fans is not None else "",
            "粉丝数(结束)": end_fans if end_fans is not None else "",
            "新增粉丝量": new_fans,
        }
        rows.append(row)

        # 该段的弹幕单独CSV
        sess_dir = os.path.join(SESSIONS, pair_str)
        cra_dir = os.path.join(sess_dir, "crawler")
        os.makedirs(cra_dir, exist_ok=True)
        cn = f"comments_第{fi+1}段_{pair_str}.csv"
        write_csv(os.path.join(cra_dir, cn), uniq_c, ["用户", "内容", "时间"], overwrite=True)
        print(f"    弹幕: {cn} ({len(uniq_c)}条)")

        # 复制该段 FLV 到 sessions/video/（命名带「标题_商家」前缀，2026-08-01 用户要求）
        vid_dir = os.path.join(sess_dir, "video")
        os.makedirs(vid_dir, exist_ok=True)
        seg_dir = os.path.dirname(files[fi])  # 原始staging段目录
        for sf in os.listdir(seg_dir):
            if sf.endswith(".flv"):
                src = os.path.join(seg_dir, sf)
                if os.path.getsize(src) == 0: continue
                dst = os.path.join(vid_dir, f"{base_name}_video_第{fi+1}段.flv")
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)
                if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
                    print(f"    📹 第{fi+1}段: {os.path.getsize(dst)/1024/1024:.0f}MB")
                break  # 每段只有一个FLV

    # 汇总CSV
    if rows:
        sess_dir = os.path.join(SESSIONS, pair_str)
        cra_dir = os.path.join(sess_dir, "crawler")
        os.makedirs(cra_dir, exist_ok=True)
        sp = os.path.join(cra_dir, f"lives_summary_{pair_str}.csv")
        write_csv(sp, rows, list(rows[0].keys()), overwrite=True)
        print(f"  汇总: lives_summary_{pair_str}.csv ({len(rows)}行)")

    # 验证全部 FLV 复制成功
    vid_dir = os.path.join(SESSIONS, pair_str, "video")
    total_segs = len(files)
    copied_flv = sum(1 for f in os.listdir(vid_dir) if f.endswith(".flv")) if os.path.isdir(vid_dir) else 0
    if copied_flv == total_segs:
        print(f"  VIDEO_COPY_OK: {copied_flv}/{total_segs}")
    else:
        print(f"  VIDEO_COPY_FAIL: {copied_flv}/{total_segs}")

    print(f"  文件夹: {pair_str}")


def main():
    import sys
    if len(sys.argv) > 1:
        process_room(sys.argv[1])
        return

    files = sorted(glob.glob(os.path.join(STUDY_ROOT, "_staging", "data_*.json")), key=os.path.getmtime)
    if not files:
        print("staging 没有 data_*.json")
        return
    print(f"发现 {len(files)} 个文件")
    for f in files:
        try: process(f)
        except Exception as e: print(f"[跳过] {e}")


if __name__ == "__main__":
    main()
