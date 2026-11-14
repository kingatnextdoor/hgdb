# -*- coding: utf-8 -*-
"""本地视频文件库：一张表存文件，一张表存「哪次扫描扫进来的」。

与 ED2K 库（ed2kdb.py）同一套约定，看那个文件头部的说明可以少走弯路：

  · 独立文件 `output/video.sqlite3`，与 ED2K 库同一套约定
  · SQL 一律不带库名，只认表名
  · 一把锁串行化写，读也走同一把锁

两张表
------
video_batch  一次扫描一行：根目录、扫到多少视频 / 新增 / 重复 / 跳过多少非视频
video_file   一个视频一行。唯一键 (root, rel_path)：重复扫同一个文件夹是幂等的

root 的两种取值
---------------
  · 服务器扫描：绝对路径（服务在本机跑，用户直接给 `O:\\video` 这种）
  · 浏览器选文件夹：只有文件夹名（浏览器出于安全不给绝对路径）
两种来源靠 (root, rel_path) 天然区分，互不冲突。
"""
import os
import threading
from datetime import datetime
from pathlib import Path

from web import config, dbio, queries, schema, video

# 列表接口返回的字段（含刮削结果：前端在文件列表里直接显示匹配状态）
FILE_FIELDS = ('id', 'root', 'rel_path', 'name', 'stem', 'ext',
               'size', 'batch_id', 'created_at',
               'drama_id', 'episode', 'episode_label',
               'match_status', 'match_score')

BATCH_FIELDS = ('id', 'root', 'files', 'inserted', 'duplicated',
                'skipped', 'created_at')

SCRAPE_BATCH_FIELDS = ('id', 'root', 'files', 'matched', 'review',
                       'unmatched', 'applied', 'created_at')

# 详情页播放列表用（不带 created_at / batch_id，那几个字段那儿用不上）
DRAMA_FILE_FIELDS = ('id', 'name', 'stem', 'ext', 'size', 'root', 'rel_path',
                     'episode', 'episode_label', 'match_score')

# match_status 的合法值（set_match 只认这几个，别的不认识的直接拒）
MATCH_STATUSES = ('matched', 'pending', 'unmatched', 'ignored')


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _like(text):
    """LIKE 模式 + 转义（同 ed2kdb._like：输入一个 % 不能命中全库）。"""
    escaped = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return '%' + escaped + '%'


def scan_dir(root):
    """在服务器本地扫一个目录（含子目录），返回 (root_str, entries, skipped)。

    只拿文件名和大小，不读内容 —— 「快速扫描」的语义。
    root 不存在 / 不是目录抛 FileNotFoundError；软链和隐藏目录照扫，
    谁的视频库里没有几集藏在 `.assets/` 里呢。
    """
    start = Path(root)
    if not start.is_dir():
        raise FileNotFoundError('目录不存在：%s' % root)
    root_str = str(start)
    entries, skipped = [], 0
    for dirpath, _dirnames, filenames in os.walk(root_str):
        for filename in filenames:
            # Windows 的 relpath 给反斜杠，统一成正斜杠，跟浏览器交来的路径一致
            rel = os.path.relpath(os.path.join(dirpath, filename),
                                  root_str).replace(os.sep, '/')
            if not video.is_video(rel):
                skipped += 1
                continue
            fields = video.split_rel(rel)
            try:
                size = os.path.getsize(os.path.join(dirpath, filename))
            except OSError:
                size = 0
            entries.append(dict(fields, root=root_str, rel_path=rel, size=size))
    return root_str, entries, skipped


class VideoRepo:
    """视频文件库的唯一读写入口。"""

    def __init__(self, output_dir, settings=None):
        self.output_dir = Path(output_dir)
        self.settings = settings or dbio.default_settings()
        self.cfg = self.settings.for_video(self.output_dir)
        self._lock = threading.Lock()
        self._schema_ready = False

    # ---------- 基础设施 ----------

    @property
    def path(self):
        """sqlite 文件路径。"""
        return Path(self.cfg.sqlite_path)

    def _connect(self):
        conn = self.cfg.connect()
        if not self._schema_ready:
            try:
                schema.ensure_video(conn)
                conn.commit()
            except dbio.DbError:
                conn.close()
                raise
            self._schema_ready = True
        return conn

    def describe(self):
        return self.cfg.describe()

    # ---------- 写 ----------

    def import_scan(self, root, entries, skipped=0):
        """把一次扫描的条目入库，返回本次统计。

        root 以参数为准（条目里的 root 字段只是扫描时的记录，可能是空），
        这样同一批条目挂到不同根目录下也能正常各存一份。
        重复（root + rel_path 都相同）只计数、不覆盖：首次扫到的信息更有价值，
        重复扫描是常态，不该每次都刷新 created_at。
        """
        if len(entries) > config.VIDEO_MAX_FILES:
            raise ValueError('单次最多导入 %d 个文件，本次 %d 个'
                             % (config.VIDEO_MAX_FILES, len(entries)))

        now = _now()
        root = str(root or '')
        insert_sql = (
            'INSERT OR IGNORE INTO video_file '
            '(root, rel_path, name, stem, ext, size, batch_id, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)')

        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    batch_id = conn.insert_id(
                        'INSERT INTO video_batch (root, files, inserted, duplicated, '
                        'skipped, created_at) VALUES (?, ?, 0, 0, ?, ?)',
                        (root, len(entries), int(skipped), now))
                    conn.executemany(insert_sql, [
                        (root, e['rel_path'], e['name'], e['stem'], e['ext'],
                         e['size'], batch_id, now) for e in entries])
                    inserted = conn.execute(
                        'SELECT COUNT(*) AS c FROM video_file WHERE batch_id = ?',
                        (batch_id,)).fetchone()['c']
                    duplicated = len(entries) - inserted
                    conn.execute(
                        'UPDATE video_batch SET inserted = ?, duplicated = ? WHERE id = ?',
                        (inserted, duplicated, batch_id))
            finally:
                conn.close()

        return {
            'ok': True,
            'batch_id': batch_id,
            'root': root,
            'files': len(entries),
            'inserted': inserted,
            'duplicated': duplicated,
            'skipped': int(skipped),
            'total': self.stats()['total'],
        }

    def delete_file(self, file_id):
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    return conn.execute('DELETE FROM video_file WHERE id = ?',
                                        (int(file_id),)).rowcount
            finally:
                conn.close()

    def delete_batch(self, batch_id):
        """整批撤销：删文件行，批次记录保留（历史里能看到扫过什么）。"""
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    return conn.execute('DELETE FROM video_file WHERE batch_id = ?',
                                        (int(batch_id),)).rowcount
            finally:
                conn.close()

    def clear(self):
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute('DELETE FROM video_file')
                    conn.execute('DELETE FROM video_batch')
                    conn.execute('DELETE FROM scrape_batch')
                    return cur.rowcount
            finally:
                conn.close()

    # ---------- 读 ----------

    def _where(self, q):
        """搜索：文件名 + 相对路径（不搜扩展名字段，那是给筛选用的）。"""
        text = (q or '').strip()
        if not text:
            return '', ()
        pattern = _like(text)
        clause = ('WHERE name LIKE ? ESCAPE \'\\\' OR rel_path LIKE ? ESCAPE \'\\\'')
        return clause, (pattern, pattern)

    def _order_by(self):
        # 同一批扫描的按相对路径排，翻页时顺序稳定
        return 'ORDER BY root, rel_path'

    def list_files(self, q='', page=1, size=config.VIDEO_PAGE_SIZE):
        where, args = self._where(q)
        with self._lock:
            conn = self._connect()
            try:
                total = conn.execute('SELECT COUNT(*) AS c FROM video_file ' + where,
                                     args).fetchone()['c']
                rows = conn.execute(
                    'SELECT ' + ', '.join(FILE_FIELDS) + ' FROM video_file ' + where
                    + ' ' + self._order_by() + ' LIMIT ? OFFSET ?',
                    args + (size, (page - 1) * size)).fetchall()
            finally:
                conn.close()

        return {
            'total': total,
            'page': page,
            'size': size,
            'pages': queries.pages_of(total, size),
            'items': rows,
        }

    def stats(self):
        """总条数、总体积、文件夹数、扩展名分布、最近一次扫描时间。"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    # SUM(BIGINT) 在 pg 里是 numeric -> Decimal，JSON 会炸，CAST 回 BIGINT
                    'SELECT COUNT(*) AS total, '
                    'CAST(COALESCE(SUM(size), 0) AS BIGINT) AS bytes, '
                    'COUNT(DISTINCT root) AS roots, MAX(created_at) AS last_at '
                    'FROM video_file').fetchone()
                exts = conn.execute(
                    'SELECT ext, COUNT(*) AS c FROM video_file '
                    'GROUP BY ext ORDER BY c DESC').fetchall()
                batches = conn.execute(
                    'SELECT COUNT(*) AS c FROM video_batch').fetchone()['c']
            finally:
                conn.close()
        return {
            'total': row['total'],
            'bytes': row['bytes'],
            'roots': row['roots'],
            'last_at': row['last_at'] or '',
            'exts': {r['ext'] or '(无扩展名)': r['c'] for r in exts},
            'batches': batches,
        }

    def batches(self, limit=10):
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    'SELECT ' + ', '.join(BATCH_FIELDS) + ' FROM video_batch '
                    'ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
            finally:
                conn.close()
        return rows

    # ---------- 刮削（匹配结果落库） ----------

    def files_of_root(self, root, limit=config.VIDEO_MAX_FILES):
        """某个目录下已入库的全部文件（刮削的输入，不分页）。"""
        root = str(root or '')
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(
                    'SELECT ' + ', '.join(FILE_FIELDS) + ' FROM video_file '
                    'WHERE root = ? ' + self._order_by() + ' LIMIT ?',
                    (root, limit)).fetchall()
            finally:
                conn.close()

    def apply_matches(self, root, rows):
        """把一批匹配结果写进 video_file，并记一条 scrape_batch。

        rows 每项 {'id', 'drama_id', 'episode', 'episode_label',
                  'match_status', 'match_score'}，id 之外的键缺了按空值存。
        只写交上来的行：预览里用户放弃没改的行不该被顺手覆盖。
        """
        if len(rows) > config.VIDEO_MAX_FILES:
            raise ValueError('单次最多写入 %d 行，本次 %d 行'
                             % (config.VIDEO_MAX_FILES, len(rows)))
        now = _now()
        root = str(root or '')

        def field(row, key, default=None):
            value = row.get(key, default)
            return default if value is None else value

        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    counts = {'matched': 0, 'review': 0, 'unmatched': 0}
                    for row in rows:
                        status = field(row, 'match_status', 'unmatched')
                        if status == 'pending':
                            counts['review'] += 1
                        elif status == 'matched':
                            counts['matched'] += 1
                        else:
                            counts['unmatched'] += 1
                        conn.execute(
                            'UPDATE video_file SET drama_id = ?, episode = ?, '
                            'episode_label = ?, match_status = ?, match_score = ?, '
                            'matched_at = ? WHERE id = ?',
                            (field(row, 'drama_id'), field(row, 'episode'),
                             field(row, 'episode_label', ''), status,
                             field(row, 'match_score'), now, int(row['id'])))
                    batch_id = conn.insert_id(
                        'INSERT INTO scrape_batch (root, files, matched, review, '
                        'unmatched, applied, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                        (root, len(rows), counts['matched'], counts['review'],
                         counts['unmatched'], len(rows), now))
            finally:
                conn.close()

        return dict(counts, ok=True, batch_id=batch_id, root=root,
                    files=len(rows), applied=len(rows), created_at=now)

    def set_match(self, file_id, status, drama_id=None, score=None):
        """人工改一个文件的匹配结果（确认 / 改选 / 忽略 / 复位）。

        ignored 表示「别再碰这个文件」，之后的自动刮削会跳过它。
        返回更新的行数（0 = 文件不存在）。
        """
        if status not in MATCH_STATUSES:
            raise ValueError('不认识的匹配状态：%r' % status)
        now = _now() if status in ('matched', 'pending') else None
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    return conn.execute(
                        'UPDATE video_file SET match_status = ?, drama_id = ?, '
                        'match_score = ?, matched_at = COALESCE(?, matched_at) '
                        'WHERE id = ?',
                        (status, drama_id, score, now, int(file_id))).rowcount
            finally:
                conn.close()

    def scrape_stats(self):
        """全库匹配状态统计 + 每个目录的分布（弹层下拉和徽标用）。"""
        # COALESCE 必须加：空表时 SUM(CASE...) 是 NULL，pg 返回 None 一减就炸
        def when(status):
            return ("CAST(COALESCE(SUM(CASE WHEN match_status = '%s' "
                    "THEN 1 ELSE 0 END), 0) AS BIGINT)" % status)
        # 状态是内部常量；CASE 写法 sqlite/pg 都认

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    'SELECT COUNT(*) AS files, '
                    + when('matched') + ' AS matched, '
                    + when('pending') + ' AS pending, '
                    + when('ignored') + ' AS ignored '
                    'FROM video_file').fetchone()
                roots = conn.execute(
                    'SELECT root, COUNT(*) AS files, '
                    + when('matched') + ' AS matched, '
                    + when('pending') + ' AS pending, '
                    + when('unmatched') + ' AS unmatched '
                    'FROM video_file GROUP BY root ORDER BY root').fetchall()
                batches = conn.execute(
                    'SELECT ' + ', '.join(SCRAPE_BATCH_FIELDS) + ' FROM scrape_batch '
                    'ORDER BY id DESC LIMIT 10').fetchall()
            finally:
                conn.close()
        return {
            'files': row['files'],
            'matched': row['matched'],
            'pending': row['pending'],
            'ignored': row['ignored'],
            'unmatched': max(0, row['files'] - row['matched']
                             - row['pending'] - row['ignored']),
            'roots': roots,
            'batches': batches,
        }

    def scrape_batch(self, batch_id):
        """一条刮削批次记录；不存在返回 None。"""
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(
                    'SELECT ' + ', '.join(SCRAPE_BATCH_FIELDS)
                    + ' FROM scrape_batch WHERE id = ?', (int(batch_id),)).fetchone()
            finally:
                conn.close()

    # ---------- 播放（详情页按剧取文件） ----------

    def files_of_drama(self, drama_id):
        """某部剧刮削匹配到的文件，按集数排（没解析出集号的排最后）。

        只认 match_status='matched'：pending 是「还没定」的猜测，ignored 是
        用户明确否掉的，都不该出现在播放列表里。
        ORDER BY 里用 CASE 而不用 NULLS LAST —— sqlite 要 3.30+ 才认后者。
        """
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(
                    'SELECT ' + ', '.join(DRAMA_FILE_FIELDS) + ' FROM video_file '
                    "WHERE drama_id = ? AND match_status = 'matched' "
                    'ORDER BY CASE WHEN episode IS NULL THEN 1 ELSE 0 END, '
                    'episode, rel_path', (str(drama_id),)).fetchall()
            finally:
                conn.close()

    def matched_drama_ids(self):
        """刮削确认匹配上的剧 id 集合（列表页「只看本地视频」筛选用）。

        只认 match_status='matched'：pending 是「还没定」的猜测、ignored 是用户
        明确否掉的，都不算「有可播的本地视频」——跟 files_of_drama 口径一致。
        """
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT drama_id FROM video_file "
                    "WHERE match_status = 'matched' "
                    "AND drama_id IS NOT NULL AND drama_id <> ''").fetchall()
            finally:
                conn.close()
        return {str(row['drama_id']) for row in rows}

    def file(self, file_id):
        """单个文件行（播放时按 id 取路径）；不存在返回 None。"""
        with self._lock:
            conn = self._connect()
            try:
                return conn.execute(
                    'SELECT ' + ', '.join(FILE_FIELDS) + ' FROM video_file '
                    'WHERE id = ?', (int(file_id),)).fetchone()
            finally:
                conn.close()
