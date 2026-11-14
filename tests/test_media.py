# -*- coding: utf-8 -*-
"""详情页播放测试：路径定位 -> 按剧查文件 -> 接口 -> /media 流式传输（Range）。

三层分开测：
  · video.locate         纯函数，不碰网络（root 能不能定位、有没有穿越、文件还在不在）
  · VideoRepo / api      按剧取匹配到的文件，带上 playable 与播放地址
  · httpd /media/<id>    真实起服务器，验证 200 / 206 / 416 / HEAD / 404
"""
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from hgdb import api, dbio, httpd, video, videodb
from hgdb.store import Store
from tests.support import build_output, record

PAYLOAD = bytes(range(256)) * 8       # 2048 字节，内容可校验（每 256 一个循环）


def get(port, path, headers=None, method='GET'):
    """发请求，返回 (状态码, 头, 体)；4xx/5xx 也正常返回而不是抛异常。"""
    url = 'http://127.0.0.1:%d%s' % (port, path)
    req = urllib.request.Request(url, method=method, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=5) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.headers, exc.read()


# ------------------------------ 路径定位 ------------------------------

class LocateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'media'
        (self.root / 'sc').mkdir(parents=True)
        (self.root / 'sc' / '剧-01.mp4').write_bytes(PAYLOAD)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ok(self):
        path, status = video.locate(str(self.root), 'sc/剧-01.mp4')
        self.assertEqual(status, video.LOCATE_OK)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes()[:4], PAYLOAD[:4])

    def test_backslash_rel_path(self):
        """Windows 的 relpath 是反斜杠，入库前已归一，但这里也认。"""
        _path, status = video.locate(str(self.root), 'sc\\剧-01.mp4')
        self.assertEqual(status, video.LOCATE_OK)

    def test_unlocatable_when_root_is_folder_name(self):
        """浏览器选文件夹只有文件夹名，服务端拼不出路径。"""
        self.assertFalse(video.looks_absolute('video'))
        self.assertEqual(video.locate('video', 'a.mp4'),
                         (None, video.LOCATE_UNLOCATABLE))
        self.assertEqual(video.locate('', 'a.mp4'),
                         (None, video.LOCATE_UNLOCATABLE))

    def test_missing(self):
        self.assertEqual(video.locate(str(self.root), 'sc/没有这个.mp4'),
                         (None, video.LOCATE_MISSING))

    def test_traversal_blocked(self):
        for rel in ('../secret.mp4', 'sc/../../secret.mp4', '/etc/passwd'):
            self.assertEqual(video.locate(str(self.root), rel),
                             (None, video.LOCATE_UNSAFE), rel)

    def test_empty_rel(self):
        self.assertEqual(video.locate(str(self.root), ''),
                         (None, video.LOCATE_UNLOCATABLE))

    def test_absolute_root_forms(self):
        self.assertTrue(video.looks_absolute('/data/video'))
        self.assertTrue(video.looks_absolute('O:\\video'))
        self.assertTrue(video.looks_absolute('O:/video'))
        self.assertTrue(video.looks_absolute('\\\\nas\\share'))
        self.assertFalse(video.looks_absolute('video'))
        self.assertFalse(video.looks_absolute(None))


# ------------------------------ Range 解析 ------------------------------

class ParseRangeTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(httpd.parse_range('bytes=0-99', 1000), (0, 99))
        self.assertEqual(httpd.parse_range('bytes=100-', 1000), (100, 999))
        self.assertEqual(httpd.parse_range('bytes=-100', 1000), (900, 999))
        self.assertEqual(httpd.parse_range('BYTES=0-0', 1000), (0, 0))

    def test_clamped(self):
        """end 超出文件大小就截到末尾（不报错，播放器常这么发）。"""
        self.assertEqual(httpd.parse_range('bytes=900-5000', 1000), (900, 999))

    def test_unsatisfiable(self):
        for header in ('bytes=1000-', 'bytes=999999-', 'bytes=5-1',
                       'bytes=-0', 'bytes=abc-def', 'bytes=', 'items=0-1', ''):
            self.assertIsNone(httpd.parse_range(header, 1000), header)
        self.assertIsNone(httpd.parse_range('bytes=0-1', 0))   # 空文件


# ------------------------------ 仓库 + 接口 ------------------------------

class DramaVideosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.media = tmp / 'media'
        (self.media / 'sc').mkdir(parents=True)
        # 集号故意乱序写，验证按集数排；剧名要能对上数据集里的「神医毒妃」
        (self.media / '神医毒妃-03.mp4').write_bytes(PAYLOAD)
        (self.media / 'sc' / '神医毒妃-01.mp4').write_bytes(PAYLOAD)
        (self.media / '神医毒妃-02.mp4').write_bytes(PAYLOAD)
        (self.media / '无关节目.mp4').write_bytes(PAYLOAD)

        out = build_output(tmp / 'out', records=[record('101', '神医毒妃'),
                                                record('102', '别的剧')])
        self.store = Store(out, dbio.Config())
        root, entries, skipped = videodb.scan_dir(str(self.media))
        self.root = root
        self.store.video.import_scan(root, entries, skipped)
        api.dispatch(self.store, 'POST', '/api/scrape/apply', {}, {'root': root})

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, path):
        return api.dispatch(self.store, 'GET', path, {}, None)

    def test_sorted_by_episode(self):
        code, payload = self.call('/api/drama/101/videos')
        self.assertEqual(code, 200)
        self.assertTrue(payload['available'])
        self.assertEqual(payload['total'], 3)
        self.assertEqual(payload['playable'], 3)
        self.assertEqual([i['episode'] for i in payload['items']], [1, 2, 3])
        self.assertEqual(payload['bytes'], len(PAYLOAD) * 3)
        for item in payload['items']:
            self.assertTrue(item['playable'])
            self.assertEqual(item['reason'], video.LOCATE_OK)
            self.assertTrue(item['url'].startswith('/media/'))

    def test_other_drama_is_empty(self):
        _code, payload = self.call('/api/drama/102/videos')
        self.assertEqual(payload['total'], 0)
        self.assertEqual(payload['items'], [])

    def test_unknown_drama(self):
        self.assertEqual(self.call('/api/drama/999/videos')[0], 404)

    def test_browser_root_is_not_playable(self):
        """浏览器来源（root 只有文件夹名）：展示但不给播放地址。"""
        self.store.video.import_scan('video', [
            {'rel_path': '神医毒妃-09.mp4', 'name': '神医毒妃-09.mp4',
             'stem': '神医毒妃-09', 'ext': '.mp4', 'size': 1}], 0)
        rows = self.store.video.files_of_root('video')
        self.store.video.set_match(rows[0]['id'], 'matched', '101', 1.0)

        _code, payload = self.call('/api/drama/101/videos')
        by_name = {i['name']: i for i in payload['items']}
        self.assertEqual(payload['total'], 4)
        self.assertEqual(payload['playable'], 3)
        browser = by_name['神医毒妃-09.mp4']
        self.assertFalse(browser['playable'])
        self.assertEqual(browser['reason'], video.LOCATE_UNLOCATABLE)
        self.assertEqual(browser['url'], '')

    def test_pending_and_ignored_excluded(self):
        rows = self.store.video.files_of_root(self.root)
        target = rows[0]['id']
        self.store.video.set_match(target, 'pending')
        _code, payload = self.call('/api/drama/101/videos')
        self.assertNotIn(target, [i['id'] for i in payload['items']])

        self.store.video.set_match(target, 'ignored')
        _code, payload = self.call('/api/drama/101/videos')
        self.assertNotIn(target, [i['id'] for i in payload['items']])

    def test_duplicate_roots_merged_prefer_playable(self):
        """同一文件被浏览器目录和服务器路径各扫一次：列表只出一条，留能播的。"""
        self.store.video.import_scan('video', [
            {'rel_path': '神医毒妃-01.mp4', 'name': '神医毒妃-01.mp4',
             'stem': '神医毒妃-01', 'ext': '.mp4', 'size': len(PAYLOAD)}], 0)
        rows = self.store.video.files_of_root('video')
        self.store.video.set_match(rows[0]['id'], 'matched', '101', 1.0)

        _code, payload = self.call('/api/drama/101/videos')
        names = [i['name'] for i in payload['items']]
        self.assertEqual(names.count('神医毒妃-01.mp4'), 1)
        one = next(i for i in payload['items'] if i['name'] == '神医毒妃-01.mp4')
        self.assertTrue(one['playable'])
        self.assertEqual(payload['total'], 3)
        self.assertEqual(payload['bytes'], len(PAYLOAD) * 3)

    def test_deleted_file_reported_missing(self):
        """文件被挪走后不该再给播放地址（点开只会 404）。"""
        victim = self.media / '神医毒妃-02.mp4'
        victim.unlink()
        _code, payload = self.call('/api/drama/101/videos')
        row = next(i for i in payload['items'] if i['episode'] == 2)
        self.assertFalse(row['playable'])
        self.assertEqual(row['reason'], video.LOCATE_MISSING)
        self.assertEqual(payload['playable'], 2)


# ------------------------------ /media 流式 ------------------------------

class MediaStreamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.media = tmp / 'media'
        cls.media.mkdir()
        (cls.media / '剧-01.mp4').write_bytes(PAYLOAD)
        (cls.media / '剧-02.mkv').write_bytes(PAYLOAD)

        out = build_output(tmp / 'out', records=[record('101', '神医毒妃')])
        cls.store = Store(out, dbio.Config())
        root, entries, skipped = videodb.scan_dir(str(cls.media))
        cls.store.video.import_scan(root, entries, skipped)
        api.dispatch(cls.store, 'POST', '/api/scrape/apply', {}, {'root': root})
        cls.files = {r['name']: r for r in cls.store.video.files_of_root(root)}

        cls.server = httpd.make_server(cls.store, '127.0.0.1', 0, quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def url_of(self, name):
        return '/media/%d' % self.files[name]['id']

    def test_full_body(self):
        status, headers, body = get(self.port, self.url_of('剧-01.mp4'))
        self.assertEqual(status, 200)
        self.assertEqual(body, PAYLOAD)
        self.assertEqual(headers['Content-Length'], str(len(PAYLOAD)))
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        self.assertIn('video/mp4', headers['Content-Type'])

    def test_mkv_mime(self):
        """Windows 的 mimetypes 常常查不到 .mkv，靠 config.VIDEO_MIME 兜底。"""
        _status, headers, _body = get(self.port, self.url_of('剧-02.mkv'))
        self.assertIn('matroska', headers['Content-Type'])

    def test_range_middle(self):
        status, headers, body = get(self.port, self.url_of('剧-01.mp4'),
                                    {'Range': 'bytes=10-19'})
        self.assertEqual(status, 206)
        self.assertEqual(headers['Content-Range'], 'bytes 10-19/%d' % len(PAYLOAD))
        self.assertEqual(body, PAYLOAD[10:20])
        self.assertEqual(headers['Content-Length'], '10')

    def test_range_open_ended(self):
        status, headers, body = get(self.port, self.url_of('剧-01.mp4'),
                                    {'Range': 'bytes=%d-' % (len(PAYLOAD) - 5)})
        self.assertEqual(status, 206)
        self.assertEqual(body, PAYLOAD[-5:])

    def test_range_suffix(self):
        status, _headers, body = get(self.port, self.url_of('剧-01.mp4'),
                                     {'Range': 'bytes=-8'})
        self.assertEqual(status, 206)
        self.assertEqual(body, PAYLOAD[-8:])

    def test_range_unsatisfiable(self):
        status, headers, _body = get(self.port, self.url_of('剧-01.mp4'),
                                     {'Range': 'bytes=%d-' % (len(PAYLOAD) + 10)})
        self.assertEqual(status, 416)
        self.assertEqual(headers['Content-Range'], 'bytes */%d' % len(PAYLOAD))

    def test_head_has_no_body(self):
        status, headers, body = get(self.port, self.url_of('剧-01.mp4'), method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')
        self.assertEqual(headers['Content-Length'], str(len(PAYLOAD)))

    def test_unknown_id(self):
        self.assertEqual(get(self.port, '/media/999999')[0], 404)
        self.assertEqual(get(self.port, '/media/abc')[0], 404)

    def test_unlocatable_row(self):
        """浏览器来源的行：服务端拼不出路径，/media 直接 404 而不是读别处的文件。"""
        self.store.video.import_scan('video', [
            {'rel_path': '别处的.mp4', 'name': '别处的.mp4', 'stem': '别处的',
             'ext': '.mp4', 'size': 1}], 0)
        row = self.store.video.files_of_root('video')[0]
        self.assertEqual(get(self.port, '/media/%d' % row['id'])[0], 404)


if __name__ == '__main__':
    unittest.main()
