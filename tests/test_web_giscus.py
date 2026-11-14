# -*- coding: utf-8 -*-
"""giscus 评论配置的测试：config.py 读值、缺项降级、term 规则、/api/config 路由。"""
import tempfile
import unittest
from unittest import mock

from tests.web_support import build_output, record
from web import api, config, dbio, giscus
from web.store import Store

# 一份「配全了」的配置，和 giscus.app 生成的片段同构
ENV = {
    'GISCUS_REPO': 'owner/repo',
    'GISCUS_REPO_ID': 'R_kgDOxxxxxx',
    'GISCUS_CATEGORY': 'Comments',
    'GISCUS_CATEGORY_ID': 'DIC_kwDOxxxxxx',
}

MINIMAL = {k: ENV[k] for k in ('GISCUS_REPO', 'GISCUS_REPO_ID', 'GISCUS_CATEGORY_ID')}


class SettingsTest(unittest.TestCase):
    def test_missing_required_returns_none(self):
        self.assertIsNone(giscus.settings({}))
        self.assertIsNone(giscus.settings({'GISCUS_REPO': 'owner/repo'}))
        self.assertIsNone(giscus.settings(
            {'GISCUS_REPO': 'owner/repo', 'GISCUS_REPO_ID': 'R_1'}))
        # 分类名不是必填，分类 id 才是
        self.assertIsNotNone(giscus.settings(MINIMAL))

    def test_blank_counts_as_missing(self):
        env = dict(ENV, GISCUS_REPO_ID='   ')
        self.assertIsNone(giscus.settings(env))
        env = dict(ENV, GISCUS_REPO='')
        self.assertIsNone(giscus.settings(env))

    def test_full_config(self):
        conf = giscus.settings(ENV)
        self.assertEqual(conf['repo'], 'owner/repo')
        self.assertEqual(conf['repo_id'], 'R_kgDOxxxxxx')
        self.assertEqual(conf['category'], 'Comments')
        self.assertEqual(conf['category_id'], 'DIC_kwDOxxxxxx')

    def test_defaults(self):
        conf = giscus.settings(MINIMAL)
        self.assertEqual(conf['theme'], giscus.DEFAULT_THEME)
        self.assertEqual(conf['lang'], 'zh-CN')
        self.assertEqual(conf['mapping'], 'specific')
        self.assertEqual(conf['term_prefix'], 'drama')
        self.assertEqual(conf['category'], '')     # 没填就是空，不是 None

    def test_overrides(self):
        env = dict(ENV, GISCUS_THEME='dark', GISCUS_LANG='en',
                   GISCUS_MAPPING='number')
        conf = giscus.settings(env)
        self.assertEqual(conf['theme'], 'dark')
        self.assertEqual(conf['lang'], 'en')
        self.assertEqual(conf['mapping'], 'number')

    def test_env_ignores_config(self):
        """传了 env 就只用它，不去读 config.py（测试隔离靠这个）。"""
        with mock.patch('web.giscus._from_config',
                        side_effect=AssertionError('不该读 config.py')):
            self.assertIsNone(giscus.settings({}))
            self.assertEqual(giscus.settings(ENV)['repo'], 'owner/repo')

    def test_reads_config_when_env_omitted(self):
        with mock.patch.object(config, 'GISCUS_REPO', 'owner/repo'), \
             mock.patch.object(config, 'GISCUS_REPO_ID', 'R_1'), \
             mock.patch.object(config, 'GISCUS_CATEGORY_ID', 'DIC_1'):
            self.assertEqual(giscus.settings()['repo'], 'owner/repo')

    def test_configured_summary(self):
        self.assertEqual(giscus.configured({}), {'enabled': False})
        summary = giscus.configured(ENV)
        self.assertTrue(summary['enabled'])
        self.assertEqual(summary['repo'], 'owner/repo')
        self.assertNotIn('repo_id', summary)      # 摘要不带凭据


class TermTest(unittest.TestCase):
    def test_term(self):
        self.assertEqual(giscus.term_for(398), 'drama:398')
        self.assertEqual(giscus.term_for(' 7 '), 'drama:7')
        self.assertEqual(giscus.term_for(0), 'drama:0')

    def test_empty(self):
        self.assertEqual(giscus.term_for(''), '')
        self.assertEqual(giscus.term_for(None), '')
        self.assertEqual(giscus.term_for('  '), '')


class ConfigRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(build_output(self.tmp.name, records=[record('101', '神医毒妃')]),
                           dbio.Config())

    def call(self):
        return api.dispatch(self.store, 'GET', '/api/config', {}, None)

    def test_unconfigured_gives_null(self):
        with mock.patch('web.giscus.settings', return_value=None):
            code, payload = self.call()
        self.assertEqual(code, 200)
        self.assertIsNone(payload['giscus'])

    def test_configured_payload(self):
        conf = giscus.settings(ENV)
        with mock.patch('web.giscus.settings', return_value=conf):
            code, payload = self.call()
        self.assertEqual(code, 200)
        g = payload['giscus']
        self.assertEqual(g['repo'], 'owner/repo')
        self.assertEqual(g['mapping'], 'specific')
        self.assertEqual(g['theme'], 'dark_dimmed')

    def test_route_is_get_only(self):
        code, _payload = api.dispatch(self.store, 'POST', '/api/config', {}, {})
        self.assertEqual(code, 404)


class SiteConfigTest(unittest.TestCase):
    """/api/config 的 site 段：站名与页脚链接都来自配置，HTML 里只有占位文字。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(build_output(self.tmp.name, records=[record()]),
                           dbio.Config())

    def test_site_name_comes_from_config(self):
        with mock.patch.object(config, 'SITE_NAME', '别人的站'):
            code, payload = api.dispatch(self.store, 'GET', '/api/config', {}, None)
        self.assertEqual(code, 200)
        self.assertEqual(payload['site']['name'], '别人的站')

    def test_links_are_rendered_as_label_url_pairs(self):
        links = (('仓库', 'https://example.com/r'), ('留言', 'https://example.com/m'))
        with mock.patch.object(config, 'SITE_LINKS', links):
            _code, payload = api.dispatch(self.store, 'GET', '/api/config', {}, None)
        self.assertEqual(payload['site']['links'],
                         [{'label': '仓库', 'url': 'https://example.com/r'},
                          {'label': '留言', 'url': 'https://example.com/m'}])

    def test_empty_links_stay_empty(self):
        """没配链接时给空列表，前端据此把页脚整块收起来。"""
        with mock.patch.object(config, 'SITE_LINKS', ()):
            _code, payload = api.dispatch(self.store, 'GET', '/api/config', {}, None)
        self.assertEqual(payload['site']['links'], [])


if __name__ == '__main__':
    unittest.main()
