# 数据结构

`output/` 里的文件长什么样、展示站怎么读、合并时谁覆盖谁。**这是**数据契约的唯一
权威定义：任何往 `output/` 放数据的工具（本仓库之外的采集程序、脚本、手写记录）
只要满足这里的要求，展示站就能正确显示。

---

## 谁写、谁读

| 文件 | 位置 | 谁写 | 谁读 |
|---|---|---|---|
| `duanju_<时间戳>.jsonl` | `output/` | 采集工具，或你手写 | 展示站（唯一数据源） |
| `duanju_<时间戳>.csv` | `output/` | 采集工具 | 只给人看（Excel），**代码不读** |
| `covers/<文件名>` | `output/covers/` | 采集工具，或你手动放 | 展示站按 `/covers/<文件名>` 提供 |
| `tag_edits.json` | `output/` | 展示站（页面改标签） | 展示站 |
| `favorites.json` | `output/` | 展示站（页面点收藏） | 展示站 |
| `ed2k.sqlite3` | `output/` | 展示站（上传 TXT） | 展示站 |
| `video.sqlite3` | `output/` | 展示站（扫描目录、刮削） | 展示站 |
| `web.log` / `web.pid` | `output/` | `webctl.py` | `webctl.py` |

人工数据（标签、收藏、两个 sqlite 库）单独存放，**采集工具不该碰**，`manage.py reset`
也不会带走它们。这样重灌剧集数据不会冲掉你的编辑。

---

## 文件要求

- **文件名**：`duanju_` 开头、`.jsonl` 结尾。其它名字不会被读到（按 `duanju_*.jsonl` 匹配）。
- **编码**：UTF-8，带不带 BOM 都行。
- **格式**：一行一条记录，**一条记录必须在一整行里**，不能折行。
- **完整性**：坏行（漏逗号、中文引号、不是 JSON）会被**跳过**，其它行不受影响。
  没有 `drama_id` 的行同样跳过。
- **可以有多个文件**：按修改时间从旧到新依次合并，你不需要自己合并成一个。

---

## 契约版本 `schema_version`

每条记录可以带一个整数 `schema_version`，表示「这条记录按第几版契约写成」。

| 值 | 含义 |
|---|---|
| 缺省 / `1` | 当前版本。老导出没有这个字段，一律当 1 |
| 大于 `web/config.py:SCHEMA_VERSION` | 数据比本程序新 |

展示站读到**更高的版本**不会拒绝加载，而是在启动时打一条 WARNING，并把它暴露在
`/api/stats` 的 `stats.schema_version`（数据实际版本）和 `stats.schema_supported`
（本站支持的版本）里：

```
WARNING 数据契约版本 2 高于本站支持的 1，部分字段可能显示不出来
```

存在的理由：展示站和数据来源是两个独立项目、各自升级。有了这个字段，「页面少了一半
字段」能一眼看出是版本落后，而不是去翻合并逻辑。

> 加字段不算新版本（多出来的字段会被静默忽略）。只有**已有字段的含义或结构变了**
> 才需要把 `SCHEMA_VERSION` 加一，否则旧版本程序会把新数据解释错。

---

## 顶层字段

### 必填

| 字段 | 类型 | 说明 |
|---|---|---|
| `drama_id` | 字符串 | 唯一编号。详情、标签、收藏、评论都靠它 |
| `title` | 字符串 | 剧名。空值会显示成「(无标题)」 |

### 常用（不填也能跑，但页面会空）

| 字段 | 类型 | 单位 / 例 | 说明 |
|---|---|---|---|
| `url` | 字符串 | `https://example.com/video/8333/` | 详情页地址 |
| `cover_local` | 字符串 | `covers/8333_某剧.jpg` | 封面在本机的路径，见下文 |
| `cover` | 字符串 | `https://img.example.com/8333.jpg` | 封面原始 URL（只留档，页面用 `cover_local`） |
| `cover_size` | 整数 | 字节 | 封面文件大小 |
| `score` | 数字 | `8.7` | 评分 |
| `score_text` | 字符串 | `8.7分` | 评分原文 |
| `episode_total` | 整数 | `10` | 已更新集数 |
| `episode_text` | 字符串 | `更新至10集` | 集数原文 |
| `update_ts` | 整数 | 秒 | 更新时间戳 |
| `update_time` | 字符串 | `2026-10-24 18:30:41` | 更新时间 |
| `update_text` | 字符串 | `18小时前` | 数据源给的相对时间 |
| `tags` | 数组 | `["都市","逆袭"]` | 题材标签，顶部标签栏按它统计 |
| `tag_slugs` | 数组 | `["dushi","nixi"]` | 标签 slug（跟 href 末段一致） |
| `category_name` | 字符串 | `AI漫剧` | 分类名，首页分类筛选用它 |
| `category_id` | 字符串 | `12` | 分类 ID |
| `is_new` | 布尔 | `true` | 是否「新上架」，卡片有角标 |
| `is_original` | 布尔 | `false` | 是否原创 |
| `badges` | 数组 | `["新上架"]` | 角标原文 |
| `panels` | 数组 | `["latest","hot"]` | 命中的榜单（`latest` / `hot` / `original` / `random`） |
| `desc` | 字符串 | | 简介 |

### 详情字段（有就显示，没有就提示「还没详情」）

| 字段 | 类型 | 说明 |
|---|---|---|
| `author` / `author_id` / `author_url` | 字符串 | 作者名、ID、主页 |
| `duration` | 整数 | 单集时长，秒（`PT5M15S` → 315） |
| `views` / `likes` / `comments` | 整数 | 播放量 / 点赞 / 评论数（没抓到写 `null`，**不要写 0**） |
| `publish_time` | 字符串 | 上线时间，ISO 格式（`update_time` 是最近更新，两者不同） |
| `content_rating` | 字符串 | 分级，如 `18+` |

### 入库时间（可选，但排序靠它）

| 字段 | 说明 |
|---|---|
| `first_seen` | 这部剧第一次进库的时间 |
| `last_changed` | 上次发生变化的时间 |

「入库时间」「最近变动」两个排序依据就读它们。手写记录留空也能跑，只是少两个排序维度。
增量采集工具这两个字段会一直继承下去，所以能看到真实的入库时间。

### 增量相关（只有采集工具会写）

| 字段 | 说明 |
|---|---|
| `state_status` | `new` / `changed` / `unchanged` |
| `change_fields` | 本次变化的字段名，`\|` 连接（新增时为空） |

展示站**不读**这两个字段，它们是为了让 jsonl 自己就能看懂「这次导出了什么」。
手写记录不用管。

### 其它

| 字段 | 说明 |
|---|---|
| `schema_version` | 契约版本，见上文 |
| `crawled_at` | 采集时间（ISO） |
| `detail` | 嵌套结构，见下一节 |

> 不认识的字段会被**静默忽略**，多写几个自定义字段不会出错。

---

## `detail` 结构

放嵌套与一对多的数据：

```json
"detail": {
  "episode_no": 1,
  "episodes": [
    {"no": 1, "url": "https://example.com/video/8333/", "current": true},
    {"no": 2, "url": "https://example.com/video/8333/ep-2/", "current": false}
  ],
  "facts": [{"label": "上线日期", "value": "2026-10-10"}],
  "topics": [{"name": "逆袭", "url": "https://example.com/topic/nixi/"}],
  "sources": ["https://cdn.example.com/8333/1.m3u8"],
  "embed_url": null,
  "related_ids": ["8334", "8335"],
  "date_modified": "2026-10-24T18:30:41+08:00",
  "ld_stats": {"like": 1090}
}
```

| 键 | 说明 |
|---|---|
| `episode_no` | 这个详情页是第几集 |
| `episodes` | 选集列表，按集号排 |
| `facts` | 简介卡那一排「标签 / 值」，原样保留 |
| `topics` | 专题标签 |
| `sources` | 播放源（m3u8 / iframe） |
| `related_ids` | 相关推荐 |
| `ld_stats` | JSON-LD 里的原始互动数，与顶层口径不同时留作备查 |

---

## 合并规则

同一个 `drama_id` 在多个文件里出现时（增量导出必然如此），按下面五条合并。
合并逻辑在 `web/model.py:merge_record`，有单测兜着。

1. **后读的覆盖先读的。** 顺序是文件修改时间从旧到新，文件内按行序。
2. **空值不覆盖已有内容。** `null`、`""`、`[]`、`{}` 都算「这次没填」。
   所以增量导出可以只写「变化的字段 + drama_id + title」。
3. **部分字段的 `0` 也等于没填。** 这些字段写 `0` 不会覆盖旧值：
   `update_ts`、`score`、`episode_total`、`views`、`likes`、`comments`、
   `duration`、`category_id`、`author_id`。
   理由很直接：没抓到就是 `null`，但早期脚本会写成 `0`，让它覆盖会把 8.7 分刷成 0 分。
4. **`detail` 逐键合并**，不是整体替换：新记录只带 `episodes` 时，
   旧的 `facts` / `sources` 仍然保留。
5. **`drama_id` 不做类型转换。** 数字和字符串 `"8333"` 会被当成同一部剧
   （内部统一 `str()`），但请**统一写成字符串**，避免自己看混。

---

## 最小示例

新建 `output/duanju_2026-11-01.jsonl`，写入三行（每行一个完整 JSON，不换行）：

```json
{"drama_id": "9001", "title": "示例：逆袭人生", "url": "https://example.com/video/9001/", "score": 8.5, "episode_total": 12, "tags": ["都市", "逆袭"], "category_name": "都市", "desc": "一句话简介。", "schema_version": 1}
{"drama_id": "9002", "title": "示例：穿越日常", "score": 7.9, "episode_total": 8, "tags": ["穿越"], "schema_version": 1}
{"drama_id": "9003", "title": "示例：反派不好当", "score": 9.1, "episode_total": 24, "tags": ["古装", "爽剧"], "is_original": true, "schema_version": 1}
```

保存 → 页面点「刷新」→ 首页出现这三部。

## 一条完整记录

```json
{"drama_id": "8333", "title": "示例：外卖员觉醒", "url": "https://example.com/video/8333/", "cover": "https://img.example.com/8333.jpg", "cover_local": "covers/8333_示例外卖员觉醒.jpg", "score": 8.0, "score_text": "8.0分", "episode_total": 1, "episode_text": "更新至1集", "update_ts": 1793386871, "update_time": "2026-10-24 18:30:41", "update_text": "18小时前", "is_new": false, "is_original": false, "badges": [], "tags": ["大男主", "都市", "逆袭"], "category_name": "AI漫剧", "panels": ["random"], "desc": "示例简介。", "author": "示例作者", "author_url": "https://example.com/author/156291/", "duration": 320, "views": 495930, "likes": 1090, "comments": null, "publish_time": "2026-10-10T15:45:03+08:00", "content_rating": "18+", "first_seen": "2025-12-28T10:12:00+08:00", "last_changed": "2026-01-01T00:00:00+08:00", "crawled_at": "2026-01-01T00:00:00+08:00", "schema_version": 1, "detail": {"episode_no": 1, "episodes": [{"no": 1, "url": "https://example.com/video/8333/", "current": true}], "facts": [{"label": "上线日期", "value": "2026-10-10"}], "topics": [], "sources": []}}
```

---

## `cover_local` 怎么被解析

展示站收到 `cover_local` 后做三件事：

1. 绝对路径直接用；只写文件名（或写 `covers/xxx.jpg`）时，去 `output/covers/` 下找。
2. 文件**存在** → 输出 `/covers/<URL 编码后的文件名>`（文件名含中文和空格，
   必须编码）。
3. 文件**不存在** → 返回 `null`，前端画占位图，不会出现一堆裂图。

所以「搬了数据目录但忘了搬封面」的表现是封面全灰，而不是报错。

---

## 几件容易误会的事

- **`csv` 不是数据源。** 它是同一批数据的表格镜像，代码一律读 `jsonl`。
  两者的差别只有一处：`jsonl` 里 `tags` 是数组，`csv` 里是 `都市|逆袭`。
- **删记录就等于删剧。** 想让某部剧从页面消失，把它从所有 jsonl 里去掉即可；
  反过来想让它「重新入库」，也是去掉再放回来。
- **手写的记录和采集来的记录地位相同。** 合并规则对两者一视同仁，没有优先级差别。
- **没有「在架 / 下架」。** jsonl 里有记录就展示，没有就不展示；不存在一个
  隐藏状态字段。
- **人工编辑优先于 jsonl。** 页面改过的标签存在 `tag_edits.json` 里，覆盖 jsonl 的值，
  所以重灌数据不会把你的标签改回去。
