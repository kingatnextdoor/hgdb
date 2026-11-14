# -*- coding: utf-8 -*-
"""文件读写层：jsonl 导出、标签编辑与收藏文件。

这里只做「读成 Python 数据 / 把数据写回去」，不含任何业务判断
（合并规则在 model.py，统计在 dataset.py）。这样换存储格式只需要动这一个文件。

采集侧不再有状态库：导出的 jsonl 本身就是唯一的数据来源。
"""
import json
import os
from pathlib import Path

from hgdb import config


def iter_jsonl(path):
    """逐行读一个 jsonl 导出文件，跳过坏行和没有 drama_id 的行。"""
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get('drama_id'):
                yield rec


def read_exports(out_dir):
    """读全部导出文件，按修改时间从旧到新返回 (记录列表, 各文件 mtime)。

    记录顺序有意义：后面 dataset 合并时「新的覆盖旧的」。
    """
    files = sorted(Path(out_dir).glob(config.EXPORT_GLOB),
                   key=lambda p: p.stat().st_mtime)
    records = []
    mtimes = []
    for path in files:
        mtimes.append(path.stat().st_mtime)
        records.extend(iter_jsonl(path))
    return records, mtimes


def read_tag_edits(out_dir):
    """读人工编辑过的标签（drama_id -> 标签列表）。

    文件坏掉 / 结构不对就当没有 —— 展示数据不该因为一个手滑的 JSON 全挂。
    """
    path = Path(out_dir) / config.TAG_EDITS_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        if isinstance(value, list):
            out[str(key)] = [str(t).strip() for t in value if str(t).strip()]
    return out


def _write_json(path, payload):
    """原子写 JSON：先写临时文件再 rename，写一半崩溃不会毁掉原文件。"""
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    try:
        os.replace(str(tmp), str(path))
    except OSError:
        # 个别环境（沙箱/杀软）拦截 rename，退回直接写
        path.write_text(text, encoding='utf-8')
        tmp.unlink(missing_ok=True)


def write_tag_edits(out_dir, edits):
    """原子写标签编辑文件。"""
    _write_json(Path(out_dir) / config.TAG_EDITS_FILE, edits)


def read_favorites(out_dir):
    """读人工收藏（drama_id -> 收藏时间）。

    和标签编辑一样：文件坏掉 / 结构不对就当没有收藏，不要因为一个手滑的
    JSON 让整站数据出不来。
    """
    path = Path(out_dir) / config.FAVORITES_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for key, value in data.items():
        drama_id = str(key).strip()
        if drama_id:
            out[drama_id] = str(value or '')
    return out


def write_favorites(out_dir, favorites):
    """原子写收藏文件（drama_id -> 收藏时间）。"""
    _write_json(Path(out_dir) / config.FAVORITES_FILE, favorites)
