# -*- coding: utf-8 -*-
"""数据模型：一条导出记录 -> 前端直接可用的扁平结构。

两个容易踩的点，改之前先读：

1. **空值不覆盖非空值**：详情页字段只有开了 follow_detail 的那批导出才有，
   合并时不能让后面没开的那批把它们冲成 null/0。
2. **0 不等于没有**：部分字段的 0 是「没抓到」的意思（见 ZERO_IS_EMPTY），
   合并时按空值处理，否则评分会被后来的 0 分覆盖。
"""
from pathlib import Path
from urllib.parse import quote

# 这些字段 0 表示「没抓到」而不是真的 0，合并时当空值处理
ZERO_IS_EMPTY = frozenset((
    'update_ts', 'score', 'episode_total', 'views', 'likes', 'comments',
    'duration', 'category_id', 'author_id',
))

# 列表接口返回的字段（顺序即 JSON 里的顺序，常用的放前面）
LIST_FIELDS = (
    'id', 'title', 'url', 'cover', 'has_cover', 'score', 'score_text',
    'episode_total', 'episode_text', 'update_ts', 'update_time', 'update_text',
    'category', 'tags', 'panels', 'is_new', 'is_original', 'badges',
    'author', 'views', 'likes', 'comments', 'duration',
    'first_seen', 'last_changed', 'desc', 'tags_edited', 'favorite',
    'has_video', 'has_ed2k',
)


def is_blank(value):
    """判断一个字段值算不算「空」——空的不会覆盖已有值。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def merge_record(old, new):
    """合并两条同 id 的记录：new 优先，但空值不覆盖 old 的非空值。"""
    if old is None:
        return dict(new)
    out = dict(old)
    for key, value in new.items():
        if is_blank(value):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool) \
                and value == 0 and key in ZERO_IS_EMPTY:
            continue
        # detail 是嵌套 dict：新的为空就留旧的，都不空时逐键合并
        if key == 'detail' and isinstance(value, dict) and isinstance(out.get('detail'), dict):
            merged = dict(out['detail'])
            for k2, v2 in value.items():
                if not is_blank(v2):
                    merged[k2] = v2
            out['detail'] = merged
            continue
        out[key] = value
    return out


def cover_url(cover_local, covers_dir):
    """cover_local 是绝对路径（Windows 上带盘符），转成 /covers/<文件名>。

    文件名含中文和空格，必须 quote 之后才能放进 URL。
    文件真不存在就返回 None —— 前端收到 null 会画占位图，不会出现一堆裂图。
    """
    if not cover_local:
        return None
    path = Path(str(cover_local))
    if not path.is_absolute():
        path = Path(covers_dir) / path
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    return '/covers/' + quote(path.name)


def normalize(rec, covers_dir):
    """把一条原始导出记录压成前端要的扁平结构。"""
    detail = rec.get('detail') or {}
    tags = [t for t in (rec.get('tags') or []) if t]
    panels = [p for p in (rec.get('panels') or []) if p]

    return {
        'id': str(rec.get('drama_id')),
        'title': (rec.get('title') or '').strip() or '(无标题)',
        'url': rec.get('url'),
        'cover': cover_url(rec.get('cover_local'), covers_dir),
        'has_cover': bool(rec.get('cover_local')),
        'score': rec.get('score'),
        'score_text': rec.get('score_text'),
        'episode_total': rec.get('episode_total'),
        'episode_text': rec.get('episode_text'),
        'update_ts': rec.get('update_ts') or 0,
        'update_time': rec.get('update_time'),
        'update_text': rec.get('update_text'),
        'category': rec.get('category_name'),
        'tags': tags,
        'panels': panels,
        'is_new': bool(rec.get('is_new')),
        'is_original': bool(rec.get('is_original')),
        'badges': [b for b in (rec.get('badges') or []) if b],
        'author': rec.get('author'),
        'author_url': rec.get('author_url'),
        'views': rec.get('views'),
        'likes': rec.get('likes'),
        'comments': rec.get('comments'),
        'duration': rec.get('duration'),
        'publish_time': rec.get('publish_time'),
        'content_rating': rec.get('content_rating'),
        'first_seen': rec.get('first_seen'),
        'last_changed': rec.get('last_changed'),
        'crawled_at': rec.get('crawled_at'),
        'desc': (rec.get('desc') or '').strip(),
        'tags_edited': False,
        # 人工收藏：dataset 里由 favorites.json 覆盖（见 _apply_favorites）
        'favorite': False,
        'faved_at': None,
        # 本地视频库里有这部剧的已匹配文件（见 dataset._apply_videos）
        'has_video': False,
        # ED2K 链接库里能搜到这部剧的资源（见 dataset._apply_ed2k，口径同详情页）
        'has_ed2k': False,
        # 详情页才有；列表接口不返回，详情接口才带
        'detail': {
            'episode_no': detail.get('episode_no'),
            'episodes': detail.get('episodes') or [],
            'facts': detail.get('facts') or [],
            'topics': detail.get('topics') or [],
            'sources': detail.get('sources') or [],
            'embed_url': detail.get('embed_url'),
            'related_ids': detail.get('related_ids') or [],
        },
    }


def list_payload(item):
    """列表接口的精简结构：不带 detail，减少传输量。"""
    return {k: item.get(k) for k in LIST_FIELDS}


__all__ = [
    'ZERO_IS_EMPTY', 'LIST_FIELDS', 'is_blank', 'merge_record',
    'cover_url', 'normalize', 'list_payload',
]
