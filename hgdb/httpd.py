# -*- coding: utf-8 -*-
"""http.server 适配层：把 api.dispatch 的结果写回响应，外加静态文件、封面与视频流。

这一层刻意保持很薄——除了「怎么收发字节」什么都不做，业务全在 api/queries 里。
"""
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from hgdb import api, config, multipart, video


class PayloadTooLarge(Exception):
    """请求体超过上限（配置见 config.MAX_UPLOAD_BYTES）。"""


class BadRequest(Exception):
    """请求体解析不了（比如 multipart 没有 boundary）。"""


def resolve_under(root, rel):
    """把 /static 之后的相对路径安全地解析到 root 之下。

    目录穿越（`../../etc/passwd`）在这里被挡住；找不到返回 None。
    """
    root = Path(root).resolve()
    try:
        target = (root / rel).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def parse_range(header, size):
    """解析单段 `Range: bytes=...`，返回闭区间 (start, end) 或 None。

    只支持单段（`bytes=0-1023` / `bytes=1024-` / `bytes=-2048`）——播放器
    从来只发单段，多段（multipart/byteranges）纯属负担，直接当不可满足。
    返回 None 表示「这个 Range 给不了」，调用方回 416。
    """
    text = str(header or '').strip()
    if not text.lower().startswith('bytes=') or size <= 0:
        return None
    spec = text[len('bytes='):].split(',')[0].strip()
    first, sep, last = spec.partition('-')
    if not sep:
        return None
    first, last = first.strip(), last.strip()
    try:
        if not first:                       # bytes=-N：最后 N 字节
            count = int(last)
            if count <= 0:
                return None
            return max(0, size - count), size - 1
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or start > end:
        return None
    return start, min(end, size - 1)


class Handler(BaseHTTPRequestHandler):
    """所有路由的分发器。store / 目录由 make_server() 注入到子类。"""

    server_version = 'DuanjuWeb/1.0'
    store = None
    static_dir = config.STATIC_DIR
    covers_dir = None
    quiet = False           # 测试里设 True，不打印请求日志

    # ---------- 输出 ----------

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        extra = dict(extra or {})
        # 默认不缓存；调用方用 extra 里的 Cache-Control 覆盖（响应里不能出现两个同名头，
        # 浏览器只认第一个，覆盖就永远不生效）
        extra.setdefault('Cache-Control', 'no-store')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for key, value in extra.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, ensure_ascii=False))

    def _text(self, code, text):
        self._send(code, text.encode('utf-8'), 'text/plain; charset=utf-8')

    def _file(self, path, cache=False):
        try:
            body = Path(path).read_bytes()
        except OSError:
            return self._text(404, 'not found')
        suffix = Path(path).suffix.lower()
        ctype = (config.MIME_FALLBACK.get(suffix)
                 or mimetypes.guess_type(str(path))[0]
                 or 'application/octet-stream')
        extra = {'Cache-Control': 'public, max-age=86400'} if cache else None
        self._send(200, body, ctype, extra)

    def _stream(self, path, chunk=256 * 1024):
        """流式发一个视频文件，支持单段 Range（播放器拖进度条全靠它）。

        不做整文件 read：几个 G 的视频读进内存会直接撑爆。
        客户端 seek 时会主动断开连接，BrokenPipe 属正常现象，别当错误。
        """
        try:
            handle = open(path, 'rb')
        except OSError:
            return self._text(404, 'not found')

        with handle:
            size = os.fstat(handle.fileno()).st_size
            suffix = Path(path).suffix.lower()
            ctype = (config.VIDEO_MIME.get(suffix)
                     or mimetypes.guess_type(str(path))[0]
                     or 'application/octet-stream')

            start, end, partial = 0, max(0, size - 1), False
            header = self.headers.get('Range')
            if header and size > 0:
                parsed = parse_range(header, size)
                if parsed is None:
                    self.send_response(416)
                    self.send_header('Content-Range', 'bytes */%d' % size)
                    self.send_header('Content-Length', '0')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.end_headers()
                    return
                start, end = parsed
                partial = True

            length = end - start + 1 if size > 0 else 0
            self.send_response(206 if partial else 200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(length))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-store')
            if partial:
                self.send_header('Content-Range',
                                 'bytes %d-%d/%d' % (start, end, size))
            self.end_headers()
            if self.command == 'HEAD' or length <= 0:
                return

            handle.seek(start)
            remaining = length
            try:
                while remaining > 0:
                    data = handle.read(min(chunk, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True      # 播放器拖走了，正常

    def log_message(self, fmt, *args):
        """默认日志太吵，只留一行精简的；测试里直接静音。"""
        if self.quiet:
            return
        sys.stderr.write('%s - %s\n' % (self.address_string(), (fmt % args)[:160]))

    # ---------- 请求 ----------

    def _decoded_path(self):
        """self.path 是按 ISO-8859-1 解出来的，中文查询串会变成乱码。

        浏览器一般会百分号编码（那就没问题），但 curl / 手敲地址可能直接发
        原始字节，必须先还原成字节再解码，否则搜索中文永远命中 0。
        Windows 上的 curl 按系统代码页（GBK）编码，所以 UTF-8 解不出来时再试 GBK。
        """
        try:
            raw = self.path.encode('iso-8859-1')
        except UnicodeEncodeError:
            return self.path
        for encoding in ('utf-8', 'gbk'):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return self.path

    def _read_body(self):
        """按 Content-Type 读请求体，返回 Python 对象（None = 没有或坏了）。

        · `application/json`    -> dict / list（上限 MAX_JSON_BYTES：视频库扫描
                                   一次可能提交几万个文件名，1MB 不够用）
        · `multipart/form-data` -> {字段: str | {'filename','content_type','data'}}
        · `text/plain` 等       -> {'text': bytes, 'filename': ...}（curl 直传的便利通道）

        超限抛 PayloadTooLarge、解析不了抛 BadRequest，由 _write_request 转成 413/400。
        """
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return None

        ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()

        if ctype == 'multipart/form-data':
            if length > config.MAX_UPLOAD_BYTES:
                raise PayloadTooLarge('上传文件超过 %d MB'
                                      % (config.MAX_UPLOAD_BYTES >> 20))
            raw = self.rfile.read(length)
            try:
                return multipart.parse(raw, self.headers.get('Content-Type') or '')
            except ValueError as exc:
                raise BadRequest(str(exc)) from exc

        if ctype in ('', 'text/plain', 'application/octet-stream'):
            if length > config.MAX_UPLOAD_BYTES:
                raise PayloadTooLarge('请求体超过 %d MB'
                                      % (config.MAX_UPLOAD_BYTES >> 20))
            return {
                'text': self.rfile.read(length),
                'filename': self.headers.get('X-Filename') or '',
            }

        if length > config.MAX_JSON_BYTES:
            return None
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return None

    # ---------- 路由 ----------

    def do_HEAD(self):
        self.do_GET()

    def _dispatch(self, method, path, params, body=None):
        """调 api.dispatch 并兜住异常。

        不兜的话，处理函数里一个 bug 会让连接被直接掐断（浏览器只看到
        ERR_EMPTY_RESPONSE），排查时完全看不到线索；这里统一转成 500 + 原因。
        """
        try:
            return api.dispatch(self.store, method, path, params, body)
        except Exception as exc:                       # noqa: BLE001 —— 故意兜全部
            self.log_error('接口异常 %s %s: %r', method, path, exc)
            return 500, {'error': 'internal error', 'detail': str(exc)}

    def do_GET(self):
        url = urlparse(self._decoded_path())
        path = unquote(url.path)
        params = parse_qs(url.query)

        if path.startswith('/api/'):
            status, payload = self._dispatch('GET', path, params)
            return self._json(payload, status)
        if path.startswith('/covers/'):
            return self._serve_under(self.covers_dir, path[len('/covers/'):],
                                     cache=True, missing='no cover')
        if path.startswith('/media/'):
            return self._serve_media(path[len('/media/'):])
        if path.startswith('/static/'):
            return self._serve_under(self.static_dir, path[len('/static/'):])
        # favicon 走根路径（浏览器不读 <link> 时会直接请求 /favicon.ico），
        # 且给一天缓存——图标基本不变，没必要每次导航都重新拉
        if path in ('/favicon.ico', '/favicon.svg'):
            return self._serve_under(self.static_dir, path.lstrip('/'),
                                     cache=True, missing='not found')
        if path in ('/', '/index.html'):
            return self._file(self.static_dir / 'index.html')
        self._text(404, 'not found')

    def do_POST(self):
        self._write_request('POST')

    def do_DELETE(self):
        self._write_request('DELETE')

    def _write_request(self, method):
        path = unquote(urlparse(self._decoded_path()).path)
        try:
            body = self._read_body()
        except PayloadTooLarge as exc:
            # 体还没读完就回响应，必须断开连接，否则残留字节会被当成下一个请求
            self.close_connection = True
            return self._send(413, json.dumps({'error': str(exc)}).encode('utf-8'),
                              extra={'Connection': 'close'})
        except BadRequest as exc:
            return self._json({'error': '请求体解析失败：%s' % exc}, 400)
        status, payload = self._dispatch(method, path, {}, body)
        self._json(payload, status)

    def _serve_under(self, root, rel, cache=False, missing='not found'):
        """静态文件 / 封面：只允许 root 之下的文件，挡掉目录穿越。"""
        target = resolve_under(root, rel)
        if target is None:
            return self._text(404, missing)
        self._file(target, cache=cache)

    def _serve_media(self, rel):
        """本地视频文件：`/media/<video_file.id>`。

        只按 id 去库里取行再拼路径 —— 客户端给不了任意路径，路径穿越在
        video.locate 里另有一层校验（root 之下 + 不含 `..`）。
        """
        try:
            file_id = int(str(rel).strip('/').split('/')[0])
        except (TypeError, ValueError):
            return self._text(404, 'not found')
        row = self.store.video.file(file_id)
        if row is None:
            return self._text(404, 'not found')
        path, status = video.locate(row['root'], row['rel_path'])
        if path is None:
            return self._text(404, {
                video.LOCATE_UNLOCATABLE: 'file not locatable from server '
                                          '(scanned via browser folder picker)',
                video.LOCATE_MISSING: 'file missing on disk',
                video.LOCATE_UNSAFE: 'unsafe path',
            }.get(status, 'not found'))
        self._stream(path)


def make_server(store, host=config.DEFAULT_HOST, port=config.DEFAULT_PORT,
                static_dir=config.STATIC_DIR, covers_dir=None, quiet=False):
    """建一个可直接 serve_forever() 的服务器。

    通过派生子类注入依赖，而不是改 Handler 的类属性 —— 测试里同时起多个
    服务器时不会互相污染。quiet=True 关掉请求日志（测试用）。
    """
    handler_cls = type('BoundHandler', (Handler,), {
        'store': store,
        'static_dir': Path(static_dir),
        'covers_dir': Path(covers_dir) if covers_dir else None,
        'quiet': quiet,
    })
    return ThreadingHTTPServer((host, port), handler_cls)
