# -*- coding: utf-8 -*-
"""刮削匹配测试：文件名清洗 -> 四级匹配引擎 -> 落库 -> 接口。

匹配是纯函数（web/scrape.py），不碰库；仓库与接口的测试用临时 sqlite。
"""
import tempfile
import unittest
from pathlib import Path

from tests.web_support import build_output, record
from web import api, dbio, scrape, videodb
from web.store import Store

DRAMAS = [
    {'id': '101', 'title': '50元买断人妻'},
    {'id': '102', 'title': '神医毒妃'},
    {'id': '103', 'title': '神医毒妃（2026）'},
    {'id': '104', 'title': '遇见你之后'},
    {'id': '105', 'title': '爱情公寓'},
]


def params(**kwargs):
    return {k: [v] for k, v in kwargs.items()}


# ------------------------------ 引擎：清洗与解析 ------------------------------

class CleanTest(unittest.TestCase):
    def test_clean_stem_strips_site_and_noise(self):
        self.assertEqual(scrape.clean_stem('www.98T.la@神医毒妃'), '神医毒妃')
        self.assertEqual(scrape.clean_stem('神医毒妃 1080P 国语'), '神医毒妃')
        self.assertEqual(scrape.clean_stem('神医毒妃【4K HDR】'), '神医毒妃')
        self.assertEqual(scrape.clean_stem('神医毒妃（2026）'), '神医毒妃')
        self.assertEqual(scrape.clean_stem('神医毒妃 - '), '神医毒妃')

    def test_clean_stem_keeps_normal_title(self):
        self.assertEqual(scrape.clean_stem('遇见你之后'), '遇见你之后')
        # 数字年份不在括号里也不带「完整版」等词时，不会被误删
        self.assertEqual(scrape.clean_stem('爱你2026'), '爱你2026')

    def test_clean_stem_empty(self):
        self.assertEqual(scrape.clean_stem(''), '')
        self.assertEqual(scrape.clean_stem(None), '')
        self.assertEqual(scrape.clean_stem('www.98T.la@'), '')


class EpisodeTest(unittest.TestCase):
    def test_parse_episode_variants(self):
        self.assertEqual(scrape.parse_episode('神医毒妃-011'), ('神医毒妃', 11, '011'))
        self.assertEqual(scrape.parse_episode('神医毒妃 第12集'), ('神医毒妃', 12, '第12集'))
        self.assertEqual(scrape.parse_episode('神医毒妃.EP03'), ('神医毒妃', 3, '03'))
        self.assertEqual(scrape.parse_episode('神医毒妃-01-10 合集'), ('神医毒妃', 1, '01-10'))

    def test_parse_episode_without_number(self):
        title, episode, label = scrape.parse_episode('神医毒妃')
        self.assertEqual((title, episode, label), ('神医毒妃', None, ''))


# ------------------------------ 引擎：四级匹配 ------------------------------

class MatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = scrape.TitleMatcher(DRAMAS)

    def test_exact(self):
        r = self.m.match('www.98T.la@50元买断人妻-011')
        self.assertEqual(r['status'], 'matched')
        self.assertEqual(r['level'], 'exact')
        self.assertEqual(r['drama_id'], '101')
        self.assertEqual(r['episode'], 11)

    def test_exact_after_cleaning(self):
        r = self.m.match('神医毒妃【4K HDR 国语】')
        self.assertEqual(r['status'], 'matched')
        self.assertEqual(r['drama_id'], '102')

    def test_contains(self):
        r = self.m.match('神医毒妃 独家番外')
        self.assertEqual(r['status'], 'matched')
        self.assertEqual(r['level'], 'contains')
        self.assertIn(r['drama_id'], ('102', '103'))

    def test_fuzzy_auto(self):
        # 相似度足够高（>= 0.85）时自动确认
        r = self.m.match('遇见你之后 完整版')
        self.assertEqual(r['drama_id'], '104')

    def test_fuzzy_pending_with_candidates(self):
        r = self.m.match('爱情公玉')
        self.assertEqual(r['status'], 'pending')
        self.assertEqual(r['level'], 'fuzzy')
        self.assertTrue(r['candidates'])
        self.assertEqual(r['candidates'][0]['title'], '爱情公寓')

    def test_unmatched(self):
        r = self.m.match('完全无关的节目名称')
        self.assertEqual(r['status'], 'unmatched')
        self.assertEqual(r['drama_id'], None)
        self.assertEqual(r['score'], 0.0)

    def test_unmatched_pure_number(self):
        self.assertEqual(self.m.match('12345')['status'], 'unmatched')

    def test_duplicate_titles_go_pending(self):
        m = scrape.TitleMatcher([
            {'id': 'a', 'title': '同名剧'}, {'id': 'b', 'title': '同名剧'}])
        r = m.match('同名剧-01')
        self.assertEqual(r['status'], 'pending')
        self.assertEqual(len(r['candidates']), 2)

    def test_match_files_batch(self):
        rows = scrape.match_files(['神医毒妃-01', '乱七八糟'], DRAMAS)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['status'], 'matched')
        self.assertEqual(rows[1]['status'], 'unmatched')
        self.assertEqual(rows[0]['stem'], '神医毒妃-01')


# ------------------------------ 仓库 ------------------------------

class RepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = videodb.VideoRepo(self.tmp.name, dbio.Config())
        self.repo.import_scan('media', [
            {'rel_path': 'a.mp4', 'name': '神医毒妃-01.mp4', 'stem': '神医毒妃-01',
             'ext': '.mp4', 'size': 1},
            {'rel_path': 'b.mp4', 'name': '爱情公玉.mp4', 'stem': '爱情公玉',
             'ext': '.mp4', 'size': 2},
        ], 0)
        self.files = self.repo.files_of_root('media')

    def test_files_of_root(self):
        self.assertEqual(len(self.files), 2)
        self.assertEqual(self.files[0]['match_status'], 'unmatched')   # 新列默认值
        self.assertEqual(self.repo.files_of_root('nope'), [])

    def test_apply_matches_and_stats(self):
        result = self.repo.apply_matches('media', [
            {'id': self.files[0]['id'], 'drama_id': '102', 'episode': 1,
             'episode_label': '01', 'match_status': 'matched', 'match_score': 0.95},
            {'id': self.files[1]['id'], 'match_status': 'pending'},
        ])
        self.assertEqual(result['matched'], 1)
        self.assertEqual(result['review'], 1)
        self.assertEqual(result['applied'], 2)

        stats = self.repo.scrape_stats()
        self.assertEqual(stats['files'], 2)
        self.assertEqual(stats['matched'], 1)
        self.assertEqual(stats['pending'], 1)

        row = self.repo.scrape_batch(result['batch_id'])
        self.assertEqual(row['root'], 'media')
        self.assertEqual(row['matched'], 1)

    def test_apply_respects_explicit_rows_only(self):
        """没交上来的行不动：预览里放弃的文件不该被顺手覆盖。"""
        self.repo.apply_matches('media', [
            {'id': self.files[0]['id'], 'drama_id': '102',
             'match_status': 'matched', 'match_score': 0.95},
        ])
        again = self.repo.files_of_root('media')
        self.assertEqual(again[1]['match_status'], 'unmatched')

    def test_set_match_status_transitions(self):
        fid = self.files[1]['id']
        self.assertEqual(self.repo.set_match(fid, 'ignored'), 1)
        self.assertEqual(self.repo.scrape_stats()['ignored'], 1)
        self.assertEqual(self.repo.set_match(fid, 'unmatched'), 1)
        self.assertEqual(self.repo.scrape_stats()['ignored'], 0)

    def test_set_match_bad_status(self):
        with self.assertRaises(ValueError):
            self.repo.set_match(1, 'haha')

    def test_set_match_missing_file(self):
        self.assertEqual(self.repo.set_match(999, 'ignored'), 0)

    def test_clear_wipes_scrape_batch(self):
        self.repo.apply_matches('media', [
            {'id': self.files[0]['id'], 'drama_id': '102',
             'match_status': 'matched', 'match_score': 0.95}])
        self.repo.clear()
        self.assertEqual(self.repo.scrape_stats()['files'], 0)
        self.assertEqual(self.repo.scrape_stats()['batches'], [])


# ------------------------------ 接口 ------------------------------

class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(
            build_output(self.tmp.name, records=[
                record('101', '神医毒妃'), record('102', '遇见你之后'),
                record('103', '爱情公寓')]),
            dbio.Config())
        api.dispatch(self.store, 'POST', '/api/video/scan', {}, {'folder': 'media',
            'files': [
                {'path': 'media/神医毒妃-01.mp4', 'size': 1},
                {'path': 'media/sc/遇见你之后 第02集.mp4', 'size': 2},
                {'path': 'media/爱情公玉.mp4', 'size': 3},
                {'path': 'media/完全无关.mp4', 'size': 4},
            ]})

    def test_scan_is_dry_run(self):
        code, p = api.dispatch(self.store, 'POST', '/api/scrape/scan', {},
                               {'root': 'media'})
        self.assertEqual(code, 200)
        self.assertEqual((p['matched'], p['review'], p['unmatched']), (2, 1, 1))
        # dry-run：库里还是 unmatched
        still = self.store.video.files_of_root('media')
        self.assertTrue(all(f['match_status'] == 'unmatched' for f in still))
        by_stem = {i['stem']: i for i in p['items']}
        self.assertEqual(by_stem['神医毒妃-01']['match_title'], '神医毒妃')
        self.assertEqual(by_stem['神医毒妃-01']['episode'], 1)
        self.assertEqual(by_stem['爱情公玉']['match_status'], 'pending')
        self.assertEqual(by_stem['爱情公玉']['match_title'], '爱情公寓')

    def test_scan_unknown_root(self):
        for body in ({}, {'root': ''}, {'root': 'nope'}, None):
            code, p = api.dispatch(self.store, 'POST', '/api/scrape/scan', {}, body)
            self.assertEqual(code, 400, body)
            self.assertIn('error', p)

    def test_apply_writes_and_records_batch(self):
        code, p = api.dispatch(self.store, 'POST', '/api/scrape/apply', {},
                               {'root': 'media'})
        self.assertEqual(code, 200)
        self.assertEqual(p['applied'], 4)
        self.assertEqual(p['batch_id'], 1)

        rows = self.store.video.files_of_root('media')
        matched = {r['stem']: r for r in rows if r['match_status'] == 'matched'}
        self.assertEqual(set(matched), {'神医毒妃-01', '遇见你之后 第02集'})
        self.assertEqual(matched['神医毒妃-01']['drama_id'], '101')
        self.assertEqual(matched['神医毒妃-01']['episode'], 1)

        code, p = api.dispatch(self.store, 'GET', '/api/scrape/report/1', {}, None)
        self.assertEqual(code, 200)
        self.assertEqual(p['batch']['matched'], 2)
        code, _p = api.dispatch(self.store, 'GET', '/api/scrape/report/99', {}, None)
        self.assertEqual(code, 404)

    def test_apply_is_idempotent_and_force_redoes(self):
        api.dispatch(self.store, 'POST', '/api/scrape/apply', {}, {'root': 'media'})
        first = api.dispatch(self.store, 'POST', '/api/scrape/apply', {}, {'root': 'media'})[1]
        self.assertEqual(first['matched'], 2)     # 重复执行结果一致
        forced = api.dispatch(self.store, 'POST', '/api/scrape/apply', {},
                              {'root': 'media', 'force': True})[1]
        self.assertEqual(forced['matched'], 2)    # force 重算一遍也一致

    def test_ignored_is_sticky(self):
        api.dispatch(self.store, 'POST', '/api/scrape/apply', {}, {'root': 'media'})
        # 人工忽略「爱情公玉」所在的待确认行（id=3）
        api.dispatch(self.store, 'POST', '/api/scrape/match', {},
                     {'file_id': 3, 'status': 'ignored'})
        for force in (False, True):
            _code, p = api.dispatch(self.store, 'POST', '/api/scrape/apply', {},
                                    {'root': 'media', 'force': force})
            # ignored 的行以 decision=kept 出现（连 force 也不覆盖）
            self.assertIn(3, [r['id'] for r in p['items']
                              if r.get('decision') == 'kept'])
        self.assertEqual(self.store.video.files_of_root('media')[2]['match_status'],
                         'ignored')

    def test_manual_match_and_reset(self):
        code, p = api.dispatch(self.store, 'POST', '/api/scrape/match', {},
                               {'file_id': 3, 'status': 'matched', 'drama_id': '103'})
        self.assertEqual(code, 200)
        row = self.store.video.files_of_root('media')[2]
        self.assertEqual((row['match_status'], row['drama_id']), ('matched', '103'))

        # drama 不存在 -> 404；状态不认识 -> 400；文件不存在 -> 404
        self.assertEqual(api.dispatch(self.store, 'POST', '/api/scrape/match', {},
                                      {'file_id': 3, 'status': 'matched',
                                       'drama_id': '999'})[0], 404)
        self.assertEqual(api.dispatch(self.store, 'POST', '/api/scrape/match', {},
                                      {'file_id': 3, 'status': 'haha'})[0], 400)
        self.assertEqual(api.dispatch(self.store, 'POST', '/api/scrape/match', {},
                                      {'file_id': 999, 'status': 'ignored'})[0], 404)

        code, p = api.dispatch(self.store, 'POST', '/api/scrape/match', {},
                               {'file_id': 3, 'status': 'unmatched'})
        self.assertEqual(code, 200)

    def test_manual_match_survives_auto_scrape(self):
        """人工改选的行（score=1.0）在自动刮削里保留，只有 force 才按名字重猜。"""
        api.dispatch(self.store, 'POST', '/api/scrape/match', {},
                     {'file_id': 4, 'status': 'matched', 'drama_id': '103'})
        code, p = api.dispatch(self.store, 'POST', '/api/scrape/apply', {},
                               {'root': 'media'})
        self.assertEqual(code, 200)
        row = next(r for r in self.store.video.files_of_root('media') if r['id'] == 4)
        self.assertEqual((row['match_status'], row['drama_id']), ('matched', '103'))
        self.assertEqual(row['match_score'], 1.0)
        self.assertIn(4, [i['id'] for i in p['items'] if i.get('decision') == 'kept'])
        self.assertEqual(p['matched'], 3)          # 2 条自动 + 1 条人工

        api.dispatch(self.store, 'POST', '/api/scrape/apply', {},
                     {'root': 'media', 'force': True})
        row = next(r for r in self.store.video.files_of_root('media') if r['id'] == 4)
        self.assertEqual(row['match_status'], 'unmatched')   # force 才会重算

    def test_status_route(self):
        code, p = api.dispatch(self.store, 'GET', '/api/scrape/status', {}, None)
        self.assertEqual(code, 200)
        self.assertEqual(p['files'], 4)
        self.assertEqual([r['root'] for r in p['roots']], ['media'])

    def test_unknown_endpoint(self):
        code, _p = api.dispatch(self.store, 'GET', '/api/scrape/whatever', {}, None)
        self.assertEqual(code, 404)


if __name__ == '__main__':
    unittest.main()
