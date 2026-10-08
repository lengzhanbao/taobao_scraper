"""Synthetic URL management checks. Never touch research input files."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

from src.control.url_lists import inspect, parse_input, save_list, correct_count, canonical_live_id
from src.control.settings import read_url_list, defaults
from src.control.server import Manager


class UrlListsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / '_control' / 'selfchecks' / ('urls_' + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.path = self.root / 'urls_1.txt'

    def test_validation_and_dedup(self):
        text = 'https://tbzb.taobao.com/live?liveId=123,已录制2/3\n123\n# 停用\n'
        self.assertEqual(parse_input(text), {'123': 2})
        for bad in ('https://evil.example/?liveId=123', 'https://taobao.com.evil.example/?liveId=123', 'abc', 'https://tbzb.taobao.com/live?liveId=abc', '1' * 31):
            with self.assertRaises(ValueError):
                parse_input(bad)
        with self.assertRaises(ValueError):
            parse_input('123\ninvalid')

    def test_replace_preserves_counts_backup_and_omitted(self):
        original = '# 人工备注\nhttps://tbzb.taobao.com/live?liveId=123,已录制2/3\nhttps://tbzb.taobao.com/live?liveId=456,已录制1/3\n'.encode('utf-8-sig')
        self.path.write_bytes(original)
        document = inspect(self.path, 4)
        result = save_list(self.path, 4, '123\n789', document['revision'])
        self.assertEqual(Path(result['backup']).read_bytes(), original)
        self.assertIn('# 历史保留 https://tbzb.taobao.com/live?liveId=456', result['text'])
        self.assertEqual([(r['live_id'], r['count']) for r in read_url_list(self.path, 4)], [('123', 2), ('789', 0)])
        result = save_list(self.path, 4, '456', result['revision'], 'append')
        counts = {row['live_id']: row['count'] for row in read_url_list(self.path, 4)}
        self.assertEqual(counts, {'456': 1, '123': 2, '789': 0})

    def test_invalid_or_stale_never_overwrites(self):
        self.path.write_text('123', encoding='utf-8')
        doc = inspect(self.path, 3)
        self.path.write_text('456', encoding='utf-8')
        with self.assertRaises(ValueError):
            save_list(self.path, 3, '789', doc['revision'])
        self.assertEqual(self.path.read_text(), '456')
        with self.assertRaises(ValueError):
            save_list(self.path, 3, 'invalid', inspect(self.path, 3)['revision'])
        self.assertEqual(self.path.read_text(), '456')

    def test_empty_list_soft_disables_and_missing_file_creation(self):
        result = save_list(self.path, 3, '123', hashlib.sha256(b'').hexdigest())
        self.assertIsNone(result['backup'])
        result = save_list(self.path, 3, '', result['revision'])
        self.assertEqual(read_url_list(self.path, 3), [])
        self.assertIn('liveId=123', result['text'])

    def test_repeated_save_deduplicates_history_and_backups(self):
        self.path.write_text('https://tbzb.taobao.com/live?liveId=123,已录制2/3\n', encoding='utf-8')
        document = inspect(self.path, 3)
        saved = save_list(self.path, 3, '456', document['revision'])
        first_backup = saved['backup']
        saved = save_list(self.path, 3, '456', saved['revision'])
        self.assertIsNone(saved['backup'])
        self.assertEqual(len(list(self.root.glob('urls_1.txt.bak_*'))), 1)
        saved = save_list(self.path, 3, '456', saved['revision'])
        self.assertEqual(len(list(self.root.glob('urls_1.txt.bak_*'))), 1)
        self.assertEqual(saved['text'].count('liveId=123'), 1)
        self.assertTrue(Path(first_backup).is_file())

    def test_exact_count_correction_updates_active_and_history_with_backup(self):
        original = ('\ufeff# 历史保留 https://tbzb.taobao.com/live?liveId=123,已录制5/3\r\n'
                    'https://tbzb.taobao.com/live?liveId=123,已录制3/3\r\n').encode('utf-8')
        self.path.write_bytes(original)
        document = inspect(self.path, 3)
        corrected = correct_count(self.path, 3, '123', 1, document['revision'])
        self.assertEqual(Path(corrected['backup']).read_bytes(), original)
        self.assertEqual(corrected['text'].count('已录制1/3'), 2)
        self.assertTrue(self.path.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'\r\n', self.path.read_bytes())
        self.assertEqual(read_url_list(self.path, 3)[0]['count'], 1)
        # Normal URL saves retain their safety guard, now based on corrected evidence.
        save_list(self.path, 3, '123', corrected['revision'])
        self.assertEqual(read_url_list(self.path, 3)[0]['count'], 1)
        self.assertTrue(Path(corrected['backup']).is_file())
        with self.assertRaises(ValueError):
            correct_count(self.path, 3, '123', 0, document['revision'])

    def test_correction_requires_active_room_and_integer(self):
        self.path.write_text('# https://tbzb.taobao.com/live?liveId=123,已录制2/3\n', encoding='utf-8')
        document = inspect(self.path, 3)
        with self.assertRaisesRegex(ValueError, '启用行'):
            correct_count(self.path, 3, '123', 1, document['revision'])
        with self.assertRaises(ValueError):
            correct_count(self.path, 3, '123', True, document['revision'])

    def test_correction_accepts_numeric_or_taobao_url(self):
        self.path.write_text('https://tbzb.taobao.com/live?liveId=123,已录制2/3\n', encoding='utf-8')
        document = inspect(self.path, 3)
        self.assertEqual(canonical_live_id(' 123 '), '123')
        self.assertEqual(canonical_live_id('https://tbzb.taobao.com/live?liveId=123'), '123')
        corrected = correct_count(self.path, 3, 'https://tbzb.taobao.com/live?liveId=123', 1,
                                  document['revision'])
        self.assertEqual(read_url_list(self.path, 3)[0]['count'], 1)
        self.assertEqual(Path(corrected['backup']).read_text(encoding='utf-8'),
                         'https://tbzb.taobao.com/live?liveId=123,已录制2/3\n')

    def test_url_file_lock_serializes_cross_process_correction(self):
        self.path.write_text('https://tbzb.taobao.com/live?liveId=123,已录制2/3\n', encoding='utf-8')
        document = inspect(self.path, 3)
        ready = self.root / 'lock-held.txt'
        script = """
import sys, time
from pathlib import Path
from src.control.url_lists import url_file_lock
with url_file_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text('locked', encoding='utf-8')
    time.sleep(0.6)
"""
        child = subprocess.Popen([sys.executable, '-c', script, str(self.path), str(ready)],
                                 cwd=Path(__file__).resolve().parents[1],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), 'child process failed to acquire URL lock')
            started = time.monotonic()
            correct_count(self.path, 3, '123', 1, document['revision'])
            waited = time.monotonic() - started
            self.assertGreaterEqual(waited, 0.35)
            self.assertEqual(read_url_list(self.path, 3)[0]['count'], 1)
        finally:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)

    def test_manager_uses_configured_file_only(self):
        manager = Manager(defaults(self.root / 'study'), control=self.root / 'control')
        document = manager.url_document(1)
        result = manager.save_urls({'instance_id': 1, 'text': '123\n123', 'revision': document['revision']})
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['active_text'], 'https://tbzb.taobao.com/live?liveId=123,已录制0/3')
        corrected = manager.correct_url_count({'instance_id': 1,
            'live_id': 'https://tbzb.taobao.com/live?liveId=123', 'count': 1,
            'reason': 'synthetic URL-form correction', 'confirmed': True,
            'revision': result['revision']})
        self.assertEqual(corrected['rows'][0]['count'], 1)
        self.assertTrue(Path(result['path']).is_relative_to(self.root / 'study'))
        for identifier in (0, 6, True, '../other'):
            with self.assertRaises(ValueError):
                manager.url_document(identifier)

    def test_settings_migration_fills_new_known_fields_and_backs_up(self):
        initial = defaults(self.root / 'study')
        control = self.root / 'control'
        control.mkdir()
        saved = {key: value for key, value in initial.items() if key != 'launch_gap_seconds'}
        saved['max_minutes'] = 13
        saved['instances'] = [{key: value for key, value in row.items() if key != 'segments'}
                              for row in saved['instances'][:2]]
        settings_path = control / 'settings.json'
        settings_path.write_text(json.dumps(saved), encoding='utf-8')
        manager = Manager(initial, control=control)
        self.assertEqual(manager.settings['launch_gap_seconds'], initial['launch_gap_seconds'])
        self.assertEqual(manager.settings['max_minutes'], 13)
        self.assertEqual([row['segments'] for row in manager.settings['instances']], [3, 3, 3, 4, 3])
        backups = list(control.glob('settings.json.bak_*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding='utf-8')), saved)

    def test_settings_unknown_or_corrupt_file_is_retained(self):
        initial = defaults(self.root / 'study')
        control = self.root / 'control'
        control.mkdir()
        settings_path = control / 'settings.json'
        saved = dict(initial, unexpected=True)
        settings_path.write_text(json.dumps(saved), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '不认识'):
            Manager(initial, control=control)
        self.assertEqual(json.loads(settings_path.read_text(encoding='utf-8')), saved)
        settings_path.write_text('{ broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '保留原文件'):
            Manager(initial, control=control)
        self.assertEqual(settings_path.read_text(encoding='utf-8'), '{ broken')

    def test_strict_version_gate(self):
        from src.control.server import compatible_existing_service
        state = {'code_root': 'E:/app', 'version': '2.4-local-panel'}
        self.assertFalse(compatible_existing_service(state, 'E:/app', '2.5-local-panel'))
        state['version'] = '2.5-local-panel'
        self.assertTrue(compatible_existing_service(state, 'E:/app', '2.5-local-panel'))
        self.assertFalse(compatible_existing_service(state, 'E:/other', '2.5-local-panel'))
