# -*- coding: utf-8 -*-
"""数据集持有者：内存里放一份，数据变了自动重载，并提供唯一的写入入口
（标签编辑、收藏）。

为什么需要它：ThreadingHTTPServer 每个请求一个线程，反复 `dataset.load()` 会很慢
（1300 部 + 1000 多张封面 stat），所以做一个签名，签名不变就复用内存里的那份。

签名怎么算
----------
看 `output/` 下 jsonl / ed2k.sqlite3 / video.sqlite3 / tag_edits.json /
favorites.json 的 mtime + 文件数，精确且零成本（采集工具或管理操作一写文件就重载）。
剧集数据没有缓存副本，jsonl 就是唯一来源，所以文件一变就一定能看到新数据。

ED2K 链接库不整体进内存：只取「剧名集合」给数据集打 has_ed2k（列表筛选用），
逐条链接仍然直接查库，见 ed2kdb.py。
"""
import threading
from datetime import datetime
from pathlib import Path

from hgdb import config, dataset, dbio, ed2kdb, sources, videodb


def _now():
    return datetime.now().isoformat(timespec='seconds')


class Store:
    """持有数据集，并在数据源变化时自动重载。"""

    def __init__(self, output_dir, settings=None):
        self.output_dir = Path(output_dir)
        self.settings = settings or dbio.default_settings()
        self._edit_lock = threading.Lock()   # 多线程写文件要串行
        self._sig = None
        self.data = None
        # 链接库的 in_drama 标记要靠「站内已有剧名」，来源就是内存数据集
        self.ed2k = ed2kdb.Ed2kRepo(self.output_dir, self.settings,
                                    drama_titles=self.drama_titles)
        self.video = videodb.VideoRepo(self.output_dir, self.settings)
        self.load(force=True)

    # ---------- 读 ----------

    def _watch_files(self):
        """会说话的本地文件：每次抓取的导出 + 标签编辑 + 收藏 + 各库文件。

        视频库变了要重算 has_video（列表页「只看本地视频」靠它），扫完 / 刮削完
        不用手点刷新；链接库变了重算 has_ed2k（上传 / 删除链接后「只看 ED2K」
        立刻跟上）。
        """
        files = sorted(self.output_dir.glob(config.EXPORT_GLOB))
        names = [config.TAG_EDITS_FILE, config.FAVORITES_FILE,
                 config.VIDEO_DB, config.ED2K_DB]
        for name in names:
            path = self.output_dir / name
            if path.is_file():
                files.append(path)
        return files

    def signature(self):
        """数据源的「版本号」：所有被监听文件里最新的 mtime + 文件数。"""
        files = self._watch_files()
        if files:
            return (max(f.stat().st_mtime for f in files), len(files))
        return (0.0, 0)

    def load(self, force=False):
        sig = self.signature()
        if not force and self.data is not None and sig == self._sig:
            return self.data
        self.data = dataset.load(self.output_dir,
                                 video_ids=self.video_drama_ids(),
                                 ed2k_titles=self.ed2k_link_titles())
        # 数据集就位了，链接库的「是否已收录」标记才有比对依据
        self.ed2k.sync_drama_flags()
        self._sig = sig
        return self.data

    def drama_titles(self):
        """内存数据集里的剧名（ED2K 的 in_drama 标记按它比对，与页面同源）。"""
        return [i.get('title') for i in (self.data or {}).get('items', ())]

    def video_drama_ids(self):
        """本地视频库里「已匹配」的剧 id 集合，喂给数据集打 has_video。

        两个降级：sqlite 下视频库文件还没生成就直接返回空集（不能为了一次判断
        凭空造一个空库）；库坏了 / 打不开也只是「没有任何剧有本地视频」，
        不该让整个列表挂掉。
        """
        path = self.video.path
        if path is not None and not path.is_file():
            return set()
        try:
            return self.video.matched_drama_ids()
        except dbio.DbError:
            return set()

    def ed2k_link_titles(self):
        """ED2K 链接库里出现过的剧名，喂给数据集打 has_ed2k。

        降级同 video_drama_ids：sqlite 下链接库文件还没生成就直接返回空列表
        （不能为了一次判断凭空造一个空库）；库坏了也只是「没有任何剧有资源」。
        """
        path = self.ed2k.path
        if path is not None and not path.is_file():
            return []
        try:
            return self.ed2k.titles()
        except dbio.DbError:
            return []

    def get(self):
        """每次请求都调一次：数据源变了就悄悄重载。"""
        if self._sig != self.signature():
            self.load(force=True)
        return self.data

    def describe_db(self):
        """给启动日志/状态用：当前数据从哪来。

        跟随 output 目录时不再重复打一遍数据目录（两者本来就是同一个地方）。
        """
        if self.settings.sqlite_path is None:
            return '%s：%s' % (self.settings.describe(), self.output_dir)
        return '%s（数据：%s）' % (self.settings.describe(), self.output_dir)

    # ---------- 写 ----------

    def save_tag_edit(self, drama_id, tags):
        """写入/撤销一条标签编辑，然后强制重载数据集。

        tags=None 表示撤销编辑（恢复抓取到的原始标签）。
        """
        with self._edit_lock:
            edits = sources.read_tag_edits(self.output_dir)
            if tags is None:
                edits.pop(drama_id, None)
            else:
                edits[drama_id] = tags
            sources.write_tag_edits(self.output_dir, edits)
        self.load(force=True)

    def save_favorite(self, drama_id, on=True):
        """收藏 / 取消收藏，然后强制重载数据集（列表星标、筛选、统计都靠它）。

        收藏是幂等的：已经收藏过的再收藏一次，保留原来的收藏时间（「什么时候
        开始追的」不该被一次误点改掉）。返回 (是否收藏, 收藏时间)。
        """
        with self._edit_lock:
            favorites = sources.read_favorites(self.output_dir)
            if on:
                at = favorites.get(drama_id) or _now()
                favorites[drama_id] = at
            else:
                at = favorites.pop(drama_id, '')
            sources.write_favorites(self.output_dir, favorites)
        self.load(force=True)
        return bool(on), (at if on else '')
