"""短剧数据展示站（默认零第三方依赖，标准库实现）。

    python web/server.py                 # 直接跑（数据从 output/ 读）
    python -m web.server --port 9000     # 也可以在仓库根这样跑

模块分工（自下而上，箭头表示依赖方向）::

    config   路径、常量、MIME、数据库位置、评论配置      （无依赖）
    dbio     数据库连接：按 config 建配置、连接包装、DbError（只依赖 config）
    schema   建表 DDL（SQLite）                          （只依赖 dbio）
    sources  读/写文件：jsonl、tag_edits、favorites      （只依赖 config）
    model    归一化、合并、列表字段                      （只依赖 config）
    dataset  load()：把 sources 读到的数据拼成数据集      （sources + model）
    queries  筛选、排序、分页（纯函数）                   （不依赖 HTTP）
    ed2k     ED2K 链接解析（纯函数）                      （只依赖 config）
    ed2kdb   ED2K 链接库读写                              （ed2k + dbio + schema）
    video    视频文件名解析（纯函数）                      （只依赖 config）
    videodb  本地视频文件库读写                            （video + dbio + schema）
    giscus   评论（giscus）配置读取（纯函数）              （只依赖 config）
    store    持有数据集、热重载、写标签编辑 / 收藏        （dataset + ed2kdb + videodb）
    api      路由表 + 处理函数，返回 (状态码, JSON)        （queries + store）
    httpd    http.server 适配层：静态文件、封面、日志      （api）
    server   CLI 入口                                  （httpd）

关键约束：

1. **api 及其以下都不 import http.server**，只吃 dict / 查询参数。
   所以加接口、改筛选逻辑都能脱离 HTTP 直接单测（见 tests/test_web_*.py）。
2. **除了 dbio / schema，任何模块都不出现 `sqlite3`**。
   SQL 一律用 `?` 占位符，走 `conn.execute(sql, params)`。
"""

__version__ = '1.1.0'
