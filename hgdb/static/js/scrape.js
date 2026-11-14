/* 刮削匹配：把视频库里的文件名对到本地剧库上。
   流程：选目录 -> 预览匹配（dry-run，可逐行改选/忽略）-> 执行入库。
   大量复用 video.css 的骨架类（video__pick / video-table / video__result），
   自己只补状态色和候选选择器（css/scrape.css）。 */
import { el, esc } from './dom.js';
import { api } from './api.js';

let rendered = false;
let status = null;       // /api/scrape/status 的结果
let root = '';           // 当前选中的目录（video_file.root 的值）
let items = [];          // 预览结果（本地改选后同步更新）
let searching = new Map();  // 行号 -> 剧名搜索结果 [{id,title}]

const modal = () => el('scrape-modal');
const panel = () => el('scrape-panel');
const SEARCH_DEBOUNCE_MS = 250;

const STATUS_TEXT = {
    matched: '已匹配', pending: '待确认', unmatched: '未匹配', ignored: '已忽略',
};

/* ---------- 骨架 ---------- */

function skeleton() {
    panel().innerHTML = `
        <button class="modal__close" id="scrape-close" title="关闭">×</button>
        <h2 class="video__title">刮削匹配<span class="video__stats" id="scrape-stats"></span></h2>

        <div class="video__pick">
            <select id="scrape-root" title="要刮削的目录（来自本地视频库）"></select>
            <button class="btn" id="scrape-preview">预览匹配</button>
            <button class="btn btn--primary" id="scrape-apply" disabled>执行入库</button>
            <label class="scrape__force" title="连已匹配/已忽略的也重新算（已忽略的仍不会被覆盖）">
                <input type="checkbox" id="scrape-force"> 重刮已匹配
            </label>
            <div class="video__pick-info">
                用文件名在本地剧库里找同名剧：匹配上的直接入库，没把握的列出来人工确认；
                只写数据库，不改动磁盘上的任何文件
            </div>
        </div>

        <div class="video__result" id="scrape-result" hidden></div>

        <div class="video__tablewrap" id="scrape-tablewrap">
            <div class="empty">先在「本地视频」里扫描文件，然后选目录点「预览匹配」</div>
        </div>`;

    el('scrape-close').onclick = closeScrape;
    el('scrape-preview').onclick = preview;
    el('scrape-apply').onclick = apply;
    el('scrape-root').onchange = (e) => { root = e.target.value; };
    bindRows();
    rendered = true;
}

/* ---------- 状态与目录下拉 ---------- */

async function loadStatus() {
    try {
        status = await api.scrapeStatus();
    } catch (err) {
        status = { files: 0, matched: 0, pending: 0, unmatched: 0, ignored: 0, roots: [] };
    }
    renderStatus();
    renderRoots();
}

function renderStatus() {
    const s = status || {};
    el('scrape-stats').textContent = s.files
        ? `匹配 ${s.matched} · 待确认 ${s.pending} · 未匹配 ${s.unmatched}` +
          (s.ignored ? ` · 已忽略 ${s.ignored}` : '')
        : '视频库为空';
}

function renderRoots() {
    const sel = el('scrape-root');
    const roots = (status && status.roots) || [];
    if (!roots.length) {
        sel.innerHTML = '<option value="">（视频库为空，先去扫描）</option>';
        sel.disabled = true;
        return;
    }
    sel.disabled = false;
    if (!root || !roots.some((r) => r.root === root)) root = roots[0].root;
    sel.innerHTML = roots.map((r) => {
        const name = r.root.split(/[\\/]/).pop() || r.root;
        return `<option value="${esc(r.root)}"${r.root === root ? ' selected' : ''}>
            ${esc(name)}（${r.files} 个文件，已匹配 ${r.matched}）</option>`;
    }).join('');
}

/* ---------- 预览 / 执行 ---------- */

function notice(kind, html) {
    const box = el('scrape-result');
    box.hidden = false;
    box.className = 'video__result is-' + kind;
    box.innerHTML = html;
}

async function preview() {
    if (!root) return;
    await run(api.scrapeScan({ root, force: el('scrape-force').checked }), '预览匹配');
}

async function apply() {
    if (!root) return;
    const r = await run(api.scrapeApply({ root, force: el('scrape-force').checked }), '执行入库');
    if (r) {
        notice('ok', `<b>${esc(r.root)}</b>：入库 ${r.applied} 条 —— 自动匹配 <b>${r.matched}</b>，`
            + `待确认 ${r.review}，未匹配 ${r.unmatched}；库内累计已匹配 ${status ? status.matched : '…'} 条。`);
        el('scrape-apply').disabled = false;
    }
}

async function run(request, label) {
    const btn = label === '执行入库' ? el('scrape-apply') : el('scrape-preview');
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = label + '中…';
    try {
        const r = await request;
        if (r && r.error) throw new Error(r.error);
        items = r.items || [];
        root = r.root || root;
        renderTable();
        if (label !== '执行入库') {
            notice('ok', `<b>${esc(r.root)}</b>：${r.total} 个文件 —— 自动匹配 <b>${r.matched}</b>，`
                + `待确认 ${r.review}，未匹配 ${r.unmatched}。检查无误后点「执行入库」。`);
        }
        await loadStatus();
        return r;
    } catch (err) {
        notice('error', label + '失败：' + esc(err.message));
        return null;
    } finally {
        btn.textContent = old;
        btn.disabled = false;
        if (label === '执行入库') el('scrape-apply').disabled = !items.length;
    }
}

/* ---------- 表格 ---------- */

const scoreText = (it) => it.match_score ? Math.round(it.match_score * 100) + '%' : '';

function matchCell(it) {
    if (it.match_status === 'matched' && it.match_title) {
        return `<span class="scrape__hit">${esc(it.match_title)}</span>`
            + `<span class="scrape__score">${scoreText(it)}</span>`;
    }
    if (it.match_status === 'ignored') return '<span class="scrape__ignored">已忽略</span>';
    if (it.match_status === 'unmatched') return '<span class="scrape__none">未找到候选</span>';
    return '—';
}

function optionsHtml(list, selectedId) {
    return list.map((d) =>
        `<option value="${esc(d.id)}"${d.id === selectedId ? ' selected' : ''}>${esc(d.title)}</option>`
    ).join('');
}

function editCell(it) {
    const fid = it.id;
    if (it.match_status === 'matched' && !it._editing) {
        return `<button class="btn btn--mini" data-act="change" data-fid="${fid}">改选</button>`
            + `<button class="btn btn--mini" data-act="ignore" data-fid="${fid}">忽略</button>`;
    }
    if (it.match_status === 'ignored') {
        return `<button class="btn btn--mini" data-act="reset" data-fid="${fid}">恢复</button>`;
    }
    const cands = it.candidates || [];
    const picked = it.drama_id || (cands[0] && cands[0].drama_id) || '';
    const found = searching.get(String(fid)) || [];
    const list = cands.map((c) => ({ id: c.drama_id, title: c.title })).concat(found);
    return `<span class="scrape__pick">
            <input type="search" class="scrape__search" data-fid="${fid}"
                   placeholder="搜剧名…" value="">
            <select class="scrape__select" data-fid="${fid}">${optionsHtml(list, picked)}</select>
        </span>
        <button class="btn btn--mini btn--primary" data-act="confirm" data-fid="${fid}">确认</button>
        <button class="btn btn--mini" data-act="ignore" data-fid="${fid}">忽略</button>`;
}

function rowHtml(it) {
    const ep = it.episode ? `<span class="scrape__ep">第${it.episode}集</span>` : '';
    return `<tr class="is-${esc(it.match_status)}">
        <td class="video-table__name" title="${esc(it.name)}">${esc(it.stem)}</td>
        <td class="scrape-cell-match">${ep}${matchCell(it)}</td>
        <td class="video-table__path" title="${esc(it.rel_path)}">${esc(it.rel_path)}</td>
        <td class="video-table__ops">${editCell(it)}</td>
    </tr>`;
}

function renderTable() {
    const wrap = el('scrape-tablewrap');
    if (!items.length) {
        wrap.innerHTML = '<div class="empty">这个目录还没有文件，先去「本地视频」扫描</div>';
        el('scrape-apply').disabled = true;
        return;
    }
    wrap.innerHTML = `<table class="video-table scrape-table">
        <thead><tr>
            <th>文件名</th><th>匹配结果</th><th>位置</th><th>操作</th>
        </tr></thead>
        <tbody>${items.map(rowHtml).join('')}</tbody>
    </table>`;
    el('scrape-apply').disabled = false;
}

function updateItem(fid, patch) {
    const it = items.find((r) => String(r.id) === String(fid));
    if (it) Object.assign(it, patch);
    renderTable();
}

/* ---------- 行内操作 ---------- */

function bindRows() {
    // 按钮走事件委托；搜索框单独防抖
    el('scrape-tablewrap').addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-act]');
        if (!btn) return;
        const fid = btn.dataset.fid;
        const act = btn.dataset.act;
        try {
            if (act === 'change') {
                updateItem(fid, { _editing: true });
                return;
            }
            if (act === 'confirm') {
                const sel = panel().querySelector(`.scrape__select[data-fid="${fid}"]`);
                const dramaId = sel && sel.value;
                if (!dramaId) { notice('error', '先从候选里选一部剧，或搜索后再确认'); return; }
                const opt = sel.selectedOptions[0];
                await api.scrapeMatch({ file_id: fid, status: 'matched', drama_id: dramaId });
                updateItem(fid, {
                    match_status: 'matched', drama_id: dramaId,
                    match_title: opt ? opt.textContent : '', match_score: 1, _editing: false,
                });
                await loadStatus();
                return;
            }
            if (act === 'ignore') {
                await api.scrapeMatch({ file_id: fid, status: 'ignored' });
                updateItem(fid, { match_status: 'ignored', match_title: '', _editing: false });
                await loadStatus();
                return;
            }
            if (act === 'reset') {
                await api.scrapeMatch({ file_id: fid, status: 'unmatched' });
                updateItem(fid, { match_status: 'unmatched', drama_id: null, match_title: '' });
                await loadStatus();
            }
        } catch (err) {
            notice('error', '操作失败：' + esc(err.message));
        }
    });

    el('scrape-tablewrap').addEventListener('input', (e) => {
        const input = e.target.closest('.scrape__search');
        if (!input) return;
        const fid = input.dataset.fid;
        const q = input.value.trim();
        clearTimeout(input._timer);
        input._timer = setTimeout(async () => {
            if (q.length < 2) { searching.delete(fid); renderTable(); return; }
            try {
                const r = await api.dramas(new URLSearchParams({ q, size: '8' }).toString());
                searching.set(fid, (r.items || []).map((d) => ({ id: d.id, title: d.title })));
                renderTable();
                // 重绘后焦点丢了，还回去
                const again = panel().querySelector(`.scrape__search[data-fid="${fid}"]`);
                if (again) { again.value = q; again.focus(); }
            } catch (err) { /* 搜索失败就只留引擎给的候选 */ }
        }, SEARCH_DEBOUNCE_MS);
    });
}

/* ---------- 开关 ---------- */

export function closeScrape() {
    modal().hidden = true;
}

export function openScrape() {
    if (!rendered) skeleton();
    modal().hidden = false;
    items = [];
    searching.clear();
    renderTable();
    loadStatus();
}

export function bindScrape() {
    modal().addEventListener('click', (e) => {
        if (e.target === modal()) closeScrape();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal().hidden) closeScrape();
    });
    // 支持直接分享 /#scrape 打开
    if (location.hash === '#scrape') openScrape();
}
