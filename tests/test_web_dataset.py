# -*- coding: utf-8 -*-
"""数据集合并规则的测试：新覆盖旧、空值不覆盖、状态库不兜底、标签编辑优先级。"""
import tempfile
import unittest
from pathlib import Path

from tests.web_support import build_output, record
from web import config, dataset


class MergeRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, **kwargs):
        build_output(self.root, **kwargs)
        return dataset.load(self.root)

    def test_new_batch_overrides_old(self):
        data = self.load(
            old_records=[record(title='旧标题', score=6.0)],
            records=[record(title='新标题', score=8.5)],
        )
        item = data['by_id']['1']
        self.assertEqual(item['title'], '新标题')
        self.assertEqual(item['score'], 8.5)

    def test_blank_does_not_override(self):
        """详情页字段只有开了 follow_detail 的那批才有，不能被后来的空值冲掉。"""
        data = self.load(
            old_records=[record(author='某作者', views=1234)],
            records=[record(author=None, views=0)],
        )
        item = data['by_id']['1']
        self.assertEqual(item['author'], '某作者')
        self.assertEqual(item['views'], 1234)

    def test_zero_is_empty_only_for_known_fields(self):
        """ZERO_IS_EMPTY 里的 0 当空值；不在里面的 0 是真值。"""
        data = self.load(
            old_records=[record(score=7.0, episode_total=80)],
            records=[record(score=0, episode_total=0)],
        )
        item = data['by_id']['1']
        self.assertEqual(item['score'], 7.0)
        self.assertEqual(item['episode_total'], 80)

    def test_state_status_never_becomes_a_field(self):
        """jsonl 里的 state_status（new/changed/unchanged）是抓取结果，不是上下架。

        曾经把它直接当 `status`，于是默认筛选 active 把整站筛空。现在数据集里
        根本没有 status 这个字段，任何在架/下架语义都不该回来。
        """
        data = self.load(records=[record(state_status='new')])
        self.assertNotIn('status', data['by_id']['1'])
        self.assertNotIn('active', data['stats'])
        self.assertNotIn('gone', data['stats'])
    def test_tag_edits_override_and_mark(self):
        data = self.load(records=[record(tags=['都市'])], edits={'1': ['自定义', '标签']})
        item = data['by_id']['1']
        self.assertEqual(item['tags'], ['自定义', '标签'])
        self.assertTrue(item['tags_edited'])

    def test_empty_tag_edit_is_valid(self):
        """用户把标签删光也要生效，不能被「空值不覆盖」规则吃掉。"""
        data = self.load(records=[record(tags=['都市'])], edits={'1': []})
        self.assertEqual(data['by_id']['1']['tags'], [])

    def test_facets_follow_edits(self):
        data = self.load(
            records=[record(drama_id='1', tags=['都市']), record(drama_id='2', tags=['都市'])],
            edits={'1': ['古装']},
        )
        tags = {t['name']: t['count'] for t in data['facets']['tags']}
        self.assertEqual(tags['都市'], 1)
        self.assertEqual(tags['古装'], 1)

    def test_cover_url_only_when_file_exists(self):
        data = self.load(
            records=[
                record(drama_id='1', cover_local=str(self.root / 'covers' / '1_剧.jpg')),
                record(drama_id='2', cover_local=str(self.root / 'covers' / 'gone.jpg')),
            ],
            covers=[('1_剧.jpg', b'\xff\xd8\xff\xe0fake')],
        )
        self.assertEqual(data['by_id']['1']['cover'], '/covers/1_%E5%89%A7.jpg')
        self.assertIsNone(data['by_id']['2']['cover'])
        self.assertEqual(data['stats']['with_cover'], 1)

    def test_unknown_tag_edit_ignored(self):
        """编辑文件里残留了库里没有的 id（比如剧被清库了）不该炸。"""
        data = self.load(records=[record()], edits={'不存在': ['x']})
        self.assertEqual(data['stats']['total'], 1)

    def test_broken_edit_file_ignored(self):
        build_output(self.root, records=[record()])
        (self.root / 'tag_edits.json').write_text('{坏 JSON', encoding='utf-8')
        data = dataset.load(self.root)
        self.assertEqual(data['by_id']['1']['tags'], ['都市'])

    def test_empty_output_dir(self):
        data = dataset.load(self.root)
        self.assertEqual(data['stats']['total'], 0)
        self.assertEqual(data['items'], [])
        self.assertEqual(data['facets']['tags'], [])


class SchemaVersionTest(unittest.TestCase):
    """数据契约版本：展示站靠它判断自己读不读得懂这批数据。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_missing_version_counts_as_one(self):
        """拆仓之前导出的老数据没有这个字段，不能因此被判成「版本过低」。"""
        build_output(self.root, records=[record()])
        stats = dataset.load(self.root)['stats']
        self.assertEqual(stats['schema_version'], 1)
        self.assertEqual(stats['schema_supported'], config.SCHEMA_VERSION)

    def test_newer_version_is_reported_not_rejected(self):
        """对面升到 99，我们照样把看得懂的字段显示出来，只把版本号报上去。"""
        build_output(self.root, records=[record(schema_version=99)])
        data = dataset.load(self.root)
        self.assertEqual(data['stats']['schema_version'], 99)
        self.assertEqual(data['stats']['total'], 1)

    def test_garbage_version_does_not_crash(self):
        build_output(self.root, records=[record(schema_version='不是数字')])
        self.assertEqual(dataset.load(self.root)['stats']['schema_version'], 1)


class ListPayloadTest(unittest.TestCase):
    def test_list_payload_has_no_detail(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        build_output(tmp.name, records=[record()])
        item = dataset.load(tmp.name)['by_id']['1']
        payload = dataset.list_payload(item)
        self.assertNotIn('detail', payload)
        self.assertIn('tags', payload)
        self.assertFalse(payload['tags_edited'])


if __name__ == '__main__':
    unittest.main()
