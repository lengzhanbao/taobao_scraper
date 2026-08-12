# -*- coding: utf-8 -*-
"""Generate data_*_final.json for valid staging segments >= 7 minutes.

Read-only to original data; creates only final JSON files.
"""

import json
import os
import time


from config import STUDY_ROOT

STAGING = os.path.join(STUDY_ROOT, "_staging")
SESSIONS = os.path.join(STUDY_ROOT, "sessions")
MIN_SEC = 420


def get_session_ids():
    ids = set()
    if not os.path.isdir(SESSIONS):
        return ids
    for name in os.listdir(SESSIONS):
        lid = name.rsplit("_", 1)[-1]
        if os.path.isdir(os.path.join(SESSIONS, name)) and lid.isdigit():
            ids.add(lid)
    return ids


def main():
    sess_ids = get_session_ids()
    created = []
    skipped_no_json = []

    for browser in sorted(os.listdir(STAGING)):
        browser_dir = os.path.join(STAGING, browser)
        if not os.path.isdir(browser_dir):
            continue
        for room in sorted(os.listdir(browser_dir)):
            room_dir = os.path.join(browser_dir, room)
            if not os.path.isdir(room_dir):
                continue
            lid = room.replace("room_", "")
            if lid in sess_ids:
                continue
            for seg in sorted(os.listdir(room_dir)):
                seg_dir = os.path.join(room_dir, seg)
                if not os.path.isdir(seg_dir):
                    continue
                files = os.listdir(seg_dir)
                if any(f.endswith("_final.json") for f in files):
                    continue

                flvs = [
                    f for f in files
                    if f.endswith(".flv")
                    and "待删除" not in f
                    and os.path.getsize(os.path.join(seg_dir, f)) > 0
                ]
                if not flvs:
                    continue

                data_files = [
                    f for f in files
                    if f.startswith("data_") and f.endswith(".json") and "_final" not in f
                ]
                if not data_files:
                    skipped_no_json.append(os.path.join(seg_dir, "no_data_json"))
                    continue

                data_path = os.path.join(seg_dir, sorted(data_files)[-1])
                with open(data_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                start_t = data.get("record_start_t")
                if not start_t:
                    skipped_no_json.append(os.path.join(seg_dir, "no_start_t"))
                    continue

                elapsed = time.time() - float(start_t)
                if elapsed < MIN_SEC:
                    continue

                data["record_end_t"] = time.time()
                data["manual_finalize"] = True
                final_name = data_path.replace(".json", "_final.json")
                with open(final_name, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                created.append(final_name)

    print(f"created_final={len(created)}")
    for path in created:
        print(path)
    print(f"skipped_no_data_json={len(skipped_no_json)}")
    for path in skipped_no_json[:20]:
        print(path)


if __name__ == "__main__":
    main()
