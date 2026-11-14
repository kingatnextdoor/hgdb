# -*- coding: utf-8 -*-
"""查询层：筛选、排序、分页。纯函数，不碰 HTTP、不碰文件。

前端工具栏的每一项都对应这里的一个参数；加筛选条件只需要改这一个文件
（外加前端加个控件），docs/web.md 里有步骤。
"""
from web import config

# 排序键。值是取值函数，允许 lambda 之外的普通函数，方便单测。
SORTS = {
    'update': lambda i: i.get('update_ts') or 0,
    'score': lambda i: i.get('score') or 0,
    'episode': lambda i: i.get('episode_total') or 0,
    'views': lambda i: i.get('views') or 0,
    'title': lambda i: i.get('title') or '',
    'added': lambda i: i.get('first_seen') or '',
    'changed': lambda i: i.get('last_changed') or '',
    'faved': lambda i: i.get('faved_at') or '',
}
DEFAULT_SORT = 'update'

# only=... 支持的取值 -> 判定函数（可多选，逗号分隔，多个取交集）
ONLY_FILTERS = {
    'new': lambda i: bool(i.get('is_new')),
    'original': lambda i: bool(i.get('is_original')),
    'cover': lambda i: bool(i.get('cover')),
    'detail': lambda i: bool(i['detail'].get('episodes')) or bool(i.get('author')),
    'fav': lambda i: bool(i.get('favorite')),
    # 本地视频库里有这部剧匹配到的文件（dataset 打标，来源是视频库而不是 jsonl）
    'video': lambda i: bool(i.get('has_video')),
    # ED2K 链接库里能搜到这部剧的资源（dataset 打标，口径与详情页 search_by_title 一致）
    'ed2k': lambda i: bool(i.get('has_ed2k')),
}

SEARCH_FIELDS = ('title', 'id', 'author', 'desc')


def one(params, key, default=''):
    """从 parse_qs 的结果里取第一个值（parse_qs 的值永远是 list）。"""
    values = params.get(key) or []
    return values[0] if values else default


def _search_text(item):
    """搜索用的拼接文本：剧名 + ID + 标签 + 作者 + 简介。"""
    parts = [item.get(f) or '' for f in SEARCH_FIELDS]
    parts.insert(2, ' '.join(item.get('tags') or []))
    return ' '.join(parts).lower()


def filter_sort(items, params):
    """按查询参数过滤 + 排序，返回结果列表。

    参数语义（与前端工具栏一一对应）：
      q          关键词，匹配剧名/ID/标签/作者/简介
      tag        标签，逗号分隔，多个是「同时满足」
      category   分类名，精确匹配
      only       逗号分隔多选：new / original / cover / detail / fav（收藏）/ video（本地视频）
                 / ed2k（链接库有资源），多个是「同时满足」（与 tag 一致，点得越多范围越窄）
      min_score  最低评分
      sort       见 SORTS，未知值退回 update
      order      asc / desc（默认 desc）
    """
    q = one(params, 'q').strip().lower()
    tags = [t.strip() for t in one(params, 'tag').split(',') if t.strip()]
    category = one(params, 'category').strip()
    onlys = [v.strip() for v in one(params, 'only').split(',') if v.strip()]
    min_score = one(params, 'min_score').strip()
    sort = one(params, 'sort', DEFAULT_SORT).strip()
    order = one(params, 'order', 'desc').strip()


    if category:
        items = [i for i in items if i.get('category') == category]

    if tags:
        wanted = set(tags)
        items = [i for i in items if wanted.issubset(set(i.get('tags') or []))]

    if min_score:
        try:
            floor = float(min_score)
        except ValueError:
            floor = 0.0
        items = [i for i in items if (i.get('score') or 0) >= floor]

    # 多选是交集：每个开关都过一遍，未知取值直接忽略（老链接/手改 URL 不会 500）
    for key in onlys:
        predicate = ONLY_FILTERS.get(key)
        if predicate is not None:
            items = [i for i in items if predicate(i)]

    if q:
        items = [i for i in items if q in _search_text(i)]

    key = SORTS.get(sort, SORTS[DEFAULT_SORT])
    try:
        items = sorted(items, key=key, reverse=(order != 'asc'))
    except TypeError:
        # 同一字段混了 str 和 None 之类的情况，保持原顺序，不要让整个接口 500
        pass
    return items


def parse_paging(params, default_size=None, max_size=None):
    """解析 page/size，非法值退回默认；size 上限 max_size。

    默认值给 None 时用全局分页配置；ED2K 链接库要另设一套（行更密，默认 50/页）。
    """
    default_size = config.DEFAULT_PAGE_SIZE if default_size is None else default_size
    max_size = config.MAX_PAGE_SIZE if max_size is None else max_size
    try:
        page = max(1, int(one(params, 'page', '1')))
        size = min(max_size, max(1, int(one(params, 'size', str(default_size)))))
    except ValueError:
        page, size = 1, default_size
    return page, size


def pages_of(total, size):
    """总页数：空结果也算 1 页（前端分页器不用特判 0）。"""
    return max(1, (total + size - 1) // size)


def paginate(items, page, size):
    """切页并返回分页元信息（total/page/size/pages/chunk）。"""
    total = len(items)
    start = (page - 1) * size
    return {
        'total': total,
        'page': page,
        'size': size,
        'pages': pages_of(total, size),
        'chunk': items[start:start + size],
    }
