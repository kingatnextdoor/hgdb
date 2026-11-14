# -*- coding: utf-8 -*-
"""展示站入口。

    python web/server.py                    # 默认 127.0.0.1:8765
    python web/server.py --port 9000 --open
    python -m web.server --output ./output  # 在仓库根也可以这样跑

只管四件事：解析参数、准备配置、建 Store、把服务器跑起来。
真正的 HTTP 处理在 httpd.py，数据库连接在 dbio.py。
"""
import argparse
import sys
import webbrowser
from pathlib import Path

try:                                    # 作为包安装后（pip install）直接可用
    from web import config, dbio, httpd
    from web.store import Store
except ImportError:                     # 直接 python web/server.py 时把仓库根加进来
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from web import config, dbio, httpd  # noqa: E402
    from web.store import Store  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(description='短剧数据展示服务（sqlite）')
    parser.add_argument('--output', default=str(config.DEFAULT_OUTPUT),
                        help='output 目录（默认 %(default)s）')
    parser.add_argument('--host', default=config.DEFAULT_HOST)
    parser.add_argument('--port', type=int, default=config.DEFAULT_PORT)
    parser.add_argument('--open', action='store_true', help='启动后自动打开浏览器')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    # Windows 控制台默认 GBK，中文日志会乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass

    output = Path(args.output).resolve()
    if not output.is_dir():
        print('output 目录不存在：%s' % output)
        return 2

    try:
        settings = dbio.Config.from_config()
    except dbio.DbError as exc:
        print('数据库配置有误：%s' % exc)
        return 2

    try:
        store = Store(output, settings)
    except dbio.DbError as exc:
        print('数据库连接失败：%s' % exc)
        return 2

    stats = store.data['stats']
    print('数据目录 : %s' % output)
    print('数据库   : %s' % store.describe_db())
    print('ED2K 库  : %s' % store.ed2k.describe())
    print('剧集     : %d 部（有封面 %d，有详情 %d）'
          % (stats['total'], stats['with_cover'], stats['with_detail']))
    data_ver = stats.get('schema_version') or 0
    if data_ver > config.SCHEMA_VERSION:
        print('提示     : 数据契约版本 %d 比本程序支持的 %d 新，'
              '不认识的字段会被忽略（详见 docs/data.md）'
              % (data_ver, config.SCHEMA_VERSION))
    print('地址     : http://%s:%d' % (args.host, args.port))
    print('Ctrl-C 停止')

    if args.open:
        webbrowser.open('http://%s:%d' % (args.host, args.port))

    server = httpd.make_server(store, args.host, args.port,
                               covers_dir=output / config.COVERS_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
