/* 本地视频文件库：填服务器目录路径 -> 扫描视频文件名入库 -> 搜索 / 删除。
   只支持服务端路径扫描（浏览器选文件夹拿不到绝对路径，扫进来也播不了）。
   自成模块，只依赖 dom / api / format；列表、详情、ED2K 弹层都不需要知道它的存在。 */
import { el, esc } from './dom.js';
import { api } from './api.js';
import { fmtSize } from './format.js';
import { openScrape } from './scrape.js';

const PAGE_SIZES = [50, 100, 200, 500];
const SEARCH_DEBOUNCE_MS = 250;
const HISTORY_LIMIT = 6;

let page = 1;
let size = 50;
let q = '';
let rendered = false;

const modal = () => el('video-modal');
const panel = () => el('video-panel');

const fmtTime = (text) => (text || '').replace('T', ' ');
const fmtShort = (text) => fmtTime(text).slice(5, 16);

/* ---------- 骨架 ---------- */

function skeleton() {
    panel().innerHTML = `
        <button class="modal__close" id="video-close" title="关闭">×</button>
        <h2 class="video__title">本地视频文件库<span class="video__stats" id="video-stats"></span></h2>

        <div class="video__upload">
            <div class="video__pick">
                <input id="video-path" type="text" spellcheck="false"
                       placeholder="填服务器上的目录路径，如 O:\\video">
                <button class="btn btn--primary" id="video-scanpath">扫描该目录</button>
                <div class="video__pick-info">
                    扫描该目录及其子目录里的视频文件，扫完自动刮削匹配入库（只取文件名和大小，
                    不读文件内容）；服务需运行在这台机器上
                </div>
            </div>
        </div>

        <div class="video__result" id="video-result" hidden></div>

        <div class="video__bar">
            <input id="video-q" type="search" autocomplete="off"
                   placeholder="搜索文件名 / 相对路径">
            <select id="video-size">${PAGE_SIZES.map((n) =>
        `<option value="${n}"${n === size ? ' selected' : ''}>${n} / 页</option>`).join('')}
            </select>
            <button class="btn" id="btn-scrape" title="重跑 / 人工修正刮削匹配：预览 -> 改选 -> 入库">刮削匹配</button>
        </div>

        <div class="video__history" id="video-history"></div>
        <div class="video__tablewrap" id="video-tablewrap"></div>
        <nav class="pager" id="video-pager"></nav>
        <div class="video__foot">
            <button class="btn btn--danger" id="video-clear" hidden>清空文件库</button>
        </div>`;

    el('video-close').onclick = closeVideo;
    bindScan();
    bindToolbar();
    bindRows();
    rendered = true;
}

/* ---------- 扫描 ---------- */

function notice(kind, html) {
    const box = el('video-result');
    box.hidden = false;
    box.className = 'video__result is-' + kind;
    box.innerHTML = html;
}

async function scanPath() {
    const input = el('video-path');
    if (el('video-scanpath').disabled) return;   // 正在扫 / 正在刮，别并发再来一遍
    const path = input.value.trim();
    if (!path) {
        notice('error', '先填一个服务器上的目录路径，例如 <b>O:\\video</b>');
        input.focus();
        return;
    }
    await submit({ path });
}

async function submit(payload) {
    const btn = el('video-scanpath');
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = '扫描中…';
    try {
        const r = await api.videoScan(payload);
        const root = esc(r.root || '(未命名)');
        const line = `<b>${root}</b>：扫到 ${r.files} 个视频文件，新增 <b>${r.inserted}</b> 个，`
            + `重复 ${r.duplicated} 个`
            + (r.skipped ? `，跳过 ${r.skipped} 个非视频文件` : '')
            + `；库内共 <b>${r.total}</b> 个。`;
        // 扫完直接刮削入库，不用再点一次「刮削匹配」
        if (!r.files) {
            notice('ok', line);
        } else {
            btn.textContent = '刮削中…';
            const job = await autoScrape(r.root);
            notice(job.ok ? 'ok' : 'error', line + scrapeNote(job));
        }
        page = 1;
        await Promise.all([loadList(), loadHistory(), refreshVideoBadge()]);
    } catch (err) {
        notice('error', '扫描失败：' + esc(err.message));
    } finally {
        btn.textContent = old;
        btn.disabled = false;
    }
}

/** 扫描后自动跑一遍刮削入库；失败只影响提示，已入库的文件记录照留。 */
async function autoScrape(root) {
    try {
        const s = await api.scrapeApply({ root });
        return { ok: true, matched: s.matched, review: s.review, unmatched: s.unmatched };
    } catch (err) {
        return { ok: false, error: err.message };
    }
}

/** 自动刮削的结果补在扫描结果后面；有待确认/未匹配时告诉人去哪儿修。 */
function scrapeNote(job) {
    if (!job.ok) {
        return `<br>扫描结果已入库，但自动刮削失败：${esc(job.error)}`
            + '（可点上面的「刮削匹配」重试）';
    }
    const rest = [];
    if (job.review) rest.push(`待确认 ${job.review} 个`);
    if (job.unmatched) rest.push(`未匹配 ${job.unmatched} 个`);
    return `<br>已自动刮削：匹配 <b>${job.matched}</b> 个`
        + (rest.length ? `，${rest.join('，')}（可在「刮削匹配」里人工修正）` : '')
        + '。';
}

function bindScan() {
    el('video-scanpath').onclick = scanPath;
    el('video-path').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') scanPath();
    });
    el('video-clear').onclick = clearAll;
    el('btn-scrape').onclick = () => {
        closeVideo();
        openScrape();
    };
}

async function clearAll() {
    if (!confirm('清空整个本地视频文件库？所有文件记录和扫描记录都会被删除，且无法撤销。')) return;
    if (!confirm('再确认一次：扫描记录也不保留，重新扫描可以再补回来。确定清空？')) return;
    try {
        const r = await api.videoClear();
        page = 1;
        await Promise.all([loadList(), loadHistory(), refreshVideoBadge()]);
        notice('ok', `已清空，删除 ${r.deleted} 条文件记录`);
    } catch (err) {
        notice('error', '清空失败：' + esc(err.message));
    }
}

/* ---------- 列表 ---------- */

const STATUS_LABEL = {
    matched: '已匹配', pending: '待确认', unmatched: '', ignored: '已忽略',
};

function matchChip(it) {
    const label = STATUS_LABEL[it.match_status] || '';
    if (!label) return '<span class="video-table__match">—</span>';
    return `<span class="video-table__match is-${esc(it.match_status)}">${esc(label)}` +
        (it.match_status === 'matched' && it.episode ? `·第${it.episode}集` : '') +
        `</span>`;
}

function rowHtml(it) {
    return `<tr>
        <td class="video-table__name" title="${esc(it.name)}">${esc(it.name)}</td>
        <td class="video-table__size">${fmtSize(it.size)}</td>
        <td class="video-table__ext">${esc(it.ext || '—')}</td>
        <td class="video-table__matchcell">${matchChip(it)}</td>
        <td class="video-table__path" title="${esc(it.root)} / ${esc(it.rel_path)}">${esc(it.rel_path)}</td>
        <td class="video-table__time">${esc(fmtShort(it.created_at))}</td>
        <td class="video-table__ops">
            <button class="btn btn--mini btn--danger" data-del="${it.id}">删除</button>
        </td>
    </tr>`;
}

function tableHtml(items) {
    if (!items.length) {
        return '<div class="empty">没有匹配的文件，换个关键词或先扫描一个服务器目录</div>';
    }
    return `<table class="video-table">
        <thead><tr>
            <th>文件名</th><th>大小</th><th>类型</th><th>刮削</th><th>位置</th><th>入库</th><th></th>
        </tr></thead>
        <tbody>${items.map(rowHtml).join('')}</tbody>
    </table>`;
}

function bindRows() {
    el('video-tablewrap').onclick = async (e) => {
        const delBtn = e.target.closest('[data-del]');
        if (!delBtn || !confirm('删除这条文件记录？重新扫描同一个文件夹可以再补回来。')) return;
        try {
            await api.videoDeleteFile(delBtn.dataset.del);
            await Promise.all([loadList(), refreshVideoBadge()]);
        } catch (err) {
            notice('error', '删除失败：' + esc(err.message));
        }
    };
}

function pagerHtml(count, pages) {
    if (pages <= 1) return `<span class="pager__gap">共 ${count} 条</span>`;
    const nums = [];
    for (let n = 1; n <= pages; n++) {
        if (n === 1 || n === pages || Math.abs(n - page) <= 2) nums.push(n);
        else if (nums[nums.length - 1] !== '…') nums.push('…');
    }
    const button = (n, label, disabled) =>
        `<button data-page="${n}"${disabled ? ' disabled' : ''}
                 class="${n === page && !disabled ? 'is-on' : ''}">${label}</button>`;
    return button(page - 1, '上一页', page <= 1)
        + nums.map((n) => (n === '…'
            ? '<span class="pager__gap">…</span>'
            : `<button data-page="${n}" class="${n === page ? 'is-on' : ''}">${n}</button>`)).join('')
        + button(page + 1, '下一页', page >= pages)
        + `<span class="pager__gap">共 ${count} 条 / ${pages} 页</span>`;
}

function bindPager(pages) {
    el('video-pager').querySelectorAll('[data-page]').forEach((btn) => {
        btn.onclick = () => {
            const n = Number(btn.dataset.page);
            if (n < 1 || n > pages || n === page) return;
            page = n;
            loadList();
        };
    });
}

function renderStats(s) {
    const exts = Object.entries(s.exts || {})
        .map(([ext, count]) => `${ext}×${count}`).join(' · ');
    el('video-stats').textContent = s.total
        ? `${s.total} 个 · ${s.roots} 个文件夹 · ${fmtSize(s.bytes)}`
            + (exts ? `（${exts}）` : '')
        : '库为空';
}

async function loadList() {
    const wrap = el('video-tablewrap');
    wrap.innerHTML = '<div class="loading">加载中…</div>';
    const params = new URLSearchParams({ page: String(page), size: String(size) });
    if (q) params.set('q', q);
    try {
        const data = await api.videoList(params.toString());
        renderStats(data.stats || {});
        wrap.innerHTML = tableHtml(data.items || []);
        el('video-pager').innerHTML = pagerHtml(data.total, data.pages);
        bindPager(data.pages);
        el('video-clear').hidden = !data.total;
    } catch (err) {
        wrap.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
    }
}

/* ---------- 扫描历史 ---------- */

async function loadHistory() {
    const box = el('video-history');
    try {
        const data = await api.videoBatches();
        const list = (data.batches || []).slice(0, HISTORY_LIMIT);
        if (!list.length) {
            box.innerHTML = '';
            return;
        }
        box.innerHTML = '<span class="video__history-label">扫描记录</span>'
            + list.map((b) => `<span class="video__batch"
                    title="${esc(fmtTime(b.created_at))} 扫描 ${esc(b.root || '未命名')}">
                    ${esc(fmtShort(b.created_at))} · ${esc(b.root.split(/[\\/]/).pop() || '未命名')}
                    · +${b.inserted}${b.duplicated ? ` / 重复 ${b.duplicated}` : ''}
                    <button class="video__batch-x" data-batch="${b.id}"
                            title="撤销这次扫描">×</button>
                </span>`).join('');

        box.querySelectorAll('[data-batch]').forEach((btn) => {
            btn.onclick = async () => {
                if (!confirm('撤销这次扫描？只删除这批扫描到的文件记录，其它批次不受影响。')) return;
                try {
                    const r = await api.videoDeleteBatch(btn.dataset.batch);
                    page = 1;
                    await Promise.all([loadList(), loadHistory(), refreshVideoBadge()]);
                    notice('ok', `已撤销，删除 ${r.deleted} 条文件记录`);
                } catch (err) {
                    notice('error', '撤销失败：' + esc(err.message));
                }
            };
        });
    } catch (err) {
        box.innerHTML = '';
    }
}

/* ---------- 开关 ---------- */

export function closeVideo() {
    modal().hidden = true;
}

export function openVideo(keyword) {
    if (!rendered) skeleton();
    if (typeof keyword === 'string') {   // 详情页跳过来时带上剧名，直接看这部剧的文件
        q = keyword.trim();
        el('video-q').value = q;
    }
    modal().hidden = false;
    page = 1;
    loadList();
    loadHistory();
}

/** 顶栏按钮上的条数徽标（页面初始化时刷一次，扫描/删除后再刷）。 */
export async function refreshVideoBadge() {
    const badge = el('video-badge');
    if (!badge) return;
    try {
        const s = await api.videoStats();
        badge.textContent = s.total ? String(s.total) : '';
        badge.hidden = !s.total;
    } catch (err) {
        badge.hidden = true;
    }
}

export function bindVideo() {
    el('btn-video').addEventListener('click', () => openVideo());
    modal().addEventListener('click', (e) => {
        if (e.target === modal()) closeVideo();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal().hidden) closeVideo();
    });
    // 详情页的「在视频库中查看」：带上剧名当搜索词（跨模块只走事件，不互相 import）
    document.addEventListener('video:open', (e) => {
        openVideo(((e.detail || {}).q) || '');
    });
    refreshVideoBadge();
    // 支持直接分享 /#video 打开文件库
    if (location.hash === '#video') openVideo();
}

/* ---------- 搜索 / 每页条数 ---------- */

function bindToolbar() {
    let timer = null;
    el('video-q').addEventListener('input', (e) => {
        clearTimeout(timer);
        const value = e.target.value;
        timer = setTimeout(() => {
            q = value.trim();
            page = 1;
            loadList();
        }, SEARCH_DEBOUNCE_MS);
    });

    el('video-size').addEventListener('change', (e) => {
        size = Number(e.target.value);
        page = 1;
        loadList();
    });
}
