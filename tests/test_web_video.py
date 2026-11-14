# -*- coding: utf-8 -*-
"""本地视频文件库测试：文件名解析 -> 目录扫描 -> 入库去重 -> 接口。

扫描对象用 tempfile 现造的小目录，不碰真实 output/video。
"""
import tempfile
import unittest
from pathlib import Path

from tests.web_support import build_output, record
from web import api, config, video, videodb
from web.store import Store


def params(**kwargs):
    return {k: [v] for k, v in kwargs.items()}


def make_video_tree(root):
    """造一个两层目录：3 个视频（两种扩展名）+ 2 个非视频 + 1 个空扩展名。"""
    root = Path(root)
    (root / 'sc').mkdir(parents=True, exist_ok=True)
    (root / 'www.98T.la@剧-01.mp4').write_bytes(b'v' * 100)
    (root / 'www.98T.la@剧-02.MP4').write_bytes(b'v' * 200)      # 大写扩展名
    (root / '剧-01-10 合集.mkv').write_bytes(b'v' * 300)
    (root / 'sc' / '第二季-01.webm').write_bytes(b'v' * 400)
    (root / '说明.txt').write_text('不是视频', encoding='utf-8')
    (root / 'sc' / 'cover.jpg').write_bytes(b'j')
    (root / 'noext').write_bytes(b'n')                            # 无扩展名
    return root


# ------------------------------ 纯函数 ------------------------------

class PureTest(unittest.TestCase):
    def test_is_video(self):
        self.assertTrue(video.is_video('sc/a.mp4'))
        self.assertTrue(video.is_video('a.MP4'))          # 大写扩展名
        self.assertTrue(video.is_video('a.mkv'))
        self.assertFalse(video.is_video('a.txt'))
        self.assertFalse(video.is_video('noext'))         # 没有扩展名
        self.assertFalse(video.is_video('.mp4'))          # 隐藏文件，不算
        self.assertFalse(video.is_video(''))
        self.assertFalse(video.is_video(None))

    def test_split_rel(self):
        fields = video.split_rel('video/sc/www.x@剧-01.mp4')
        self.assertEqual(fields['name'], 'www.x@剧-01.mp4')
        self.assertEqual(fields['stem'], 'www.x@剧-01')
        self.assertEqual(fields['ext'], '.mp4')
        self.assertEqual(fields['dirname'], 'video/sc')
        # Windows 反斜杠也认
        self.assertEqual(video.split_rel('sc\\a.webm')['dirname'], 'sc')

    def test_normalize_files(self):
        entries, skipped, invalid = video.normalize_files([
            {'path': 'video/sc/a.mp4', 'size': '123'},
            {'path': 'video\\b.MKV'},                      # 反斜杠 + 无 size
            {'path': 'video/说明.txt'},                    # 非视频 -> skipped
            {'path': ''},                                  # 空 -> invalid
            'not-a-dict',                                  # 形状不对 -> invalid
            {'path': 'video/c.mp4', 'size': 'bad'},        # size 解析不了按 0
        ])
        self.assertEqual(len(entries), 3)
        self.assertEqual(skipped, 1)
        self.assertEqual(invalid, 2)
        first = entries[0]
        self.assertEqual(first['root'], 'video')
        self.assertEqual(first['rel_path'], 'sc/a.mp4')
        self.assertEqual(first['size'], 123)
        self.assertEqual(entries[1]['ext'], '.mkv')
        self.assertEqual(entries[1]['root'], 'video')
        self.assertEqual(entries[2]['size'], 0)

    def test_normalize_root_file(self):
        """单段路径（拖单个文件进来）没有文件夹层。"""
        entries, _s, _i = video.normalize_files([{'path': 'a.mp4', 'size': 1}])
        self.assertEqual(entries[0]['root'], '')
        self.assertEqual(entries[0]['rel_path'], 'a.mp4')

    def test_normalize_not_a_list(self):
        self.assertEqual(video.normalize_files(None), ([], 0, 0))
        self.assertEqual(video.normalize_files('x'), ([], 0, 0))


# ------------------------------ 扫描与仓库 ------------------------------

class RepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = make_video_tree(Path(self.tmp.name) / 'media')
        self.store = Store(
            build_output(self.tmp.name, records=[record()]),
            self._settings())

    def _settings(self):
        from web import dbio
        return dbio.Config()

    def _scan(self):
        return videodb.scan_dir(str(self.tree))

    def test_scan_dir(self):
        root, entries, skipped = self._scan()
        self.assertTrue(root.endswith('media'))
        self.assertEqual(len(entries), 4)       # 3 个根下 + 1 个子目录
        self.assertEqual(skipped, 3)            # txt + jpg + noext
        rels = {e['rel_path'] for e in entries}
        # Windows 的 relpath 给反斜杠，入库前必须归一成正斜杠
        self.assertIn('sc/第二季-01.webm', rels)
        big = next(e for e in entries if e['name'] == '剧-01-10 合集.mkv')
        self.assertEqual(big['size'], 300)

    def test_scan_missing_dir(self):
        with self.assertRaises(FileNotFoundError):
            videodb.scan_dir(str(Path(self.tmp.name) / 'nope'))

    def test_import_and_idempotent(self):
        root, entries, skipped = self._scan()
        first = self.store.video.import_scan(root, entries, skipped)
        self.assertEqual(first['files'], 4)
        self.assertEqual(first['inserted'], 4)
        self.assertEqual(first['duplicated'], 0)
        self.assertEqual(first['skipped'], 3)

        again = self.store.video.import_scan(root, entries, skipped)
        self.assertEqual(again['inserted'], 0)      # 重复扫描是幂等的
        self.assertEqual(again['duplicated'], 4)
        self.assertEqual(self.store.video.stats()['total'], 4)

    def test_import_different_roots_both_kept(self):
        """同一个文件出现在两个根目录下，是两条记录（root 参与唯一键）。"""
        root1, entries, skipped = self._scan()
        self.store.video.import_scan(root1, entries, skipped)
        other = str(self.tree) + '_copy'
        self.store.video.import_scan(other, entries, skipped)
        self.assertEqual(self.store.video.stats()['total'], 8)

    def test_list_search_and_stats(self):
        root, entries, skipped = self._scan()
        self.store.video.import_scan(root, entries, skipped)
        payload = self.store.video.list_files(q='合集')
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['items'][0]['stem'], '剧-01-10 合集')

        stats = self.store.video.stats()
        self.assertEqual(stats['total'], 4)
        self.assertEqual(stats['bytes'], 1000)
        self.assertEqual(stats['roots'], 1)
        self.assertEqual(stats['exts'], {'.mp4': 2, '.mkv': 1, '.webm': 1})

    def test_delete_file_batch_clear(self):
        root, entries, _skipped = self._scan()
        self.store.video.import_scan(root, entries, 0)
        batch_id = self.store.video.batches()[0]['id']
        self.assertEqual(self.store.video.delete_file(1), 1)
        self.assertEqual(self.store.video.delete_file(1), 0)    # 已不存在
        self.assertEqual(self.store.video.delete_batch(batch_id), 3)
        self.assertEqual(self.store.video.stats()['total'], 0)
        self.assertEqual(len(self.store.video.batches()), 1)    # 批次记录保留
        self.assertEqual(self.store.video.clear(), 0)


# ------------------------------ 接口 ------------------------------

class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = make_video_tree(Path(self.tmp.name) / 'media')
        self.store = Store(
            build_output(self.tmp.name, records=[record()]),
            self._settings())

    def _settings(self):
        from web import dbio
        return dbio.Config()

    def test_scan_by_path(self):
        code, payload = api.dispatch(self.store, 'POST', '/api/video/scan', {},
                                     {'path': str(self.tree)})
        self.assertEqual(code, 200)
        self.assertEqual(payload['inserted'], 4)
        self.assertEqual(payload['scanned'], 'server')
        self.assertTrue(payload['root'].endswith('media'))

    def test_scan_by_browser_list(self):
        body = {'folder': 'media', 'files': [
            {'path': 'media/a.mp4', 'size': 1},
            {'path': 'media/sc/b.mkv'},
            {'path': 'media/说明.txt'},      # 跳过
            'bad', {}, {'path': ''},         # invalid
        ]}
        code, payload = api.dispatch(self.store, 'POST', '/api/video/scan', {}, body)
        self.assertEqual(code, 200)
        self.assertEqual(payload['inserted'], 2)
        self.assertEqual(payload['skipped'], 1)
        self.assertEqual(payload['invalid'], 3)
        self.assertEqual(payload['scanned'], 'browser')

    def test_scan_errors(self):
        cases = [
            ({}, 400),                                   # 什么都没给
            ('notdict', 400),                            # body 不是对象
            ({'path': str(Path(self.tmp.name) / 'nope')}, 400),
            ({'files': [{'path': 'a.txt'}]}, 400),       # 清单里没有视频
            ({'folder': 'x'}, 400),                      # 有 folder 没 files
            ({'folder': '', 'files': [{'path': 'x/a.mp4'}]}, 400),
        ]
        for body, expected in cases:
            code, payload = api.dispatch(self.store, 'POST', '/api/video/scan', {}, body)
            self.assertEqual(code, expected, body)
            self.assertIn('error', payload)

    def test_list_and_delete_routes(self):
        api.dispatch(self.store, 'POST', '/api/video/scan', {}, {'path': str(self.tree)})
        code, payload = api.dispatch(self.store, 'GET', '/api/video',
                                     params(q='合集', size='1'), None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['pages'], 1)
        self.assertEqual(payload['stats']['total'], 4)

        code, payload = api.dispatch(self.store, 'GET', '/api/video/stats', {}, None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['exts'], {'.mp4': 2, '.mkv': 1, '.webm': 1})

        code, payload = api.dispatch(self.store, 'GET', '/api/video/batches', {}, None)
        self.assertEqual(code, 200)
        self.assertEqual(len(payload['batches']), 1)

        code, payload = api.dispatch(self.store, 'DELETE', '/api/video/file/1', {}, None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['deleted'], 1)

        code, _payload = api.dispatch(self.store, 'DELETE', '/api/video/file/999', {}, None)
        self.assertEqual(code, 404)

        batch_id = payload['batches'][0]['id'] if 'batches' in payload else 1
        code, payload = api.dispatch(self.store, 'DELETE',
                                     '/api/video/batch/%d' % batch_id, {}, None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['deleted'], 3)
        self.assertEqual(payload['stats']['total'], 0)

    def test_clear(self):
        api.dispatch(self.store, 'POST', '/api/video/scan', {}, {'path': str(self.tree)})
        code, payload = api.dispatch(self.store, 'DELETE', '/api/video/all', {}, None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['deleted'], 4)
        self.assertEqual(payload['stats']['total'], 0)
        self.assertEqual(payload['stats']['batches'], 0)

    def test_unknown_endpoint(self):
        code, payload = api.dispatch(self.store, 'GET', '/api/video/whatever', {}, None)
        self.assertEqual(code, 404)


    def test_matched_drama_ids(self):
        """列表筛选「只看本地视频」的数据来源：只要 matched，pending/ignored 不算。"""
        root, entries, skipped = videodb.scan_dir(str(self.tree))
        self.store.video.import_scan(root, entries, skipped)
        files = self.store.video.list_files()['items']
        self.assertEqual(self.store.video.matched_drama_ids(), set())   # 还没刮削

        self.store.video.set_match(files[0]['id'], 'matched', drama_id='1', score=90)
        self.store.video.set_match(files[1]['id'], 'pending', drama_id='2', score=60)
        self.store.video.set_match(files[2]['id'], 'ignored')
        self.assertEqual(self.store.video.matched_drama_ids(), {'1'})


# ------------------------------ 列表筛选 ------------------------------

class LocalVideoFilterTest(unittest.TestCase):
    """`only=video`：只看本地视频库里匹配到文件的剧。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = make_video_tree(Path(self.tmp.name) / 'media')
        from web import dbio
        self.settings = dbio.Config()
        self.store = Store(build_output(
            self.tmp.name, records=[record('1', '有视频的剧'), record('2', '没视频的剧')]),
            self.settings)

    def _match_to(self, drama_id):
        root, entries, skipped = videodb.scan_dir(str(self.tree))
        self.store.video.import_scan(root, entries, skipped)
        files = self.store.video.list_files()['items']
        self.store.video.set_match(files[0]['id'], 'matched',
                                   drama_id=drama_id, score=95)

    def test_flag_and_stats(self):
        self.assertFalse(self.store.get()['by_id']['1']['has_video'])
        self._match_to('1')

        data = self.store.get()      # 视频库变了，签名变更应该自动重载
        self.assertTrue(data['by_id']['1']['has_video'])
        self.assertFalse(data['by_id']['2']['has_video'])
        self.assertEqual(data['stats']['video'], 1)

    def test_only_video_endpoint(self):
        self._match_to('1')
        code, payload = api.dispatch(self.store, 'GET', '/api/dramas',
                                     params(only='video'), None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['total'], 1)
        self.assertEqual([i['id'] for i in payload['items']], ['1'])
        self.assertTrue(payload['items'][0]['has_video'])

    def test_no_video_db_means_empty(self):
        """视频库文件还没生成时：不凭空建库，筛选结果为空但统计正常。"""
        self.assertFalse((Path(self.tmp.name) / config.VIDEO_DB).exists())
        code, payload = api.dispatch(self.store, 'GET', '/api/dramas',
                                     params(only='video'), None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['total'], 0)
        self.assertEqual(self.store.get()['stats']['video'], 0)


# ------------------------------ 配置 ------------------------------

class ConfigTest(unittest.TestCase):
    def test_video_exts_cover_common_formats(self):
        for ext in ('.mp4', '.mkv', '.avi', '.webm', '.ts', '.rmvb'):
            self.assertIn(ext, config.VIDEO_EXTS)

    def test_for_video_sqlite_uses_own_file(self):
        from web import dbio
        cfg = dbio.Config().for_video(Path('out'))
        self.assertEqual(cfg.sqlite_path, Path('out') / config.VIDEO_DB)
