/* 详情弹层：展示 + 标签编辑 + 这部剧的 ED2K 资源。
   标签编辑走「提交完整列表」语义（增删改名都是同一个 POST），
   保存后依次刷新统计、标签栏、列表和弹层自身。 */
import { el, esc } from './dom.js';
import { api } from './api.js';
import { factHtml, fmtDuration, fmtNum, fmtSize, tagsView } from './format.js';
import { cardHtml, reload } from './list.js';
import { commentsSectionHtml, mountComments, unmountComments,
         rememberLoginReturn, forgetLoginReturn } from './comment.js';
import { loadStats } from './stats.js';
import { loadTags } from './tagbar.js';

const modal = el('modal');
const panel = el('modal-panel');

export function closeDetail() {
    stopPlayback();          // 关弹层别让声音继续放
    unmountComments();       // 顺带把 giscus iframe 拿掉，别在后台轮询
    forgetLoginReturn();     // 用户自己关的，登录回来就不用再弹了
    modal.hidden = true;
}

/* ---------- 各区块的 HTML ---------- */

function factsHtml(d) {
    return [
        factHtml('评分', d.score_text || d.score),
        factHtml('集数', d.episode_text || d.episode_total),
        factHtml('更新时间', d.update_time || d.update_text),
        factHtml('分类', d.category),
        factHtml('作者', d.author),
        factHtml('单集时长', fmtDuration(d.duration)),
        factHtml('播放量', fmtNum(d.views)),
        factHtml('点赞', fmtNum(d.likes)),
        factHtml('评论', fmtNum(d.comments)),
        factHtml('上线时间', (d.publish_time || '').replace('T', ' ')),
        factHtml('分级', d.content_rating),
        factHtml('入库时间', (d.first_seen || '').replace('T', ' ')),
        factHtml('最近变更', (d.last_changed || '').replace('T', ' ')),
    ].filter(Boolean).join('');
}

function topicsHtml(topics) {
    if (!(topics || []).length) return '';
    return `<div class="topics">${topics.map((t) =>
        `<span class="topic">${esc(t.name || '')}</span>`).join('')}</div>`;
}

/* 选集 / 播放源：暂时不在详情页展示（见 openDetail 里的注释），
   函数留着，恢复时把那一行调用放回模板即可。 */
function episodesHtml(episodes) {
    if (!(episodes || []).length) return '';
    return `<div class="section"><div class="section__label">选集（${episodes.length}）</div>
         <div class="episodes">${episodes.map((e) =>
        `<span class="ep${e.current ? ' is-current' : ''}">${esc(e.no)}</span>`).join('')}</div></div>`;
}

function sourcesHtml(sources) {
    if (!(sources || []).length) return '';
    return `<div class="section"><div class="section__label">播放源</div>
         <div class="sources">${sources.map((s) =>
        `<div class="source">${esc(s)}</div>`).join('')}</div></div>`;
}

function relatedHtml(related) {
    if (!(related || []).length) return '';
    return `<div class="section"><div class="section__label">相关推荐</div>
         <div class="related">${related.map(cardHtml).join('')}</div></div>`;
}

function noDetailHtml(d, detail) {
    if (d.author || (detail.episodes || []).length) return '';
    return `<div class="section"><div class="hint">这部剧还没有详情数据。`
        + `详情字段（作者、播放量、每集列表等）由采集工具产出，`
        + `补齐后重新导出即可，页面这边不用做任何事。</div></div>`;
}

/* ED2K 资源区块：先出骨架，数据异步取，不拖慢详情首屏。 */
function ed2kSectionHtml() {
    return `<div class="section" id="ed2k-section">
        <div class="section__label">ED2K 资源<span class="hits__count" id="ed2k-count"></span>
            <button class="btn btn--mini" id="ed2k-copyall" hidden>复制全部</button>
            <button class="btn btn--mini" id="ed2k-open">在链接库中搜索</button></div>
        <div class="hits" id="ed2k-hits"><div class="loading">查询中…</div></div>
    </div>`;
}

/* 本地视频区块：刮削匹配到这部剧的文件，点一下就在弹层里播。
   骨架先出，数据异步取；播放地址指向 /media/<file_id>（后端支持 Range，能拖进度）。 */
function videosSectionHtml() {
    return `<div class="section" id="videos-section">
        <div class="section__label">本地视频<span class="hits__count" id="videos-count"></span>
            <button class="btn btn--mini" id="videos-open">在视频库中查看</button></div>
        <div class="player" id="player" hidden>
            <video id="player-video" controls preload="metadata" playsinline></video>
            <div class="player__bar">
                <span class="player__now" id="player-now"></span>
                <button class="btn btn--mini" id="player-stop">收起播放器</button>
            </div>
        </div>
        <div class="hits" id="videos-hits"><div class="loading">查询中…</div></div>
    </div>`;
}

function tagsSectionHtml(d) {
    return `<div class="section" id="tags-section">
        <div class="section__label">标签${d.tags_edited ? ' <span class="edited-mark">已人工编辑</span>' : ''}
            <button class="btn btn--mini" id="tags-edit">编辑</button></div>
        <div class="topics" id="tags-view">${tagsView(d.tags)}</div>
        <div class="tags-editor" id="tags-editor" hidden>
            <div class="topics" id="tags-editlist"></div>
            <div class="tags-editor__row">
                <input id="tags-input" maxlength="30" placeholder="输入标签回车添加；点标签文字可改名">
                <button class="btn" id="tags-add">添加</button>
                <button class="btn btn--primary" id="tags-save">保存</button>
                <button class="btn" id="tags-cancel">取消</button>
            </div>
        </div>
    </div>`;
}

/* ---------- 标签编辑 ---------- */

function bindTagEditor(d) {
    let editTags = (d.tags || []).slice();
    const editor = el('tags-editor');
    const editList = el('tags-editlist');
    const input = el('tags-input');

    const renderEditList = () => {
        editList.innerHTML = editTags.map((t) =>
            `<span class="topic topic--edit" data-tag="${esc(t)}" title="点击改名">`
            + `${esc(t)}<button class="topic__x" data-x="${esc(t)}" title="删除标签">×</button></span>`
        ).join('');

        editList.querySelectorAll('[data-x]').forEach((x) => {
            x.onclick = (ev) => {
                ev.stopPropagation();
                editTags = editTags.filter((t) => t !== x.dataset.x);
                renderEditList();
            };
        });
        // 点标签文字 = 改名：从列表取出放进输入框
        editList.querySelectorAll('.topic--edit').forEach((chip) => {
            chip.onclick = () => {
                editTags = editTags.filter((t) => t !== chip.dataset.tag);
                input.value = chip.dataset.tag;
                input.focus();
                renderEditList();
            };
        });
    };

    const addTag = () => {
        const t = input.value.trim().slice(0, 30);
        if (t && !editTags.includes(t)) editTags.push(t);
        input.value = '';
        renderEditList();
    };

    el('tags-edit').onclick = () => {
        if (editor.hidden) {
            editTags = (d.tags || []).slice();
            renderEditList();
        }
        editor.hidden = !editor.hidden;
    };
    el('tags-add').onclick = addTag;
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addTag();
        }
    });
    el('tags-cancel').onclick = () => { editor.hidden = true; };

    el('tags-save').onclick = async () => {
        const btn = el('tags-save');
        btn.disabled = true;
        try {
            const result = await api.saveTags(d.id, editTags);
            if (result.error) {
                alert('保存失败：' + result.error);
                return;
            }
            editor.hidden = true;
            loadStats();
            loadTags();      // 标签计数变了
            reload();        // 列表的标签筛选/搜索也要跟上
            openDetail(d.id);  // 重新拉详情，展示保存后的标签
        } catch (err) {
            alert('保存失败：' + err.message);
        } finally {
            btn.disabled = false;
        }
    };
}

/* ---------- ED2K 资源（按剧名去链接库里搜） ---------- */

function hitHtml(item) {
    const ep = item.episode_label || (item.episode ? String(item.episode) : '—');
    return `<div class="hit" data-url="${esc(item.url)}" title="${esc(item.name)}">
        <span class="hit__ep">${esc(ep)}</span>
        <span class="hit__name">${esc(item.name)}</span>
        <span class="hit__size">${esc(fmtSize(item.size))}</span>
        <button class="btn btn--mini" data-copy="${esc(item.url)}">复制</button>
    </div>`;
}

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (err) {
        return false;
    }
}

/**
 * 异步填 ED2K 区块。
 * 关键词和条数都是后端算的（ed2k.title_keywords 逐档降级），前端只负责展示，
 * 点「在链接库中搜索」时把用上的关键词带过去，两边的搜索口径才一致。
 */
function bindEd2kSection(d) {
    const box = el('ed2k-hits');
    const count = el('ed2k-count');
    const copyAll = el('ed2k-copyall');
    if (!box) return;

    let items = [];
    let keyword = d.title || '';
    // 连着点开不同的剧时，上一个请求可能晚到；这个节点已被替换就丢弃结果
    const alive = () => box.isConnected;

    box.addEventListener('click', (e) => {
        const row = e.target.closest('.hit');
        if (!row) return;
        const btn = e.target.closest('[data-copy]');
        copyText(btn ? btn.dataset.copy : row.dataset.url).then((ok) => {
            if (!btn) return;
            btn.textContent = ok ? '已复制' : '复制失败';
            setTimeout(() => { btn.textContent = '复制'; }, 1200);
        });
    });

    copyAll.onclick = async () => {
        const ok = await copyText(items.map((i) => i.url).join('\n'));
        copyAll.textContent = ok ? `已复制 ${items.length} 条` : '复制失败';
        setTimeout(() => { copyAll.textContent = '复制全部'; }, 1500);
    };

    el('ed2k-open').onclick = () => {
        document.dispatchEvent(new CustomEvent('ed2k:open', { detail: { q: keyword } }));
    };

    api.dramaEd2k(d.id).then((r) => {
        if (!alive() || !r) return;
        if (r.available === false) {
            box.innerHTML = `<div class="hint">链接库暂时不可用：${esc(r.error || '未知错误')}</div>`;
            return;
        }
        items = r.items || [];
        keyword = r.keyword || keyword;
        if (!items.length) {
            box.innerHTML = `<div class="hint">链接库里还没有包含「${esc(r.title || d.title)}」的资源，`
                + '可以点右上「在链接库中搜索」上传一份包含该剧的 TXT。</div>';
            return;
        }
        // 走了降级关键词才提示，否则「按『同名剧』匹配」是废话
        const note = keyword && keyword !== (r.title || '') ? `（按「${keyword}」匹配）` : '';
        // 后端有条数上限（config.ED2K_TITLE_LIMIT），超出时说明只列了前 N 条
        const shown = items.length < r.total ? `（显示前 ${items.length} 条）` : '';
        count.textContent = `${r.total} 条 · ${fmtSize(r.bytes)}${note}${shown}`;
        copyAll.hidden = false;
        box.innerHTML = items.map(hitHtml).join('');
    }).catch((err) => {
        if (!alive()) return;
        box.innerHTML = `<div class="hint">资源查询失败：${esc(err.message)}</div>`;
    });
}

/* ---------- 本地视频（刮削匹配到的文件，可直接播） ---------- */

/* 不可播的三种原因：说清楚「为什么点了没反应」，比灰一个按钮有用 */
const REASON_TEXT = {
    unlocatable: '服务端定位不到',
    missing: '文件不在了',
    unsafe: '路径不安全',
};

function videoHitHtml(it) {
    const ep = it.episode_label || (it.episode ? String(it.episode) : '—');
    const tip = it.playable ? `${it.root} / ${it.rel_path}`
        : (REASON_TEXT[it.reason] || '不可播放');
    return `<div class="hit${it.playable ? ' hit--play' : ' hit--off'}"
                 data-fid="${it.id}"${it.playable ? ` data-url="${esc(it.url)}"` : ''}
                 title="${esc(tip)}">
        <span class="hit__ep">${esc(ep)}</span>
        <span class="hit__name">${esc(it.name)}</span>
        <span class="hit__size">${esc(fmtSize(it.size))}</span>
        ${it.playable
        ? '<button class="btn btn--mini" data-play="1">播放</button>'
        : `<span class="hit__off">${esc(REASON_TEXT[it.reason] || '不可播')}</span>`}
    </div>`;
}

/** 停掉播放并清掉 src：关弹层 / 换一部剧时必须调，否则声音还在响。 */
function stopPlayback() {
    const player = el('player-video');
    if (!player) return;
    player.pause();
    player.removeAttribute('src');
    player.load();          // 真正释放解码器与连接
}

function bindVideosSection(d) {
    const box = el('videos-hits');
    if (!box) return;
    const count = el('videos-count');
    const playerBox = el('player');
    const player = el('player-video');
    const now = el('player-now');

    let items = [];
    // 连着点开不同的剧时，上一个请求可能晚到；节点被替换就丢弃结果
    const alive = () => box.isConnected;

    const stop = () => {
        stopPlayback();
        player.dataset.fid = '';
        playerBox.hidden = true;
        now.textContent = '';
        box.querySelectorAll('.hit.is-playing')
            .forEach((n) => n.classList.remove('is-playing'));
    };

    const play = (row) => {
        const it = items.find((x) => String(x.id) === row.dataset.fid);
        if (!it || !it.playable) return;
        if (player.dataset.fid === String(it.id)) {
            // 再点同一集 = 暂停 / 继续，不重新加载
            if (player.paused) player.play().catch(() => {});
            else player.pause();
            return;
        }
        player.dataset.fid = String(it.id);
        player.poster = d.cover || '';
        player.src = it.url;
        playerBox.hidden = false;
        now.textContent = it.name;
        now.title = `${it.root} / ${it.rel_path}`;
        box.querySelectorAll('.hit')
            .forEach((n) => n.classList.toggle('is-playing', n === row));
        // 有声音的自动播放可能被浏览器拦住，那就在播放器里再点一次
        player.play().catch(() => {});
        playerBox.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    };

    box.addEventListener('click', (e) => {
        const row = e.target.closest('.hit');
        if (row && row.dataset.url) play(row);
    });
    el('player-stop').onclick = stop;
    el('videos-open').onclick = () => {
        document.dispatchEvent(new CustomEvent('video:open', { detail: { q: d.title || '' } }));
    };

    api.dramaVideos(d.id).then((r) => {
        if (!alive() || !r) return;
        if (r.available === false) {
            box.innerHTML = `<div class="hint">视频库暂时打不开：${esc(r.error || '未知错误')}</div>`;
            return;
        }
        items = r.items || [];
        if (!items.length) {
            box.innerHTML = `<div class="hint">本地视频库里还没有匹配到「${esc(r.title || d.title)}」的文件。`
                + '在「本地视频」里填服务器目录扫描一次（扫完会自动刮削匹配），这里就能直接播放。</div>';
            return;
        }
        count.textContent = `${r.total} 个 · 可播 ${r.playable} · ${fmtSize(r.bytes)}`;
        box.innerHTML = items.map(videoHitHtml).join('')
            // 有定位不到的文件时补一句怎么修，否则这一列灰条看不懂
            + (r.playable < r.total
                ? '<div class="hint">灰色行是服务端定位不到的文件（早期用浏览器选文件夹入库的，'
                  + '只记了文件夹名）：在「本地视频」里填服务器路径重新扫一次该目录即可播放。</div>'
                : '');
    }).catch((err) => {
        if (!alive()) return;
        box.innerHTML = `<div class="hint">本地视频查询失败：${esc(err.message)}</div>`;
    });
}

/* ---------- 收藏 ---------- */

function favoriteBtnHtml(d) {
    const on = !!d.favorite;
    return `<button class="btn btn--fav${on ? ' is-on' : ''}" id="modal-fav"
        title="${on ? '取消收藏' : '加入收藏'}">${on ? '★ 已收藏' : '☆ 收藏'}</button>`;
}

/**
 * 收藏按钮：点一下先变样子，再发请求。
 * 网络慢的时候按钮不能像卡住；请求失败就原地回滚，别让界面和库里的数据不一致。
 */
function bindFavorite(d) {
    const btn = el('modal-fav');
    if (!btn) return;

    let on = !!d.favorite;
    const paint = (v) => {
        btn.textContent = v ? '★ 已收藏' : '☆ 收藏';
        btn.classList.toggle('is-on', v);
        btn.title = v ? '取消收藏' : '加入收藏';
    };

    btn.onclick = async () => {
        const next = !on;
        btn.disabled = true;
        paint(next);
        try {
            const r = next ? await api.favorite(d.id) : await api.unfavorite(d.id);
            if (r.error) throw new Error(r.error);
            on = !!r.favorite;
            paint(on);
            loadStats();   // 顶栏「收藏 N」跟着变
            reload();      // 列表卡片的星标、「只看收藏」的结果跟着变
        } catch (err) {
            paint(on);     // 失败回滚（比如服务被关掉了）
            alert('操作失败：' + err.message);
        } finally {
            btn.disabled = false;
        }
    };
}

/* ---------- 主流程 ---------- */

export async function openDetail(id) {
    stopPlayback();          // 换剧时先停掉上一部在播的
    modal.hidden = false;
    panel.innerHTML = '<div class="loading">加载中…</div>';
    try {
        const d = await api.drama(id);
        if (d.error) {
            panel.innerHTML = '<div class="empty">没找到这部剧</div>';
            return;
        }
        const detail = d.detail || {};
        rememberLoginReturn(d.id);   // 评论区登录是整页跳转，回来要靠它把这部剧重新弹开

        // 选集 / 播放源两块暂时不展示（detail 里的数据照旧解析，去掉这两行注释即可恢复）
        //   ${episodesHtml(detail.episodes)}${sourcesHtml(detail.sources)}
        panel.innerHTML = `
            <button class="modal__close" id="modal-close">×</button>
            <div class="intro">
                <div class="poster">${d.cover ? `<img src="${esc(d.cover)}" alt="${esc(d.title)}">` : ''}</div>
                <div class="intro__main">
                    <h2 class="intro__title">${esc(d.title)}</h2>
                    ${d.score_text ? `<div class="intro__score">${esc(d.score_text)}</div>` : ''}
                    <div class="facts">${factsHtml(d)}</div>
                    ${topicsHtml(detail.topics)}
                </div>
            </div>
            <div class="detail-fav">${favoriteBtnHtml(d)}</div>
            ${d.desc ? `<div class="section"><div class="section__label">简介</div>
                <p class="desc">${esc(d.desc)}</p></div>` : ''}
            ${tagsSectionHtml(d)}
            ${videosSectionHtml()}
            ${ed2kSectionHtml()}
            ${relatedHtml(d.related)}${noDetailHtml(d, detail)}
            ${commentsSectionHtml()}
            <div class="actions">
                ${d.url ? `<a class="btn btn--primary" href="${esc(d.url)}" target="_blank" rel="noreferrer">打开原站页面</a>` : ''}
                ${d.cover ? `<a class="btn" href="${esc(d.cover)}" target="_blank">查看封面原图</a>` : ''}
                <button class="btn" id="modal-copy">复制 ID</button>
            </div>`;

        el('modal-close').onclick = closeDetail;
        el('modal-copy').onclick = () => {
            if (navigator.clipboard) navigator.clipboard.writeText(d.id);
            el('modal-copy').textContent = '已复制 ' + d.id;
        };
        bindTagEditor(d);
        bindVideosSection(d);
        bindEd2kSection(d);
        bindFavorite(d);
        mountComments(d.id, d.title);   // 评论异步加载，不拖慢首屏
    } catch (err) {
        panel.innerHTML = '<div class="empty">详情加载失败</div>';
    }
}

/* ---------- 弹层自身的交互（只挂一次） ---------- */

modal.addEventListener('click', (e) => {
    if (e.target === modal) closeDetail();
});

document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    // 从详情里打开的链接库 / 视频库叠在上面，Esc 先关它（它自己也在监听），
    // 别把两层一起关掉
    const stacked = ['ed2k-modal', 'video-modal']
        .map((id) => el(id))
        .some((node) => node && !node.hidden);
    if (stacked) return;
    closeDetail();
});

// 相关推荐的卡片点击：委托处理，避免每个弹层重新绑定
panel.addEventListener('click', (e) => {
    const card = e.target.closest('.related .card');
    if (card) openDetail(card.dataset.id);
});
