/* 入口：绑定事件 + 首次加载。
   只在 main.js 里操作 DOM 事件，其他模块都只导出函数，方便按需复用。 */
import { el } from './dom.js';
import { api } from './api.js';
import { state, readUrl, syncControls } from './state.js';
import { loadStats } from './stats.js';
import { loadTags } from './tagbar.js';
import { initOnlyBar } from './onlybar.js';
import { loadList } from './list.js';
import { openDetail } from './detail.js';
import { bindEd2k } from './ed2k.js';
import { bindVideo } from './video.js';
import { bindScrape } from './scrape.js';
import { consumeLoginReturn } from './comment.js';
import { loadSite } from './site.js';

const SEARCH_DEBOUNCE_MS = 250;

function bindSearch() {
    let timer = null;
    el('q').addEventListener('input', (e) => {
        clearTimeout(timer);
        const value = e.target.value;
        timer = setTimeout(() => {
            state.q = value;
            state.page = 1;
            loadList();
        }, SEARCH_DEBOUNCE_MS);
    });
}

function bindFilters() {
    ['sort', 'order', 'min_score'].forEach((id) => {
        el(id).addEventListener('change', (e) => {
            state[id] = e.target.value;
            state.page = 1;
            loadList();
        });
    });

    el('size').addEventListener('change', (e) => {
        state.size = Number(e.target.value);
        state.page = 1;
        loadList();
    });
}

function bindReload() {
    el('btn-reload').addEventListener('click', async () => {
        await api.reload();
        loadStats();
        loadTags();
        loadList();
    });
}

/** 卡片点击用事件委托：列表重绘多少次都不用重新绑定。 */
function bindCardGrid() {
    el('grid').addEventListener('click', (e) => {
        const card = e.target.closest('.card');
        if (card) openDetail(card.dataset.id);
    });
}

function init() {
    // giscus 登录是整页跳转（target=_top），回跳地址上带 ?giscus=<session>：
    // 必须在 readUrl 之前收下，否则参数会被当成筛选条件、登录态也会丢
    const backTo = consumeLoginReturn();

    readUrl();
    syncControls();
    bindSearch();
    bindFilters();
    initOnlyBar();
    bindReload();
    bindCardGrid();
    bindEd2k();
    bindVideo();
    bindScrape();

    loadStats();
    loadTags();
    loadList();
    // 站名 / 页脚链接：独立走一次 /api/config，失败也不影响列表
    loadSite();

    // 刚登录回来：把跳走前正在看的那部剧重新打开，评论区这时就是已登录状态
    if (backTo) openDetail(backTo);
}

init();
