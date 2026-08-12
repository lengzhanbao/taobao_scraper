# -*- coding: utf-8 -*-
"""更新 urls 状态（基于有 _final.json 的片段）。用法: python update_urls_v2.py [--apply]"""
import os, sys

config = r'E:/直播爬取/直播研究数据/_config'
staging = r'E:/直播爬取/直播研究数据/_staging'
sessions = r'E:/直播爬取/直播研究数据/sessions'
APPLY = '--apply' in sys.argv

# sessions 已归档 liveId
sess_ids = set()
for d in os.listdir(sessions):
    dp = os.path.join(sessions, d)
    if os.path.isdir(dp):
        lid = d.rsplit('_', 1)[-1]
        if lid.isdigit():
            sess_ids.add(lid)

def count_final_segs(lid):
    """统计 staging 中该房间有 _final.json 的段数"""
    n = 0
    for browser in os.listdir(staging):
        rpath = os.path.join(staging, browser, 'room_' + lid)
        if not os.path.isdir(rpath):
            continue
        for seg in os.listdir(rpath):
            spath = os.path.join(rpath, seg)
            if not os.path.isdir(spath):
                continue
            if any('_final.json' in f for f in os.listdir(spath)):
                n += 1
    return n

max_rounds = {'urls_1.txt': 3, 'urls_2.txt': 3, 'urls_3.txt': 3, 'urls_4.txt': 4, 'urls_5.txt': 3}

total_changes = 0
for fname, max_r in max_rounds.items():
    fpath = os.path.join(config, fname)
    if not os.path.exists(fpath):
        print('WARN: {} 不存在，跳过'.format(fname))
        continue
    with open(fpath, encoding='utf-8') as f:
        lines = f.readlines()
    lines_out = []
    changes = 0
    for line in lines:
        line_s = line.rstrip('\n')
        if not line_s.strip():
            lines_out.append(line_s)
            continue
        parts = line_s.split(',')
        url = parts[0]
        lid = url.split('liveId=')[-1] if 'liveId=' in url else ''
        old_status = parts[-1].strip()

        # 实际有效段数
        if lid in sess_ids:
            actual = max_r  # 已归档 = 满段
        else:
            actual = count_final_segs(lid)
            if actual > max_r:
                actual = max_r
        new_status = '已录制{}/{}'.format(actual, max_r)

        # 规则：待录制且无数据 -> 保持原样
        if ('待录制' in old_status or '待' in old_status) and actual == 0:
            lines_out.append(line_s)
            continue
        # 已经一致 -> 不变
        if old_status == new_status:
            lines_out.append(line_s)
            continue

        # 需要修改：保留 url 和店名，只改状态
        if len(parts) >= 3:
            new_line = '{},{},{}'.format(parts[0], parts[1], new_status)
        else:
            new_line = '{},{}'.format(parts[0], new_status)
        changes += 1
        total_changes += 1
        print('  {}: {} [{}] -> [{}]'.format(fname, lid, old_status, new_status))
        lines_out.append(new_line)

    if APPLY:
        with open(fpath, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(lines_out) + '\n')
        print('OK {}: 已写入 ({} 处修改)'.format(fname, changes))
    else:
        print('PREVIEW {}: {} 处修改 (未写入)'.format(fname, changes))

print('总计修改: {} 处 | 模式: {}'.format(total_changes, 'APPLY(已写入)' if APPLY else 'DRY-RUN(仅预览)'))
