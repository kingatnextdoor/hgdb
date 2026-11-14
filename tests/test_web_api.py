# -*- coding: utf-8 -*-
"""接口层测试：一半直接调 api.dispatch（不起服务器），一半走真实 HTTP。

直接调 dispatch 的用例用来锁业务语义（状态码 + JSON 结构）；
真实 HTTP 的用例只验「字节层面」的东西：路由、MIME、静态文件、请求体解析。
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.web_support import build_output, record
from web import api, config, httpd
from web.store import Store


def params(**kwargs):
    return {k: [v] for k, v in kwargs.items()}


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = build_output(
            self.tmp.name,
            records=[
                record(drama_id='1', title='都市之王', tags=['都市']),
                record(drama_id='2', title='古装传', tags=['古装']),
            ],
            edits={'1': ['改过的标签']},
        )
        self.store = Store(root)

    def call(self, method, path, **kwargs):
        return api.dispatch(self.store, method, path, kwargs.pop('params', {}),
                            kwargs.pop('body', None))

    def test_stats(self):
        status, payload = self.call('GET', '/api/stats')
        self.assertEqual(status, 200)
        self.assertEqual(payload['stats']['total'], 2)
        self.assertNotIn('runs', payload)      # 抓取历史不再落盘，接口也不再返回

    def test_facets_reflect_edits(self):
        _, payload = self.call('GET', '/api/facets')
        names = {t['name'] for t in payload['tags']}
        self.assertIn('改过的标签', names)

    def test_dramas_default_paging(self):
        status, payload = self.call('GET', '/api/dramas', params=params(size='1'))
        self.assertEqual(status, 200)
        self.assertEqual(payload['total'], 2)
        self.assertEqual(len(payload['items']), 1)
        self.assertEqual(payload['pages'], 2)
        self.assertNotIn('detail', payload['items'][0])

    def test_dramas_search_and_tag(self):
        _, payload = self.call('GET', '/api/dramas', params=params(q='都市'))
        self.assertEqual(payload['total'], 1)
        _, payload = self.call('GET', '/api/dramas', params=params(tag='古装'))
        self.assertEqual(payload['total'], 1)

    def test_drama_detail_with_related(self):
        status, payload = self.call('GET', '/api/drama/1')
        self.assertEqual(status, 200)
        self.assertEqual(payload['title'], '都市之王')
        self.assertEqual(payload['tags'], ['改过的标签'])
        self.assertIn('related', payload)

    def test_drama_trailing_slash(self):
        status, _ = self.call('GET', '/api/drama/1/')
        self.assertEqual(status, 200)

    def test_drama_not_found(self):
        status, payload = self.call('GET', '/api/drama/999')
        self.assertEqual(status, 404)
        self.assertEqual(payload, api.NOT_FOUND)

    def test_unknown_endpoint(self):
        status, payload = self.call('GET', '/api/nope')
        self.assertEqual(status, 404)
        self.assertEqual(payload, api.UNKNOWN_ENDPOINT)

    def test_save_tags_overwrites(self):
        status, payload = self.call('POST', '/api/drama/2/tags',
                                    body={'tags': [' 新标签 ', '古装', '新标签', '']})
        self.assertEqual(status, 200)
        self.assertEqual(payload['tags'], ['新标签', '古装'])   # 去空白 / 去重
        _, detail = self.call('GET', '/api/drama/2')
        self.assertTrue(detail['tags_edited'])

    def test_save_tags_bad_body(self):
        status, _ = self.call('POST', '/api/drama/1/tags', body={'wrong': 1})
        self.assertEqual(status, 400)
        status, _ = self.call('POST', '/api/drama/1/tags', body=None)
        self.assertEqual(status, 400)

    def test_save_tags_unknown_drama_does_not_write(self):
        status, _ = self.call('POST', '/api/drama/999/tags', body={'tags': ['x']})
        self.assertEqual(status, 404)
        edits = json.loads((Path(self.tmp.name) / config.TAG_EDITS_FILE).read_text('utf-8'))
        self.assertNotIn('999', edits)

    def test_reset_tags(self):
        status, payload = self.call('DELETE', '/api/drama/1/tags')
        self.assertEqual(status, 200)
        self.assertEqual(payload['tags'], ['都市'])
        _, detail = self.call('GET', '/api/drama/1')
        self.assertFalse(detail['tags_edited'])

    def test_tags_are_clamped(self):
        long_tag = 'x' * 50
        _, payload = self.call('POST', '/api/drama/1/tags',
                               body={'tags': [long_tag] + ['t%d' % i for i in range(200)]})
        self.assertEqual(len(payload['tags'][0]), config.TAG_MAX_LEN)
        self.assertEqual(len(payload['tags']), config.TAG_MAX_COUNT)

    def test_reload(self):
        status, payload = self.call('GET', '/api/reload')
        self.assertEqual(status, 200)
        self.assertEqual(payload['total'], 2)


class HttpTest(unittest.TestCase):
    """真实起一个服务器（端口 0 由系统分配），验证 HTTP 层。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = build_output(cls.tmp.name, records=[record(drama_id='1')])
        cls.store = Store(root)
        cls.server = httpd.make_server(cls.store, '127.0.0.1', 0,
                                       covers_dir=root / 'covers', quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def request(self, path, method='GET', body=None):
        url = 'http://127.0.0.1:%d%s' % (self.port, path)
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={'Content-Type': 'application/json'}
                                     if data else {})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            with exc:                       # 不 close 会留下 ResourceWarning
                return exc.code, exc.headers, exc.read()

    def test_api_json(self):
        status, headers, body = self.request('/api/stats')
        self.assertEqual(status, 200)
        self.assertIn('application/json', headers['Content-Type'])
        self.assertEqual(json.loads(body)['stats']['total'], 1)

    def test_static_nested_path(self):
        """/static/js/*.js 这种子目录必须能取到，且 MIME 要让浏览器当模块执行。"""
        status, headers, body = self.request('/static/js/main.js')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers['Content-Type'])
        self.assertIn(b'import', body)

    def test_static_css(self):
        status, headers, _ = self.request('/static/css/base.css')
        self.assertEqual(status, 200)
        self.assertIn('text/css', headers['Content-Type'])

    def test_index(self):
        status, headers, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('text/html', headers['Content-Type'])
        self.assertIn(b'/static/js/main.js', body)

    def test_favicon_routes(self):
        """不读 <link> 的浏览器/工具会直接请求根路径的 /favicon.ico。"""
        for path, mime in (('/favicon.ico', 'image/x-icon'),
                           ('/favicon.svg', 'image/svg+xml')):
            status, headers, _ = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertIn(mime, headers['Content-Type'], path)
            # 图标基本不变，应该带缓存
            self.assertIn('max-age', headers.get('Cache-Control') or '', path)

    def test_favicon_static_assets(self):
        status, headers, body = self.request('/static/site.webmanifest')
        self.assertEqual(status, 200)
        self.assertIn('manifest+json', headers['Content-Type'])
        # 图标路径必须指向 /static/（原始包里是根路径，不改会 404）
        self.assertIn(b'/static/web-app-manifest-192x192.png', body)

        status, headers, _ = self.request('/static/apple-touch-icon.png')
        self.assertEqual(status, 200)
        self.assertIn('image/png', headers['Content-Type'])

    def test_directory_traversal_blocked(self):
        for path in ('/static/../server.py', '/static/..%2fserver.py',
                     '/covers/../ed2k.sqlite3'):
            status, _, _ = self.request(path)
            self.assertEqual(status, 404, path)

    def test_post_tags_roundtrip(self):
        status, _, body = self.request('/api/drama/1/tags', 'POST', {'tags': ['甲', '乙']})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['tags'], ['甲', '乙'])

        status, _, body = self.request('/api/drama/1/tags', 'DELETE')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['tags'], ['都市'])

    def test_head_has_no_body(self):
        status, _, body = self.request('/api/stats', method='HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(body, b'')


if __name__ == '__main__':
    unittest.main()
