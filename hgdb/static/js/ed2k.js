/* ED2K 链接库：上传 TXT -> 解析入库 -> 搜索 / 删除 / 导出。
   自成模块，只依赖 dom / api / format；列表、详情弹层都不需要知道它的存在
   （详情页要打开本弹层时发 `ed2k:open` 事件，见文件末尾）。 */
import { el, esc } from './dom.js';
import { api } from './api.js';
import { fmtSize } from './format.js';

const PAGE_SIZES = [30, 50, 100, 200];
const SEARCH_DEBOUNCE_MS = 250;
const HISTORY_LIMIT = 6;

let pending = null;      // 待上传的 File
let page = 1;
let size = 50;
let q = '';
let rendered = false;    // 弹层骨架只建一次，之后只刷新内容

const modal = () => el('ed2k-modal');
const panel = () => el('ed2k-panel');

/* ---------- 格式化 ---------- */

// fmtSize 本来在本模块，详情页也要用，挪到 format.js 统一；这里再导出一次保持兼容
export { fmtSize };

const fmtTime = (text) => (text || '').replace('T', ' ');
const fmtShort = (text) => fmtTime(text).slice(5, 16);   // 10-29 21:44

/* ---------- 骨架 ---------- */

function skeleton() {
    panel().innerHTML = `
        <button class="modal__close" id="ed2k-close" title="关闭">×</button>
        <h2 class="ed2k__title">ED2K 链接库<span class="ed2k__stats" id="ed2k-stats"></span></h2>

        <input type="file" id="ed2k-file" accept=".txt,text/plain" hidden>
        <div class="ed2k__upload">
            <div class="drop" id="ed2k-drop" tabindex="0" role="button"
                 aria-label="选择或拖入 TXT 文件">
                <div class="drop__main" id="ed2k-drop-main">把 TXT 拖到这里，或点击选择文件</div>
                <div class="drop__sub">支持 UTF-8 / GBK，一行一条 ed2k 链接；库里已有的链接自动跳过</div>
            </div>
            <div class="ed2k__actions">
                <button class="btn btn--primary" id="ed2k-submit" disabled>上传并导入</button>
                <button class="btn" id="ed2k-export">导出全部</button>
                <button class="btn btn--danger" id="ed2k-clear" hidden>清空链接库</button>
            </div>
        </div>

        <div class="ed2k__result" id="ed2k-result" hidden></div>

        <div class="ed2k__bar">
            <input id="ed2k-q" type="search" autocomplete="off"
                   placeholder="搜索剧名 / 文件名 / HASH">
            <select id="ed2k-size">${PAGE_SIZES.map((n) =>
        `<option value="${n}"${n === size ? ' selected' : ''}>${n} / 页</option>`).join('')}
            </select>
        </div>

        <div class="ed2k__history" id="ed2k-history"></div>
        <div class="ed2k__tablewrap" id="ed2k-tablewrap"></div>
        <nav class="pager" id="ed2k-pager"></nav>`;

    el('ed2k-close').onclick = closeEd2k;
    bindUpload();
    bindToolbar();
    rendered = true;
}

/* ---------- 上传 ---------- */

function notice(kind, html) {
    const box = el('ed2k-result');
    box.hidden = false;
    box.className = 'ed2k__result is-' + kind;
    box.innerHTML = html;
}

function setPending(file) {
    if (!file) return;
    if (!/\.txt$/i.test(file.name) && file.type !== 'text/plain') {
        notice('error', `只支持 .txt 文件，选中的是 ${esc(file.name)}`);
        return;
    }
    pending = file;
    el('ed2k-drop-main').innerHTML =
        `<b>${esc(file.name)}</b><span>${fmtSize(file.size)}</span>`;
    el('ed2k-submit').disabled = false;
}

function clearPending() {
    pending = null;
    el('ed2k-file').value = '';
    el('ed2k-drop-main').textContent = '把 TXT 拖到这里，或点击选择文件';
    el('ed2k-submit').disabled = true;
}

async function upload() {
    if (!pending) return;
    const btn = el('ed2k-submit');
    btn.disabled = true;
    btn.textContent = '导入中…';
    try {
        const r = await api.uploadEd2k(pending);
        notice('ok',
            `<b>${esc(r.filename || pending.name)}</b>：解析 ${r.parsed} 条链接，`
            + `新增 <b>${r.inserted}</b> 条，重复 ${r.duplicated} 条，`
            + `无效行 ${r.invalid} 行`
            + (r.ignored ? `，忽略 ${r.ignored} 行非链接文本` : '')
            + (r.not_in_drama ? `；<b>${r.not_in_drama}</b> 条剧名不在短剧表` : '')
            + `；库内共 <b>${r.total}</b> 条。`
            + (r.encoding && r.encoding !== 'utf-8' ? `（按 ${esc(r.encoding)} 解码）` : ''));
        clearPending();
        page = 1;
        await Promise.all([loadList(), loadHistory(), refreshEd2kBadge()]);
    } catch (err) {
        notice('error', '导入失败：' + esc(err.message));
    } finally {
        btn.disabled = !pending;
        btn.textContent = '上传并导入';
    }
}

function bindUpload() {
    const drop = el('ed2k-drop');
    const input = el('ed2k-file');

    drop.addEventListener('click', () => input.click());
    drop.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            input.click();
        }
    });
    input.addEventListener('change', () => setPending(input.files[0]));

    ['dragenter', 'dragover'].forEach((type) => drop.addEventListener(type, (e) => {
        e.preventDefault();
        drop.classList.add('is-over');
    }));
    ['dragleave', 'drop'].forEach((type) => drop.addEventListener(type, (e) => {
        e.preventDefault();
        drop.classList.remove('is-over');
    }));
    drop.addEventListener('drop', (e) => {
        const file = e.dataTransfer.files && e.dataTransfer.files[0];
        setPending(file);
    });

    el('ed2k-submit').onclick = upload;
    el('ed2k-export').onclick = exportAll;
    el('ed2k-clear').onclick = clearAll;
}

/* ---------- 导出 / 清空 ---------- */

async function exportAll() {
    try {
        const params = new URLSearchParams();
        if (q) params.set('q', q);
        const data = await api.ed2kExport(params.toString());
        if (!data.count) {
            notice('error', '没有可导出的链接');
            return;
        }
        const blob = new Blob([data.text], { type: 'text/plain;charset=utf-8' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `ed2k_${new Date().toISOString().slice(0, 10)}.txt`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(link.href);
        notice('ok', `已导出 ${data.count} 条链接${q ? `（筛选：${esc(q)}）` : ''}`);
    } catch (err) {
        notice('error', '导出失败：' + esc(err.message));
    }
}

async function clearAll() {
    if (!confirm('清空整个 ED2K 链接库？所有链接和导入记录都会被删除，且无法撤销。')) return;
    if (!confirm('再确认一次：建议先「导出全部」备份。确定清空？')) return;
    try {
        const r = await api.ed2kClear();
        page = 1;
        await Promise.all([loadList(), loadHistory(), refreshEd2kBadge()]);
        notice('ok', `已清空，删除 ${r.deleted} 条链接`);
    } catch (err) {
        notice('error', '清空失败：' + esc(err.message));
    }
}

/* ---------- 列表 ---------- */

function rowHtml(it) {
    // in_drama=0 表示拆出来的剧名在短剧表（drama）里找不到，打标提醒
    const flag = it.in_drama ? ''
        : `<span class="ed2k-flag" title="短剧表里没有这个名字，可能是站外剧或名字写法不同">不在短剧表</span>`;
    return `<tr>
        <td class="ed2k-table__title" title="${esc(it.title)}">${esc(it.title || '—')}${flag}</td>
        <td class="ed2k-table__ep">${esc(it.episode_label || '—')}</td>
        <td class="ed2k-table__size">${fmtSize(it.size)}</td>
        <td class="ed2k-table__name" title="${esc(it.name)}">${esc(it.name)}</td>
        <td class="ed2k-table__hash" title="${esc(it.hash)}">${esc(it.hash.slice(0, 8))}…</td>
        <td class="ed2k-table__time">${esc(fmtShort(it.created_at))}</td>
        <td class="ed2k-table__ops">
            <button class="btn btn--mini" data-copy="${esc(it.url)}">复制</button>
            <button class="btn btn--mini btn--danger" data-del="${esc(it.hash)}">删除</button>
        </td>
    </tr>`;
}

function tableHtml(items) {
    if (!items.length) {
        return '<div class="empty">没有匹配的链接，换个关键词或先上传一个 TXT</div>';
    }
    return `<table class="ed2k-table">
        <thead><tr>
            <th>剧名</th><th>集</th><th>大小</th><th>文件名</th>
            <th>HASH</th><th>入库</th><th></th>
        </tr></thead>
        <tbody>${items.map(rowHtml).join('')}</tbody>
    </table>`;
}

function bindRows() {
    el('ed2k-tablewrap').onclick = async (e) => {
        const copyBtn = e.target.closest('[data-copy]');
        if (copyBtn) {
            try {
                await navigator.clipboard.writeText(copyBtn.dataset.copy);
                copyBtn.textContent = '已复制';
            } catch (err) {
                copyBtn.textContent = '复制失败';
            }
            setTimeout(() => { copyBtn.textContent = '复制'; }, 1200);
            return;
        }

        const delBtn = e.target.closest('[data-del]');
        if (!delBtn || !confirm('删除这条链接？之后重新上传同一个 TXT 可以再补回来。')) return;
        try {
            await api.ed2kDeleteLink(delBtn.dataset.del);
            await Promise.all([loadList(), refreshEd2kBadge()]);
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
    el('ed2k-pager').querySelectorAll('[data-page]').forEach((btn) => {
        btn.onclick = () => {
            const n = Number(btn.dataset.page);
            if (n < 1 || n > pages || n === page) return;
            page = n;
            loadList();
        };
    });
}

function renderStats(s) {
    el('ed2k-stats').textContent = s.total
        ? `${s.total} 条 · ${s.titles} 部 · ${fmtSize(s.bytes)}`
            + (s.last_at ? ` · 最近 ${fmtTime(s.last_at)}` : '')
        : '库为空';
}

async function loadList() {
    const wrap = el('ed2k-tablewrap');
    wrap.innerHTML = '<div class="loading">加载中…</div>';
    const params = new URLSearchParams({ page: String(page), size: String(size) });
    if (q) params.set('q', q);
    try {
        const data = await api.ed2kList(params.toString());
        renderStats(data.stats || {});
        wrap.innerHTML = tableHtml(data.items || []);
        el('ed2k-pager').innerHTML = pagerHtml(data.total, data.pages);
        bindPager(data.pages);
        bindRows();
        el('ed2k-clear').hidden = !data.total;
    } catch (err) {
        wrap.innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
    }
}

/* ---------- 导入历史 ---------- */

async function loadHistory() {
    const box = el('ed2k-history');
    try {
        const data = await api.ed2kBatches();
        const list = (data.batches || []).slice(0, HISTORY_LIMIT);
        if (!list.length) {
            box.innerHTML = '';
            return;
        }
        box.innerHTML = '<span class="ed2k__history-label">导入记录</span>'
            + list.map((b) => `<span class="ed2k__batch"
                    title="${esc(fmtTime(b.created_at))} 上传 ${esc(b.filename || '未命名')}">
                    ${esc(fmtShort(b.created_at))} · ${esc(b.filename || '未命名')}
                    · +${b.inserted}${b.duplicated ? ` / 重复 ${b.duplicated}` : ''}
                    <button class="ed2k__batch-x" data-batch="${b.id}"
                            title="撤销这次上传">×</button>
                </span>`).join('');

        box.querySelectorAll('[data-batch]').forEach((btn) => {
            btn.onclick = async () => {
                if (!confirm('撤销这次上传？只删除这批导入的链接，其它批次不受影响。')) return;
                try {
                    const r = await api.ed2kDeleteBatch(btn.dataset.batch);
                    page = 1;
                    await Promise.all([loadList(), loadHistory(), refreshEd2kBadge()]);
                    notice('ok', `已撤销，删除 ${r.deleted} 条链接`);
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

export function closeEd2k() {
    modal().hidden = true;
}

/**
 * 打开链接库。query 传了就把搜索框设成它并重新查（详情页「这部剧的资源」用）。
 * 不传则保持上一次的关键词。
 */
export function openEd2k(query) {
    if (!rendered) skeleton();
    modal().hidden = false;
    page = 1;
    if (typeof query === 'string') {
        q = query.trim();
        el('ed2k-q').value = q;
    }
    loadList();
    loadHistory();
}

/** 顶栏按钮上的条数徽标（页面初始化时刷一次，导入/删除后再刷）。 */
export async function refreshEd2kBadge() {
    const badge = el('ed2k-badge');
    if (!badge) return;
    try {
        const s = await api.ed2kStats();
        badge.textContent = s.total ? String(s.total) : '';
        badge.hidden = !s.total;
    } catch (err) {
        badge.hidden = true;
    }
}

export function bindEd2k() {
    el('btn-ed2k').addEventListener('click', () => openEd2k());
    modal().addEventListener('click', (e) => {
        if (e.target === modal()) closeEd2k();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal().hidden) closeEd2k();
    });
    // 详情页的「在这部剧的链接库里搜」：用事件解耦，detail.js 不需要 import 本模块
    document.addEventListener('ed2k:open', (e) => {
        openEd2k((e.detail && e.detail.q) || '');
    });
    refreshEd2kBadge();
    // 支持直接分享 /#ed2k 打开链接库
    if (location.hash === '#ed2k') openEd2k();
}

/* ---------- 搜索 / 每页条数 ---------- */

function bindToolbar() {
    let timer = null;
    el('ed2k-q').addEventListener('input', (e) => {
        clearTimeout(timer);
        const value = e.target.value;
        timer = setTimeout(() => {
            q = value.trim();
            page = 1;
            loadList();
        }, SEARCH_DEBOUNCE_MS);
    });

    el('ed2k-size').addEventListener('change', (e) => {
        size = Number(e.target.value);
        page = 1;
        loadList();
    });
}
