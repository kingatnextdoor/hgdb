# -*- coding: utf-8 -*-
"""ED2K 链接解析：纯函数，不碰 sqlite、不碰 HTTP。

一行合法的文件链接形如::

    ed2k://|file|www.98T.la@某剧-01.mp4|90015171|BCD1...10D|/

本模块只做三件事：

1. 从一整段文本里挑出 ed2k 文件链接（标题行、空行、注释都不算错）
2. 从文件名里拆出「剧名 + 集数」，让管理表能按剧聚合
3. 猜编码（UTF-8 / GBK）—— 人工整理的 TXT 两种都常见

解析失败的**链接行**（含 `ed2k://` 但格式不对）单独计数，页面上提示用户；
不含 `ed2k://` 的行算「忽略」而不是「失败」，否则每个标题行都会被报成错误。

单独成模块的原因：这段逻辑最需要单测（见 tests/test_web_ed2k.py），
而它跟存储、HTTP 都没有关系，放在一起会让测试被迫造数据库。
"""
import re

from web import config

# ed2k://|file|名字|字节数|32位HASH|/   —— 末尾可能还有 h=AICH 段，也可能缺 `/`
FILE_LINK = re.compile(
    r'ed2k://\|file\|(?P<name>[^|\r\n]+)\|(?P<size>\d+)\|(?P<hash>[0-9A-Fa-f]{32})\|'
    r'(?:h=[^|\r\n]*\|)?/?')

# 站名水印前缀：www.98T.la@xxx.mp4 —— 只认 ASCII 站点名，避免把中文里的 @ 当分隔
SITE_PREFIX = re.compile(r'^\s*[A-Za-z0-9][A-Za-z0-9._\- ]{1,39}@')

VIDEO_EXT = re.compile(
    r'\.(?:mp4|mkv|avi|wmv|rmvb|rm|flv|mov|m4v|m2ts|ts|mpg|mpeg|webm|iso|zip|rar|7z)$',
    re.I)

_RANGE = r'\d{1,4}(?:\s*[-~–]\s*\d{1,4})?'
_TAIL_CLEAN = re.compile(r'[\s\-_.·]+$')

# 「某剧第01集」：中文集数标记前不一定有分隔符
CN_EP = re.compile(r'^(?P<title>.*?)(?P<label>第\s*' + _RANGE + r'\s*[集话話])$')

# 「某剧-01」「某剧-01-10 合集」「某剧.EP03」
TAIL_EP = re.compile(
    r'^(?P<title>.*?)[\s\-_.·]+(?:第\s*)?(?:ep|e)?\s*(?P<label>' + _RANGE + r')'
    r'\s*(?:集|话|話|期)?\s*(?:合集|全集|完整版)?$',
    re.I)

# 「某剧-01-素素篇」：集数不在结尾，但后面还跟着分集标题
MID_EP = re.compile(
    r'^(?P<title>.*?)[\s\-_.·]+(?:第\s*)?(?P<label>\d{1,4})'
    r'(?=[\s\-_.·]|集|话|話)')

# ---- 剧名归一化（详情页拿剧名去链接库里搜，两边名字几乎不会一模一样）----
# 书名号：ED2K 文件名里不会出现，留着一条都搜不到
_TITLE_BOOK = re.compile(r'[《》「」]')
# 括号备注：《某剧》(2026)、某剧【完整版】
_TITLE_BRACKETS = re.compile(r'[（(【\[][^）)】\]]*[）)】\]]')
# 季数后缀：第二季、第2部、Season 3
_TITLE_SEASON = re.compile(
    r'(?:第\s*[0-9一二三四五六七八九十百]+\s*[季部]|season\s*\d+)\s*$', re.I)
# 续集号：爱情公寓5、家有儿女 2
_TITLE_TAIL_NUM = re.compile(r'第?\s*\d{1,3}\s*[季部]?$')
# 营销词后缀：某剧短剧、某剧全集
_TITLE_SUFFIX = re.compile(r'(?:短剧|全集|合集|完结|完整版|未删减|高清版?|已完结)+$')
# 片段分隔符：空格、间隔号、中英文标点
_TITLE_SPLIT = re.compile(r'[\s·・:：,，、|/\\_\-—–~～!！?？。.]+')


def decode_bytes(data):
    """把上传的字节解成文本，返回 (文本, 编码名)。

    GB18030 放在最后：它几乎能解出任何字节序列，先试它会把 UTF-8 也「解成功」，
    结果整篇乱码而不报错，所以必须 UTF-8 优先。
    """
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        return text, ('utf-8' if encoding == 'utf-8-sig' else encoding)
    return data.decode('utf-8', errors='replace'), 'utf-8/replace'


def split_name(name):
    """从原始文件名拆出 (剧名, 起始集号, 集数标记)。

    >>> split_name('www.98T.la@50元买断人妻-011.mp4')
    ('50元买断人妻', 11, '011')
    >>> split_name('某剧-01-10 合集.mp4')
    ('某剧', 1, '01-10')
    """
    stem = VIDEO_EXT.sub('', SITE_PREFIX.sub('', name.strip())).strip()

    for pattern in (CN_EP, TAIL_EP, MID_EP):
        found = pattern.match(stem)
        if not found:
            continue
        title = _TAIL_CLEAN.sub('', found.group('title').strip())
        label = re.sub(r'\s+', '', found.group('label'))
        if not title:                     # 纯数字文件名，不拆
            break
        digit = re.search(r'\d{1,4}', label)
        episode = int(digit.group()) if digit else None
        return title, episode, label

    return _TAIL_CLEAN.sub('', stem) or name.strip(), None, ''


def _strip_decorations(text):
    """去掉季数 / 续集号 / 营销后缀，反复几轮直到稳定（「某剧2 第二季」要拆两次）。"""
    for _ in range(3):
        before = text
        text = _TITLE_SEASON.sub('', text)
        text = _TITLE_TAIL_NUM.sub('', text)
        text = _TITLE_SUFFIX.sub('', text)
        text = text.strip(' \t-_.·')
        if text == before:
            break
    return text


def title_keywords(title):
    """把剧名拆成「从严格到宽松」的几档搜索关键词，返回 [(关键词, 档位说明)]。

    详情页要拿剧名去 ED2K 库里搜，但两边几乎不会完全一致：库里是「某剧」，
    剧集却叫「某剧 第二季」；或者剧名带《》和（2026）这种备注。
    逐档降级、命中即停，比一上来就做模糊匹配更容易解释「为什么搜出这些」。
    """
    # 书名号一律去掉：ED2K 文件名里不会带《》，留着只会一条都搜不到
    raw = _TITLE_BOOK.sub('', title or '').strip()
    if not raw:
        return []

    found = []

    def push(value, label):
        value = _TAIL_CLEAN.sub('', (value or '').strip())
        if len(value) >= 2 and all(value != k for k, _ in found):
            found.append((value, label))

    push(raw, '完整剧名')

    simplified = _strip_decorations(_TITLE_BRACKETS.sub('', raw))
    push(simplified, '去掉季数/后缀')

    # 最后兜底：切出来的最长一两个片段（「某剧 独家番外」-> 「独家番外」不理想时还有「某剧」）
    parts = [p for p in _TITLE_SPLIT.split(simplified or raw) if len(p) >= 2]
    for part in sorted(parts, key=len, reverse=True)[:2]:
        push(part, '剧名片段')
    return found


def normalize_title(text):
    """把剧名归一化成「比对用的键」，判断链接的剧名在不在短剧表里。

    两边（链接拆出的剧名 / drama 表里的剧名）都用本函数处理之后再比相等，
    这样链接里是「某剧 第二季」、表里是「某剧（2026）」也能对上。
    只做严格档的归一化（书名号、括号备注、季数 / 续集号 / 营销后缀），
    不做 `title_keywords()` 那种宽松的片段匹配 —— 那样会把太多链接误判成「在」。
    """
    raw = _TITLE_BOOK.sub('', (text or '').strip())
    return _strip_decorations(_TITLE_BRACKETS.sub('', raw))


def parse_link_line(line):
    """解析单行，返回链接记录；不是合法 ed2k 文件链接时返回 None。"""
    found = FILE_LINK.search(line)
    if not found:
        return None
    name = found.group('name').strip()[:config.ED2K_NAME_MAX_LEN]
    digest = found.group('hash').upper()
    size = int(found.group('size'))
    title, episode, label = split_name(name)
    return {
        'hash': digest,
        'name': name,
        'size': size,
        'title': title,
        'episode': episode,
        'episode_label': label,
        # 用解析出的字段重建，顺手丢掉原串里的空白 / 多余尾字符
        'url': 'ed2k://|file|%s|%d|%s|/' % (name, size, digest),
    }


def parse_links(text):
    """解析整段文本。

    返回 dict：
      links     去重后的链接记录（同一文件重复出现只留第一条）
      lines     非空行数
      invalid   含 ed2k:// 但格式不对的行数
      ignored   不含 ed2k:// 的行数（标题、注释、空行之类）
      repeated  文件内重复出现的链接数
    """
    links = []
    seen = set()
    lines = invalid = ignored = repeated = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lines += 1

        if 'ed2k://' not in line:
            ignored += 1
            continue

        record = parse_link_line(line)
        if record is None:
            invalid += 1
            continue
        if record['hash'] in seen:
            repeated += 1
            continue
        seen.add(record['hash'])
        links.append(record)

    return {
        'links': links,
        'lines': lines,
        'invalid': invalid,
        'ignored': ignored,
        'repeated': repeated,
    }
