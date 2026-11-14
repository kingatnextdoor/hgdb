# -*- coding: utf-8 -*-
"""本地视频文件名解析：只做「文件路径 -> 结构化字段」的纯函数，不碰文件系统。

视频库跟 ED2K 库不一样：扫描时不需要解析内容，文件名本身就是全部信息。
这里回答三个问题：

    is_video('sc/某剧-01.mp4')   是不是视频（按扩展名，见 config.VIDEO_EXTS）
    split_rel('sc/某剧-01.mp4')  拆成目录 / 文件名 / 主名 / 扩展名
    normalize_files(raw)         浏览器选文件夹交来的列表 -> 可入库的条目

浏览器拿不到文件的绝对路径（安全限制），只有「相对所选文件夹的路径」，
所以入库的定位信息是：root（所选文件夹，服务器扫描时是绝对路径）+ rel_path
（相对路径）。同名同位置的文件重复扫描不会产生重复记录，靠唯一键保证。
"""
from pathlib import Path, PurePosixPath, PureWindowsPath

from web import config


def is_video(rel_path):
    """按扩展名判断是不是视频文件。目录里那些 .txt / .jpg / .nfo 都会被跳过。"""
    name = str(rel_path or '').replace('\\', '/').rsplit('/', 1)[-1]
    dot = name.rfind('.')
    if dot <= 0:
        return False
    return name[dot:].lower() in config.VIDEO_EXTS


def split_rel(rel_path):
    """把相对路径拆成字段；统一用 `/` 分隔（浏览器和服务器扫描都归一成这个）。"""
    text = str(rel_path or '').strip().replace('\\', '/')
    parts = [p for p in text.split('/') if p not in ('', '.')]
    name = parts[-1] if parts else ''
    dot = name.rfind('.')
    ext = name[dot:].lower() if dot > 0 else ''
    return {
        'name': name,
        'stem': name[:dot] if dot > 0 else name,
        'ext': ext,
        'dirname': '/'.join(parts[:-1]),
    }


def normalize_files(raw, max_files=config.VIDEO_MAX_FILES):
    """清洗浏览器交来的文件列表，返回 (entries, seen, invalid)。

    raw 的每个元素形如 {'path': 'video/sc/x.mp4', 'size': 12345}：
      · path 必须有，size 缺了按 0 存（文件名才是关键字段）
      · 第一段当作所选文件夹名（root），余下是 rel_path；单段就是根下的文件
      · 非视频扩展名计入 skipped，不算错误
      · path 为空 / 不是字符串 / 不是列表计入 invalid

    返回 (entries, skipped, invalid)，entries 每项：
        {'root', 'rel_path', 'name', 'stem', 'ext', 'dirname', 'size'}
    """
    if not isinstance(raw, list):
        return [], 0, 0

    entries, skipped, invalid = [], 0, 0
    for item in raw:
        if not isinstance(item, dict):
            invalid += 1
            continue
        path = item.get('path') or item.get('name') or ''
        if not isinstance(path, str) or not path.strip():
            invalid += 1
            continue
        parts = [p for p in path.replace('\\', '/').split('/') if p not in ('', '.')]
        if not parts:
            invalid += 1
            continue
        if not is_video('/'.join(parts)):
            skipped += 1
            continue
        root = parts[0] if len(parts) > 1 else ''
        rel = '/'.join(parts[1:]) if len(parts) > 1 else parts[0]
        fields = split_rel(rel)
        try:
            size = int(item.get('size') or 0)
        except (TypeError, ValueError):
            size = 0
        entries.append(dict(fields, root=root, rel_path=rel, size=max(0, size)))
        if len(entries) >= max_files:
            break

    return entries, skipped, invalid


def display_root(root):
    """给界面看的根目录名：服务器扫描时显示完整路径，浏览器扫描只有文件夹名。"""
    return str(root or '').replace('\\', '/').rstrip('/') or '(未命名文件夹)'


def rel_of(root, rel_path):
    """拼出「文件夹 + 相对路径」的展示串，唯一键也按这两个字段算。"""
    return PurePosixPath(display_root(root)) / str(rel_path or '')


# 定位结果：只有 ok 能播，其余给前端解释原因
LOCATE_OK = 'ok'
LOCATE_UNLOCATABLE = 'unlocatable'   # root 只有文件夹名（浏览器选目录），服务端找不到
LOCATE_UNSAFE = 'unsafe'             # rel_path 有 .. 或跑出了 root
LOCATE_MISSING = 'missing'           # 路径合法但文件不在了


def looks_absolute(root):
    """这条 root 是不是本机绝对路径。

    两种入库来源的 root 长得不一样（见本文件头部说明）：服务器扫描给
    `O:\\video` / `/data/video`，浏览器选文件夹只给文件夹名 `video`。
    后者无论服务端怎么拼都到不了真实文件，**只能展示不能播**。
    """
    text = str(root or '').strip()
    if not text:
        return False
    # 两个 PurePath 都试：Windows 盘符 / UNC 归前者，POSIX 绝对路径归后者
    return PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute()


def locate(root, rel_path):
    """把 (root, rel_path) 还原成本机文件路径，返回 (path, status)。

        ('O:\\video\\sc\\x.mp4', 'ok')   找到了，可以播
        (None, 'unlocatable')            root 不是绝对路径，拼不出来
        (None, 'unsafe')                 rel_path 有 ../ 或解析后跑出了 root
        (None, 'missing')                路径合法但文件不在了（挪走 / 外接盘没插）

    这里会做一次 stat（is_file），调用方按需使用即可 —— 详情页一次几十条，
    代价可以忽略；换来的是「点播放前就知道文件还在不在」。
    """
    root_text = str(root or '').strip()
    rel = str(rel_path or '').strip().replace('\\', '/')
    if not rel or not looks_absolute(root_text):
        return None, LOCATE_UNLOCATABLE
    if rel.startswith('/') or any(part == '..' for part in rel.split('/')):
        return None, LOCATE_UNSAFE

    base = Path(root_text)
    try:
        target = (base / rel).resolve()
        target.relative_to(base.resolve())      # 解析软链之后仍在 root 之下
    except (ValueError, OSError):
        return None, LOCATE_UNSAFE
    if not target.is_file():
        return None, LOCATE_MISSING
    return target, LOCATE_OK
