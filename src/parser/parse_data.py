# -*- coding: utf-8 -*-
"""淘宝直播数据解析 → sessions/<店铺>/crawler/ CSV"""
import json, re, os, glob, csv, subprocess, shutil, sys

# 控制台编码兜底：子进程管道输出默认 GBK，中文/emoji 会导致父进程 UTF-8 解码崩溃
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.utils.config import STUDY_ROOT, FFPROBE
from src.utils.safe_io import atomic_json, verified_copy, probe_video
from src.utils.segment_evidence import video_validation, digital_observation, file_evidence
from pathlib import Path
import uuid
from src.utils.digital_flags import (
    classify_digital_segment, find_values_by_key,
    summarize_digital_flags, summarize_room_digital_flags,
    title_keywords_in_values,
)
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
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak_" + uuid.uuid4().hex)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})

def comment_capture_state(responses, row_count):
    """An empty captured comment list differs from no capture or parse failure."""
    if not responses:
        return {"state": "not_captured", "row_count": row_count}
    failures = 0
    for response in responses:
        parsed = strip_jsonp(response.get("body"))
        if not isinstance(parsed, dict):
            failures += 1
            continue
        ret = parsed.get("ret", [])
        if ret and (not isinstance(ret, list) or any(not str(item).startswith("SUCCESS") for item in ret)):
            failures += 1
            continue
        part = parsed.get("data", {})
        known_list = part.get("comments") if isinstance(part, dict) else None
        if not isinstance(known_list, list) and not list(walk_find_list(parsed, ["content", "comment", "text"])):
            failures += 1
    return {"state": "parse_failed" if failures else "extracted" if row_count else "empty_confirmed",
            "row_count": row_count, "response_count": len(responses), "failed_response_count": failures}


def process_room(room_dir):
    """一个房间所有段 → 1个店铺文件夹 + 1汇总CSV + N弹幕CSV
    步骤: 1)复制JSON到sessions/raw 2)从副本解析"""
    files = []
    for root, dirs, filenames in os.walk(room_dir):
        for f in filenames:
            if f.startswith("data_") and f.endswith("_final.json"):
                files.append(os.path.join(root, f))
    def recording_order(path):
        with open(path, encoding="utf-8") as stream:
            metadata = json.load(stream)
        return (metadata.get("segment_index", 0), metadata.get("record_start_t", 0), os.path.basename(path))
    files.sort(key=recording_order)
    if not files:
        print("  无 _final.json")
        return False
    seen_indexes = set()
    measured = {}
    for fp in files:
        with open(fp, encoding="utf-8") as stream:
            metadata = json.load(stream)
        index = metadata.get("segment_index")
        if index is not None:
            if index in seen_indexes:
                raise ValueError(f"重复有效段编号 {index}，保留原文件，需先核对录制进度")
            seen_indexes.add(index)
        source_files = metadata.get("recorded_files", [])
        if not isinstance(source_files, list) or len(source_files) != 1:
            raise ValueError("每段原始 JSON 必须明确对应一个视频；保留源文件待核查")
        source_video = Path(source_files[0]).resolve()
        if not source_video.is_relative_to(Path(room_dir).resolve()) or not source_video.is_file():
            raise ValueError("视频缺失或路径超出本房间，保留原始数据")
        # Preflight all media before creating archive copies. Never trust wall clock.
        measured[fp] = video_validation(source_video, metadata, FFPROBE, probe=probe_video)

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
    archive_entries = []
    archive_segments = []
    for fp in files:
        dst = os.path.join(raw_dir, os.path.basename(fp))
        archive_entries.append(verified_copy(fp, dst))
        copied.append(dst)
    print(f"  复制 {len(copied)} 个JSON到 {raw_dir}")

    # —— 从副本解析 ——
    rows = []
    segment_flag_summaries = []
    segment_titles_by_row = []
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
                if v is None:
                    continue
                if key == "isDigitalAnchorLive":
                    if key not in live:
                        live[key] = v
                elif not live.get(key):
                    live[key] = v

        observation = digital_observation({**data, "video_duration_seconds": measured[files[fi]]["duration_seconds"]}, live_id)
        segment_flag_summary = observation["flag_summary"]

        _title = live.get("title") or live.get("liveTitle") or ""
        _anchor = live.get("accountName") or live.get("anchorName") or ""
        segment_title_values = [
            value
            for obj in dc_parsed
            for value in find_values_by_key(obj, "liveTitle")
        ]
        if not segment_title_values:
            segment_title_values = [
                value
                for obj in dc_parsed
                for value in find_values_by_key(obj, "title")
            ]
        title_hits = title_keywords_in_values(segment_title_values or [_title])

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
        rec_dur = measured[files[fi]]["duration_seconds"]
        comment_state = comment_capture_state(cc, len(uniq_c))
        recording_id = str(data.get("recording_id") or tag)
        segment_classification = classify_digital_segment(
            segment_flag_summary, title_hits
        )
        digital = segment_classification["digital_label"]
        sample_handling = segment_classification["sample_handling"]

        row = {
            "段": f"第{fi+1}段",
            "录制编号": tag,
            "录制唯一ID": recording_id,
            "原始段号": data.get("segment_index", fi + 1),
            "标题": _title, "主播名": _anchor, "账号ID": live.get("accountId", ""),
            "日期": date_s, "录制时间": cap_s, "总录制时长(秒)": rec_dur,
            "录制开始时间戳": data.get("record_start_t", ""),
            "录制结束时间戳": data.get("record_end_t", ""),
            "时长核验来源": "ffprobe",
            "弹幕提取状态": comment_state["state"],
            "已捕获标记是否全true": "是" if observation["all_true"] is True else
                                   "否" if observation["all_true"] is False else "未捕获",
            "标记缺失响应数": observation["missing_flag_response_count"],
            "标记无法关联响应数": observation["unlinked_response_count"],
            "录制期间标记检测次数": observation["in_recording_flagged_response_count"],
            "最大检测空窗(秒)": observation["maximum_observation_gap_seconds"],
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
            "是否数字人": digital,
            "平台原始isDigitalAnchorLive": segment_flag_summary["raw_label"],
            "段落是否全true": "是" if segment_flag_summary["all_true"] else "否",
            "段落是否含false": "是" if segment_flag_summary["has_false"] else "否",
            "数字人标题规则命中": "、".join(title_hits) or "否",
            "数字人样本处理": sample_handling,
            "品类": live.get("categoryLevelOneName", ""),
            "店铺ID": live.get("shopId", "") or live.get("accountId", ""),
            "店铺类型": live.get("bizCode", ""),
            "主播认证": anchor_cert, "是否代理店": agent_shop_flag,
            "粉丝数(开始)": start_fans if start_fans is not None else "",
            "粉丝数(结束)": end_fans if end_fans is not None else "",
            "新增粉丝量": new_fans,
        }
        rows.append(row)
        segment_flag_summaries.append(segment_flag_summary)
        segment_titles_by_row.append(title_hits)

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
        source_candidates = data.get("recorded_files", [])
        if not source_candidates:
            source_candidates = [os.path.join(seg_dir, sf) for sf in os.listdir(seg_dir) if sf.endswith(".flv")]
        if len(source_candidates) != 1:
            raise ValueError("每个有效段必须明确对应一个视频文件")
        src = Path(source_candidates[0]).resolve()
        if not src.is_file():
            src = (Path(seg_dir) / src.name).resolve()
        if not src.is_relative_to(Path(room_dir).resolve()) or not src.is_file() or src.stat().st_size == 0:
            raise ValueError("视频缺失或路径超出本房间，保留原始数据")
        duration = measured[files[fi]]["duration_seconds"]
        dst = os.path.join(vid_dir, f"{base_name}_video_第{fi+1}段.flv")
        entry = verified_copy(src, dst)
        if entry["sha256"] != measured[files[fi]]["video_sha256"]:
            raise ValueError("视频在时长核验后发生变化，源文件与副本保留待核查")
        entry.update(segment_index=data.get("segment_index", fi + 1),
                     recording_id=recording_id, duration_seconds=duration)
        archive_entries.append(entry)
        archive_segments.append({"run_id": data.get("run_id"), "recording_id": recording_id,
                                 "segment_index": data.get("segment_index", fi + 1),
                                 "raw_json": archive_entries[fi], "video": entry,
                                 "comments": file_evidence(os.path.join(cra_dir, cn)),
                                 "comment_extraction": comment_state,
                                 "duration_seconds": rec_dur,
                                 "technical_validation": measured[files[fi]],
                                 "digital_observation": observation})
        print(f"    📹 第{fi+1}段: {duration:.1f}秒，SHA256 校验通过")

    # 汇总CSV
    if rows:
        room_summary = summarize_room_digital_flags(
            segment_flag_summaries, segment_titles_by_row
        )
        for row in rows:
            row.update({
                "房间平台标记": room_summary["flag_label"],
                "房间是否全段true": "是" if room_summary["all_true"] else "否",
                "房间是否含false": "是" if room_summary["has_false"] else "否",
                "房间是否含缺失标记": "是" if room_summary["has_missing"] else "否",
                "房间true段数": room_summary["true_segment_count"],
                "房间false段数": room_summary["false_segment_count"],
                "房间缺失段数": room_summary["missing_segment_count"],
                "房间标题规则命中": "、".join(room_summary["title_matches"]) or "否",
                "房间最终归类": room_summary["classification"],
            })
            if room_summary["title_matches"]:
                row["是否数字人"] = "是"
                row["数字人样本处理"] = "保留：room标题规则覆盖"

        for row, summary, title_hits in zip(
            rows, segment_flag_summaries, segment_titles_by_row
        ):
            print(
                f"    数字人标记: 平台={summary['raw_label']}，"
                f"段落全true={'是' if summary['all_true'] else '否'}，"
                f"段落含false={'是' if summary['has_false'] else '否'}，"
                f"标题规则={'、'.join(title_hits) or '无'}，"
                f"是否数字人={row['是否数字人']}，"
                f"处理={row['数字人样本处理']}"
            )

        sess_dir = os.path.join(SESSIONS, pair_str)
        cra_dir = os.path.join(sess_dir, "crawler")
        os.makedirs(cra_dir, exist_ok=True)
        sp = os.path.join(cra_dir, f"lives_summary_{pair_str}.csv")
        write_csv(sp, rows, list(rows[0].keys()), overwrite=True)
        print(
            f"  房间数字人标记: {room_summary['flag_label']}，"
            f"true段={room_summary['true_segment_count']} "
            f"false段={room_summary['false_segment_count']} "
            f"缺失段={room_summary['missing_segment_count']}，"
            f"最终={room_summary['classification']}"
        )
        print(f"  汇总: lives_summary_{pair_str}.csv ({len(rows)}行)")

    # Evidence is per file, including source identity/hash, rather than a folder count.
    sess_dir = os.path.join(SESSIONS, pair_str)
    for index in range(1, len(files) + 1):
        comments = os.path.join(sess_dir, "crawler", f"comments_第{index}段_{pair_str}.csv")
        if not os.path.isfile(comments):
            raise ValueError("缺少段落弹幕 CSV")
    if not rows or not os.path.isfile(sp) or len(archive_entries) != 2 * len(files):
        raise ValueError("归档文件不完整")
    atomic_json(os.path.join(sess_dir, "archive_manifest.json"),
                {"schema_version": 2, "room_id": live_id, "segment_count": len(files), "entries": archive_entries,
                 "segments": archive_segments, "summary": file_evidence(sp),
                 "source_preserved": True}, backup=True)
    print(f"  ARCHIVE_COPY_OK: {len(files)} 段，逐文件校验，源文件保留")
    print(f"  文件夹: {pair_str}")
    return True


def main():
    import sys
    if len(sys.argv) > 1:
        if not process_room(sys.argv[1]):
            raise SystemExit(1)
        return

    # 全量模式：遍历 _staging/browser_*/room_*/*_final.json
    staging_root = os.path.join(STUDY_ROOT, "_staging")
    if not os.path.isdir(staging_root):
        print(f"staging 目录不存在: {staging_root}")
        return
    room_dirs = []
    for browser in sorted(os.listdir(staging_root)):
        bpath = os.path.join(staging_root, browser)
        if not os.path.isdir(bpath):
            continue
        for room in sorted(os.listdir(bpath)):
            rpath = os.path.join(bpath, room)
            if os.path.isdir(rpath) and room.startswith("room_"):
                room_dirs.append(rpath)
    if not room_dirs:
        print("staging 没有 room_* 目录")
        return
    print(f"发现 {len(room_dirs)} 个房间目录")
    for room_dir in room_dirs:
        try:
            process_room(room_dir)
        except Exception as e:
            print(f"[跳过] {room_dir}: {e}")


if __name__ == "__main__":
    main()
