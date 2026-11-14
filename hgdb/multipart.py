# -*- coding: utf-8 -*-
"""multipart/form-data 解析（标准库 `cgi` 在 Python 3.13 已经移除）。

只实现浏览器上传文件用到的最小语义：

  · 按 boundary 切段
  · 每段读 Content-Disposition 的 name / filename
  · 文件段保留原始字节，普通字段解成字符串

不做嵌套 multipart/mixed、Content-Transfer-Encoding —— 浏览器不会发这些，
真要做（拖整个文件夹上传）就是另一个量级的东西了，不如换框架。

为什么不用第三方库：整个展示站的卖点是「零依赖、clone 就能跑」，为了一个
上传接口装 python-multipart 不值得。这段代码只有几十行，且有 test_ed2k.py 兜着。
"""
from urllib.parse import unquote

MAX_PARTS = 64          # 一个表单里最多几个字段/文件
_SEPARATORS = (b'\r\n\r\n', b'\n\n')


def boundary_of(content_type):
    """从 `multipart/form-data; boundary=----WebKitFormBoundaryxxx` 里取 boundary。"""
    for chunk in (content_type or '').split(';')[1:]:
        key, _, value = chunk.partition('=')
        if key.strip().lower() != 'boundary':
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        return value.encode('latin-1', 'replace')
    return None


def _params(header):
    """解析 `form-data; name="file"; filename="a.txt"` 里的参数。"""
    params = {}
    for chunk in header.split(';')[1:]:
        key, _, value = chunk.partition('=')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        params[key.strip().lower()] = value
    return params


def _decode_filename(raw):
    """浏览器把文件名按 UTF-8 直接塞进 header，但 header 是按 latin-1 解的。

    先还原成字节再按 UTF-8 解，中文名才不会变成 `æ ·æ¬.txt`。
    Windows 上的 curl 用的是系统代码页（GBK），所以 UTF-8 失败后再试 GB18030 ——
    否则 `样本.txt` 会存成 `Á´½ÓÑù±¾.txt` 这种乱码进库。
    """
    try:
        data = raw.encode('latin-1')
    except UnicodeEncodeError:
        return raw
    for encoding in ('utf-8', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw


def _disposition(header):
    """返回 (字段名, 文件名或 None)。filename* 优先（RFC 5987）。"""
    params = _params(header)
    name = params.get('name')
    if 'filename*' in params:
        value = params['filename*']
        _, _, encoded = value.partition("''")          # UTF-8''%E4%B8%AD.txt
        return name, unquote(encoded or value)
    if 'filename' in params:
        return name, _decode_filename(params['filename'])
    return name, None


def parse(body, content_type):
    """把请求体解析成 `{字段名: 值}`。

    普通字段的值是 str，文件字段的值是
    `{'filename': str, 'content_type': str, 'data': bytes}`。
    """
    boundary = boundary_of(content_type)
    if not boundary:
        raise ValueError('缺少 multipart boundary')

    fields = {}
    delimiter = b'--' + boundary
    for chunk in body.split(delimiter)[1:]:
        if chunk.startswith(b'--'):        # 结束标记 --boundary--
            break
        if len(fields) >= MAX_PARTS:
            raise ValueError('表单字段过多')

        if chunk.startswith(b'\r\n'):
            chunk = chunk[2:]
        if chunk.endswith(b'\r\n'):
            chunk = chunk[:-2]

        head, separator, data = chunk.partition(_SEPARATORS[0])
        if not separator:
            head, separator, data = chunk.partition(_SEPARATORS[1])
        if not separator:
            continue

        headers = {}
        for line in head.decode('latin-1', 'replace').splitlines():
            key, found, value = line.partition(':')
            if found:
                headers[key.strip().lower()] = value.strip()

        name, filename = _disposition(headers.get('content-disposition', ''))
        if not name:
            continue

        if filename is None:
            fields[name] = data.decode('utf-8', 'replace')
        else:
            fields[name] = {
                'filename': filename,
                'content_type': headers.get('content-type', ''),
                'data': data,
            }
    return fields
