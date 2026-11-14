# hgdb · 短剧数据展示站

把你手上的短剧数据（一行一条的 JSONL + 一批封面）变成一个能用的本地网站：
**搜索、筛选、看详情、标标签、点收藏、管 ED2K 链接、扫本地视频并在线播放。**

跑在你自己电脑上，**零第三方依赖** —— 只用 Python 标准库（`http.server` + `sqlite3`），
clone 下来就能跑。不用 MySQL，不用 Node，不用前端构建。

---

## 30 秒看效果

```bash
git clone https://github.com/kingatnextdoor/hgdb.git
cd hgdb
python hgdbctl.py start --output sample --open
```

`sample/` 里放了一份**完全虚构**的演示数据（24 部剧 + 18 张占位封面），只为了让你在
三十秒内看到界面长什么样。看完 `python hgdbctl.py stop` 收工。

换成你自己的数据：把 `duanju_*.jsonl` 和 `covers/` 放进 `output/`，然后

```bash
python hgdbctl.py start            # 默认读 output/，默认 http://127.0.0.1:8765
```

---

## 能做什么

**列表页**
搜索（剧名 / 标签 / 作者 / 剧 ID）、分类筛选、标签栏、多个快捷开关叠加（收藏 / 有视频 /
有 ED2K / 新上架 / 原创 / 有封面 / 有详情）、多维度排序、每页 24/48/96 切换。
所有筛选条件都写在 URL 里，复制地址就能分享同一份结果，刷新也不丢。

**详情弹层**
封面、评分、集数、分类、标签、简介、作者、播放 / 点赞 / 评论数、上线时间、选集列表、
相关推荐。标签和收藏直接在里面改，改完立刻存盘。

**ED2K 链接库**
上传一个 TXT（一行一条 `ed2k://`），自动解析剧名和集数入库，重复链接自动跳过，
导入批次可撤销，对不上短剧清单的行标黄。列表页会因此多出一个「有 ED2K」筛选。

**本地视频库**
给一个文件夹路径，扫描视频文件名入库，然后把文件名刮削匹配到剧。只读文件名，
**不移动、不改名、不删除你的任何文件**。详情页里点一行就在弹层里播放，支持拖动进度条
（服务端实现了 Range）。

**评论（可选）**
详情页底部可嵌 [giscus](https://github.com/giscus/giscus)，评论存在 GitHub Discussions
里，本站不建表、不做用户系统。不配置就整块隐藏。

**其它**
深浅色主题自适应、移动端可用、中文界面、`/api/*` 全程 JSON（想接别的前端也行）。

---

## 它不做什么

这几条是刻意的，不是没做完：

- **不抓数据。** 这个仓库一行采集代码都没有。数据从哪来由你决定（手写、脚本、别的
  程序），只要满足 [docs/data.md](docs/data.md) 的契约。
- **不是多用户服务。** 没有登录、没有权限、没有账号。默认只监听 `127.0.0.1`。
- **不把剧集数据存进数据库。** jsonl 就是唯一来源，没有副本、没有缓存表。
  数据库只装人工数据（ED2K 链接、视频索引）。
- **不引入依赖。** 想加分页组件、ORM、前端框架之前，先看看能不能用标准库解决。

---

## 目录

```
hgdb/               站点本体
  server.py         请求入口 + 路由表
  httpd.py          HTTP 细节（Range、静态文件、上传、错误兜底）
  api.py            路由处理函数（每个接口一个函数）
  dataset.py        读 output/ 并合并成内存数据集
  model.py          单条记录的归一化与合并规则
  queries.py        筛选、排序、分页（纯函数）
  store.py          数据集持有者：文件变了自动重载
  ed2k.py / ed2kdb.py    链接解析 + 链接库
  video.py / videodb.py  视频扫描、刮削匹配 + 视频库
  scrape.py         「还没详情」时的补抓入口
  giscus.py         评论配置下发
  config.py         全部配置（唯一的配置来源）
  static/           前端：原生 ES module + 原生 CSS，没有构建步骤
hgdbctl.py           启停管理：start / stop / restart / status / log / open
manage.py           维护工具：stats / recent / reset
sample/             虚构演示数据（可直接跑）
docs/               文档
tests/              单测（标准库 unittest，pytest 也能跑）
```

`hgdb/` 里的代码遵守一条规矩：**业务逻辑不碰 HTTP**。筛选、排序、合并全是纯函数，
所以能脱离服务器单测，测试不需要起服务。

---

## 文档

| 文档 | 讲什么 |
|---|---|
| [docs/guide.md](docs/guide.md) | 手把手：装 Python、启动、每天怎么用、界面上每个按钮 |
| [docs/data.md](docs/data.md) | **数据契约**：字段表、合并规则、`schema_version`、封面路径 |
| [docs/hgdb.md](docs/hgdb.md) | 代码结构：加字段 / 加筛选 / 加接口怎么改 |
| [docs/ops.md](docs/ops.md) | 运维：启停、日志、备份、清空数据、排错 |

数据格式改不动的话，先看 [docs/data.md](docs/data.md) —— 那是唯一权威定义，
本文和别的文档都从属于它。

---

## 要求

- Python **3.10+**（用到 `X | None` 类型标注）
- 不需要任何第三方包；`pip install -e ".[dev]"` 只装测试和 lint 用的东西

跑测试：

```bash
python -m pytest tests/ -q        # 或 python -m unittest discover tests
```

---

## 许可

[MIT](LICENSE)。数据不在本仓库里，演示数据全部虚构。
