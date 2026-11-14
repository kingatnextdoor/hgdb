# -*- coding: utf-8 -*-
"""数据库连接层：SQLite 单后端。

为什么还要有这一层
------------------
展示站的 SQL 只有十几条，上 ORM 不划算；但「连接怎么开、异常怎么包、行怎么变
dict」如果不收口，sqlite3.connect / row_factory / Error 这些东西会散落到
ed2kdb / videodb / dataset 里。所以约定：

    conn.execute(sql, params)   参数永远是元组（或 list），不要拼接字符串
    conn.executemany(sql, rows) 批量写
    conn.insert_id(sql, params) 拿自增主键
    except dbio.DbError         不要捕获 sqlite3.Error

库文件位置
----------
两个库跟着 output 目录走，各有独立的 sqlite 文件（采集侧不再有数据库）：

    output/ed2k.sqlite3    ED2K 链接库
    output/video.sqlite3   本地视频文件库

要整体指到别处（换盘 / 备份）就改 `hgdb/config.py` 里的 `DB_SQLITE`（相对路径按
仓库根解析），改完重启服务。**没有 .env 了**：配置只有「代码里的常量」一个来源，
不读配置文件、也不读环境变量，出问题看 config.py 就能看全。
"""
import sqlite3
import threading
from pathlib import Path

from hgdb import config


class DbError(RuntimeError):
    """所有数据库异常的统一种类（上层只认这一个）。"""


# ------------------------------ 连接配置 ------------------------------

class Config:
    """一份连接配置：只描述「去哪找 sqlite 文件」。

    sqlite_path 为 None 表示「跟随 output 目录」，具体路径要等
    for_ed2k() / for_video() 才知道。
    """

    __slots__ = ('sqlite_path',)

    def __init__(self, sqlite_path=None):
        self.sqlite_path = Path(sqlite_path) if sqlite_path else None

    # ---- 构造 ----

    @classmethod
    def from_config(cls, sqlite_path=None, root=None):
        """按 `hgdb/config.py` 里的 `DB_SQLITE` 造配置。

        没有配置文件、没有环境变量：想换位置就改那个常量（改完重启服务）。
        `sqlite_path` 显式给值时优先，测试靠它隔离。
        """
        raw = str(sqlite_path if sqlite_path is not None else config.DB_SQLITE or '').strip()
        if not raw:
            return cls(None)
        path = Path(raw)
        if not path.is_absolute():
            path = Path(root or config.ROOT_DIR) / path
        return cls(path)

    # ---- 派生 ----

    def _for(self, output_dir, filename):
        if self.sqlite_path is not None:
            return Config(self.sqlite_path)
        return Config(Path(output_dir) / filename)

    def for_ed2k(self, output_dir):
        """ED2K 链接库的文件位置。"""
        return self._for(output_dir, config.ED2K_DB)

    def for_video(self, output_dir):
        """本地视频文件库的文件位置。"""
        return self._for(output_dir, config.VIDEO_DB)

    def describe(self):
        """给日志/状态用的一行描述。"""
        if self.sqlite_path is None:
            return 'sqlite（跟随 output 目录）'
        return 'sqlite:%s' % self.sqlite_path

    def __repr__(self):
        return '<Config %s>' % self.describe()

    # ---- 连 ----

    def connect(self):
        """开一个连接。失败一律抛 DbError（带排查提示）。"""
        path = self.sqlite_path
        if path is None:
            raise DbError('没有指定数据库文件（改 config.DB_SQLITE 或 for_ed2k/for_video）')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = sqlite3.connect(str(path), timeout=10)
            raw.row_factory = sqlite3.Row        # 让 Cursor 能 dict(row)
        except (sqlite3.Error, OSError) as exc:
            raise DbError('连不上 sqlite 文件 %s：%s' % (path, _explain(exc))) from exc
        return Connection(raw)


# ------------------------------ 连接 ------------------------------

class Cursor:
    """对底层 cursor 的薄包装：行统一成 dict，异常统一成 DbError。"""

    __slots__ = ('_cur',)

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(row) for row in self._cur.fetchall()]

    @property
    def rowcount(self):
        try:
            return self._cur.rowcount
        except Exception:                      # pragma: no cover - 驱动差异兜底
            return -1

    @property
    def lastrowid(self):
        return getattr(self._cur, 'lastrowid', None)


class Connection:
    """一个连接。支持 `with conn:` 当事务用（正常结束提交，异常回滚）。"""

    __slots__ = ('raw',)

    def __init__(self, raw):
        self.raw = raw

    # ---- 执行 ----

    def execute(self, sql, params=()):
        # 每条语句一个新游标：复用同一个会让 rowcount 被后一条语句覆盖
        # （`clear()` 里就是「先删链接、再删批次，返回第一条的行数」）
        try:
            cur = self.raw.cursor()
            if params:
                cur.execute(sql, tuple(params))
            else:
                cur.execute(sql)
        except sqlite3.Error as exc:
            raise DbError(_explain(exc)) from exc
        return Cursor(cur)

    def executemany(self, sql, rows):
        rows = list(rows)
        if not rows:
            return Cursor(self.raw.cursor())
        try:
            cur = self.raw.cursor()
            cur.executemany(sql, rows)
        except sqlite3.Error as exc:
            raise DbError(_explain(exc)) from exc
        return Cursor(cur)

    def insert_id(self, sql, params=()):
        """执行 INSERT 并返回自增主键。"""
        return self.execute(sql, params).lastrowid

    def script(self, sql):
        """执行多语句脚本（建表用）。"""
        try:
            self.raw.executescript(sql)
        except sqlite3.Error as exc:
            raise DbError(_explain(exc)) from exc

    # ---- 事务 / 生命周期 ----

    def commit(self):
        try:
            self.raw.commit()
        except sqlite3.Error as exc:
            raise DbError(_explain(exc)) from exc

    def rollback(self):
        try:
            self.raw.rollback()
        except sqlite3.Error:                  # 回滚失败也没什么可做的
            pass

    def close(self):
        try:
            self.raw.close()
        except Exception:                      # pragma: no cover
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def _explain(exc):
    """把驱动异常包成一句人能读的话，保留原始文本方便搜。"""
    text = str(exc).strip().splitlines()
    return '%s: %s' % (type(exc).__name__, text[0] if text else exc)


# ------------------------------ 默认配置 ------------------------------

_default = None
_default_lock = threading.Lock()


def default_settings():
    """进程级默认配置（按 config.DB_SQLITE 造一次就缓存）。"""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = Config.from_config()
    return _default


def set_default(cfg):
    """覆盖默认配置。测试用它把整个进程钉在临时目录上，避免误连真库。"""
    global _default
    with _default_lock:
        _default = cfg


def reset_default():
    """丢掉缓存，下次重新按 config.DB_SQLITE 造（改了常量之后有用）。"""
    global _default
    with _default_lock:
        _default = None
