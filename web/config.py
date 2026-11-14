# -*- coding: utf-8 -*-
"""展示站的全部配置：路径、端口、分页上限、数据库位置、评论。

**这里是唯一的配置来源。** 没有 .env、不读环境变量、不读配置文件——要改什么就
改这里的常量，然后 `python webctl.py restart`（端口/数据库/评论都要重启才生效）。
"""
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent        # web/
ROOT_DIR = PKG_DIR.parent                        # 仓库根
STATIC_DIR = PKG_DIR / 'static'
DEFAULT_OUTPUT = ROOT_DIR / 'output'

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8765

# 分页
DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 200

# output/ 里的文件名约定。这些名字由**数据契约**规定，见 docs/data.md：
# 采集工具（独立仓库）负责写，本仓库只读。
EXPORT_GLOB = 'duanju_*.jsonl'
COVERS_DIR = 'covers'
TAG_EDITS_FILE = 'tag_edits.json'
# 人工收藏（drama_id -> 收藏时间）。跟 tag_edits.json 同性质：展示站自己写的、
# 重抓 / manage.py reset 都不该带走的小文件
FAVORITES_FILE = 'favorites.json'
# ED2K 链接库单独一个 sqlite：重抓（清 output 里的数据）时不能把人工上传的链接一起带走
ED2K_DB = 'ed2k.sqlite3'

# ---- 数据契约版本 ----
# 本仓库能读懂的 jsonl 契约版本（契约全文见 docs/data.md）。记录里的
# schema_version 比它高只告警、不拒绝：陌生字段本来就忽略，多显示一部分总比白屏好。
SCHEMA_VERSION = 1

# ---- 数据库位置 ----
# 留空 = 每个库一个文件、跟着 output 目录走（ed2k.sqlite3 / video.sqlite3）；
# 填路径 = 两个库合并到这一个文件里（相对路径按仓库根解析，换盘/备份用）。
# 改完要重启服务才生效：python webctl.py restart
DB_SQLITE = ''

# ---- 站点身份 ----
# 站名与页脚外链。想换成自己的站，只改这三行，不用动 HTML。
# 页脚 SITE_LINKS 留空就不显示页脚整块；每项是 (显示文字, 链接) 的二元组。
SITE_NAME = '短剧资料库'
SITE_TAGLINE = '本地短剧资料库'
SITE_LINKS = (
    # ('GitHub 仓库', 'https://github.com/you/your-repo'),
    # ('反馈', 'https://github.com/you/your-repo/issues'),
)

# ---- 评论（giscus：评论存在 GitHub Discussions 里）----
# 四个值都从 https://giscus.app 的配置页生成（选好仓库和分类，页面底部就是带参数的
# <script> 片段）。仓库要公开、装过 giscus app、并且开了 Discussions。
# 必填三项（repo / repo_id / category_id）缺任一项就整块关掉：前端不加载任何外部脚本。
# 开源版本默认全部留空 —— 填上你自己的，别把别人的仓库写在这里。
GISCUS_REPO = ''
GISCUS_REPO_ID = ''
GISCUS_CATEGORY = 'General'
GISCUS_CATEGORY_ID = ''
# 可选：主题 / 界面语言 / 讨论映射方式。留空 = 用 web/giscus.py 里的默认值
#（dark_dimmed / zh-CN / specific）。别把 mapping 改成 pathname 或 url —— 详情页是
# 弹层、URL 不变，会把所有剧的评论混到一条讨论下面。
GISCUS_THEME = ''
GISCUS_LANG = ''
GISCUS_MAPPING = ''

# 标签人工编辑的限制
MAX_BODY_BYTES = 1 << 20     # 1MB，超过直接当坏请求
TAG_MAX_LEN = 30
TAG_MAX_COUNT = 100

# ED2K 上传的限制
MAX_UPLOAD_BYTES = 16 << 20  # 16MB 的 TXT，约 10 万条链接
ED2K_MAX_LINKS = 200000      # 单次导入条数上限
ED2K_NAME_MAX_LEN = 400      # 原始文件名最大长度（超出按「截断 + 哈希」存）
ED2K_PAGE_SIZE = 50
ED2K_MAX_PAGE_SIZE = 500
# 详情页「这部剧的 ED2K 资源」最多列这么多条（一部剧百来集，留够余量）
ED2K_TITLE_LIMIT = 300

# 本地视频文件库（video.sqlite3，同样独立于采集工具的导出数据）
VIDEO_DB = 'video.sqlite3'
VIDEO_EXTS = frozenset((
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v',
    '.mpg', '.mpeg', '.ts', '.m2ts', '.vob', '.rmvb', '.rm', '.3gp',
))
VIDEO_MAX_FILES = 100000     # 单次扫描条数上限（目录里再多也先拦住）
VIDEO_PAGE_SIZE = 50
VIDEO_MAX_PAGE_SIZE = 500

# JSON 请求体上限：标签编辑很小，但视频库扫描可能一次提交几万个文件名
MAX_JSON_BYTES = 8 << 20

# mimetypes 在没有系统映射表时可能返回 None，这几个必须保证正确
MIME_FALLBACK = {
    '.js': 'application/javascript; charset=utf-8',
    '.mjs': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.webmanifest': 'application/manifest+json',
    '.json': 'application/json; charset=utf-8',
    '.woff2': 'font/woff2',
}

# 视频流响应用的 MIME（Windows 上 mimetypes 依赖注册表，.mkv/.rmvb 经常查不到，
# 猜错类型 Chrome 会直接不播）
VIDEO_MIME = {
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.webm': 'video/webm',
    '.mkv': 'video/x-matroska',
    '.avi': 'video/x-msvideo',
    '.mov': 'video/quicktime',
    '.wmv': 'video/x-ms-wmv',
    '.flv': 'video/x-flv',
    '.mpg': 'video/mpeg',
    '.mpeg': 'video/mpeg',
    '.vob': 'video/mpeg',
    '.ts': 'video/mp2t',
    '.m2ts': 'video/mp2t',
    '.rmvb': 'application/vnd.rn-realmedia-vbr',
    '.rm': 'application/vnd.rn-realmedia',
    '.3gp': 'video/3gpp',
}
