# -*- coding: utf-8 -*-
"""数据库连接层的单元测试：配置解析、连接封装、建表、Store 签名。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hgdb import config, dbio, schema
from hgdb.store import Store


def tmpdir(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    return Path(tmp.name)


# ------------------------------ 配置 ------------------------------

class ConfigTest(unittest.TestCase):
    def test_default_follows_output_dir(self):
        # config.DB_SQLITE 留空 = 每个库一个文件、跟着 output 目录走
        cfg = dbio.Config.from_config()
        self.assertIsNone(cfg.sqlite_path)
        self.assertEqual(cfg.for_ed2k('out').sqlite_path, Path('out') / config.ED2K_DB)

    def test_reads_config_constant(self):
        """改 config.DB_SQLITE 就生效（唯一配置来源，没有 .env / 环境变量）。"""
        with mock.patch.object(config, 'DB_SQLITE', './data/x.db'):
            cfg = dbio.Config.from_config()
        self.assertEqual(cfg.sqlite_path, config.ROOT_DIR / 'data' / 'x.db')

    def test_absolute_path_is_kept(self):
        root = tmpdir(self)
        cfg = dbio.Config.from_config(str(root / 'x.db'))
        self.assertEqual(cfg.sqlite_path, root / 'x.db')

    def test_db_sqlite_relative_to_root(self):
        root = tmpdir(self)
        cfg = dbio.Config.from_config('./data/x.db', root=root)
        self.assertEqual(cfg.sqlite_path, root / 'data' / 'x.db')

    def test_two_libs_use_two_files(self):
        cfg = dbio.Config.from_config()
        ed2k = cfg.for_ed2k('out')
        video = cfg.for_video('out')
        self.assertEqual(ed2k.sqlite_path.name, config.ED2K_DB)
        self.assertEqual(video.sqlite_path.name, config.VIDEO_DB)
        self.assertNotEqual(ed2k.sqlite_path, video.sqlite_path)

    def test_describe_names_the_path(self):
        cfg = dbio.Config.from_config('x.db', root=tmpdir(self))
        self.assertIn('x.db', cfg.describe())
        self.assertIn('x.db', repr(cfg))
        self.assertIn('output', dbio.Config().describe())


# ------------------------------ 建表 ------------------------------

class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.cfg = dbio.Config(tmpdir(self) / 'x.sqlite3')
        self.conn = self.cfg.connect()
        self.addCleanup(self.conn.close)

    def test_ddl_resolves_serial(self):
        text = schema.ddl(schema.ED2K_DDL)
        self.assertIn('AUTOINCREMENT', text)
        self.assertNotIn('{serial}', text)

    def test_ensure_all_is_idempotent(self):
        schema.ensure_all(self.conn)
        self.conn.commit()
        schema.ensure_all(self.conn)
        self.conn.commit()
        self.assertEqual(schema.missing_tables(self.conn), [])

    def test_missing_tables_before_init(self):
        self.assertEqual(schema.missing_tables(self.conn),
                         ['ed2k_link', 'ed2k_batch',
                          'video_file', 'video_batch', 'scrape_batch'])

    def test_ensure_ed2k_only(self):
        schema.ensure_ed2k(self.conn)
        self.conn.commit()
        # 链接表唯一键必须真的生效，否则去重就废了
        self.conn.execute('INSERT INTO ed2k_link (hash, name, url, created_at) '
                          'VALUES (?, ?, ?, ?)', ('A' * 32, 'n', 'u', 'now'))
        self.conn.commit()
        rows = self.conn.execute('SELECT COUNT(*) AS c FROM ed2k_link').fetchone()
        self.assertEqual(rows['c'], 1)
        with self.assertRaises(dbio.DbError):
            self.conn.execute('INSERT INTO ed2k_link (hash, name, url, created_at) '
                              'VALUES (?, ?, ?, ?)', ('A' * 32, 'n2', 'u2', 'now'))


# ------------------------------ 连接 ------------------------------

class SqliteConnectionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = dbio.Config(tmpdir(self) / 'x.sqlite3')
        self.conn = self.cfg.connect()
        self.addCleanup(self.conn.close)
        schema.ensure_ed2k(self.conn)
        self.conn.commit()

    def test_rows_are_dicts(self):
        row = self.conn.execute('SELECT 1 AS one').fetchone()
        self.assertEqual(row, {'one': 1})

    def test_fetchone_none(self):
        self.assertIsNone(self.conn.execute('SELECT 1 AS one WHERE 0').fetchone())

    def test_insert_id_and_lastrowid(self):
        with self.conn:
            first = self.conn.insert_id(
                'INSERT INTO ed2k_batch (filename, created_at) VALUES (?, ?)',
                ('a.txt', 'now'))
            second = self.conn.insert_id(
                'INSERT INTO ed2k_batch (filename, created_at) VALUES (?, ?)',
                ('b.txt', 'now'))
        self.assertEqual((first, second), (1, 2))

    def test_executemany_and_rowcount(self):
        self.conn.executemany(
            'INSERT INTO ed2k_link (hash, name, url, created_at) VALUES (?, ?, ?, ?)',
            [('A' * 32, 'a', 'u', 'now'), ('B' * 32, 'b', 'u', 'now')])
        self.conn.commit()
        cur = self.conn.execute('DELETE FROM ed2k_link')
        self.assertEqual(cur.rowcount, 2)

    def test_transaction_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with self.conn:
                self.conn.execute(
                    'INSERT INTO ed2k_link (hash, name, url, created_at) '
                    'VALUES (?, ?, ?, ?)', ('C' * 32, 'c', 'u', 'now'))
                raise RuntimeError('boom')
        rows = self.conn.execute('SELECT COUNT(*) AS c FROM ed2k_link').fetchone()
        self.assertEqual(rows['c'], 0)

    def test_bad_sql_becomes_db_error(self):
        with self.assertRaises(dbio.DbError):
            self.conn.execute('SELECT * FROM no_such_table')

    def test_script_runs_many_statements(self):
        schema.ensure_ed2k(self.conn)
        schema.ensure_video(self.conn)
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT name AS n FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()
        self.assertEqual({row['n'] for row in rows},
                         {'ed2k_link', 'ed2k_batch', 'video_file',
                          'video_batch', 'scrape_batch'})


# ------------------------------ 默认配置 ------------------------------

class DefaultSettingsTest(unittest.TestCase):
    def test_set_and_reset(self):
        before = dbio.default_settings()
        try:
            cfg = dbio.Config(tmpdir(self) / 'y.sqlite3')
            dbio.set_default(cfg)
            self.assertIs(dbio.default_settings(), cfg)
        finally:
            dbio.set_default(before)
        self.assertIs(dbio.default_settings(), before)

    def test_store_uses_given_settings(self):
        root = tmpdir(self)
        (root / 'duanju_1.jsonl').write_text('', encoding='utf-8')
        with mock.patch('hgdb.store.dataset.load',
                        return_value={'items': [], 'by_id': {}, 'stats': {},
                                      'facets': {}, 'mtime': 0}):
            cfg = dbio.Config(tmpdir(self) / 'z.sqlite3')
            store = Store(root, cfg)
        # 两个库都要用传进来的这份配置（DB_SQLITE 显式指定时两库合一）
        self.assertIs(store.ed2k.settings, cfg)
        self.assertIs(store.video.settings, cfg)


class StoreSignatureTest(unittest.TestCase):
    def _store(self, cfg, root):
        with mock.patch('hgdb.store.dataset.load',
                        return_value={'items': [], 'by_id': {}, 'stats': {},
                                      'facets': {}, 'mtime': 0}):
            return Store(root, cfg)

    def test_signature_has_two_fields(self):
        root = tmpdir(self)
        sig = self._store(dbio.Config(), root).signature()
        self.assertEqual(len(sig), 2)

    def test_file_mtime_moves_signature(self):
        root = tmpdir(self)
        store = self._store(dbio.Config(), root)
        first = store.signature()
        (root / 'duanju_1.jsonl').write_text('{"drama_id": "1"}\n', encoding='utf-8')
        self.assertNotEqual(store.signature(), first)


if __name__ == '__main__':
    unittest.main()
