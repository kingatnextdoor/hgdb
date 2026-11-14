# -*- coding: utf-8 -*-
"""展示站测试用的合成数据目录。

不碰真实 output/：每个用例自己在临时目录里造一份最小数据集，
jsonl + 封面文件 + 标签编辑文件都能按需拼。
"""
import json
from pathlib import Path



def record(drama_id='1', title='测试剧', **extra):
    """一条最小可用的导出记录（形状与导出契约对齐，见 docs/data.md）。"""
    rec = {
        'drama_id': drama_id,
        'title': title,
        'url': 'https://example.com/video/%s/' % drama_id,
        'cover_local': None,
        'score': 8.5,
        'score_text': '8.5',
        'episode_total': 60,
        'episode_text': '60集',
        'update_ts': 1700000000,
        'update_time': '2026-01-01',
        'tags': ['都市'],
        'category_name': '短剧',
        'detail': {},
    }
    rec.update(extra)
    return rec


def build_output(root, records=(), *, old_records=None, edits=None,
                 favorites=None, covers=()):
    """在 root 下造一个 output 目录，返回路径。

    records      最新的 jsonl 内容
    old_records  更早的一批（用于验证「新覆盖旧、空值不覆盖」）
    edits        tag_edits.json 的内容（drama_id -> 标签列表）
    favorites    favorites.json 的内容（drama_id -> 收藏时间）
    covers       (文件名, 内容) 列表，写到 covers/ 下
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    def write_jsonl(name, recs):
        path = root / name
        with open(path, 'w', encoding='utf-8') as fh:
            for rec in recs:
                fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
        return path

    if old_records is not None:
        # 旧批次的文件名时间戳更小，dataset 按 mtime 排序合并
        write_jsonl('duanju_0001.jsonl', old_records)
    if records:
        write_jsonl('duanju_0002.jsonl', records)

    covers_dir = root / 'covers'
    if covers:
        covers_dir.mkdir(exist_ok=True)
        for name, content in covers:
            (covers_dir / name).write_bytes(content)

    if edits is not None:
        (root / 'tag_edits.json').write_text(
            json.dumps(edits, ensure_ascii=False), encoding='utf-8')

    if favorites is not None:
        (root / 'favorites.json').write_text(
            json.dumps(favorites, ensure_ascii=False), encoding='utf-8')

    return root
