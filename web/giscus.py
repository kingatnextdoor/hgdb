# -*- coding: utf-8 -*-
"""giscus 评论的配置读取（GitHub Discussions 当评论库）。

为什么不让前端硬编码
--------------------
这些值都要从 https://giscus.app 的配置页生成，换仓库就得改。统一收在
`web/config.py` 里（跟数据库位置、分页上限放在一起），页面启动时用
`GET /api/config` 取一次：

    GISCUS_REPO = 'owner/repo'          # 必须是公开仓库，且开了 Discussions
    GISCUS_REPO_ID = 'R_kgDOxxxxxx'     # giscus.app 生成的 data-repo-id
    GISCUS_CATEGORY = 'Comments'        # Discussions 分类名
    GISCUS_CATEGORY_ID = 'DIC_kwDOxxx'  # 分类的 node id
    GISCUS_THEME = ''                   # 可选，留空用下面的默认值
    GISCUS_LANG = ''                    # 可选
    GISCUS_MAPPING = ''                 # 可选；留空 = specific，每部剧一个 term

必填三项（repo / repo_id / category_id）缺任意一个就返回 None ——
前端据此显示「还没配置」的说明，**不会去加载 giscus.app 的脚本**，
没配的环境里页面一个外部请求都不多。

term 为什么是 `drama:<id>`
--------------------------
详情页是弹层，URL 不变，`pathname` / `url` 两种 mapping 会把所有剧映射到同一条
讨论。用 `specific` + 我们的 term 最稳：一部剧一条讨论，剧名改了也不会串
（剧名进 term 就会因为改名而"换一条讨论"）。
"""
from web import config

# 配置常量名 -> 内部字段名。顺序也是文档里的顺序
KEYS = {
    'repo': 'GISCUS_REPO',
    'repo_id': 'GISCUS_REPO_ID',
    'category': 'GISCUS_CATEGORY',
    'category_id': 'GISCUS_CATEGORY_ID',
    'theme': 'GISCUS_THEME',
    'lang': 'GISCUS_LANG',
    'mapping': 'GISCUS_MAPPING',
}

# 缺一个就不干活：repo 用来显示来源，两个 id 是 giscus 查库的凭据
REQUIRED = ('repo', 'repo_id', 'category_id')

DEFAULT_THEME = 'dark_dimmed'   # 跟本站深底、弱对比的风格最接近
DEFAULT_LANG = 'zh-CN'
DEFAULT_MAPPING = 'specific'    # 唯一能区分「不同剧」的映射方式

TERM_PREFIX = 'drama'


def _from_config():
    """按 web/config.py 里的常量拼一份「键名 -> 值」的表。"""
    return {name: getattr(config, name, '') for name in KEYS.values()}


def settings(env=None):
    """读配置；没配全返回 None。

    env 给定时**只用它**（不读 config.py），测试靠这个隔离。
    """
    values = dict(env) if env is not None else _from_config()

    def get(key):
        return str(values.get(KEYS[key]) or '').strip()

    conf = {name: get(name) for name in KEYS}
    if not all(conf[name] for name in REQUIRED):
        return None

    conf['theme'] = conf['theme'] or DEFAULT_THEME
    conf['lang'] = conf['lang'] or DEFAULT_LANG
    conf['mapping'] = conf['mapping'] or DEFAULT_MAPPING
    # 前端拼 term 用，免得「drama」这个前缀在两边各写一份、改一处忘一处
    conf['term_prefix'] = TERM_PREFIX
    return conf


def term_for(drama_id):
    """一部剧的讨论标识。只认 id，不掺剧名 —— 改名不该换讨论。"""
    value = str(drama_id if drama_id is not None else '').strip()
    return '%s:%s' % (TERM_PREFIX, value) if value else ''


def configured(env=None):
    """给「状态接口 / 文档示例」用的一行摘要，不泄露多余信息。"""
    conf = settings(env)
    if conf is None:
        return {'enabled': False}
    return {'enabled': True, 'repo': conf['repo'],
            'category': conf['category'], 'mapping': conf['mapping']}


__all__ = ['KEYS', 'REQUIRED', 'DEFAULT_THEME', 'DEFAULT_LANG',
           'DEFAULT_MAPPING', 'TERM_PREFIX', 'settings', 'term_for',
           'configured']
