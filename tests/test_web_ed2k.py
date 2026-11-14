# -*- coding: utf-8 -*-
"""ED2K 链接库测试：解析 -> 入库去重 -> 接口 -> 真实 HTTP 上传。

样本链接全部是**合成**的（文件名和 hash 都不对应任何真实文件），
真实样本有版权问题，不放进仓库。
"""
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from tests.web_support import build_output, record
from web import api, config, ed2k, httpd, multipart, queries
from web.store import Store

HASH_A = 'A' * 32
HASH_B = 'B' * 32
HASH_C = 'C' * 32

SAMPLE = '''
》》》》》》 单集链接-更新到 2026.10.12 》》》》》》

ed2k://|file|www.98T.la@示例剧-01.mp4|90015171|%s|/
ed2k://|file|www.98T.la@示例剧-02.mp4|149337066|%s|/
ed2k://|file|www.98T.la@另一部剧-01-10 合集.mp4|721038298|%s|/
''' % (HASH_A, HASH_B, HASH_C)


def params(**kwargs):
    return {k: [v] for k, v in kwargs.items()}


def multipart_body(filename, content, field='file', boundary='----test-boundary',
                   encoding='utf-8'):
    """拼一个浏览器风格的 multipart 请求体（文件名按 UTF-8 放进 header）。"""
    head = ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
            'Content-Type: text/plain\r\n\r\n'
            % (boundary, field, filename)).encode(encoding)
    tail = ('\r\n--%s--\r\n' % boundary).encode('utf-8')
    return head + content + tail, 'multipart/form-data; boundary=%s' % boundary


# ------------------------------ 解析 ------------------------------

class ParseTest(unittest.TestCase):
    def test_basic_link(self):
        link = ed2k.parse_link_line(
            'ed2k://|file|www.98T.la@示例剧-01.mp4|90015171|%s|/' % HASH_A)
        self.assertEqual(link['hash'], HASH_A)
        self.assertEqual(link['size'], 90015171)
        self.assertEqual(link['name'], 'www.98T.la@示例剧-01.mp4')
        self.assertEqual(link['title'], '示例剧')
        self.assertEqual(link['episode'], 1)
        self.assertEqual(link['episode_label'], '01')
        self.assertEqual(link['url'],
                         'ed2k://|file|www.98T.la@示例剧-01.mp4|90015171|%s|/' % HASH_A)

    def test_hash_uppercased_and_aich_tolerated(self):
        link = ed2k.parse_link_line(
            'ed2k://|file|剧.mp4|1|%s|h=ABCDEF|/' % HASH_A.lower())
        self.assertEqual(link['hash'], HASH_A)
        self.assertEqual(link['episode'], None)

    def test_split_name_variants(self):
        cases = {
            'www.98T.la@示例剧-011.mp4': ('示例剧', 11, '011'),
            '某剧-01-10 合集.mp4': ('某剧', 1, '01-10'),
            '某剧第03集.mp4': ('某剧', 3, '第03集'),
            '某剧.EP07.mkv': ('某剧', 7, '07'),
            '某剧-01-素素篇.mp4': ('某剧', 1, '01'),
            '没有集数.mp4': ('没有集数', None, ''),
        }
        for name, expected in cases.items():
            self.assertEqual(ed2k.split_name(name), expected, name)

    def test_counters(self):
        parsed = ed2k.parse_links(SAMPLE + 'ed2k://|file|坏链接|/\n' + SAMPLE)
        self.assertEqual(len(parsed['links']), 3)      # 重复的不重复入表
        self.assertEqual(parsed['invalid'], 1)         # 有 ed2k:// 但格式不对
        self.assertGreaterEqual(parsed['ignored'], 1)  # 标题行不算错
        self.assertEqual(parsed['repeated'], 3)

    def test_decode_gbk_and_bom(self):
        text = 'ed2k://|file|测试-01.mp4|1|%s|/' % HASH_A
        for raw in (text.encode('utf-8'), text.encode('gb18030'),
                    b'\xef\xbb\xbf' + text.encode('utf-8')):
            decoded, encoding = ed2k.decode_bytes(raw)
            self.assertEqual(decoded.strip('\ufeff'), text)
            self.assertIn(encoding, ('utf-8', 'gb18030'))

    def test_title_keywords(self):
        """剧名归一化：从严格到宽松，命中一档就停。"""
        def keywords(title):
            return [k for k, _ in ed2k.title_keywords(title)]

        self.assertEqual(keywords('示例剧'), ['示例剧'])
        # 书名号、季数、括号备注都要能剥掉
        self.assertEqual(keywords('《示例剧》 第二季'), ['示例剧 第二季', '示例剧'])
        self.assertEqual(keywords('示例剧（2026）'), ['示例剧（2026）', '示例剧'])
        self.assertEqual(keywords('爱情公寓5'), ['爱情公寓5', '爱情公寓'])
        # 「第101次求婚」的结尾不是续集号，不能被剥成「第」
        self.assertEqual(keywords('第101次求婚'), ['第101次求婚'])
        self.assertEqual(ed2k.title_keywords('   '), [])

    def test_normalize_title(self):
        """「在不在短剧表」的比对键：两边归一化后比相等。"""
        self.assertEqual(ed2k.normalize_title('《示例剧》 第二季'), '示例剧')
        self.assertEqual(ed2k.normalize_title('示例剧（2026）【完整版】'), '示例剧')
        self.assertEqual(ed2k.normalize_title('爱情公寓5'), '爱情公寓')
        # 「第101次求婚」的结尾不是续集号，不能被剥成「第」
        self.assertEqual(ed2k.normalize_title('第101次求婚'), '第101次求婚')
        self.assertEqual(ed2k.normalize_title('   '), '')

    def test_name_is_capped(self):
        link = ed2k.parse_link_line(
            'ed2k://|file|%s.mp4|1|%s|/' % ('x' * 900, HASH_A))
        self.assertEqual(len(link['name']), config.ED2K_NAME_MAX_LEN)


class MultipartTest(unittest.TestCase):
    def test_parse_file_field(self):
        body, ctype = multipart_body('样本.txt', SAMPLE.encode('utf-8'))
        fields = multipart.parse(body, ctype)
        self.assertEqual(fields['file']['filename'], '样本.txt')
        self.assertEqual(fields['file']['data'], SAMPLE.encode('utf-8'))

    def test_parse_gbk_filename(self):
        """Windows 的 curl 用系统代码页（GBK）发文件名，不能变成乱码。"""
        body, ctype = multipart_body('样本.txt', b'x', encoding='gb18030')
        self.assertEqual(multipart.parse(body, ctype)['file']['filename'], '样本.txt')

    def test_parse_text_field(self):
        body = (b'--X\r\nContent-Disposition: form-data; name="note"\r\n\r\n'
                b'hello\r\n--X--\r\n')
        self.assertEqual(multipart.parse(body, 'multipart/form-data; boundary=X'),
                         {'note': 'hello'})

    def test_missing_boundary(self):
        with self.assertRaises(ValueError):
            multipart.parse(b'', 'multipart/form-data')

    def test_parse_paging_custom_default(self):
        self.assertEqual(queries.parse_paging({}, 50, 500), (1, 50))
        self.assertEqual(queries.parse_paging(params(size='9999'), 50, 500), (1, 500))


# ------------------------------ 接口 ------------------------------

class Ed2kApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = build_output(self.tmp.name,
                            records=[record(drama_id='1', title='示例剧 第二季')])
        self.store = Store(root)

    def call(self, method, path, **kwargs):
        return api.dispatch(self.store, method, path, kwargs.pop('params', {}),
                            kwargs.pop('body', None))

    def upload(self, text=SAMPLE, name='样本.txt'):
        return self.call('POST', '/api/ed2k/upload',
                         body={'file': {'filename': name,
                                        'data': text.encode('utf-8')}})

    def test_upload_inserts_links(self):
        status, payload = self.upload()
        self.assertEqual(status, 200)
        self.assertEqual(payload['parsed'], 3)
        self.assertEqual(payload['inserted'], 3)
        self.assertEqual(payload['duplicated'], 0)
        self.assertEqual(payload['total'], 3)
        self.assertEqual(payload['filename'], '样本.txt')

        status, listing = self.call('GET', '/api/ed2k')
        self.assertEqual(status, 200)
        self.assertEqual(listing['total'], 3)
        self.assertEqual(listing['stats']['titles'], 2)
        self.assertEqual(listing['items'][0]['title'], '另一部剧')   # 按剧名排序

    def test_reupload_is_idempotent(self):
        self.upload()
        status, payload = self.upload()
        self.assertEqual(status, 200)
        self.assertEqual(payload['inserted'], 0)
        self.assertEqual(payload['duplicated'], 3)
        self.assertEqual(payload['total'], 3)

    def test_upload_marks_links_not_in_drama_table(self):
        """短剧表里没有的剧名要打上 in_drama=0，接口把标记和计数一起返回。"""
        status, payload = self.upload()
        self.assertEqual(status, 200)
        self.assertEqual(payload['not_in_drama'], 1)   # 另一部剧不在短剧表

        _, listing = self.call('GET', '/api/ed2k')
        flags = {i['title']: i['in_drama'] for i in listing['items']}
        # 短剧表是「示例剧 第二季」，链接里是「示例剧」：归一化后要能对上
        self.assertEqual(flags['示例剧'], 1)
        self.assertEqual(flags['另一部剧'], 0)

    def test_old_db_backfills_in_drama_on_upgrade(self):
        """老库补上 in_drama 列后，按当前短剧表回填，不用重新上传。"""
        self.upload()
        path = Path(self.tmp.name) / config.ED2K_DB
        conn = sqlite3.connect(str(path))
        conn.execute('ALTER TABLE ed2k_link DROP COLUMN in_drama')
        conn.commit()
        conn.close()

        store = Store(self.tmp.name)     # 重新打开：触发补列 + 回填
        flags = {i['title']: i['in_drama'] for i in store.ed2k.list_links()['items']}
        self.assertEqual(flags['示例剧'], 1)
        self.assertEqual(flags['另一部剧'], 0)

    def test_upload_rejects_bad_input(self):
        status, _ = self.call('POST', '/api/ed2k/upload', body={'nope': 1})
        self.assertEqual(status, 400)

        status, payload = self.call('POST', '/api/ed2k/upload',
                                    body={'file': {'filename': 'x.txt',
                                                   'data': b'not a link\n'}})
        self.assertEqual(status, 400)
        self.assertIn('没有找到', payload['error'])

        status, _ = self.upload(text='   \n\n')
        self.assertEqual(status, 400)

    def test_search_and_paging(self):
        self.upload()
        _, payload = self.call('GET', '/api/ed2k', params=params(q='示例'))
        self.assertEqual(payload['total'], 2)

        _, payload = self.call('GET', '/api/ed2k', params=params(q=HASH_C[:8]))
        self.assertEqual(payload['total'], 1)

        _, payload = self.call('GET', '/api/ed2k', params=params(size='2', page='2'))
        self.assertEqual(len(payload['items']), 1)
        self.assertEqual(payload['pages'], 2)

        # 百分号不能当成通配符
        _, payload = self.call('GET', '/api/ed2k', params=params(q='%'))
        self.assertEqual(payload['total'], 0)

    def test_search_by_title_degrades(self):
        """详情页的「这部剧有哪些资源」：剧名不一致时逐档降级，命中即停。"""
        self.upload()

        payload = self.store.ed2k.search_by_title('示例剧')
        self.assertEqual(payload['keyword'], '示例剧')
        self.assertEqual(payload['matched_by'], '完整剧名')
        self.assertEqual(payload['total'], 2)
        self.assertEqual(payload['bytes'], 90015171 + 149337066)
        self.assertEqual([i['episode_label'] for i in payload['items']], ['01', '02'])

        # 剧集叫「示例剧 第二季」而库里只有「示例剧」：降级之后仍要命中
        payload = self.store.ed2k.search_by_title('示例剧 第二季')
        self.assertEqual((payload['keyword'], payload['matched_by']),
                         ('示例剧', '去掉季数/后缀'))
        self.assertEqual(payload['total'], 2)

        payload = self.store.ed2k.search_by_title('完全没有这部')
        self.assertEqual((payload['total'], payload['items'], payload['bytes']), (0, [], 0))
        # 没命中时回第一个关键词，前端提示文案要用
        self.assertEqual(payload['keyword'], '完全没有这部')

    def test_drama_ed2k_endpoint(self):
        self.upload()
        status, payload = self.call('GET', '/api/drama/1/ed2k')
        self.assertEqual(status, 200)
        self.assertTrue(payload['available'])
        self.assertEqual(payload['drama_id'], '1')
        self.assertEqual(payload['total'], 2)
        self.assertEqual(payload['items'][0]['title'], '示例剧')

        status, _ = self.call('GET', '/api/drama/404/ed2k')
        self.assertEqual(status, 404)

    def test_delete_link(self):
        self.upload()
        status, payload = self.call('DELETE', '/api/ed2k/link/' + HASH_A)
        self.assertEqual(status, 200)
        self.assertEqual(payload['stats']['total'], 2)
        status, _ = self.call('DELETE', '/api/ed2k/link/' + HASH_A)
        self.assertEqual(status, 404)

    def test_batches_and_undo(self):
        self.upload()
        _, payload = self.call('GET', '/api/ed2k/batches')
        self.assertEqual(len(payload['batches']), 1)
        batch = payload['batches'][0]
        self.assertEqual(batch['inserted'], 3)

        status, payload = self.call('DELETE', '/api/ed2k/batch/%d' % batch['id'])
        self.assertEqual(status, 200)
        self.assertEqual(payload['deleted'], 3)
        self.assertEqual(payload['stats']['total'], 0)

    def test_export_and_clear(self):
        self.upload()
        status, payload = self.call('GET', '/api/ed2k/export')
        self.assertEqual(status, 200)
        self.assertEqual(payload['count'], 3)
        self.assertIn(HASH_A, payload['text'])

        status, payload = self.call('DELETE', '/api/ed2k/all')
        self.assertEqual(status, 200)
        self.assertEqual(payload['deleted'], 3)
        _, payload = self.call('GET', '/api/ed2k/stats')
        self.assertEqual(payload['total'], 0)

    def test_db_is_separate_file(self):
        """ED2K 库是独立文件：重灌数据（清 output 里的数据）不该带走人工上传的链接。"""
        self.upload()
        self.assertTrue((Path(self.tmp.name) / config.ED2K_DB).is_file())
        self.assertNotEqual(config.ED2K_DB, config.VIDEO_DB)


# ------------------------------ 列表筛选 ------------------------------

class Ed2kOnlyFilterTest(unittest.TestCase):
    """`only=ed2k`：只看链接库里能搜到资源的剧。

    口径与详情页 `search_by_title` 一致（逐档关键词、任一命中即算），
    所以这里特意放了一部「名字比链接剧名长」的剧，验证宽松档也生效。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(build_output(self.tmp.name, records=[
            record(drama_id='1', title='示例剧'),
            record(drama_id='2', title='另一部剧'),
            record(drama_id='3', title='没资源的剧'),
            # 链接里只有「示例剧」，这部靠「剧名片段」档命中
            record(drama_id='4', title='示例剧 番外'),
        ]))

    def upload(self):
        return api.dispatch(self.store, 'POST', '/api/ed2k/upload', {},
                            {'file': {'filename': '样本.txt',
                                      'data': SAMPLE.encode('utf-8')}})

    def test_titles_dedup(self):
        self.assertEqual(self.store.ed2k.titles(), [])
        self.upload()
        self.assertEqual(sorted(self.store.ed2k.titles()), ['另一部剧', '示例剧'])

    def test_flag_and_stats(self):
        self.assertFalse(self.store.get()['by_id']['1']['has_ed2k'])
        self.upload()

        data = self.store.get()      # 链接库变了，签名变更应该自动重载
        self.assertTrue(data['by_id']['1']['has_ed2k'])
        self.assertTrue(data['by_id']['2']['has_ed2k'])
        self.assertTrue(data['by_id']['4']['has_ed2k'])    # 宽松档命中
        self.assertFalse(data['by_id']['3']['has_ed2k'])
        self.assertEqual(data['stats']['ed2k'], 3)

    def test_only_ed2k_endpoint(self):
        self.upload()
        code, payload = api.dispatch(self.store, 'GET', '/api/dramas',
                                     params(only='ed2k'), None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['total'], 3)
        self.assertEqual(sorted(i['id'] for i in payload['items']), ['1', '2', '4'])
        self.assertTrue(all(i['has_ed2k'] for i in payload['items']))

    def test_no_ed2k_db_means_empty(self):
        """链接库文件还没生成时：不凭空建库，筛选为空但统计正常。"""
        self.assertFalse((Path(self.tmp.name) / config.ED2K_DB).exists())
        code, payload = api.dispatch(self.store, 'GET', '/api/dramas',
                                     params(only='ed2k'), None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['total'], 0)
        self.assertEqual(self.store.get()['stats']['ed2k'], 0)
        self.assertFalse((Path(self.tmp.name) / config.ED2K_DB).exists())


# ------------------------------ 真实 HTTP ------------------------------

class Ed2kHttpTest(unittest.TestCase):
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

    def send(self, path, data=None, headers=None, method='POST'):
        url = 'http://127.0.0.1:%d%s' % (self.port, path)
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def test_multipart_upload(self):
        body, ctype = multipart_body('样本.txt', SAMPLE.encode('utf-8'))
        status, raw = self.send('/api/ed2k/upload', body,
                                {'Content-Type': ctype})
        self.assertEqual(status, 200)
        payload = json.loads(raw)
        self.assertEqual(payload['filename'], '样本.txt')   # 中文文件名没乱码
        self.assertEqual(payload['inserted'], 3)

    def test_multipart_with_lone_crlf_content(self):
        """文件内容里有空行 / 只有 CRLF 的行，不能被当成段边界。"""
        body, ctype = multipart_body('a.txt', SAMPLE.replace('\n\n', '\r\n\r\n')
                                     .encode('utf-8'))
        status, raw = self.send('/api/ed2k/upload', body, {'Content-Type': ctype})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['parsed'], 3)

    def test_plain_text_upload(self):
        status, raw = self.send('/api/ed2k/upload', SAMPLE.encode('utf-8'),
                                {'Content-Type': 'text/plain; charset=utf-8',
                                 'X-Filename': 'curl.txt'})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['filename'], 'curl.txt')

    def test_oversized_upload_returns_413(self):
        with mock.patch.object(config, 'MAX_UPLOAD_BYTES', 10):
            status, raw = self.send('/api/ed2k/upload', b'x' * 100,
                                    {'Content-Type': 'text/plain'})
        self.assertEqual(status, 413)
        self.assertIn('error', json.loads(raw))

    def test_missing_boundary_returns_400(self):
        status, raw = self.send('/api/ed2k/upload', b'junk',
                                {'Content-Type': 'multipart/form-data'})
        self.assertEqual(status, 400)


if __name__ == '__main__':
    unittest.main()
