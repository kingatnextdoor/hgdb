# 展示站

把 `output/` 里的抓取结果变成可浏览、可筛选、可标注的页面，另带两个人工库：
**ED2K 链接库**（上传 TXT）和**本地视频库**（扫描目录 + 刮削匹配 + 在线播放）。

三条设计取向，所有取舍都从这里来：

1. **零第三方依赖**：只用标准库 `http.server` + SQLite，clone 下来就能跑。
2. **业务不碰 HTTP**：路由、筛选、合并全是纯函数，能脱离服务器单测。
3. **前端无构建**：原生 ES module + 原生 CSS，没有 npm，改完刷新就生效。

配置**只有一个来源**：`web/config.py` 里的常量。
没有 `.env`，不读环境变量。改完 `python webctl.py restart`。

---

## 目录与分层

```
web/
├── server.py       入口：argparse → Store → httpd.make_server().serve_forever()
├── config.py       路径 / 端口 / 分页上限 / 文件名 / MIME / 评论（唯一配置来源）
├── dbio.py         数据库连接层：按 config 建配置、连接包装、DbError
├── schema.py       建表 DDL + 幂等加列
├── sources.py      读写文件：jsonl、tag_edits.json、favorites.json
├── model.py        归一化、合并规则、列表字段白名单
├── dataset.py      load()：合并 jsonl → 归一化 → 套人工标签/收藏/标记 → 统计
├── queries.py      筛选 / 排序 / 分页（纯函数）
├── ed2k.py         ED2K 链接解析：链接、剧名、集数、编码回退、剧名归一化
├── ed2kdb.py       ED2K 链接库：导入去重 / 查询 / 按剧名找资源 / 删除 / 导出
├── video.py        视频文件名解析、路径安全校验（纯函数）
├── videodb.py      本地视频库：目录扫描 / 导入去重 / 刮削结果落库
├── scrape.py       刮削匹配引擎（纯函数，四级降级）
├── multipart.py    multipart/form-data 解析（标准库 cgi 在 3.13 已移除）
├── store.py        内存数据集 + 热重载 + 标签/收藏写入 + 挂两个仓库
├── api.py          路由表 + 处理函数
├── httpd.py        HTTP 适配：静态文件、封面、视频流、请求体、错误兜底
└── static/         index.html + styles.css(@import css/) + js/*.js + 图标
```

依赖方向单向（箭头 = 依赖）：

```
server → httpd → api → queries
            │      ├→ store → dataset → sources → dbio → schema
            │      │        │         └→ model
            │      │        └→ ed2kdb → ed2k
            │      │        └→ videodb → video
            │      └→ giscus / scrape
            └→ multipart
```

两条硬约束：

- **`api` 及其以下不 import `http.server`。** 想验一条路由，直接
  `api.dispatch(...)`，不用起服务器。
- **除了 `dbio` / `schema`，任何模块不出现 `sqlite3`。** SQL 一律 `?` 占位符。

---

## 一次请求

以 `GET /api/dramas?q=都市&tag=现代&page=2` 为例：

1. `httpd.Handler.do_GET` 解出路径与查询串（中文走 `_decoded_path()`，见「常见坑」）
2. `api.dispatch` 按 `ROUTES` 里的正则匹配到 `dramas()`
3. `store.get()` 检查数据源签名——`output/` 下 jsonl / 两个 sqlite / 人工文件的
   **最大 mtime + 文件数**。变了就重新 `dataset.load()`，没变直接复用内存那份
4. `queries.filter_sort()` 过滤排序，`parse_paging` / `paginate` 切页
5. `model.list_payload()` 裁掉 `detail` 等大字段，返回 `{total, page, size, pages, items}`

所以采集工具跑完、链接传完、视频扫完，**都不用重启服务**，下一次请求自己会发现。

---

## 数据来源

| 来源 | 作用 | 谁写 |
|---|---|---|
| `output/duanju_*.jsonl` | 剧集数据。按 mtime 从旧到新合并，新覆盖旧、空值不覆盖 | 采集工具 / 你 |
| `output/tag_edits.json` | 人工改过的标签，**优先级最高** | 本站 |
| `output/favorites.json` | 收藏（`drama_id → 收藏时间`） | 本站 |
| `output/covers/` | 解密后的封面，映射成 `/covers/<文件名>` | 采集工具 |
| `ed2k.sqlite3` | ED2K 链接。只读它的剧名集合给列表打 `has_ed2k` | 本站 |
| `video.sqlite3` | 本地视频。只读 `match_status='matched'` 的 `drama_id` 打 `has_video` | 本站 |

剧集数据**只有 jsonl 一个来源**，没有数据库副本。人工数据单独存，数据重灌不丢，
`manage.py reset` 也不会带走。

字段含义与合并规则见 [data.md](data.md)。

### `dataset.load()` 的顺序不能换

```
jsonl 逐条合并（新覆盖旧、空值不覆盖）
  → 归一化（jsonl 有记录就展示，不读任何状态）
  → 套人工标签编辑（命中就整覆盖，空列表也是合法编辑）
  → 套人工收藏（只认数据集里有的剧）
  → 打 has_video / has_ed2k
  → 统计 + facets
```

收藏「只认数据集里有的剧」是有意的：某部剧的记录被清掉后，`favorites.json` 那一条
仍然保留（用户没取消过），但页面上本来就没有它——统计和筛选必须按现有剧算，
否则会出现「收藏 12 部，筛出来只有 9 部」。

---

## 数据层（SQLite）

两个文件，都跟着 `output/` 走：

| 文件 | 表 | 谁读写 |
|---|---|---|
| `ed2k.sqlite3` | `ed2k_batch` `ed2k_link` | 本站 |
| `video.sqlite3` | `video_batch` `video_file` `scrape_batch` | 本站 |

想整体挪位置（换盘、备份）就改 `config.DB_SQLITE = './data/duanju.sqlite3'`，
两个库会合并到这一个文件。改完要重启。

### 加表 / 加字段

`CREATE TABLE IF NOT EXISTS` **不会给已存在的表加列**。给老库补字段用幂等 ALTER：

```sql
ALTER TABLE ed2k_link ADD COLUMN note TEXT NOT NULL DEFAULT '';
```

代码里的做法见 `schema._upgrade_ed2k_link()` / `_upgrade_video_file()`：
先 `PRAGMA table_info` 查已有列，缺哪列补哪列，`ensure_*()` 启动时跑一次。
**不要靠重建库来加字段**——那会把用户上传的链接和扫描记录清掉。

---

## 查询参数

`GET /api/dramas`：

| 参数 | 说明 |
|---|---|
| `q` | 关键词，匹配剧名 / ID / 标签 / 作者 / 简介（不区分 ASCII 大小写） |
| `tag` | 标签，逗号分隔，**多个是同时满足**（交集） |
| `category` | 分类名，精确匹配 |
| `only` | 开关式多选，逗号分隔，**交集**：`new` `original` `cover` `detail` `fav` `video` `ed2k` |
| `min_score` | 最低评分 |
| `sort` | `update`(默认) / `score` / `episode` / `views` / `title` / `added` / `changed` / `faved` |
| `order` | `desc`(默认) / `asc` |
| `page` / `size` | 页码 / 每页条数（默认 24，上限 200） |

`only` 的语义值得单独记：**点得越多范围越窄**。未知取值只跳过自己，不会让整条
筛选失效。`sort` 传未知值退回 `update`。

`only=video` / `only=ed2k` 读的是数据集上的标记，不是 jsonl 字段：

- `has_video` ← `videodb.matched_drama_ids()`（只认 `match_status='matched'`）
- `has_ed2k` ← `ed2kdb.titles()` + 与详情页**同一套**关键词降级比对

所以「详情页看得到资源」和「列表能筛出来」永远是同一批剧。两个库的 mtime 都在
热重载签名里，传完 / 删完立刻生效。

---

## 接口表

处理函数签名统一 `(store, match, params, body) -> (状态码, 可 JSON 化对象)`。

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/api/config` | — | 站点配置，目前只有评论：`{"giscus": {...}}`，没配全就是 `null` |
| GET | `/api/stats` | — | 统计 + 当前库位置：`{"stats": {...}, "db": "sqlite（…）"}` |
| GET | `/api/facets` | — | 分类 / 标签计数（顶部标签栏用） |
| GET | `/api/dramas` | 见上一节 | 列表，只返回列表字段 |
| GET | `/api/drama/<id>` | — | 详情 + `related`（最多 12 部） |
| GET | `/api/drama/<id>/ed2k` | — | 按剧名找链接：`keyword` `matched_by` `total` `bytes` `items` |
| GET | `/api/drama/<id>/videos` | — | 匹配到这部剧的视频，每条带 `playable` `reason` `url` |
| GET | `/api/reload` | — | 强制重读 `output/` |
| POST | `/api/drama/<id>/tags` | `{"tags": ["…"]}` | 覆盖式保存标签（增删改名都走它） |
| DELETE | `/api/drama/<id>/tags` | — | 撤销人工编辑，恢复抓取值 |
| POST | `/api/drama/<id>/favorite` | — | 收藏（幂等，保留首次收藏时间） |
| DELETE | `/api/drama/<id>/favorite` | — | 取消收藏（本来没收藏也算成功） |
| POST | `/api/ed2k/upload` | `multipart` 字段 `file`；或 `text/plain` + `X-Filename` 头 | 导入链接，返回本次统计 |
| GET | `/api/ed2k` | `q` `page` `size` | 链接分页列表 + `stats` |
| GET | `/api/ed2k/stats` | — | 条数 / 总体积 / 剧名数 / 最近导入时间 |
| GET | `/api/ed2k/batches` | — | 最近 10 次导入记录 |
| GET | `/api/ed2k/export` | `q` | 导出纯文本（一行一条，可直接喂下载器） |
| DELETE | `/api/ed2k/link/<hash>` | — | 删除单条 |
| DELETE | `/api/ed2k/batch/<id>` | — | 撤销一次上传 |
| DELETE | `/api/ed2k/all` | — | 清空链接库 |
| POST | `/api/video/scan` | `{"path": "O:\\video"}`（前端只发这种）；也兼容 `{"folder": "...", "files": [...]}` | 扫描入库，返回本次统计 |
| GET | `/api/video` | `q` `page` `size` | 文件分页列表 + `stats`（含扩展名分布） |
| GET | `/api/video/stats` | — | 条数 / 体积 / 文件夹数 / 最近扫描时间 |
| GET | `/api/video/batches` | — | 最近 10 次扫描 |
| DELETE | `/api/video/file/<id>` | — | 删除单条文件记录 |
| DELETE | `/api/video/batch/<id>` | — | 撤销一次扫描（批次行保留） |
| DELETE | `/api/video/all` | — | 清空文件库 |
| POST | `/api/scrape/scan` | `{"root": "video", "force": false}` | 刮削预览（dry-run），逐文件给建议 |
| POST | `/api/scrape/apply` | 同上 | 执行刮削：写 `video_file` + 记 `scrape_batch` |
| POST | `/api/scrape/match` | `{"file_id": 3, "status": "matched", "drama_id": "398"}` | 人工修正（也认 `ignored` / `unmatched`） |
| GET | `/api/scrape/status` | — | 全库匹配统计 + 每个目录的分布 + 最近 10 次 |
| GET | `/api/scrape/report/<id>` | — | 一条刮削批次记录 |
| GET | `/covers/<文件名>` | — | 封面（带 1 天缓存） |
| GET | `/media/<file_id>` | `Range` 头可选 | 视频流，支持单段 Range（206 / 416） |
| GET | `/static/…` | — | 前端资源，支持子目录，禁止目录穿越 |
| GET | `/favicon.ico` `/favicon.svg` | — | 图标（带 1 天缓存；不读 `<link>` 的浏览器会直接请求） |

**没有 `status` 参数**：数据里没有「在架 / 下架」，jsonl 有记录就展示。

---

## ED2K 链接库

首页顶栏「ED2K 链接库」→ 拖入或选择一个 TXT。一次导入：

1. `httpd._read_body()` 解析 multipart（上限 16MB，超了 413）
2. `ed2k.decode_bytes()` 猜编码：UTF-8（含 BOM）→ GB18030
3. `ed2k.parse_links()` 逐行提取 `ed2k://|file|名|字节|HASH|/`，从文件名拆出
   「剧名 + 集数」（去 `www.xx@` 水印前缀、扩展名；认 `-01`、`-01-10 合集`、
   `第3集`、`EP07`）
4. `ed2kdb.import_text()` 写 `ed2k_batch` + `ed2k_link`（`hash` 唯一，冲突静默跳过，
   所以重复上传同一个文件不会产生重复行）
5. **剧名比对**：拆出的剧名与站内剧名各归一化一遍
   （`ed2k.normalize_title()`：去书名号 / 括号备注 / 季数 / 续集号 / 营销后缀），
   相等就把 `in_drama` 写 1。站内剧名由 `Store.drama_titles()` 注入，
   来源就是内存数据集——与页面展示同源
6. 返回统计：解析 / 新增 / 重复 / 无效行 / 忽略行 / 不在短剧表 / 库内总数

`in_drama` 不只导入时算：`Store` 每次重载数据集后调 `sync_drama_flags()`，
站内新增了剧，老链接的标记跟着翻正（剧名集合没变就什么都不做）。

「无效行」只统计**含 `ed2k://` 但格式不对**的行；标题行、空行算「忽略」，
否则每个 TXT 的说明文字都会被报成错误。

### 详情页联动

详情弹层的「ED2K 资源」区块拿这部剧的 `title` 去库里搜，按集号列出（集号 + 文件名 + 大小），
可单条复制、一次复制全部。两边名字很少完全一致，所以关键词**逐档降级、命中即停**
（`ed2k.title_keywords()`）：

| 档位 | 例子 |
|---|---|
| 完整剧名 | `示例剧` |
| 去掉季数 / 营销后缀 | `示例剧 第二季` → `示例剧` |
| 剧名片段 | `甲 乙丙丁` → `乙丙丁`（按标点空格切出的最长片段） |

只匹配 `title`（文件名拆出的剧名），不匹配 HASH / 完整文件名——那是管理表搜索的语义。
降级过的话区块标题会写「（按『示例剧』匹配）」，点「在链接库中搜索」用的也是同一个词。

> 前端用 `ed2k:open` 事件通信：`detail.js` 不 import `ed2k.js`，详情页只负责
> 「我要按这个词搜」，怎么开弹层由 `ed2k.js` 自己决定。

---

## 本地视频库

首页顶栏「本地视频」→ 填**服务器上的目录路径**（如 `O:\video`）→ 扫描。
`os.walk` 递归该目录及子目录，只记文件名、相对路径、大小，**不读文件内容**。

入库规则：

1. `config.VIDEO_EXTS` 之外的扩展名（`.txt` / `.jpg` / `.nfo` / 无扩展名）跳过，计入 `skipped`
2. 唯一键是 **(root, rel_path)**，重复扫同一个目录是幂等的；`created_at` 不会被刷新
3. `video_batch` 每次扫描一行，可整批撤销；撤销只删文件记录，批次行保留
4. Windows 拿到的相对路径带反斜杠，入库前统一归一成正斜杠

**扫描完自动刮削**：点「扫描该目录」= 扫描入库 + `POST /api/scrape/apply` 连做，
结果条直接给出匹配 / 待确认 / 未匹配的个数。刮削失败不影响已入库的记录。

> 为什么只有「服务器路径」一种入口：浏览器出于安全不给绝对路径，用旧版
> `webkitdirectory` 扫进来的记录 root 只有文件夹名，详情页定位不到文件，播放是灰条。
> 接口仍接受 `{"folder", "files"}` 清单形式（脚本用得上），只是前端不再提供。
>
> JSON 请求体上限是 `MAX_JSON_BYTES`（8MB）——一次提交几万个文件名，
> `MAX_BODY_BYTES` 的 1MB 不够用。

---

## 刮削匹配

把视频文件名对到本地剧库上——**对内匹配，零网络请求**：元数据全部来自已经抓回来的剧，
不请求豆瓣 / TMDB 之类的任何外部源。

手工流程刻意拆成两步：预览（`/api/scrape/scan`，dry-run 只算不写）→ 执行
（`/api/scrape/apply`，写库 + 记 `scrape_batch`）。前端渲染成确认表，每行可改选、忽略。

引擎在 `web/scrape.py`（纯函数），四级降级、命中即停：

| 级别 | 方法 | 分数 | 结果 |
|---|---|---|---|
| exact | 清洗后与剧名相等（lower） | 0.95 | 自动 matched |
| contains | 一方包含另一方（都 ≥ 2 字） | 0.80 | 自动 matched |
| fuzzy | difflib 相似度 ≥ 0.85 | 相似度（封顶 0.85） | 自动 matched |
| fuzzy | 相似度 0.60 ~ 0.85 | 相似度 | pending，人工确认 |
| — | 都没戏 | 0 | unmatched |

清洗会剥掉站点水印（`www.98T.la@`）、画质 / 语言噪音词（4K / 1080P / 国语 / 中字…）、
括号备注和集数尾巴；集数解析复用 `ed2k.split_name()`，结果存进 `video_file.episode`。

三条人工规则比引擎本身更重要：

- **`ignored` 是粘滞的**：用户点过「忽略」的文件，连 `force=true` 也不碰
- **人工确认过的匹配默认保留**：手选的剧 `match_score` 记成 `1.0`（自动最高 0.95），
  不带 `force` 时原样留着，否则每次自动刮削都会把人的结论覆盖回去
- **同名剧不替用户做主**：精确匹配命中多部同名剧时降级为 pending，列出候选

> 只写数据库：不会往视频目录生成 NFO / poster.jpg，磁盘上的文件一个字节都不动。

---

## 在线播放

详情弹层的「本地视频」区块列出匹配到的文件（只算 `matched`，按 `episode` 排，
没集号的排最后），点一行在弹层内 `<video>` 里播，再点同一下暂停 / 继续。

链路：`GET /api/drama/<id>/videos` → 每条的 `url` 是 `/media/<video_file.id>` →
httpd 按 id 取行、拼路径、流式发字节。

**能不能播取决于入库时的 root**：

| 入库方式 | root | 结果 |
|---|---|---|
| 填服务器路径扫描 | 绝对路径 `O:\video` | 能播 |
| 旧版用浏览器选文件夹 | 只有文件夹名 `video` | 灰条「服务端定位不到」 |

另外两种灰条：文件被挪走 / 外接盘没插 →「文件不在了」；`rel_path` 含 `..` 或
解析后跑出 root →「路径不安全」（`video.locate()` 在播放前就判，不用等点开才 404）。

**流式与 Range**：256KB 分块边读边发（几个 G 不能整读进内存），支持单段 `Range`，
命中回 `206 + Content-Range`，越界 `416`，`HEAD` 只回头。客户端 seek 时主动断连
属正常，不记错误日志。MIME 走 `config.VIDEO_MIME`（Windows 的 `mimetypes`
常常查不到 `.mkv`）。

安全边界：客户端只能给 id，给不了路径；`video.locate()` 再做一层「必须在 root 之下
且不含 `..`」的校验。

---

## 评论（giscus）

详情弹层最底部的评论区块用 [giscus](https://github.com/giscus/giscus)：评论存在
GitHub Discussions 里，本站不建表、不做用户系统，只嵌一个 iframe。

配置在 `web/config.py`：

```python
GISCUS_REPO = 'owner/repo'          # 公开仓库，装过 giscus app，且开了 Discussions
GISCUS_REPO_ID = 'R_kgDOxxxxxxx'
GISCUS_CATEGORY = 'Comments'
GISCUS_CATEGORY_ID = 'DIC_kwDOxxxxxxx'
# 可选：GISCUS_THEME / GISCUS_LANG / GISCUS_MAPPING，留空用默认
```

四个值都在 [giscus.app](https://giscus.app) 的配置页生成。**必填三项**
（`repo` / `repo_id` / `category_id`）缺任意一个，`/api/config` 就返回
`{"giscus": null}`，前端连 giscus.app 的脚本都不加载——离线环境不会多出一个外部请求。

**每部剧一条讨论**：详情页是弹层、URL 永远是 `/`，所以 `pathname` / `url` 映射
会让所有剧挤到同一条讨论下。固定用 `mapping=specific` + `term`：

```
term = drama:<drama_id>        # 例：drama:398
```

只用 id、不掺剧名：改名不会「换一条讨论」（换了 term 老评论就找不回来）。
前缀由 `giscus.TERM_PREFIX` 统一下发（配置里的 `term_prefix`），前后端不各写一份。

**登录回跳必须自己接**（踩过的坑）：giscus 的「使用 GitHub 登录」是
`<a target="_top">`，点下去整页跳 GitHub，授权完 302 回：

```
http://127.0.0.1:8765/?giscus=<加密后的 token>
```

这个参数本来是给页面上的 `client.js` 消费的，但我们只在打开详情弹层时才注入它，
回跳落地时弹层是关着的，没人接 → 表现为「授权成功，评论区仍显示『使用 GitHub 登录』」。
所以 `comment.js` 提供 `consumeLoginReturn()`，由 `main.js` 在 `init()` 最前面调用：
按 `JSON.stringify` 写进 localStorage（必须与 client.js 同键名、同序列化）→
`replaceState` 洗掉地址栏 token → 返回跳走前正在看的剧并自动重新 `openDetail()`。

> 自建部署注意：若仓库里放了 `giscus.json` 的 `origins` 白名单，记得把本站域名加进去。

---

## 前端

```
main.js       绑事件 + 首次加载（唯一操作 DOM 事件的地方）
 ├── state.js    筛选条件对象 + URL <-> 条件
 ├── api.js      fetch 封装（页面里不要直接 fetch）
 ├── stats.js    顶部统计 chip
 ├── tagbar.js / onlybar.js   标签条、开关条
 ├── list.js     卡片墙 + 分页（不绑卡片点击，避免反向依赖 detail）
 ├── detail.js   详情弹层 + 标签编辑 + 本地视频播放 + 这部剧的 ED2K 资源
 ├── comment.js  giscus iframe 的挂载 / 卸载
 ├── ed2k.js     链接库弹层
 ├── video.js    视频库弹层
 └── scrape.js   刮削弹层
```

三个刻意的设计：

- **卡片点击用事件委托**（`main.js` 绑在 `#grid` 上）：`list.js` 不用 import
  `detail.js`，依赖图无环，列表重绘多少次都不用重绑。
- **筛选条件全在 URL 上**：复制链接就能分享同一份筛选结果。
- **两个人工库各自成支线**：`ed2k.js` / `video.js` 不 import `detail.js`，
  详情页要「带着剧名打开某库」只发 `ed2k:open` / `video:open` 事件，反方向无依赖。

---

## 怎么加东西

### 加一个筛选条件

1. `queries.py`：`filter_sort` 里读参数过滤，或在 `ONLY_FILTERS` 加一项
2. `static/index.html`：加 `<select id="…">`，或在 `#onlybar` 加
   `<button class="tag" data-only="xxx">`
3. `static/js/main.js`：`select` 的 id 加进 `bindFilters`（`#onlybar` 不用，`onlybar.js` 自己绑）
4. `static/js/state.js`：`state` 加字段，`readUrl` / `writeUrl` / `queryString` 同步
5. `tests/test_web_queries.py` 补用例

### 加一个字段

1. 采集侧在导出的 jsonl 里带上这个字段（字段清单见 [data.md](data.md)）
2. `web/model.py`：`normalize()` 加映射；要出现在列表就加进 `LIST_FIELDS`
3. 前端在 `list.js:cardHtml` 或 `detail.js:factsHtml` 里展示

### 加一个接口

```python
def my_endpoint(store, match, params, body):
    return 200, {'hello': 'world'}

ROUTES = (
    ...,
    ('GET', re.compile(r'^/api/hello$'), my_endpoint),
)
```

返回 `(状态码, 可 JSON 化对象)` 即可。异常不用自己兜——`httpd._dispatch` 会把未捕获
异常转成 500 + 原因。传文件用 `httpd._read_body()` 解析好的 `body`（文件字段是
`{'filename','content_type','data'}`），上限 `config.MAX_UPLOAD_BYTES` 由 httpd 统一拦。

### 加一个页面板块

`index.html` 加带 id 的容器 → `js/` 下新建模块导出 `render()` → `main.js` import 并调用。

### 换主题

`css/base.css` 的 `:root` 变量（`--bg` `--panel` `--primary` `--text` `--muted`）。
其他文件不写死颜色，改一处全站生效。

---

## 常见坑

**中文查询串**：`http.server` 拿到的 `self.path` 是按 ISO-8859-1 解码的。浏览器会
百分号编码所以没事，但 curl / 手敲地址可能直接发原始字节（Windows 上还是 GBK）。
`httpd._decoded_path()` 先还原成字节再依次试 UTF-8 / GBK——缺了这步中文搜索恒为 0。

**ES module 必须走 http**：`<script type="module">` 在 `file://` 下会被 CORS 拒绝，
只能通过服务访问。

**本机代理**：shell 里有 `http_proxy` 指向本地代理时，访问 `127.0.0.1` 会被劫持
（表现为 502 或空响应）。测试脚本用 `build_opener(ProxyHandler({}))`，
命令行用 `curl --noproxy "*"`。

**`tags_edited`**：老数据可能是 `null`，现在统一给 `false`，前端一律按真值判断。

**multipart**：Python 3.13 起标准库 `cgi` 被移除，`cgi.FieldStorage` 直接 ImportError。
`web/multipart.py` 是最小实现，只覆盖浏览器上传用得到的那部分。要做「拖整个文件夹」
别在这上面加，换框架更划算。

**上传的中文文件名**：multipart header 按 latin-1 解码，浏览器塞进去的是 UTF-8 字节。
不还原会看到 `æ ·æ¬.txt`；`multipart._decode_filename()` 负责这步。

**`SUM(BIGINT)` 返回浮点**：SQLite 的 `SUM` 是 float，统计 SQL 统一
`CAST(... AS BIGINT)`，否则 JSON 里会出现 `3.2680000000000002e+03`。

**搜索的大小写**：SQLite 的 `LIKE` 对 ASCII 天然不敏感，中文没有大小写，
所以直接用 `LIKE` 就够，不需要 `lower()`。
