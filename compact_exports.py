# -*- coding: utf-8 -*-
"""压实历史导出：把 output 下所有 duanju_*.jsonl 合并成一个新快照。

背景：每次抓取都生成新的 duanju_<时间戳>.jsonl，增量模式只写「有变化的剧」，
同一部剧会散落在很多个文件里。展示站读取时本来就自动合并（新覆盖旧、空值不
覆盖），文件多不影响正确性——但每次加载都全量读盘，文件越攒越慢。压实把
N 个文件收拢成 1 个，加载恢复到最快。

步骤：
  1. 读全部 jsonl（复用展示站的 sources.read_exports，按 mtime 旧到新）
  2. 按 drama_id 合并（复用 model.merge_record，与展示站同一套规则）
  3. 写出一个新文件 duanju_<本次时间戳>.jsonl
  4. 旧文件移进 output/exports_backup_<本次时间戳>/（只移动不删除，随时移回）

用法：
    python compact_exports.py              # 试运行：只报告合并结果，不写任何文件
    python compact_exports.py --write      # 真正执行：写新文件 + 旧文件进备份目录
    python compact_exports.py --write --keep-old   # 只多写一个合并文件，旧文件原地不动

压实完成后建议：展示站点一次「刷新」，确认总数没变，再考虑清理备份目录。

CSV 快照（duanju_*.csv）不参与——它们只是给人看的镜像，展示站不读。
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hgdb import model, sources  # noqa: E402

EXPORT_GLOB = 'duanju_*.jsonl'


def load(out_dir):
    """读全部 jsonl 并合并，返回 (merged dict, 文件列表, 总行数)。"""
    records, _mtimes = sources.read_exports(out_dir)
    if not records:
        sys.exit('没有可读的 jsonl 记录：%s' % Path(out_dir))

    merged = {}
    for rec in records:
        did = str(rec['drama_id'])
        merged[did] = model.merge_record(merged.get(did), rec)
    return merged, records


def write_merged(out_dir, merged, stamp=None):
    """写合并快照，返回新文件路径。"""
    stamp = stamp or time.strftime('%Y%m%d_%H%M%S')
    path = Path(out_dir) / ('duanju_%s.jsonl' % stamp)
    with open(path, 'w', encoding='utf-8') as f:
        for rec in merged.values():
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return path


def backup_old(out_dir, keep_paths, stamp=None):
    """把除 keep_paths 之外的 jsonl 移进备份目录，返回备份目录路径。"""
    stamp = stamp or time.strftime('%Y%m%d_%H%M%S')
    backup_dir = Path(out_dir) / ('exports_backup_%s' % stamp)
    backup_dir.mkdir(parents=True, exist_ok=True)
    keep = {p.resolve() for p in keep_paths}
    moved = []
    for path in sorted(Path(out_dir).glob(EXPORT_GLOB)):
        if path.resolve() in keep:
            continue
        shutil.move(str(path), str(backup_dir / path.name))
        moved.append(path.name)
    return backup_dir, moved


def verify(out_dir, merged):
    """回读磁盘重合并一遍，和内存里的结果比对。"""
    reread, _ = load(out_dir)
    return reread == merged


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='把 output 下所有 duanju_*.jsonl 压实成一个新快照')
    parser.add_argument('--write', action='store_true',
                        help='真正执行（默认只试运行，不写文件）')
    parser.add_argument('--keep-old', action='store_true',
                        help='旧 jsonl 留在原地，只多写一个合并文件')
    parser.add_argument('--out-dir', default=str(ROOT / 'output'),
                        help='导出目录（默认 output/）')
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    files = sorted(out_dir.glob(EXPORT_GLOB))
    if len(files) <= 1:
        sys.exit('只有 %d 个 jsonl，没有可压实的冗余：%s'
                 % (len(files), out_dir))

    merged, records = load(out_dir)
    dup = len(records) - len(merged)
    print('jsonl 文件 %d 个，共 %d 条记录，按 drama_id 合并后 %d 部剧（跨文件重叠 %d 条）'
          % (len(files), len(records), len(merged), dup))

    if not args.write:
        print('\n试运行结束（未写任何文件）。确认无误后加 --write 执行，'
              '旧文件会移进 exports_backup_<时间戳>/ 备份目录。')
        return 0

    new_path = write_merged(out_dir, merged)
    print('已写出 %s' % new_path.name)

    if args.keep_old:
        print('--keep-old：旧文件原地保留（下次压实前记得先清，否则又攒回去了）')
    else:
        backup_dir, moved = backup_old(out_dir, [new_path])
        print('旧文件 %d 个已移进 %s' % (len(moved), backup_dir.name))

    if verify(out_dir, merged):
        print('校验通过：回读磁盘重合并与内存结果一致（%d 部剧）' % len(merged))
    else:
        print('!! 校验失败：磁盘回读与预期不一致。'
              '如已备份，把 %s 下的文件移回 %s 即可还原。'
              % (backup_dir.name, out_dir))
        return 1

    print('\n完成。到展示站点一次「刷新」确认总数；备份目录确认没问题后可自行清理。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
