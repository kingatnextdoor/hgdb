# -*- coding: utf-8 -*-
"""hgdb 展示站服务的启停小工具。

放在项目根（manage.py 旁边），用法：

    python hgdbctl.py start              # 后台启动 http://127.0.0.1:8765
    python hgdbctl.py start --port 9000  # 换端口
    python hgdbctl.py start --open       # 起来后自动开浏览器
    python hgdbctl.py start -f           # 前台跑（Ctrl-C 停，报错直接看得见）
    python hgdbctl.py status             # 在不在跑、端口通不通、库里多少条
    python hgdbctl.py stop               # 停（先礼后兵）
    python hgdbctl.py restart            # 停了再起（沿用上次的端口）
    python hgdbctl.py log [-n 50] [-f]   # 看日志，-f 实时跟随
    python hgdbctl.py open               # 只在浏览器打开，不动进程

为什么不全手写命令
------------------
直接 `python hgdb/server.py` 也能跑，但它是个前台进程：终端一关就没了，
没有 pid 可查，第二次启动会因为端口占用崩一屏 OSError，而 "xx 端口被占用"
在 Windows 上还得 netstat -ano 反查 pid 再手工杀。这个脚本补的就是这几件事：

  · 后台脱离终端运行，stdout/stderr 落到 output/hgdb.log
  · pid + 启动参数写 output/hgdb.pid（JSON），restart 能沿用原端口
  · 结合端口反查避免重复启动、避免误杀别的进程
  · 停服务先发 Ctrl-Break（http.server 走 KeyboardInterrupt 自己收尾），
    赖着不走才升级到强杀
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / 'hgdb' / 'server.py'
OUTPUT = ROOT / 'output'
PID_FILE = OUTPUT / 'hgdb.pid'
LOG_FILE = OUTPUT / 'hgdb.log'

DEFAULT_PORT = 8765
DEFAULT_HOST = '127.0.0.1'
IS_WIN = os.name == 'nt'


# ------------------------------ 底层 helpers ------------------------------

def _run(cmd):
    """跑一条命令拿输出，失败返回空串（这类查询命令失败一律当"没查到"）。"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=8)
        return r.stdout.decode('utf-8', 'ignore') or r.stdout.decode('gbk', 'ignore')
    except Exception:
        return ''


def read_meta():
    """读 pid 文件。兼容两种写法：JSON（现在）和裸 pid（万一有人手写的）。"""
    if not PID_FILE.exists():
        return {}
    try:
        raw = PID_FILE.read_text(encoding='utf-8').strip()
        if raw.startswith('{'):
            return json.loads(raw)
        return {'pid': int(raw)}
    except Exception:
        return {}


def write_meta(pid, host, port, cmd):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(json.dumps({
        'pid': pid, 'host': host, 'port': port, 'cmd': cmd,
        'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }), encoding='utf-8')


def pid_alive(pid):
    if not pid or pid <= 0:
        return False
    if IS_WIN:
        out = _run(['tasklist', '/FI', 'PID eq %d' % pid, '/NH'])
        out += _run(['tasklist', '/FI', 'PID eq %d' % pid, '/NH', '/FO', 'CSV'])
        return bool(re.search(r'\b%d\b' % pid, out))
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def port_pid(port):
    """谁在监听这个端口。找不到返回 None。"""
    if IS_WIN:
        for line in _run(['netstat', '-ano', '-p', 'TCP']).splitlines():
            line = line.strip()
            if ':%d ' % port not in line and not line.endswith(':%d' % port):
                continue
            if 'LISTENING' not in line.upper():
                continue
            parts = line.split()
            try:
                return int(parts[-1])
            except ValueError:
                pass
        return None
    out = _run(['lsof', '-nP', '-iTCP:%d' % port, '-sTCP:LISTEN', '-t'])
    m = re.search(r'\d+', out)
    if m:
        return int(m.group())
    for line in _run(['ss', '-ltnp']).splitlines():
        if ':%d ' % port in line:
            m = re.search(r'pid=(\d+)', line)
            if m:
                return int(m.group(1))
    return None


def api_stats(host, port, timeout=3):
    """探一下 /api/stats，能通就返回 dict，不通返回 None（服务还活着但不响应也算不通）。"""
    try:
        with urlopen('http://%s:%d/api/stats' % (host, port), timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:
        return None


def wait_alive(host, port, seconds=8):
    """等服务起来（轮询接口），返回是否成功。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if api_stats(host, port, timeout=2):
            return True
        time.sleep(0.3)
    return False


def soft_kill(pid):
    """先礼：请求它自己退出。Windows 发 Ctrl-Break，让 Python 走 KeyboardInterrupt。

    后台进程是 DETACHED_PROCESS 起的（见 cmd_start），没有控制台，这一步会失败，
    那也没关系——kill_pid() 会退到 taskkill。前台模式（-f）下它是好使的。
    """
    try:
        if IS_WIN:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except Exception:
        return False


def hard_kill(pid):
    """后兵：没控制台的后台进程只能到这一步（Windows 上等价于优雅退出的最后一档）。"""
    if IS_WIN:
        # 先不加 /F 试一次；ctrlhandler 不鸟就直接 /F
        _run(['taskkill', '/PID', str(pid), '/T'])
        for _ in range(6):
            if not pid_alive(pid):
                return
            time.sleep(0.3)
        _run(['taskkill', '/PID', str(pid), '/T', '/F'])
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def kill_pid(pid, graceful=6):
    """停一个 pid；返回 True 表示已经不在了。"""
    if not pid_alive(pid):
        return True
    if soft_kill(pid):
        deadline = time.time() + graceful
        while time.time() < deadline:
            if not pid_alive(pid):
                return True
            time.sleep(0.3)
    hard_kill(pid)
    for _ in range(10):
        if not pid_alive(pid):
            return True
        time.sleep(0.3)
    return not pid_alive(pid)


def clear_meta():
    """清掉 pid 文件。

    删不掉的情况不少见：文件被占用、只读、或者在某些受管环境里删除操作被拦。
    这时候退一步写个空壳就行——read_meta() 会把 `{}` 当「没在跑」处理。
    清理这种收尾动作不该把整条命令搞崩。
    """
    try:
        PID_FILE.unlink(missing_ok=True)
        return
    except FileNotFoundError:
        return
    except Exception:
        pass
    try:
        PID_FILE.write_text('{}', encoding='utf-8')
    except Exception:
        pass


def build_cmd(host, port, output, open_browser=False):
    cmd = [sys.executable, '-u', str(SERVER), '--host', host, '--port', str(port)]
    if output:
        cmd += ['--output', str(output)]
    if open_browser:
        cmd.append('--open')
    return cmd


# ------------------------------ 子命令 ------------------------------

def cmd_start(args):
    if not SERVER.exists():
        sys.exit('找不到服务文件：%s' % SERVER)

    meta_port = read_meta().get('port')
    port = args.port or meta_port or DEFAULT_PORT

    # 1) 已经在跑（pid 文件有效）就直接收工
    meta = read_meta()
    pid = meta.get('pid')
    if pid and pid_alive(pid) and (meta.get('port') or DEFAULT_PORT) == port:
        print('服务已经在跑：pid=%d  http://%s:%d' % (pid, meta.get('host', DEFAULT_HOST), port))
        if args.open:
            webbrowser.open('http://%s:%d' % (meta.get('host', DEFAULT_HOST), port))
        return

    # 2) 端口被别人占着：清掉失效的 pid 文件，然后拒绝启动
    owner = port_pid(port)
    if owner:
        if pid and not pid_alive(pid):
            clear_meta()
        sys.exit('端口 %d 已被 pid=%d 占用（不是本服务记录的进程）。\n'
                 '换个端口：  python hgdbctl.py start --port %d\n'
                 '或者先看清楚那是谁：  tasklist /FI "PID eq %d"' % (port, owner, port + 1, owner))
    clear_meta()
    if pid and pid_alive(pid):
        # pid 还活着但端口不通——多半是上次崩了一半的残骸
        print('发现残留进程 pid=%d（端口已不在监听），先清理' % pid)
        kill_pid(pid)

    cmd = build_cmd(args.host or DEFAULT_HOST, port, args.output, args.open and not args.foreground)
    host = args.host or DEFAULT_HOST

    if args.foreground:
        # 前台：日志打屏幕，Ctrl-C 直接结束，方便调试
        print('前台运行：%s' % ' '.join('"%s"' % c if ' ' in c else c for c in cmd))
        print('Ctrl-C 停止\n')
        clear_meta()
        try:
            subprocess.run(cmd, cwd=str(ROOT))
        except KeyboardInterrupt:
            print('\n已停止')
        return

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'ab') as log:
        log.write(('\n=== %s start ===\n' % time.strftime('%Y-%m-%d %H:%M:%S')).encode('utf-8'))
        creationflags = 0
        if IS_WIN:
            # DETACHED_PROCESS：让子进程完全脱离当前控制台。
            # 之前用 CREATE_NEW_PROCESS_GROUP 只是「给它单独一组」，控制台一关（比如
            # 敲完 start 就关掉这个终端窗口）它照样跟着没，表现为 pid 文件里的进程
            # 莫名其妙消失。代价是它不再有控制台，Ctrl-Break 发不进去了——
            # stop 会自动降级成 taskkill，见 kill_pid()。
            creationflags = getattr(subprocess, 'DETACHED_PROCESS', 0)
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=creationflags)
    write_meta(proc.pid, host, port, cmd)

    if not wait_alive(host, port):
        print('启动了，但 %d 秒后接口还没响应——大概率起不来，看下日志：' % 8)
        print('  python hgdbctl.py log -n 30')
        return 1

    stats = api_stats(host, port) or {}
    total = (stats.get('stats') or {}).get('total')
    print('已启动 pid=%d' % proc.pid)
    print('  地址 http://%s:%d' % (host, port))
    if total is not None:
        print('  数据 %s 部' % total)
    print('  pid 文件 %s' % PID_FILE)
    print('  日志 %s' % LOG_FILE)
    if args.open:
        webbrowser.open('http://%s:%d' % (host, port))


def cmd_stop(args):
    meta = read_meta()
    pid = args.pid or meta.get('pid')
    port = meta.get('port') or DEFAULT_PORT

    if not pid:
        # 没有 pid 文件：看看端口上有没有人
        owner = port_pid(port)
        if owner:
            sys.exit('没有 pid 文件，但端口 %d 正被 pid=%d 占着。\n'
                     '确认是本站的话：  python hgdbctl.py stop --pid %d' % (port, owner, owner))
        print('服务没在跑（端口 %d 空闲，pid 文件也不存在）' % port)
        clear_meta()
        return

    if not pid_alive(pid):
        print('pid=%d 已经不在了，清理 pid 文件' % pid)
        clear_meta()
        return

    ok = kill_pid(pid)
    clear_meta()
    if ok:
        print('已停止 pid=%d' % pid)
    else:
        print('pid=%d 没能正常结束，试试：taskkill /PID %d /F' % (pid, pid))
        return 1


def cmd_restart(args):
    # restart 的解析器没逐个声明 start 的开关，这里补齐，免得 AttributeError
    args.host = getattr(args, 'host', None)
    args.output = getattr(args, 'output', None)
    args.foreground = getattr(args, 'foreground', False)
    meta = read_meta()
    pid = meta.get('pid')
    if pid and pid_alive(pid):
        kill_pid(pid)
        clear_meta()
    elif pid:
        clear_meta()
    if not args.port:
        args.port = meta.get('port')
    args.open = False
    cmd_start(args)


def cmd_status(args):
    meta = read_meta()
    pid = meta.get('pid')
    port = meta.get('port') or DEFAULT_PORT
    host = meta.get('host') or DEFAULT_HOST
    owner = port_pid(port)

    print('pid 文件 %s' % PID_FILE)
    print('日志文件 %s%s' % (LOG_FILE, '' if LOG_FILE.exists()
                            else '（不存在）'))
    print()

    if pid and pid_alive(pid):
        print('状态     运行中 pid=%d（%s 启动）' % (pid, meta.get('started_at', '?')))
        print('地址     http://%s:%d' % (host, port))
        stats = api_stats(host, port)
        if stats:
            s = stats.get('stats') or {}
            print('数据     %s 部（封面 %s，详情 %s）' %
                  (s.get('total'), s.get('with_cover'), s.get('with_detail')))
            if stats.get('db'):
                print('数据库   %s' % stats['db'])
            print('接口     /api/stats 正常')
        else:
            print('接口     http://%s:%d 没响应（进程还在，可能刚起或卡住了）' % (host, port))
        return

    # pid 文件指向的进程没了
    if pid:
        print('状态     未运行（pid 文件里是 pid=%d，但那个进程已经没了）' % pid)
    else:
        print('状态     未运行（无 pid 文件）')
    if owner:
        print('注意     端口 %d 正被 pid=%d 占着，但不是本脚本记录的进程' % (port, owner))
        print('         python hgdbctl.py stop --pid %d   # 确认是本站再杀' % owner)
    else:
        print('端口     %d 空闲' % port)


def cmd_log(args):
    if not LOG_FILE.exists():
        sys.exit('还没有日志：%s（先 python hgdbctl.py start）' % LOG_FILE)
    data = LOG_FILE.read_bytes()
    text = data.decode('utf-8', 'replace')
    lines = text.splitlines()

    if args.follow:
        for line in lines[-args.n:]:
            print(line)
        print('\n--- 跟随中，Ctrl-C 退出 ---')
        pos = len(data)
        try:
            while True:
                with open(LOG_FILE, 'rb') as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    sys.stdout.write(chunk.decode('utf-8', 'replace'))
                    sys.stdout.flush()
                time.sleep(0.5)
        except KeyboardInterrupt:
            print('\n退出跟随')
        return

    for line in lines[-args.n:]:
        print(line)


def cmd_open(args):
    meta = read_meta()
    port = meta.get('port') or DEFAULT_PORT
    host = meta.get('host') or DEFAULT_HOST
    if not (meta.get('pid') and pid_alive(meta['pid'])) and not port_pid(port):
        sys.exit('服务没在跑，先 python hgdbctl.py start')
    print('http://%s:%d' % (host, port))
    webbrowser.open('http://%s:%d' % (host, port))


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog='hgdbctl.py',
        description='短剧数据展示服务的启停管理',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='常用：start → status → log -f → stop')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('start', help='启动服务（默认后台）')
    p.add_argument('--port', type=int, help='端口（默认沿用上次，再默认 8765）')
    p.add_argument('--host', default=None, help='监听地址（默认 127.0.0.1）')
    p.add_argument('--output', default=None, help='数据目录（默认 output/）')
    p.add_argument('--open', action='store_true', help='启动后打开浏览器')
    p.add_argument('-f', '--foreground', action='store_true',
                   help='前台运行，Ctrl-C 停止（调试用）')
    p.set_defaults(func=cmd_start)

    p = sub.add_parser('stop', help='停止服务')
    p.add_argument('--pid', type=int, help='指定 pid（用于 pid 文件丢失时的残留进程）')
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser('restart', help='重启（沿用上次的端口）')
    p.add_argument('--port', type=int, help='顺便换端口')
    p.add_argument('--host', default=None, help='监听地址（默认沿用上次）')
    p.add_argument('--output', default=None, help='数据目录（默认 output/）')
    p.set_defaults(func=cmd_restart)

    p = sub.add_parser('status', help='看服务在不在、数据多少')
    p.set_defaults(func=cmd_status)

    p = sub.add_parser('log', help='查看运行日志')
    p.add_argument('-n', type=int, default=50, help='显示最后几行（默认 50）')
    p.add_argument('-f', '--follow', action='store_true', help='实时跟随')
    p.set_defaults(func=cmd_log)

    p = sub.add_parser('open', help='在浏览器打开')
    p.set_defaults(func=cmd_open)

    args = parser.parse_args()
    rc = args.func(args)
    if rc:
        sys.exit(rc)


if __name__ == '__main__':
    main()
