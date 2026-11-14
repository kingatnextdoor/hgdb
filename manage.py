# -*- coding: utf-8 -*-
"""项目维护小工具：看看数据有多少、清掉重来。

用法：

    python manage.py stats                 # 导出文件里有多少部剧、封面几张
    python manage.py recent [-n 20]        # 最近有变动的剧（按 last_changed）
    python manage.py reset --purge-output  # 删 output 下的导出文件（= 清空剧集数据）
    python manage.py reset --drop-covers   # 删本地封面（默认保留复用）
    python manage.py reset --purge-output --drop-covers -y   # 一起删，不确认

删数据和删封面是两件事
----------------------
剧集数据只在 output/duanju_*.jsonl 里，删掉就没有了；封面在 output/covers/，跟
剧集文件相互独立。所以清空数据不会连封面一起删，重新灌同一批数据时按文件名直接
复用封面，不必重新下载。只有显式 --drop-covers 才会真删。
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'output'
COVERS = OUTPUT / 'covers'


def _load():
    """读全部导出并合并，返回 (合并后的记录 dict, 文件列表)。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from web import model, sources

    records, _ = sources.read_exports(OUTPUT)
    merged = {}
    for rec in records:
        did = str(rec['drama_id'])
        merged[did] = model.merge_record(merged.get(did), rec)
    files = sorted(OUTPUT.glob('duanju_*.jsonl'), key=lambda p: p.stat().st_mtime)
    return merged, files


def cmd_stats(args):
    merged, files = _load()
    if not files:
        print('output/ 下还没有导出文件：%s' % OUTPUT)
        return
    covers = len(list(COVERS.glob('*'))) if COVERS.is_dir() else 0
    with_cover = sum(1 for r in merged.values() if r.get('cover_local'))
    firsts = [r.get('first_seen') for r in merged.values() if r.get('first_seen')]
    changed = [r.get('last_changed') for r in merged.values() if r.get('last_changed')]

    print('导出目录：%s' % OUTPUT)
    print('  文件        %d 个（最新 %s）' % (len(files), files[-1].name))
    print('  去重后      %d 部' % len(merged))
    print('  封面        本地 %d 张（记录里带本地路径的 %d 部）' % (covers, with_cover))
    if firsts:
        print('  最早入库    %s' % min(firsts))
    if changed:
        print('  最近变动    %s' % max(changed))
    print('\n提示：剧集数据就这些导出文件，删掉等于清空数据（见 reset --purge-output）。')


def cmd_recent(args):
    merged, files = _load()
    if not files:
        print('output/ 下还没有导出文件：%s' % OUTPUT)
        return
    rows = sorted(merged.values(),
                  key=lambda r: r.get('last_changed') or '', reverse=True)
    print('最近变动的 %d 部（按 last_changed 倒序）：' % args.n)
    for r in rows[:args.n]:
        print('  %-8s %-28s %s' % (r.get('drama_id'), (r.get('title') or '')[:26],
                                   (r.get('last_changed') or '').replace('T', ' ')))


def cmd_reset(args):
    if not args.drop_covers and not args.purge_output:
        print('没有指定要删什么。可选：')
        print('  --purge-output  删导出文件 duanju_*（清空剧集数据）')
        print('  --drop-covers   删本地封面（默认保留，灌回数据时按文件名复用）')
        return

    exports = sorted(OUTPUT.glob('duanju_*'))
    print('即将清理：')
    if args.drop_covers:
        print('  删除封面目录 %s%s' % (COVERS, '' if COVERS.exists() else '（当前不存在）'))
    else:
        print('  保留封面目录 %s（灌回数据时按文件名复用，不用重下）' % COVERS)
    if args.purge_output:
        print('  删除导出文件 duanju_*（%d 个）—— 这会清空剧集数据' % len(exports))
    else:
        print('  保留导出文件 duanju_*（%d 个，剧集数据还在）' % len(exports))

    if not args.yes:
        ans = input('\n确认删除？[y/N] ').strip().lower()
        if ans not in ('y', 'yes'):
            print('已取消')
            return

    removed = []
    if args.drop_covers and COVERS.exists():
        shutil.rmtree(COVERS)
        removed.append(str(COVERS))
    if args.purge_output:
        for path in exports:
            path.unlink()
            removed.append(str(path))

    print('\n已删除 %d 项：' % len(removed))
    for path in removed:
        print('  -', path)

    print('\n清空数据后页面会变成空的。只要封面目录还在，灌回同一批数据就能直接复用封面。')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('stats', help='导出文件里有多少剧、封面几张')
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser('recent', help='最近有变动的剧')
    p.add_argument('-n', type=int, default=20)
    p.set_defaults(func=cmd_recent)

    p = sub.add_parser('reset', help='删导出文件 / 封面（至少指定一项）')
    p.add_argument('--drop-covers', action='store_true',
                   help='删本地封面（默认保留，灌回数据时按文件名复用）')
    p.add_argument('--purge-output', action='store_true',
                   help='删导出文件 duanju_*（等于清空剧集数据）')
    p.add_argument('--yes', '-y', action='store_true', help='跳过确认提示')
    p.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
