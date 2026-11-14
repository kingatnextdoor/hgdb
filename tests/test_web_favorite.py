# -*- coding: utf-8 -*-
"""收藏功能：接口语义、落盘、数据集合并、筛选排序、坏文件降级。

收藏是展示站自己写的第二份人工数据（第一份是 tag_edits.json），所以重点验：
  · 收藏 / 取消都是幂等的，连点不会出怪状态
  · 写进 favorites.json，进程重启后还在
  · 数据集里合成的 favorite / faved_at 能被列表、筛选、排序、统计共用
  · favorites.json 里指向已消失剧集的条目不影响统计
"""
import json
import tempfile
import unittest
from pathlib import Path

from tests.web_support import build_output, record
from web import api, config
from web.store import Store


def params(**kwargs):
    return {k: [v] for k, v in kwargs.items()}


class FavoriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = build_output(
            self.tmp.name,
            records=[record(drama_id='1', title='都市之王'),
                     record(drama_id='2', title='古装传')],
        )
        self.store = Store(self.root)

    def call(self, method, path, **kwargs):
        return api.dispatch(self.store, method, path,
                            kwargs.pop('params', {}), kwargs.pop('body', None))

    def favorites_file(self):
        return Path(self.root) / config.FAVORITES_FILE

    def write_favorites(self, mapping):
        """直接写文件再强制重载：验证「外部改文件也能被看见」。"""
        self.favorites_file().write_text(
            json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
        self.store.load(force=True)

    def test_favorite_then_unfavorite(self):
        status, payload = self.call('POST', '/api/drama/1/favorite')
        self.assertEqual(status, 200)
        self.assertTrue(payload['favorite'])
        self.assertTrue(payload['faved_at'])
        self.assertEqual(payload['total'], 1)
        self.assertTrue(payload['item']['favorite'])
        self.assertIn('1', json.loads(self.favorites_file().read_text('utf-8')))

        status, payload = self.call('DELETE', '/api/drama/1/favorite')
        self.assertEqual(status, 200)
        self.assertFalse(payload['favorite'])
        self.assertIsNone(payload['faved_at'])
        self.assertEqual(payload['total'], 0)
        self.assertNotIn('1', json.loads(self.favorites_file().read_text('utf-8')))

    def test_favorite_is_idempotent(self):
        _, first = self.call('POST', '/api/drama/1/favorite')
        _, second = self.call('POST', '/api/drama/1/favorite')
        # 再收藏一次不能把「什么时候开始追的」改掉
        self.assertEqual(first['faved_at'], second['faved_at'])
        self.assertEqual(second['total'], 1)

    def test_unfavorite_when_not_favorited(self):
        status, payload = self.call('DELETE', '/api/drama/2/favorite')
        self.assertEqual(status, 200)
        self.assertFalse(payload['favorite'])
        self.assertEqual(payload['total'], 0)

    def test_unknown_drama_is_404(self):
        self.assertEqual(self.call('POST', '/api/drama/999/favorite')[0], 404)
        self.assertEqual(self.call('DELETE', '/api/drama/999/favorite')[0], 404)

    def test_persists_across_store_instances(self):
        self.call('POST', '/api/drama/1/favorite')
        again = Store(self.root)          # 相当于重启服务
        self.assertTrue(again.get()['by_id']['1']['favorite'])

    def test_list_payload_has_favorite_flag(self):
        self.call('POST', '/api/drama/1/favorite')
        _, listing = self.call('GET', '/api/dramas', params=params(size='10'))
        by_id = {item['id']: item for item in listing['items']}
        self.assertTrue(by_id['1']['favorite'])
        self.assertFalse(by_id['2']['favorite'])
        # 收藏时间只在详情返回，列表不带（省流量）
        self.assertNotIn('faved_at', by_id['1'])

    def test_detail_carries_faved_at(self):
        _, fav = self.call('POST', '/api/drama/1/favorite')
        _, detail = self.call('GET', '/api/drama/1')
        self.assertTrue(detail['favorite'])
        self.assertEqual(detail['faved_at'], fav['faved_at'])

    def test_only_fav_filter(self):
        self.call('POST', '/api/drama/2/favorite')
        _, listing = self.call('GET', '/api/dramas', params=params(only='fav'))
        self.assertEqual([item['id'] for item in listing['items']], ['2'])

    def test_sort_by_faved_time(self):
        self.write_favorites({'1': '2026-01-01T00:00:00',
                              '2': '2026-05-05T00:00:00'})
        _, desc = self.call('GET', '/api/dramas',
                            params=params(only='fav', sort='faved', order='desc'))
        self.assertEqual([item['id'] for item in desc['items']], ['2', '1'])
        _, asc = self.call('GET', '/api/dramas',
                           params=params(only='fav', sort='faved', order='asc'))
        self.assertEqual([item['id'] for item in asc['items']], ['1', '2'])

    def test_stats_ignore_missing_dramas(self):
        # 999 那条剧的 jsonl 已经不在了：文件里留着没关系，统计只算现有剧
        self.write_favorites({'1': '2026-01-01T00:00:00',
                              '999': '2026-01-01T00:00:00'})
        _, payload = self.call('GET', '/api/stats')
        self.assertEqual(payload['stats']['favorite'], 1)

    def test_broken_file_degrades_to_empty(self):
        self.favorites_file().write_text('{坏 JSON', encoding='utf-8')
        self.store.load(force=True)
        _, payload = self.call('GET', '/api/stats')
        self.assertEqual(payload['stats']['favorite'], 0)
        # 坏文件之后照样能收藏，并且写回合法 JSON
        status, payload = self.call('POST', '/api/drama/1/favorite')
        self.assertEqual(status, 200)
        self.assertTrue(payload['favorite'])
        saved = json.loads(self.favorites_file().read_text('utf-8'))
        self.assertIn('1', saved)


if __name__ == '__main__':
    unittest.main()
