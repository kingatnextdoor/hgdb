# -*- coding: utf-8 -*-
"""HTTP 之上的接口层：路由表 + 处理函数，**不 import http.server**。

每个处理函数签名统一为 `handler(store, match, params, body) -> (状态码, 可 JSON 化对象)`，
这样：
  · 加接口 = 写一个函数 + 往 ROUTES 加一行
  · 测接口 = 直接 `dispatch(...)`，不用起服务器（见 tests/test_api.py）
  · 以后想换 Flask/FastAPI，把这层的函数原样包一层即可
"""
import re

from hgdb import config, dbio, ed2k, giscus, model, queries, scrape, video, videodb

NOT_FOUND = {'error': 'not found'}
UNKNOWN_ENDPOINT = {'error': 'unknown endpoint'}
BAD_TAGS_BODY = {'error': 'body 应为 {"tags": ["标签", ...]}'}
BAD_UPLOAD_BODY = {'error': '请用 multipart/form-data 上传，文件字段名 file'}
EMPTY_FILE = {'error': '文件是空的'}
BAD_SCAN_BODY = {'error': 'body 应为 {"path": "目录"} 或 {"folder": "...", "files": [...]}'}
BAD_SCRAPE_BODY = {'error': 'body 应为 {"root": "目录（video_file.root 的值）"}'}
BODY_NOT_DICT = {'error': 'body 应为 JSON 对象'}


# ------------------------------ 处理函数 ------------------------------

def stats(store, match, params, body):
    data = store.get()
    # db 只是展示用：排查「我到底连的哪个库」时不用翻日志
    return 200, {'stats': data['stats'], 'db': store.settings.describe()}


def facets(store, match, params, body):
    return 200, store.get()['facets']


def dramas(store, match, params, body):
    """列表：筛选 + 排序 + 分页，只返回列表字段（不带 detail，省流量）。"""
    data = store.get()
    found = queries.filter_sort(data['items'], params)
    page, size = queries.parse_paging(params)
    page_info = queries.paginate(found, page, size)
    return 200, {
        'total': page_info['total'],
        'page': page_info['page'],
        'size': page_info['size'],
        'pages': page_info['pages'],
        'items': [model.list_payload(i) for i in page_info['chunk']],
    }


def drama(store, match, params, body):
    """详情：完整字段 + 相关推荐（最多 12 部）。"""
    data = store.get()
    item = data['by_id'].get(match.group('id'))
    if item is None:
        return 404, NOT_FOUND
    payload = dict(item)
    related = []
    for rid in (item['detail'].get('related_ids') or [])[:12]:
        other = data['by_id'].get(str(rid))
        if other is not None:
            related.append(model.list_payload(other))
    payload['related'] = related
    return 200, payload


def reload(store, match, params, body):
    """强制重读 output/（页面右上角「刷新」按钮用）。"""
    store.load(force=True)
    return 200, {'ok': True, 'total': store.data['stats']['total']}


def clean_tags(raw):
    """清洗前端交来的标签列表：去非字符串、去空白、去重、限长限量。"""
    tags = []
    for tag in raw:
        if not isinstance(tag, str):
            continue
        tag = tag.strip()[:config.TAG_MAX_LEN]
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:config.TAG_MAX_COUNT]


def tags_save(store, match, params, body):
    """覆盖式保存某部剧的标签（增删改名都走这一个接口）。"""
    drama_id = match.group('id')
    if not isinstance(body, dict) or not isinstance(body.get('tags'), list):
        return 400, BAD_TAGS_BODY
    if drama_id not in store.data['by_id']:
        return 404, NOT_FOUND
    store.save_tag_edit(drama_id, clean_tags(body['tags']))
    item = store.data['by_id'][drama_id]
    return 200, {'ok': True, 'tags': item['tags'], 'item': model.list_payload(item)}


def tags_reset(store, match, params, body):
    """撤销人工编辑，恢复抓取到的原始标签。"""
    drama_id = match.group('id')
    if drama_id not in store.data['by_id']:
        return 404, NOT_FOUND
    store.save_tag_edit(drama_id, None)
    return 200, {'ok': True, 'tags': store.data['by_id'][drama_id]['tags']}


# ------------------------------ 收藏 ------------------------------

def _favorite_payload(store, drama_id, on, at):
    """收藏接口的统一返回：按钮状态 + 收藏总数 + 这部剧的列表字段。

    带上 total 是为了让顶栏「收藏 N」不用再拉一次 /api/stats；
    带上 item 是为了调用方（尤其是以后卡片上直接收藏）就地更新。
    """
    data = store.get()
    return {
        'ok': True,
        'favorite': bool(on),
        'faved_at': at or None,
        'total': data['stats']['favorite'],
        'item': model.list_payload(data['by_id'][drama_id]),
    }


def favorite_save(store, match, params, body):
    """收藏一部剧。幂等：已收藏时保留原来的收藏时间。"""
    drama_id = match.group('id')
    if drama_id not in store.get()['by_id']:
        return 404, NOT_FOUND
    on, at = store.save_favorite(drama_id, True)
    return 200, _favorite_payload(store, drama_id, on, at)


def favorite_delete(store, match, params, body):
    """取消收藏。本来就没收藏也算成功（前端连点两下不会报错）。"""
    drama_id = match.group('id')
    if drama_id not in store.get()['by_id']:
        return 404, NOT_FOUND
    on, at = store.save_favorite(drama_id, False)
    return 200, _favorite_payload(store, drama_id, on, at)


# ------------------------------ ED2K 链接库 ------------------------------

def drama_ed2k(store, match, params, body):
    """详情页用：拿这部剧的剧名去 ED2K 链接库里找资源。

    关键词在 ed2k.title_keywords() 里逐档降级（完整剧名 -> 去季数 -> 剧名片段），
    命中即停。返回空列表是正常结果（库里就是没有），前端据此提示上传。
    链接库打不开时不让整个详情页 500：给 available=False + 原因。
    """
    item = store.get()['by_id'].get(match.group('id'))
    if item is None:
        return 404, NOT_FOUND
    title = item.get('title') or ''
    try:
        payload = store.ed2k.search_by_title(title)
    except dbio.DbError as exc:
        return 200, {'drama_id': item['id'], 'title': title, 'available': False,
                     'error': str(exc), 'keyword': '', 'matched_by': '',
                     'total': 0, 'bytes': 0, 'items': []}
    payload['drama_id'] = item['id']
    payload['available'] = True
    return 200, payload


def ed2k_upload(store, match, params, body):
    """上传 TXT 并导入：解析 -> 去重入库，返回本次导入统计。

    支持两种提交方式：
      · multipart/form-data，文件字段 `file`（页面用）
      · text/plain 直接贴正文（curl / 脚本用，文件名走 X-Filename 头）
    """
    file_part = body.get('file') if isinstance(body, dict) else None
    if isinstance(file_part, dict):
        filename = str(file_part.get('filename') or '')
        raw = file_part.get('data') or b''
    elif isinstance(body, dict) and body.get('text') is not None:
        filename = str(body.get('filename') or '')
        raw = body['text']
    else:
        return 400, BAD_UPLOAD_BODY

    if isinstance(raw, str):
        text, encoding, size_bytes = raw, '', len(raw.encode('utf-8'))
    else:
        text, encoding = ed2k.decode_bytes(raw)
        size_bytes = len(raw)

    if not text.strip():
        return 400, EMPTY_FILE

    try:
        # 剧名来源 = 内存数据集（和页面展示同源），不再依赖状态库的 drama 表
        titles = {it['title'] for it in store.get()['items'] if it.get('title')}
        result = store.ed2k.import_text(text, filename, size_bytes,
                                        drama_titles=titles)
    except ValueError as exc:            # 没有合法链接 / 超过单次条数上限
        return 400, {'error': str(exc)}
    result['encoding'] = encoding
    return 200, result


def ed2k_list(store, match, params, body):
    page, size = queries.parse_paging(params, config.ED2K_PAGE_SIZE,
                                      config.ED2K_MAX_PAGE_SIZE)
    q = queries.one(params, 'q').strip()
    payload = store.ed2k.list_links(q, page, size)
    payload['q'] = q
    payload['stats'] = store.ed2k.stats()
    return 200, payload


def ed2k_stats(store, match, params, body):
    return 200, store.ed2k.stats()


def ed2k_batches(store, match, params, body):
    return 200, {'batches': store.ed2k.batches()}


def ed2k_delete_link(store, match, params, body):
    deleted = store.ed2k.delete_link(match.group('hash'))
    if not deleted:
        return 404, NOT_FOUND
    return 200, {'ok': True, 'deleted': deleted, 'stats': store.ed2k.stats()}


def ed2k_delete_batch(store, match, params, body):
    """撤销一次上传（链接删掉，批次记录保留，历史里能看到做过什么）。"""
    deleted = store.ed2k.delete_batch(int(match.group('batch')))
    return 200, {'ok': True, 'deleted': deleted, 'stats': store.ed2k.stats()}


def ed2k_clear(store, match, params, body):
    deleted = store.ed2k.clear()
    return 200, {'ok': True, 'deleted': deleted, 'stats': store.ed2k.stats()}


def ed2k_export(store, match, params, body):
    """导出成纯文本（前端自己拼 Blob 下载，服务端不必处理 Content-Disposition）。"""
    q = queries.one(params, 'q').strip()
    text = store.ed2k.export_text(q)
    return 200, {
        'count': text.count('\n') + 1 if text else 0,
        'text': text,
    }


# ------------------------------ 本地视频文件库 ------------------------------

def video_scan(store, match, params, body):
    """扫描本地视频文件并入库。两种入口，二选一：

      · {"path": "O:\\video"}            服务器扫描（服务在本机跑时最省事，
                                          递归 os.walk，只拿文件名和大小）
      · {"folder": "video",
         "files": [{"path": "video/sc/x.mp4", "size": 1}, ...]}
                                          浏览器选文件夹后把文件清单交上来
                                          （浏览器拿不到绝对路径，这是安全限制）

    非视频扩展名直接跳过，计入 skipped；重复文件（root+rel_path 相同）幂等。
    """
    if not isinstance(body, dict):
        return 400, BODY_NOT_DICT

    try:
        if isinstance(body.get('path'), str) and body['path'].strip():
            # 服务器扫描：root 用服务器上的绝对路径
            root, entries, skipped = videodb.scan_dir(body['path'].strip())
            result = store.video.import_scan(root, entries, skipped)
            result['scanned'] = 'server'
            return 200, result

        if isinstance(body.get('files'), list):
            folder = body.get('folder')
            if not isinstance(folder, str) or not folder.strip():
                return 400, {'error': '缺少 folder（所选文件夹名）'}
            entries, skipped, invalid = video.normalize_files(body['files'])
            if not entries:
                return 400, {'error': '清单里没有视频文件（支持的扩展名：%s）'
                                      % ', '.join(sorted(config.VIDEO_EXTS))}
            result = store.video.import_scan(folder.strip(), entries, skipped)
            result['scanned'] = 'browser'
            result['invalid'] = invalid
            return 200, result

        return 400, BAD_SCAN_BODY
    except FileNotFoundError as exc:
        return 400, {'error': str(exc)}
    except ValueError as exc:                # 超过单次条数上限
        return 400, {'error': str(exc)}


def video_list(store, match, params, body):
    page, size = queries.parse_paging(params, config.VIDEO_PAGE_SIZE,
                                      config.VIDEO_MAX_PAGE_SIZE)
    q = queries.one(params, 'q').strip()
    payload = store.video.list_files(q, page, size)
    payload['q'] = q
    payload['stats'] = store.video.stats()
    return 200, payload


def video_stats(store, match, params, body):
    return 200, store.video.stats()


def video_batches(store, match, params, body):
    return 200, {'batches': store.video.batches()}


def video_delete_file(store, match, params, body):
    deleted = store.video.delete_file(match.group('fid'))
    if not deleted:
        return 404, NOT_FOUND
    return 200, {'ok': True, 'deleted': deleted, 'stats': store.video.stats()}


def video_delete_batch(store, match, params, body):
    """撤销一次扫描（文件行删掉，批次记录保留，历史里能看到做过什么）。"""
    deleted = store.video.delete_batch(match.group('batch'))
    return 200, {'ok': True, 'deleted': deleted, 'stats': store.video.stats()}


def video_clear(store, match, params, body):
    deleted = store.video.clear()
    return 200, {'ok': True, 'deleted': deleted, 'stats': store.video.stats()}


def drama_videos(store, match, params, body):
    """详情页用：这部剧刮削匹配到的本地视频文件（按集数排）。

    每条额外带：
      · playable  能不能播（文件在服务端找得到）
      · reason    ok / unlocatable / missing / unsafe，前端据此给提示
      · url       播放地址 `/media/<id>`（只认 id，不把路径交给客户端）

    「浏览器选文件夹」入库的 root 只有文件夹名，服务端拼不出绝对路径，
    这类行 playable=False —— 视频库打不开时同样降级成 available=False，
    不让整个详情页 500。
    """
    item = store.get()['by_id'].get(match.group('id'))
    if item is None:
        return 404, NOT_FOUND
    title = item.get('title') or ''
    try:
        rows = store.video.files_of_drama(item['id'])
    except dbio.DbError as exc:
        return 200, {'drama_id': item['id'], 'title': title, 'available': False,
                     'error': str(exc), 'total': 0, 'playable': 0,
                     'bytes': 0, 'items': []}

    items = []
    for row in rows:
        _path, status = video.locate(row['root'], row['rel_path'])
        ok = status == video.LOCATE_OK
        items.append(dict(row, playable=ok, reason=status,
                          url='/media/%d' % row['id'] if ok else ''))

    # 同一个文件可能被两个 root 各扫了一次（浏览器选的文件夹只有文件夹名，
    # 后来改用服务器路径又扫一遍），列表里合并成一条：优先留能播的那条，
    # 顺序沿用先出现的（按集数排好的）。key 用 name+size，同名不同片不会误合。
    unique = {}
    for entry in items:
        key = (entry['name'], entry['size'])
        kept = unique.get(key)
        if kept is None:
            unique[key] = entry
        elif entry['playable'] and not kept['playable']:
            unique[key] = entry
    items = list(unique.values())

    return 200, {'drama_id': item['id'], 'title': title, 'available': True,
                 'total': len(items),
                 'playable': sum(1 for i in items if i['playable']),
                 'bytes': sum(int(i['size'] or 0) for i in items),
                 'items': items}


# ------------------------------ 刮削（视频文件 -> 本地数据集匹配） ------------------------------

def _manual_kept(row):
    """人工确认过的行：匹配到的剧是手选的（set_match 把 score 记成 1.0），
    自动匹配最高才 0.95，所以 score>=1 只可能是人给结论。"""
    return (row.get('match_status') == 'matched'
            and float(row.get('match_score') or 0) >= 1.0)


def _scrape_items(store, root, force=False):
    """对某个 root 的已入库文件跑一遍匹配引擎。

    返回 (items, counts)。items = 文件行 + 引擎决策。
    · ignored：用户明确说过「别碰」，连 force 也不覆盖
    · 人工确认过的 matched（score=1.0）：force=False 时保留，别把人的结论冲掉；
      force=True 表示「按文件名重新猜一遍」，这时才覆盖
    """
    files = store.video.files_of_root(root)
    dramas = store.get()['items']
    matcher = scrape.TitleMatcher(dramas)
    items, counts = [], {'matched': 0, 'review': 0, 'unmatched': 0}
    for f in files:
        if f.get('match_status') == 'ignored':
            items.append(dict(f, decision='kept'))
            counts['unmatched'] += 1
            continue
        if not force and _manual_kept(f):
            items.append(dict(f, decision='kept'))
            counts['matched'] += 1
            continue
        m = matcher.match(f.get('stem') or '')
        if m['status'] == 'matched':
            counts['matched'] += 1
        elif m['status'] == 'pending':
            counts['review'] += 1
        else:
            counts['unmatched'] += 1
        items.append(dict(f, decision='fresh', **{
            'drama_id': m['drama_id'], 'match_score': m['score'],
            'match_status': m['status'], 'match_level': m['level'],
            'match_title': m['title'], 'candidates': m['candidates'],
            'episode': m['episode'], 'episode_label': m['episode_label'],
            'cleaned': m['cleaned'],
        }))
    return items, counts


def _counts_payload(counts):
    return {'matched': counts['matched'], 'review': counts['review'],
            'unmatched': counts['unmatched']}


def scrape_scan(store, match, params, body):
    """刮削预览（dry-run）：只算不写，前端拿去渲染确认表。"""
    if not isinstance(body, dict) or not str(body.get('root') or '').strip():
        return 400, BAD_SCRAPE_BODY
    root = str(body['root']).strip()
    files = store.video.files_of_root(root)
    if not files:
        return 400, {'error': '该目录还没有扫描记录，先在本地视频里扫描入库'}
    items, counts = _scrape_items(store, root, bool(body.get('force')))
    return 200, dict({'ok': True, 'root': root, 'total': len(files),
                      'items': items, 'dry_run': True}, **_counts_payload(counts))


def scrape_apply(store, match, params, body):
    """执行刮削：跑同一套匹配，把结果写进 video_file 并记一条 scrape_batch。

    · matched -> 连剧带集数一起写
    · pending -> 只标状态，等人工在页面上指定
    · unmatched -> 未匹配的复位成 unmatched（清掉上一轮可能残留的 pending）
    ignored 的文件视为「用户说过别碰」，force=True 也不会覆盖。
    """
    if not isinstance(body, dict) or not str(body.get('root') or '').strip():
        return 400, BAD_SCRAPE_BODY
    root = str(body['root']).strip()
    files = store.video.files_of_root(root)
    if not files:
        return 400, {'error': '该目录还没有扫描记录，先在本地视频里扫描入库'}
    items, counts = _scrape_items(store, root, bool(body.get('force')))
    rows = []
    for it in items:
        if it.get('decision') != 'fresh':
            continue
        status = it['match_status']
        rows.append({
            'id': it['id'],
            'drama_id': it['drama_id'] if status == 'matched' else None,
            'episode': it.get('episode'),
            'episode_label': it.get('episode_label') or '',
            'match_status': status,
            'match_score': it.get('match_score') or None,
        })
    result = store.video.apply_matches(root, rows)
    return 200, dict(result, **_counts_payload(counts), total=len(files),
                     items=items)


def scrape_match(store, match, params, body):
    """人工修正一个文件的匹配：确认候选 / 手动选剧 / 忽略 / 复位。"""
    if not isinstance(body, dict):
        return 400, BODY_NOT_DICT
    status = str(body.get('status') or 'matched')
    if status not in videodb.MATCH_STATUSES:
        return 400, {'error': 'status 只认 %s' % ' / '.join(videodb.MATCH_STATUSES)}
    try:
        file_id = int(body.get('file_id'))
    except (TypeError, ValueError):
        return 400, {'error': 'file_id 必须是数字'}

    drama_id = None
    if status == 'matched':
        drama_id = str(body.get('drama_id') or '')
        if not drama_id:
            return 400, {'error': 'status=matched 需要给 drama_id'}
        if drama_id not in store.get()['by_id']:
            return 404, NOT_FOUND

    updated = store.video.set_match(file_id, status, drama_id,
                                    1.0 if status == 'matched' else None)
    if not updated:
        return 404, NOT_FOUND
    return 200, {'ok': True, 'file_id': file_id, 'status': status,
                 'drama_id': drama_id, 'stats': store.video.scrape_stats()}


def scrape_status(store, match, params, body):
    return 200, store.video.scrape_stats()


def scrape_report(store, match, params, body):
    row = store.video.scrape_batch(int(match.group('batch')))
    if row is None:
        return 404, NOT_FOUND
    return 200, {'batch': row}


def site_config(store, match, params, body):
    """前端启动时取一次的站点配置：站名 / 页脚链接 / 评论。

    页面上所有「这是谁的站」的信息都从这里来，HTML 里只有中性占位文字——
    换站名、换页脚链接、开关评论都只改 hgdb/config.py，不用动静态文件。

    没配全 giscus 就返回 `{"giscus": null}`，前端不加载 giscus.app 的任何脚本
    （离线环境也不会多出请求）。
    """
    return 200, {
        'site': {
            'name': config.SITE_NAME,
            'tagline': config.SITE_TAGLINE,
            'links': [{'label': label, 'url': url}
                      for label, url in config.SITE_LINKS],
        },
        'giscus': giscus.settings(),
    }


# ------------------------------ 路由表 ------------------------------

# 顺序无关（都是 ^...$ 全匹配），加接口往这里追加一行即可
ROUTES = (
    ('GET', re.compile(r'^/api/config$'), site_config),
    ('GET', re.compile(r'^/api/stats$'), stats),
    ('GET', re.compile(r'^/api/facets$'), facets),
    ('GET', re.compile(r'^/api/dramas$'), dramas),
    ('GET', re.compile(r'^/api/reload$'), reload),
    ('GET', re.compile(r'^/api/drama/(?P<id>[^/]+)/?$'), drama),
    ('GET', re.compile(r'^/api/drama/(?P<id>[^/]+)/ed2k$'), drama_ed2k),
    ('GET', re.compile(r'^/api/drama/(?P<id>[^/]+)/videos$'), drama_videos),
    ('POST', re.compile(r'^/api/drama/(?P<id>[^/]+)/tags$'), tags_save),
    ('DELETE', re.compile(r'^/api/drama/(?P<id>[^/]+)/tags$'), tags_reset),
    ('POST', re.compile(r'^/api/drama/(?P<id>[^/]+)/favorite$'), favorite_save),
    ('DELETE', re.compile(r'^/api/drama/(?P<id>[^/]+)/favorite$'), favorite_delete),

    ('POST', re.compile(r'^/api/ed2k/upload$'), ed2k_upload),
    ('GET', re.compile(r'^/api/ed2k$'), ed2k_list),
    ('GET', re.compile(r'^/api/ed2k/stats$'), ed2k_stats),
    ('GET', re.compile(r'^/api/ed2k/batches$'), ed2k_batches),
    ('GET', re.compile(r'^/api/ed2k/export$'), ed2k_export),
    ('DELETE', re.compile(r'^/api/ed2k/link/(?P<hash>[0-9A-Fa-f]{32})/?$'), ed2k_delete_link),
    ('DELETE', re.compile(r'^/api/ed2k/batch/(?P<batch>\d+)$'), ed2k_delete_batch),
    ('DELETE', re.compile(r'^/api/ed2k/all$'), ed2k_clear),

    ('POST', re.compile(r'^/api/video/scan$'), video_scan),
    ('GET', re.compile(r'^/api/video$'), video_list),
    ('GET', re.compile(r'^/api/video/stats$'), video_stats),
    ('GET', re.compile(r'^/api/video/batches$'), video_batches),
    ('DELETE', re.compile(r'^/api/video/file/(?P<fid>\d+)$'), video_delete_file),
    ('DELETE', re.compile(r'^/api/video/batch/(?P<batch>\d+)$'), video_delete_batch),
    ('DELETE', re.compile(r'^/api/video/all$'), video_clear),

    ('POST', re.compile(r'^/api/scrape/scan$'), scrape_scan),
    ('POST', re.compile(r'^/api/scrape/apply$'), scrape_apply),
    ('POST', re.compile(r'^/api/scrape/match$'), scrape_match),
    ('GET', re.compile(r'^/api/scrape/status$'), scrape_status),
    ('GET', re.compile(r'^/api/scrape/report/(?P<batch>\d+)$'), scrape_report),
)


def dispatch(store, method, path, params, body=None):
    """把一次请求分派给处理函数，返回 (状态码, 响应体)。

    找不到路由返回 404 + {'error': 'unknown endpoint'}，与重构前一致。
    """
    for route_method, pattern, handler in ROUTES:
        if route_method != method:
            continue
        match = pattern.match(path)
        if match:
            return handler(store, match, params, body)
    return 404, UNKNOWN_ENDPOINT
