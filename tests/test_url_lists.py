"""Synthetic URL management checks. Never touch research input files."""
import hashlib
from pathlib import Path
import unittest
import uuid

from src.control.url_lists import inspect, parse_input, save_list
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

    def test_manager_uses_configured_file_only(self):
        manager = Manager(defaults(self.root / 'study'), control=self.root / 'control')
        document = manager.url_document(1)
        result = manager.save_urls({'instance_id': 1, 'text': '123\n123', 'revision': document['revision']})
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['active_text'], 'https://tbzb.taobao.com/live?liveId=123,已录制0/3')
        self.assertTrue(Path(result['path']).is_relative_to(self.root / 'study'))
        for identifier in (0, 6, True, '../other'):
            with self.assertRaises(ValueError):
                manager.url_document(identifier)
