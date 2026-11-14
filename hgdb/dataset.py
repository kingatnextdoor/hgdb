# -*- coding: utf-8 -*-
"""把 output/ 下的抓取结果合并成一份前端直接可用的数据集。

为什么要合并
------------
采集工具每次跑都会生成一个带时间戳的新文件，而且增量模式（mode=incr）只导出
「有变化」的条目 —— 所以任何单个 jsonl 都不是全量：
  · 只看最新文件 -> 增量跑完只剩几条
  · 只看某个文件 -> 缺少其它批次抓到的剧

流程（顺序不能换）::

    jsonl 逐条合并（新覆盖旧、空值不覆盖）
      -> 归一化（jsonl 有数据就展示，不读 drama 状态）
      -> 应用人工标签编辑（优先级最高）
      -> 应用人工收藏（favorite / faved_at）
      -> 标记 has_video / has_ed2k（集合由调用方从视频库 / 链接库取）
      -> 统计 + facets

数据只来自 jsonl 与两个人工文件，不读任何数据库 —— 采集侧也不再有状态库。

对外只暴露 load()，返回 dict：
  {
    'items':  [ {...}, ... ],   # 已归一化，可直接序列化给前端
    'by_id':  { drama_id: {...} },
    'stats':  { ... },
    'facets': { 'categories': [...], 'tags': [...] },
    'mtime':  float,            # 用于判断要不要重新加载
  }
"""
from collections import Counter
from pathlib import Path

from hgdb import config, ed2k, model, sources

# 兼容旧的 import 路径：以前外部脚本直接 `from dataset import LIST_FIELDS / list_payload`
from hgdb.model import LIST_FIELDS, list_payload  # noqa: F401

# 兼容旧代码：以前外部脚本从 dataset 取这两个常量
TAG_EDITS_FILE = config.TAG_EDITS_FILE

__all__ = ['load', 'LIST_FIELDS', 'list_payload', 'TAG_EDITS_FILE']


def _apply_tag_edits(out_dir, normalized):
    """应用人工编辑过的标签。空列表也是合法编辑（用户删光了标签），所以命中就整覆盖。"""
    for did, edited in sources.read_tag_edits(out_dir).items():
        item = normalized.get(did)
        if item is not None:
            item['tags'] = list(edited)
            item['tags_edited'] = True


def _apply_favorites(out_dir, normalized):
    """把人工收藏合并进数据集：`favorite` / `faved_at`。

    只认数据集里有的剧：某部剧的 jsonl 记录被清掉之后，favorites.json 里的
    条目仍然保留（用户没取消过），但页面上本来就没有它 —— 统计和筛选按现有
    的剧算，不能出现「收藏 12 部、筛出来只有 9 部」。
    """
    for did, at in sources.read_favorites(out_dir).items():
        item = normalized.get(did)
        if item is not None:
            item['favorite'] = True
            item['faved_at'] = at or None


def _apply_videos(normalized, video_ids):
    """把「本地视频库里匹配到了」的标记合进数据集：`has_video`。

    video_ids 由调用方（store）从视频库取——dataset 只读 output/ 下的文件，
    不在这里开第二个库，测试传一个集合就能验。
    只标现有剧：视频库里的 drama_id 可能指向已被清掉的剧，标了也没人看。
    """
    for did in video_ids or ():
        item = normalized.get(str(did))
        if item is not None:
            item['has_video'] = True


def _apply_ed2k(normalized, link_titles):
    """把「ED2K 链接库里能搜到这部剧的资源」标成 `has_ed2k`。

    匹配口径与详情页的 `ed2kdb.search_by_title()` **完全一致**：剧名拆成逐档
    关键词（完整剧名 -> 去季数 -> 剧名片段），任一档是某条链接剧名的子串就算命中。
    这样「详情页看得到 ED2K 资源」和「列表能筛出来」永远是同一批剧。

    link_titles 由调用方从链接库取（`ed2kdb.titles()`），dataset 不为此开库。
    实现上把链接剧名拼成一个大字符串做子串判断：1461 部剧 × 最多 4 档，
    实测 12ms；换成逐部查库是 1461 × 1~3 条 SQL，没法用。
    """
    if not link_titles:
        return
    blob = '\n'.join(t.lower() for t in link_titles if t)
    if not blob:
        return
    for item in normalized.values():
        for keyword, _ in ed2k.title_keywords(item['title']):
            if keyword.lower() in blob:
                item['has_ed2k'] = True
                break


def _build_stats(values, files_count):
    return {
        'total': len(values),        'with_cover': sum(1 for i in values if i['cover']),
        'new': sum(1 for i in values if i['is_new']),
        'original': sum(1 for i in values if i['is_original']),
        'favorite': sum(1 for i in values if i['favorite']),
        'video': sum(1 for i in values if i['has_video']),
        'ed2k': sum(1 for i in values if i['has_ed2k']),
        'with_detail': sum(1 for i in values
                           if i['detail'].get('episodes') or i['author']),
        'latest_update': max((i['update_ts'] for i in values), default=0),
        'crawled_at': max((i['crawled_at'] or '' for i in values), default=''),
        'files': files_count,
    }


def _schema_version(merged):
    """数据里出现过的最高契约版本。

    没有这个字段的老导出（拆仓之前的）一律算 1 —— 那时候的字段集就是 1 的内容，
    不把「老数据」误报成「版本过低」。
    """
    versions = []
    for rec in merged.values():
        try:
            versions.append(int(rec.get('schema_version') or 1))
        except (TypeError, ValueError):
            versions.append(1)
    return max(versions, default=0)


def _build_facets(values):
    tag_counter = Counter()
    cat_counter = Counter()
    for item in values:
        tag_counter.update(item['tags'])
        if item['category']:
            cat_counter[item['category']] += 1
    return {
        'categories': [{'name': k, 'count': v} for k, v in cat_counter.most_common()],
        'tags': [{'name': k, 'count': v} for k, v in tag_counter.most_common()],
    }


def load(output_dir, with_detail=True, video_ids=None, ed2k_titles=None):
    """读一次 output/，返回完整数据集。

    with_detail 目前只是占位（详情字段一律带上，列表接口再裁），保留是为了
    以后真需要瘦身时不用改调用方签名。

    video_ids 是「本地视频库里匹配到文件的剧 id」集合，由 store 从视频库取
    （见 videodb.matched_drama_ids）；不传就全部按「没有本地视频」处理——
    dataset 本身不依赖视频库。

    ed2k_titles 是「ED2K 链接库里出现过的剧名」列表（见 ed2kdb.titles()），
    用来给列表页打 `has_ed2k`；不传就全部按「链接库里没有」处理。

    drama 状态**不参与**数据集：jsonl 有记录就展示，没有在架/下架的概念。
    """
    out_dir = Path(output_dir)
    covers_dir = out_dir / config.COVERS_DIR

    records, mtimes = sources.read_exports(out_dir)

    merged = {}
    for rec in records:
        did = str(rec['drama_id'])
        merged[did] = model.merge_record(merged.get(did), rec)

    normalized = {did: model.normalize(rec, covers_dir)
                  for did, rec in merged.items()}

    _apply_tag_edits(out_dir, normalized)
    _apply_favorites(out_dir, normalized)
    _apply_videos(normalized, video_ids)
    _apply_ed2k(normalized, ed2k_titles)
    values = list(normalized.values())

    stats = _build_stats(values, len(mtimes))
    # 契约版本放在 stats 里：/api/stats 直接把数据版本暴露给前端和命令行，
    # 排查「页面少了一半字段」时第一眼看的就是它。
    stats['schema_version'] = _schema_version(merged)
    stats['schema_supported'] = config.SCHEMA_VERSION

    return {
        'items': values,
        'by_id': normalized,
        'stats': stats,
        'facets': _build_facets(values),
        'mtime': max(mtimes) if mtimes else 0.0,
        'file_count': len(mtimes),
    }
