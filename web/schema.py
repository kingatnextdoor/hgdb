# -*- coding: utf-8 -*-
"""建表 DDL（SQLite）。

只有两组表（采集侧不再有状态库，drama / run 已随 state.sqlite3 一起删除）：
    ED2K_DDL   ed2k_link / ed2k_batch   展示站自己写的数据（人工上传）
    VIDEO_DDL  video_file / video_batch 本地视频文件库（人工扫描）

注意：`ensure_*()` 只建表不删表，重复调用安全（全是 IF NOT EXISTS）。
"""

# 自增主键（所有表共用）
SERIAL = 'INTEGER PRIMARY KEY AUTOINCREMENT'

ED2K_DDL = """
CREATE TABLE IF NOT EXISTS ed2k_batch (
    id          {serial},
    filename    TEXT    NOT NULL DEFAULT '',
    size_bytes  BIGINT  NOT NULL DEFAULT 0,
    lines       INTEGER NOT NULL DEFAULT 0,
    parsed      INTEGER NOT NULL DEFAULT 0,
    inserted    INTEGER NOT NULL DEFAULT 0,
    duplicated  INTEGER NOT NULL DEFAULT 0,
    invalid     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS ed2k_link (
    id            {serial},
    hash          TEXT   NOT NULL UNIQUE,      -- 32 位大写 MD5，ed2k 的天然主键
    name          TEXT   NOT NULL,             -- 原始文件名
    title         TEXT   NOT NULL DEFAULT '',  -- 拆出来的剧名，便于按剧聚合
    episode       INTEGER,                     -- 起始集号，拆不出来为 NULL
    episode_label TEXT   NOT NULL DEFAULT '',  -- 原始集数标记：01 / 01-10 / 第3集
    size          BIGINT NOT NULL DEFAULT 0,
    url           TEXT   NOT NULL,
    batch_id      BIGINT,                      -- 属于哪次上传
    source_file   TEXT   NOT NULL DEFAULT '',
    created_at    TEXT   NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ed2k_link_title ON ed2k_link(title);
CREATE INDEX IF NOT EXISTS idx_ed2k_link_batch ON ed2k_link(batch_id);
"""

VIDEO_DDL = """
CREATE TABLE IF NOT EXISTS video_batch (
    id          {serial},
    root        TEXT    NOT NULL DEFAULT '',  -- 扫描的根目录（服务器扫描=绝对路径）
    files       INTEGER NOT NULL DEFAULT 0,   -- 这批扫到的视频条数
    inserted    INTEGER NOT NULL DEFAULT 0,
    duplicated  INTEGER NOT NULL DEFAULT 0,
    skipped     INTEGER NOT NULL DEFAULT 0,   -- 跳过的非视频文件
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS video_file (
    id          {serial},
    root        TEXT   NOT NULL,             -- 唯一键一半：所选文件夹 / 服务器绝对路径
    rel_path    TEXT   NOT NULL,             -- 另一半：相对 root 的路径（/ 分隔）
    name        TEXT   NOT NULL,             -- 文件名（含扩展名）
    stem        TEXT   NOT NULL DEFAULT '',  -- 主名（不含扩展名，按剧名搜索时用）
    ext         TEXT   NOT NULL DEFAULT '',  -- 小写扩展名
    size        BIGINT NOT NULL DEFAULT 0,
    batch_id    BIGINT,
    created_at  TEXT   NOT NULL,
    UNIQUE (root, rel_path)                  -- 重复扫描同一个文件夹是幂等的
);

CREATE INDEX IF NOT EXISTS idx_video_file_name ON video_file(name);
CREATE INDEX IF NOT EXISTS idx_video_file_batch ON video_file(batch_id);

CREATE TABLE IF NOT EXISTS scrape_batch (
    id          {serial},
    root        TEXT    NOT NULL DEFAULT '',  -- 这次刮削的目录（对应 video_file.root）
    files       INTEGER NOT NULL DEFAULT 0,   -- 参与匹配的文件数
    matched     INTEGER NOT NULL DEFAULT 0,   -- 自动匹配成功
    review      INTEGER NOT NULL DEFAULT 0,   -- 待人工确认（置信度不够）
    unmatched   INTEGER NOT NULL DEFAULT 0,   -- 没找到候选
    applied     INTEGER NOT NULL DEFAULT 0,   -- 实际写库的行数
    created_at  TEXT    NOT NULL
);
"""

# 刮削功能给 video_file 后加的列。老库（建表时没有这些列）由
# `_upgrade_video_file()` 用幂等 ALTER 补齐，新库直接在 DDL 里放不下了——
# 放两份迟早漂移，索性统一走 ALTER：建表 DDL 保持 2026 年的样子。
SCRAPE_VIDEO_COLUMNS = (
    ('drama_id',      'TEXT'),                          # 匹配到的剧 id（取自导出记录）
    ('episode',       'INTEGER'),                       # 从文件名解析出的起始集号
    ('episode_label', "TEXT NOT NULL DEFAULT ''"),      # 原始集数标记：01 / 第3集
    ('match_status',  "TEXT NOT NULL DEFAULT 'unmatched'"),
    ('match_score',   'DOUBLE PRECISION'),              # 0~1 置信度
    ('matched_at',    'TEXT'),                          # 最近一次匹配写入时间
)

# 与 SCRAPE_VIDEO_COLUMNS 同一个思路：给老库 ed2k_link 后补的列，一律走幂等
# ALTER（`_upgrade_ed2k_link()`），建表 DDL 保持 2026 年的样子，避免两处漂移。
ED2K_LINK_EXTRA_COLUMNS = (
    # 剧名能否在站内找到：1=在，0=不在（导入时与内存数据集里的剧名比对写入）
    ('in_drama', 'INTEGER NOT NULL DEFAULT 0'),
)



def ddl(script):
    """把 `{serial}` 换成自增主键写法。"""
    return script.replace('{serial}', SERIAL)


def _upgrade_ed2k_link(conn):
    """给老库补 ED2K 后加的列：缺哪列补哪列，返回这次补上的列名（新库为空）。"""
    have = existing_columns(conn, 'ed2k_link')
    added = []
    for name, col_type in ED2K_LINK_EXTRA_COLUMNS:
        if name not in have:
            # 列名和类型都是内部常量，不拼用户输入
            conn.execute('ALTER TABLE ed2k_link ADD COLUMN %s %s' % (name, col_type))
            added.append(name)
    return added


def ensure_ed2k(conn):
    """建 ED2K 两张表（幂等），并把老库升级到带 in_drama 列的形状。

    返回这次新补上的列名列表，调用方（ed2kdb）借此决定要不要回填数据。
    """
    conn.script(ddl(ED2K_DDL))
    return _upgrade_ed2k_link(conn)


def existing_columns(conn, table):
    """表里实际有哪些列（列名小写）。给幂等 ALTER 用。"""
    rows = conn.execute('PRAGMA table_info(%s)' % table).fetchall()
    return {str(row['name']).lower() for row in rows}


def _upgrade_video_file(conn):
    """给老库补刮削列：缺哪列补哪列，新库一行都不用执行。"""
    have = existing_columns(conn, 'video_file')
    for name, col_type in SCRAPE_VIDEO_COLUMNS:
        if name not in have:
            # 列名和类型都是内部常量，不拼用户输入
            conn.execute('ALTER TABLE video_file ADD COLUMN %s %s' % (name, col_type))


def ensure_video(conn):
    """建视频文件库相关表（幂等），并把老库升级到带刮削列的形状。"""
    conn.script(ddl(VIDEO_DDL))
    _upgrade_video_file(conn)


def ensure_all(conn):
    ensure_ed2k(conn)
    ensure_video(conn)


def missing_tables(conn, names=('ed2k_link', 'ed2k_batch', 'video_file',
                                'video_batch', 'scrape_batch')):
    """返回库里缺哪些表（用来判断「要不要先 init」）。"""
    rows = conn.execute(
        "SELECT name AS tablename FROM sqlite_master WHERE type = 'table'").fetchall()
    existing = {row['tablename'] for row in rows}
    return [name for name in names if name not in existing]


__all__ = [
    'ED2K_DDL', 'VIDEO_DDL',
    'SCRAPE_VIDEO_COLUMNS', 'ED2K_LINK_EXTRA_COLUMNS',
    'ddl', 'ensure_all', 'ensure_ed2k', 'ensure_video',
    'existing_columns', 'missing_tables',
]
