# 运维与排错

三个脚本、几条固定流程、一张排错表。跑之前先看清楚**这条命令会删什么**。

---

## 三个脚本

| 脚本 | 干什么 |
|---|---|
| `manage.py` | 看数据（`stats` / `recent`）、清东西（`reset`）。只读导出文件，不碰数据库 |
| `webctl.py` | 展示站启停：`start` / `stop` / `restart` / `status` / `log` / `open` |
| `compact_exports.py` | 把攒下的多个 jsonl 合并成一个快照，顺带备份旧文件 |

```bash
python manage.py stats
python manage.py recent -n 20
python manage.py reset --purge-output --drop-covers -y
python webctl.py status
python compact_exports.py --write
```

---

## 日常巡检

```bash
python manage.py stats          # 多少部、封面几张、最近变动时间
python webctl.py status         # 服务在不在、端口、数据条数
tail -50 output/web.log         # 有异常时先看这里
```

`stats` 的输出长这样：

```
导出目录：D:\duanju-web\output
  文件        1 个（最新 duanju_2026-01-01T00-00-00.jsonl）
  去重后      24 部
  封面        本地 18 张（记录里带本地路径的 18 部）
  最早入库    2025-12-28T10:12:00+08:00
  最近变动    2026-01-01T00:00:00+08:00
```

三个数字值得盯：**部数**（突然掉一半说明数据被误删了）、**封面数与带路径的部数**
（差太多说明有图丢了）、**最近变动**（一直不动说明很久没导新数据）。

---

## 清空数据

`reset` **必须显式说明删什么**，裸跑只打印选项、什么都不动：

| 对象 | 默认 | 说明 |
|---|---|---|
| `output/covers` | 保留 | 重导时按 `drama_id` 命中，同一张图不用重复下载（`--drop-covers` 才删） |
| `output/duanju_*` | 保留 | 剧集数据（`--purge-output` 才删，`.jsonl` 和 `.csv` 一起） |

```bash
# 删剧集数据（保留封面）
python manage.py reset --purge-output -y

# 连封面一起删
python manage.py reset --purge-output --drop-covers -y

# 手工删效果一样
rm -f output/duanju_*.jsonl
```

清空之后怎么把数据放回来，取决于你的采集工具 —— 也可以先用
`sample/` 里的演示数据把页面跑起来（见 [guide.md](guide.md)）。

---

## 去掉某几部剧

把它们从所有 jsonl 里剔掉即可（页面上从此看不到，文件备份还在）：

```bash
python -c "
import json, pathlib
ids = {'12345', '67890'}
for p in pathlib.Path('output').glob('duanju_*.jsonl'):
    lines = [l for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
    kept = [l for l in lines if str(json.loads(l).get('drama_id')) not in ids]
    if len(kept) != len(lines):
        p.write_text('\n'.join(kept) + '\n', encoding='utf-8')
        print(p.name, '去掉', len(lines) - len(kept), '行')
"
```

---

## 压实导出

增量每次写一个新文件，同一部剧会散在几十个文件里。展示站加载时会全部合并，
正确性没问题，但文件多了每次加载都要全量读盘。压成一个文件后加载恢复到最快：

```bash
python compact_exports.py            # 试运行：只报告会合并成什么，不写文件
python compact_exports.py --write    # 真跑：写新文件，旧文件移进 exports_backup_<时间戳>/
python compact_exports.py --write --keep-old   # 只多写一个合并文件，旧文件不动
```

- 合并规则与展示站**同一套**（`model.merge_record`），所以压实不改变任何一部剧的
  显示结果，也不改变增量判定
- 只搬不删：旧文件在 `output/exports_backup_<时间戳>/` 里，随时移回
- `.csv` 不参与（那只是给人看的镜像）
- 压实后建议先刷一次页面确认总数没变，过几天再清备份目录

---

## 备份

| 想备份什么 | 复制什么 |
|---|---|
| 全部（含剧集数据、封面、人工数据） | 整个 `output/` |
| 只备份人工数据（体积小） | `ed2k.sqlite3`、`video.sqlite3`、`tag_edits.json`、`favorites.json` |
| 只备份人工数据 + 剧集清单 | 上面四个 + 所有 `duanju_*.jsonl`（不用带 `covers/`） |

人工数据和剧集数据是分开的：换机器时只带四个小文件过去，剧集重新抓也不会丢你的
标签、收藏、链接和视频记录。

---

## 排错：展示站

| 现象 | 原因 / 处理 |
|---|---|
| `start` 说成功，下条命令就没了 | 进程没脱离控制台（历史上踩过）。用 `webctl.py` 起，别自己 `nohup` |
| 端口被占用 | `python webctl.py start --port 9000` |
| `status` 说在跑但页面打不开 | 本机代理劫持了 `127.0.0.1`，见下一条 |
| curl / 脚本访问返回 502 或空 | shell 里有 `http_proxy` 指向本地代理。用 `curl --noproxy "*"`，脚本里 `build_opener(ProxyHandler({}))` |
| 数据 0 部 | `--output` 指错，或 `output/` 里没有 `duanju_*.jsonl` |
| 放了新文件页面不刷新 | 文件名必须是 `duanju_*.jsonl`；点「刷新」；看 `webctl.py log -n 50` |
| 中文搜索总是 0 条 | 查询串编码问题（`httpd._decoded_path` 处理 UTF-8 / GBK）。先确认浏览器里搜也一样 |
| 页面白屏、控制台报 module 错 | 用 `file://` 直接打开了 `index.html`。ES module 必须走 http |
| 静态资源 404 | `/static/js/xxx.js` 这种子路径要走 `resolve_under()` 校验，看日志里的路径 |
| 封面全灰 | `cover_local` 指的文件不存在（搬目录时没带 `covers/`） |
| 视频灰条 | 三条原因：root 不是绝对路径 / 文件不在了 / 路径含 `..` |
| 评论点登录没反应 | giscus 回跳要 `consumeLoginReturn()` 接住，见 [web.md](web.md) |
| 统计数字带 `e+03` | `SUM(BIGINT)` 是浮点，SQL 里要 `CAST(... AS BIGINT)` |
| 改了 `config.py` 不生效 | 配置只在启动时读，`python webctl.py restart` |

pid 文件丢了导致 `stop` 认不出进程：

```bash
netstat -ano | findstr 8765        # Windows
python webctl.py stop --pid <上面的 pid>
```

---

## 从旧版本升级

这个项目前后删掉了三样东西，**旧目录里留下的都可以直接删**：

| 旧文件 / 功能 | 现在 | 怎么处理 |
|---|---|---|
| `output/state.sqlite3` | 没有状态库，剧集数据就是导出文件 | 直接删 |
| `output/jobdir/` | 早期断点续跑的工作目录 | 直接删 |
| `.env` / `.env.example` | 配置全部收进 `web/config.py` | 直接删。原来的 giscus 四项已经搬进 `config.py` |

另外两处语义变化，升级时留意：

- **没有「在架 / 下架」了。** 数据里没有 `status` 字段，jsonl 有记录就展示。
- **`manage.py reset` 语义变了。** 必须显式指定 `--purge-output` 或
  `--drop-covers`，裸跑只打印选项。
