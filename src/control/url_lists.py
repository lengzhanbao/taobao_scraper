"""Editable URL lists with retained history, backups and concurrent-edit checks."""
import hashlib
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlparse, parse_qs
import uuid


def revision(data):
    return hashlib.sha256(data).hexdigest()


def parse_input(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 48000:
        raise ValueError('网址清单最多 48 KB，请分批添加')
    rows = {}
    invalid = []
    for index, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.search(r'https?://[^\s，,]+', line)
        live_id = None
        if line.isdigit() and len(line) <= 30:
            live_id = line
        elif match:
            url = urlparse(match.group(0))
            host = (url.hostname or '').lower()
            if host == 'taobao.com' or host.endswith('.taobao.com'):
                value = parse_qs(url.query).get('liveId', [''])[0]
                if value.isdigit() and len(value) <= 30:
                    live_id = value
        if live_id is None:
            invalid.append(str(index))
            continue
        count = re.search(r'已录制(\d+)/(\d+)', line)
        recorded = int(count.group(1)) if count else 0
        rows[live_id] = max(recorded, rows.get(live_id, 0))
    if invalid:
        raise ValueError('以下行没有有效的淘宝 liveId 链接或纯数字 ID：' + '、'.join(invalid[:20]))
    return rows


def inspect(path, target):
    path = Path(path)
    data = path.read_bytes() if path.exists() else b''
    text = data.decode('utf-8-sig')
    return {'path': str(path), 'text': text, 'revision': revision(data), 'target': target}


def save_list(path, target, text, expected_revision, mode='replace'):
    """Omitted entries become comments; originals and exact bytes are backed up."""
    path = Path(path)
    current = inspect(path, target)
    if expected_revision != current['revision']:
        raise ValueError('网址文件已被其他操作修改。请重新加载后再保存；页面草稿仍保留。')
    if mode not in ('replace', 'append'):
        raise ValueError('网址操作无效')
    incoming = parse_input(text)
    # Preserve the highest known counter across active and previously disabled lines.
    existing = {}
    history = {}
    other_comments = []
    for line in current['text'].splitlines():
        match = re.search(r'liveId=(\d+)\b', line)
        count = re.search(r'已录制(\d+)/(\d+)', line)
        if match:
            key = match.group(1)
            recorded = int(count.group(1)) if count else 0
            existing[key] = max(existing.get(key, 0), recorded)
            if line.lstrip().startswith('#'):
                history[key] = max(history.get(key, 0), recorded)
            elif mode == 'replace' and key not in incoming:
                history[key] = max(history.get(key, 0), recorded)
        elif line.lstrip().startswith('#'):
            other_comments.append(line)
    if mode == 'append':
        # Disabled entries stay disabled unless explicitly added again.
        for line in current['text'].splitlines():
            if not line.lstrip().startswith('#'):
                match = re.search(r'liveId=(\d+)\b', line)
                if match:
                    incoming.setdefault(match.group(1), existing[match.group(1)])
    lines = list(dict.fromkeys(line for line in other_comments if line.strip()))
    for live_id, count in history.items():
        lines.append(f'# 历史保留 https://tbzb.taobao.com/live?liveId={live_id},已录制{count}/{target}')
    for live_id, count in incoming.items():
        lines.append(f'https://tbzb.taobao.com/live?liveId={live_id},已录制{max(count, existing.get(live_id, 0))}/{target}')
    output = '\n'.join(lines) + ('\n' if lines else '')
    if output.encode('utf-8') == current['text'].encode('utf-8'):
        return {**current, 'backup': None, 'rooms': len(incoming)}
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        content_hash = revision(path.read_bytes())
        backup = path.with_name(path.name + '.bak_' + content_hash)
        if backup.exists():
            if revision(backup.read_bytes()) != content_hash:
                raise ValueError('同名备份已存在但内容校验不一致，原清单未修改')
        else:
            shutil.copy2(path, backup)
    temp = path.with_name(path.name + '.tmp_' + uuid.uuid4().hex)
    with temp.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(output)
        stream.flush()
        os.fsync(stream.fileno())
    # A second revision check catches external writes during backup preparation.
    if inspect(path, target)['revision'] != expected_revision:
        raise ValueError('保存过程中原文件发生变化，备份与临时草稿均保留。请重新加载。')
    os.replace(temp, path)
    result = inspect(path, target)
    result.update({'backup': str(backup) if backup else None, 'rooms': len(incoming)})
    return result
