# -*- coding: utf-8 -*-
"""compact_exports.py 的测试：合并规则、备份、校验、参数行为。"""
import json
import tempfile
import unittest
from pathlib import Path

from tests.web_support import record
import compact_exports as ce


def dump(path, records):
    with open(path, 'w', encoding='utf-8') as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')


class CompactTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        # 旧文件：A 全字段；新文件：A 只带标题（其余置空，模拟增量缺字段）+ 新剧 B
        dump(self.out / 'duanju_20260101_000000.jsonl',
             [record(drama_id='1', title='旧标题A', score=6.0)])
        dump(self.out / 'duanju_20260102_000000.jsonl',
             [record(drama_id='1', title='新标题A', score=None, score_text=None,
                     episode_total=None, episode_text=None, update_ts=None,
                     update_time=None, tags=None, category_name=None),
              record(drama_id='2', title='剧B')])

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_merges_with_blank_not_overriding(self):
        merged, records = ce.load(self.out)
        self.assertEqual(len(records), 3)
        self.assertEqual(set(merged), {'1', '2'})
        self.assertEqual(merged['1']['title'], '新标题A')   # 新覆盖旧
        self.assertEqual(merged['1']['score'], 6.0)          # 新文件没给 score，保留旧值

    def test_write_creates_snapshot(self):
        merged, _ = ce.load(self.out)
        path = ce.write_merged(self.out, merged, stamp='20260103_000000')
        self.assertEqual(path.name, 'duanju_20260103_000000.jsonl')
        lines = path.read_text(encoding='utf-8').strip().split('\n')
        self.assertEqual(len(lines), 2)
        rec = json.loads(lines[0])
        self.assertIn('drama_id', rec)

    def test_backup_moves_old_files_only(self):
        merged, _ = ce.load(self.out)
        new_path = ce.write_merged(self.out, merged, stamp='20260103_000000')
        backup_dir, moved = ce.backup_old(self.out, [new_path],
                                          stamp='20260103_000000')
        self.assertEqual(moved, ['duanju_20260101_000000.jsonl',
                                 'duanju_20260102_000000.jsonl'])
        self.assertFalse(list(self.out.glob('duanju_2026010[12]*.jsonl')))
        self.assertEqual(len(list(backup_dir.glob('*.jsonl'))), 2)
        # 移走旧的之后，回读结果应当与内存合并结果一致
        self.assertTrue(ce.verify(self.out, merged))

    def test_verify_detects_mismatch(self):
        merged, _ = ce.load(self.out)
        merged['1']['title'] = '被篡改的标题'
        self.assertFalse(ce.verify(self.out, merged))

    def test_main_dry_run_writes_nothing(self):
        before = sorted(p.name for p in self.out.glob('*.jsonl'))
        self.assertEqual(ce.main(['--out-dir', str(self.out)]), 0)                    # 默认试运行
        self.assertEqual(sorted(p.name for p in self.out.glob('*.jsonl')), before)

    def test_main_write_compacts(self):
        self.assertEqual(ce.main(['--write', '--out-dir', str(self.out)]), 0)
        jsonls = sorted(p.name for p in self.out.glob('*.jsonl'))
        self.assertEqual(len(jsonls), 1)                    # 只剩压实快照
        backups = [d for d in self.out.glob('exports_backup_*')]
        self.assertEqual(len(backups), 1)                   # 旧文件在备份目录
        merged, _ = ce.load(self.out)
        self.assertEqual(set(merged), {'1', '2'})
        self.assertEqual(merged['1']['score'], 6.0)

    def test_main_write_keep_old(self):
        self.assertEqual(ce.main(['--write', '--keep-old', '--out-dir', str(self.out)]), 0)
        jsonls = list(self.out.glob('duanju_*.jsonl'))
        self.assertEqual(len(jsonls), 3)                    # 旧的 2 个 + 新快照
        self.assertEqual([d.name for d in self.out.glob('exports_backup_*')], [])

    def test_main_refuses_single_file(self):
        for path in list(self.out.glob('duanju_*.jsonl'))[1:]:
            path.unlink()
        with self.assertRaises(SystemExit):
            ce.main(['--out-dir', str(self.out)])


if __name__ == '__main__':
    unittest.main()
