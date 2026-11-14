# -*- coding: utf-8 -*-
"""ED2K 链接库：一张表存链接，一张表存「谁在什么时候传的」。

为什么单独一个文件（`output/ed2k.sqlite3`）
------------------------------------------
ED2K 链接是人工整理上传的数据，重抓、`manage.py reset` 都不该把它带走。
所以放独立文件，schema 也由本模块自己管（采集工具永远不会碰它）。

两张表
------
ed2k_batch  一次上传一行：文件名、行数、新增 / 重复 / 无效各多少，导入历史可追溯
ed2k_link   一条链接一行，`hash` 唯一 —— 重复上传同一个文件不会产生重复记录

SQL 约定
--------
一律 `?` 占位符，语句直接写 SQLite 写法（`INSERT OR IGNORE` / `COLLATE NOCASE`）。

线程安全：ThreadingHTTPServer 每个请求一个线程，这里用一把锁把「建表 / 写」
串起来；读也走同一把锁（一次查询毫秒级，比引入连接池简单得多）。
"""
import threading
from datetime import datetime
from pathlib import Path

from web import config, dbio, ed2k, queries, schema

# 列表接口返回的字段（不含自增 id：对前端来说 hash 才是主键）
LINK_FIELDS = ('hash', 'name', 'title', 'episode', 'episode_label',
               'size', 'url', 'batch_id', 'source_file', 'created_at',
               'in_drama')

BATCH_FIELDS = ('id', 'filename', 'size_bytes', 'lines', 'parsed', 'inserted',
                'duplicated', 'invalid', 'created_at')


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _like(text):
    """把用户输入包成 LIKE 模式，转义 % 和 _（否则输入一个 % 就命中全库）。

    两个后端都支持自定义 ESCAPE，所以 SQL 里统一写 `ESCAPE '\\'`。
    """
    escaped = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return '%' + escaped + '%'


class Ed2kRepo:
    """ED2K 链接的唯一读写入口。"""

    def __init__(self, output_dir, settings=None, drama_titles=None):
        self.output_dir = Path(output_dir)
        self.settings = settings or dbio.default_settings()
        self.cfg = self.settings.for_ed2k(self.output_dir)
        self._lock = threading.Lock()
        self._schema_ready = False
        # 返回「站内已有剧名」的可调用对象，由 Store 注入（数据源是内存数据集）
        self.drama_titles = drama_titles
        self._last_titles = None        # 上次重算 in_drama 用的剧名集合

    # ---------- 基础设施 ----------

    @property
    def path(self):
        """sqlite 文件路径。"""
        return Path(self.cfg.sqlite_path)

    def _connect(self):
        conn = self.cfg.connect()
        if not self._schema_ready:
            try:
                schema.ensure_ed2k(conn)        # 老库缺 in_drama 列会在这里补上
                conn.commit()
            except dbio.DbError:
                conn.close()
                raise
            self._schema_ready = True
        return conn

    def sync_drama_flags(self):
        """站内剧名集合变了就把 in_drama 重算一遍；没变就什么都不做。

        不在 `_connect()` 里做这件事，因为数据集要等 Store 读完 jsonl 才有剧名，
        那时 provider 还返回空集 —— 拿空集重算会把全库刷成「不在」。
        """
        titles = self._drama_titles()
        if not titles or titles == self._last_titles:
            return 0
        if not self.path.is_file():
            return 0
        with self._lock:
            conn = self._connect()
            try:
                self._recheck_drama_flags(conn, titles)
            finally:
                conn.close()
        return len(titles)

    # ---------- 短剧表比对 ----------

    def _drama_titles(self):
        """站内已有剧名的归一化集合，用来判断链接的剧名是否收录。

        来源是构造时注入的 provider（展示站给的是内存数据集里的剧名，和页面
        展示同源）；没注入就返回空集 —— 此时所有链接都按「不在」标记，这与
        「站点上根本搜不到这部剧」的事实一致。
        """
        if self.drama_titles is None:
            return set()
        try:
            titles = self.drama_titles() or []
        except Exception:                       # noqa: BLE001 数据源异常不该拖垮链接库
            return set()
        return {ed2k.normalize_title(t) for t in titles if (t or '').strip()}

    def _recheck_drama_flags(self, conn, titles=None):
        """把库里**所有**链接的 in_drama 按给定剧名集合重算一遍并提交。

        titles 不传则回退去问 provider（站内剧名集合）。导入结束时也调它：
        站内会不断新增剧，老链接的标记跟着刷新（上次「不在」的剧，采集侧抓到
        之后再导入/查看就是「在」了）。必须在持有 self._lock 的上下文里调用。
        """
        titles = self._drama_titles() if titles is None else titles
        if not titles:
            # 一条剧名都没有（数据集还没加载 / 站点确实是空的）：不清空标记，
            # 宁可留着旧值，也不要因为一次空输入把全库刷成「不在」。
            return
        rows = conn.execute('SELECT hash, title FROM ed2k_link').fetchall()
        updates = [(1 if ed2k.normalize_title(r['title']) in titles else 0, r['hash'])
                   for r in rows]
        if updates:
            conn.executemany(
                'UPDATE ed2k_link SET in_drama = ? WHERE hash = ?', updates)
            conn.commit()
        self._last_titles = set(titles)

    def exists(self):
        return bool(self.path and self.path.is_file())

    def describe(self):
        return self.cfg.describe()

    # ---------- 写 ----------

    def import_text(self, text, filename='', size_bytes=0, drama_titles=None):
        """解析并导入一段文本，返回本次导入的统计。

        重复链接（库里已有同一 hash）只计数、不覆盖：第一次上传的来源信息
        更有价值，而且重传是常态，不该每次都改 created_at。

        drama_titles：站内已有剧名（原样即可，比对前会统一归一化），用来打
        in_drama 标记。不传则回退去问构造时注入的 provider。展示站上传走 api，
        传的是内存数据集里的剧名 —— 和页面展示同源。
        """
        parsed = ed2k.parse_links(text)
        if not parsed['links']:
            raise ValueError('文件里没有找到合法的 ed2k 链接')

        links = parsed['links']
        if len(links) > config.ED2K_MAX_LINKS:
            raise ValueError('单次最多导入 %d 条链接，本次 %d 条'
                             % (config.ED2K_MAX_LINKS, len(links)))

        # 剧名在不在短剧表：插入时就带上标记，最后再整体刷一遍（见 _recheck_drama_flags）
        # 传入的剧名可能是原样的（「示例剧 第二季」），统一归一化成比对键
        if drama_titles is None:
            drama_titles = self._drama_titles()
        else:
            drama_titles = {ed2k.normalize_title(t) for t in drama_titles}
        drama_titles.discard('')
        for link in links:
            link['in_drama'] = 1 if ed2k.normalize_title(link['title']) in drama_titles else 0
        not_in_drama = sum(1 for row in links if not row['in_drama'])

        now = _now()
        filename = (filename or '')[:200]
        insert_sql = (
            'INSERT OR IGNORE INTO ed2k_link '
            '(hash, name, title, episode, episode_label, size, url, batch_id, '
            'source_file, created_at, in_drama) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')

        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    batch_id = conn.insert_id(
                        'INSERT INTO ed2k_batch (filename, size_bytes, lines, parsed, '
                        'inserted, duplicated, invalid, created_at) '
                        'VALUES (?, ?, ?, ?, 0, 0, ?, ?)',
                        (filename, int(size_bytes), parsed['lines'], len(links),
                         parsed['invalid'], now))
                    conn.executemany(insert_sql, [
                        (row['hash'], row['name'], row['title'], row['episode'],
                         row['episode_label'], row['size'], row['url'], batch_id,
                         filename, now, row['in_drama']) for row in links])
                    inserted = conn.execute(
                        'SELECT COUNT(*) AS c FROM ed2k_link WHERE batch_id = ?',
                        (batch_id,)).fetchone()['c']
                    duplicated = len(links) - inserted
                    conn.execute(
                        'UPDATE ed2k_batch SET inserted = ?, duplicated = ? WHERE id = ?',
                        (inserted, duplicated, batch_id))
                # 事务外整体刷新：库里的老链接（含这次跳过的重复项）也跟着
                # 当前短剧表重算标记
                self._recheck_drama_flags(conn, drama_titles)
            finally:
                conn.close()

        return {
            'ok': True,
            'batch_id': batch_id,
            'filename': filename,
            'lines': parsed['lines'],
            'parsed': len(links),
            'inserted': inserted,
            # 文件内重复出现的同一条链接也算「重复」
            'duplicated': duplicated + parsed['repeated'],
            'invalid': parsed['invalid'],
            'ignored': parsed['ignored'],
            'not_in_drama': not_in_drama,
            'total': self.stats()['total'],
        }

    def delete_link(self, digest):
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute('DELETE FROM ed2k_link WHERE hash = ?',
                                       (digest.upper(),))
                    return cur.rowcount
            finally:
                conn.close()

    def delete_batch(self, batch_id):
        """整批撤销：返回删掉的链接条数（批次记录保留，免得历史凭空少一行）。"""
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute('DELETE FROM ed2k_link WHERE batch_id = ?',
                                       (int(batch_id),))
                    return cur.rowcount
            finally:
                conn.close()

    def clear(self):
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    cur = conn.execute('DELETE FROM ed2k_link')
                    conn.execute('DELETE FROM ed2k_batch')
                    return cur.rowcount
            finally:
                conn.close()

    # ---------- 读 ----------

    def _where(self, q):
        text = (q or '').strip()
        if not text:
            return '', ()
        pattern = _like(text)
        clause = ('WHERE title LIKE ? ESCAPE \'\\\' OR name LIKE ? ESCAPE \'\\\' '
                  'OR hash LIKE ? ESCAPE \'\\\'')
        return clause, (pattern, pattern, pattern)

    def _order_by(self):
        return 'ORDER BY title COLLATE NOCASE, COALESCE(episode, 0), name'

    def list_links(self, q='', page=1, size=config.ED2K_PAGE_SIZE):
        """按剧名 / 集数排序的分页列表（管理表的默认视角）。"""
        where, args = self._where(q)
        with self._lock:
            conn = self._connect()
            try:
                total = conn.execute('SELECT COUNT(*) AS c FROM ed2k_link ' + where,
                                     args).fetchone()['c']
                rows = conn.execute(
                    'SELECT ' + ', '.join(LINK_FIELDS) + ' FROM ed2k_link ' + where
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

    def titles(self):
        """链接库里出现过的剧名（去重、去空）—— 列表页「只看有 ED2K」的数据来源。

        只取 `title` 一列：3268 行一条查询，10ms 量级，比逐部剧去查库便宜得多。
        匹配口径交给调用方（dataset 用 `ed2k.title_keywords`，与 detail 的
        `search_by_title` 同一套），这里不做任何归一化，保持「库里的原样」。
        """
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT title FROM ed2k_link "
                    "WHERE title IS NOT NULL AND title <> ''").fetchall()
            finally:
                conn.close()
        return [row['title'] for row in rows]

    def search_by_title(self, title, limit=config.ED2K_TITLE_LIMIT):
        """按剧名找资源：详情页用，逐档降级关键词，命中最多的一档即返回。

        与 `list_links(q)` 的区别：那个是管理表的人工搜索（剧名 / 文件名 / HASH
        都能搜），这里是「这部剧有哪些资源」，只认剧名，而且允许剧名不完全一致。

        返回 dict：
          keyword     真正用上的关键词（≠ title 说明做了降级）
          matched_by  哪一档命中的，前端拿来解释「按 X 匹配」
          total/bytes 命中条数与总体积（bytes 用于详情页显示「共 x GB」）
          items       链接记录（按集数排序，最多 limit 条）
        """
        payload = {'title': title or '', 'keyword': '', 'matched_by': '',
                   'total': 0, 'bytes': 0, 'items': []}
        keywords = ed2k.title_keywords(title)
        if not keywords:
            return payload

        where = "WHERE title LIKE ? ESCAPE '\\'"
        with self._lock:
            conn = self._connect()
            try:
                for keyword, label in keywords:
                    pattern = _like(keyword)
                    agg = conn.execute(
                        'SELECT COUNT(*) AS total, '
                        'CAST(COALESCE(SUM(size), 0) AS BIGINT) AS bytes '
                        'FROM ed2k_link ' + where, (pattern,)).fetchone()
                    if not agg['total']:
                        continue
                    rows = conn.execute(
                        'SELECT ' + ', '.join(LINK_FIELDS) + ' FROM ed2k_link ' + where
                        + ' ' + self._order_by() + ' LIMIT ?',
                        (pattern, limit)).fetchall()
                    payload.update({
                        'keyword': keyword,
                        'matched_by': label,
                        'total': agg['total'],
                        'bytes': agg['bytes'],
                        'items': rows,
                    })
                    return payload
            finally:
                conn.close()

        # 一条都没命中：把最严格的关键词回给前端，提示文案里用得上
        payload['keyword'] = keywords[0][0]
        return payload

    def stats(self):
        """顶部统计：总条数、总体积、剧名数、最近一次上传时间。"""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    # SUM(BIGINT) 在 pg 里是 numeric，直接用会让 JSON 序列化失败
                    # （Decimal），所以两头都 CAST 回 BIGINT
                    'SELECT COUNT(*) AS total, CAST(COALESCE(SUM(size), 0) AS BIGINT) AS bytes, '
                    'COUNT(DISTINCT title) AS titles, MAX(created_at) AS last_at '
                    'FROM ed2k_link').fetchone()
                batches = conn.execute(
                    'SELECT COUNT(*) AS c FROM ed2k_batch').fetchone()['c']
            finally:
                conn.close()
        return {
            'total': row['total'],
            'bytes': row['bytes'],
            'titles': row['titles'],
            'last_at': row['last_at'] or '',
            'batches': batches,
        }

    def batches(self, limit=10):
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    'SELECT ' + ', '.join(BATCH_FIELDS) + ' FROM ed2k_batch '
                    'ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
            finally:
                conn.close()
        return rows

    def export_text(self, q=''):
        """导出成可以直接喂给下载器的纯文本，一行一条。"""
        where, args = self._where(q)
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    'SELECT url FROM ed2k_link ' + where + ' ' + self._order_by(),
                    args).fetchall()
            finally:
                conn.close()
        return '\n'.join(row['url'] for row in rows)
