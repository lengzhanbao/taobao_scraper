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


def correct_count(path, target, live_id, count, expected_revision):
    """Set one room's counter exactly, including retained history, with a full backup."""
    path = Path(path)
    if not isinstance(live_id, str) or not live_id.isdigit() or len(live_id) > 30:
        raise ValueError('直播 ID 无效')
    if type(count) is not int or not 0 <= count <= 10000:
        raise ValueError('更正段数必须是 0—10000 的整数')
    current = inspect(path, target)
    if current['revision'] != expected_revision:
        raise ValueError('网址文件已被其他操作修改。请重新加载后再更正。')
    original = path.read_bytes() if path.exists() else b''
    newline = '\r\n' if b'\r\n' in original else '\n'
    bom = b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b''
    lines = current['text'].splitlines()
    active_matches = 0
    output_lines = []
    for line in lines:
        match = re.search(r'liveId=(\d+)\b', line)
        if not match or match.group(1) != live_id:
            output_lines.append(line)
            continue
        if not line.lstrip().startswith('#'):
            active_matches += 1
        marker = f'已录制{count}/{target}'
        if re.search(r'已录制\d+/\d+', line):
            line = re.sub(r'已录制\d+/\d+', marker, line)
        else:
            line = line.rstrip() + ',' + marker
        output_lines.append(line)
    if active_matches == 0:
        raise ValueError('当前清单中找不到这个直播 ID 的启用行')
    output = newline.join(output_lines) + (newline if current['text'].endswith(('\n', '\r')) else '')
    data = bom + output.encode('utf-8')
    if data == original:
        return {**current, 'backup': None, 'rooms': active_matches}

    path.parent.mkdir(parents=True, exist_ok=True)
    content_hash = revision(original)
    backup = path.with_name(path.name + '.bak_' + content_hash)
    if backup.exists():
        if revision(backup.read_bytes()) != content_hash:
            raise ValueError('同名备份已存在但内容校验不一致，原清单未修改')
    else:
        shutil.copy2(path, backup)
    temp = path.with_name(path.name + '.tmp_' + uuid.uuid4().hex)
    with temp.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    if inspect(path, target)['revision'] != expected_revision:
        raise ValueError('更正过程中原文件发生变化，备份与临时文件均保留。请重新加载。')
    os.replace(temp, path)
    result = inspect(path, target)
    result.update({'backup': str(backup), 'rooms': active_matches})
    return result
