# -*- coding: utf-8 -*-
"""筛选 / 排序 / 分页的测试。纯函数，不需要文件也不需要服务器。"""
import unittest

from web import config, queries


def item(drama_id, **extra):
    base = {
        'id': str(drama_id), 'title': '剧%s' % drama_id, 'score': 8.0,
        'episode_total': 60, 'views': 100, 'update_ts': 1000,
        'tags': ['都市'], 'category': '短剧',
        'is_new': False, 'is_original': False, 'cover': '/covers/x.jpg',
        'author': None, 'desc': '', 'first_seen': '2026-01-01',
        'last_changed': '2026-01-02', 'detail': {'episodes': []},
    }
    base.update(extra)
    return base


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.items = [
            item(1, title='都市之王', score=9.2, tags=['都市', '现代'], is_new=True),
            item(2, title='古装传', score=7.0, tags=['古装']),
            item(3, title='都市爱情', score=8.8, tags=['都市'], author='某人',
                 detail={'episodes': [{'no': 1}]}),
            item(4, title='无封面', cover=None, tags=[]),
        ]

    def ids(self, **params):
        wrapped = {k: [v] for k, v in params.items()}
        return [i['id'] for i in queries.filter_sort(self.items, wrapped)]

    def test_no_params_returns_everything(self):
        """没有默认筛选：jsonl 里有就返回，不存在「在架 / 下架」这回事。"""
        self.assertEqual(self.ids(), ['1', '2', '3', '4'])

    def test_search_matches_title_id_tag_author(self):
        self.assertEqual(self.ids(q='都市'), ['1', '3'])
        self.assertEqual(self.ids(q='某人'), ['3'])
        self.assertEqual(self.ids(q='3'), ['3'])

    def test_multi_tag_requires_all(self):
        self.assertEqual(self.ids(tag='都市'), ['1', '3'])
        self.assertEqual(self.ids(tag='都市,现代'), ['1'])

    def test_min_score(self):
        self.assertEqual(self.ids(min_score='8.5'), ['1', '3'])

    def test_only_filters(self):
        self.assertEqual(self.ids(only='new'), ['1'])
        self.assertEqual(self.ids(only='detail'), ['3'])
        self.assertEqual(self.ids(only='cover'), ['1', '2', '3'])

    def test_only_video(self):
        """has_video 由数据集/视频库打标，筛选只是读这个标记。"""
        self.items[2]['has_video'] = True
        self.assertEqual(self.ids(only='video'), ['3'])
        # 没打标的剧默认不在结果里（老数据 / 视频库为空）
        self.assertEqual(self.items[0].get('has_video'), None)

    def test_only_multi_is_intersection(self):
        """多选开关取交集（与 tag 一致）：点得越多范围越窄。"""
        self.items[2]['has_video'] = True
        self.assertEqual(self.ids(only='cover,detail'), ['3'])
        self.assertEqual(self.ids(only='cover,detail,video'), ['3'])
        self.assertEqual(self.ids(only='new,cover'), ['1'])
        # 1 是新上架但没有详情，交集为空
        self.assertEqual(self.ids(only='new,detail'), [])
        # 逗号两侧的空格要能容忍（手改 URL / 复制粘贴常带）
        self.assertEqual(self.ids(only=' cover , detail '), ['3'])

    def test_only_unknown_or_empty_values_ignored(self):
        """未知取值只跳过自己，不能让整条筛选失效变成「返回全部」。"""
        self.assertEqual(self.ids(only='cover,nope'), ['1', '2', '3'])
        self.assertEqual(self.ids(only='nope'), ['1', '2', '3', '4'])
        self.assertEqual(self.ids(only=''), ['1', '2', '3', '4'])
        self.assertEqual(self.ids(only=','), ['1', '2', '3', '4'])

    def test_only_ed2k_and_video_intersect(self):
        """两个「外部库」开关也能叠加：交集。"""
        self.items[0]['has_ed2k'] = True
        self.items[1]['has_ed2k'] = True
        self.items[1]['has_video'] = True
        self.assertEqual(self.ids(only='ed2k'), ['1', '2'])
        self.assertEqual(self.ids(only='ed2k,video'), ['2'])
        self.assertEqual(self.ids(only='ed2k,fav'), [])

    def test_category(self):
        self.assertEqual(self.ids(category='短剧'), ['1', '2', '3', '4'])
        self.assertEqual(self.ids(category='不存在'), [])

    def test_invalid_min_score_treated_as_zero(self):
        self.assertEqual(len(self.ids(min_score='abc')), 4)


class SortTest(unittest.TestCase):
    def test_sort_by_score_desc(self):
        items = [item(1, score=7), item(2, score=9)]
        out = queries.filter_sort(items, {'sort': ['score']})
        self.assertEqual([i['id'] for i in out], ['2', '1'])

    def test_unknown_sort_falls_back_to_update(self):
        items = [item(1, update_ts=1), item(2, update_ts=5)]
        out = queries.filter_sort(items, {'sort': ['乱写']})
        self.assertEqual([i['id'] for i in out], ['2', '1'])

    def test_asc_order(self):
        items = [item(1, update_ts=1), item(2, update_ts=5)]
        out = queries.filter_sort(items, {'order': ['asc']})
        self.assertEqual([i['id'] for i in out], ['1', '2'])

    def test_mixed_types_do_not_crash(self):
        """同一字段混了 str 和 None 时排序会 TypeError，必须兜住而不是 500。"""
        items = [item(1, title='a'), item(2, title=None)]
        out = queries.filter_sort(items, {'sort': ['title']})
        self.assertEqual(len(out), 2)


class PagingTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(queries.parse_paging({}), (1, config.DEFAULT_PAGE_SIZE))

    def test_size_is_clamped(self):
        self.assertEqual(queries.parse_paging({'size': ['9999']})[1], config.MAX_PAGE_SIZE)
        self.assertEqual(queries.parse_paging({'size': ['0']})[1], 1)

    def test_bad_values_fall_back(self):
        self.assertEqual(queries.parse_paging({'page': ['x'], 'size': ['y']}),
                         (1, config.DEFAULT_PAGE_SIZE))

    def test_paginate(self):
        info = queries.paginate(list(range(10)), page=2, size=4)
        self.assertEqual(info['total'], 10)
        self.assertEqual(info['pages'], 3)
        self.assertEqual(info['chunk'], [4, 5, 6, 7])

    def test_paginate_beyond_end(self):
        info = queries.paginate([1, 2], page=9, size=4)
        self.assertEqual(info['chunk'], [])
        self.assertEqual(info['pages'], 1)


if __name__ == '__main__':
    unittest.main()
