/* 卡片墙 + 分页。
   注意：这里**不**绑定卡片点击（那会让 list 反向依赖 detail，形成循环）。
   点击由 main.js 用事件委托接管。 */
import { el, esc } from './dom.js';
import { api } from './api.js';
import { state, writeUrl, queryString } from './state.js';

const grid = () => el('grid');

export function cardHtml(it) {
    const flags = [];
    if (it.is_new) flags.push('<span class="badge badge--flag">新上架</span>');
    else if (it.is_original) flags.push('<span class="badge badge--flag">原创</span>');
    // 收藏标记放右上角：左上被「新上架」占着，右下是集数
    const fav = it.favorite ? '<span class="badge badge--fav" title="已收藏">★</span>' : '';

    const cover = it.cover
        ? `<img src="${esc(it.cover)}" title="${esc(it.desc)}" alt="${esc(it.title)}" loading="lazy">`
        : '';
    const meta = [it.category, it.update_text || it.update_time]
        .filter(Boolean).join(' · ');

    return `<article class="card" data-id="${esc(it.id)}">
        <div class="card__cover${it.cover ? '' : ' is-empty'}">
            ${cover}
            ${flags.join('')}
            ${fav}
            ${it.score_text ? `<span class="badge badge--score">${esc(it.score_text)}</span>` : ''}
            ${it.episode_text ? `<span class="badge badge--ep">${esc(it.episode_text)}</span>` : ''}
        </div>
        <h3 class="card__title" title="${esc(it.title)}">${esc(it.title)}</h3>
        <div class="card__meta">&nbsp;&nbsp;</div>
<!--        <div class="card__meta">${esc(meta || '&nbsp;')}</div>-->
    </article>`;
}

export function pagerHtml(total, pages) {
    const cur = state.page;
    const nums = [];
    const gapMark = '<span class="pager__gap">…</span>';
    const push = (n) => nums.push(
        `<button data-page="${n}" class="${n === cur ? 'is-on' : ''}">${n}</button>`);

    for (let n = 1; n <= pages; n++) {
        if (n === 1 || n === pages || Math.abs(n - cur) <= 2) push(n);
        else if (nums[nums.length - 1] !== gapMark) nums.push(gapMark);
    }
    return `<button data-page="${cur - 1}" ${cur <= 1 ? 'disabled' : ''}>上一页</button>`
        + nums.join('')
        + `<button data-page="${cur + 1}" ${cur >= pages ? 'disabled' : ''}>下一页</button>`
        + `<span class="pager__gap">共 ${total} 部 / ${pages} 页</span>`;
}

export async function loadList() {
    const loading = el('loading');
    const emptyBox = el('empty');

    loading.hidden = false;
    emptyBox.hidden = true;
    grid().innerHTML = '';
    writeUrl();

    try {
        const data = await api.dramas(queryString());
        loading.hidden = true;

        if (!data.items || !data.items.length) {
            emptyBox.hidden = false;
            el('summary').textContent = '';
            el('pager').innerHTML = '';
            return;
        }
        grid().innerHTML = data.items.map(cardHtml).join('');
        el('summary').innerHTML =
            `共 <b>${data.total}</b> 部，第 <b>${data.page}</b>/<b>${data.pages}</b> 页`;
        el('pager').innerHTML = pagerHtml(data.total, data.pages);
        bindPager(data.pages);
    } catch (err) {
        loading.hidden = true;
        emptyBox.hidden = false;
        emptyBox.textContent = '列表加载失败：' + err.message;
    }
}

function bindPager(pages) {
    el('pager').querySelectorAll('[data-page]').forEach((btn) => {
        btn.onclick = () => {
            const n = Number(btn.dataset.page);
            if (n < 1 || n > pages || n === state.page) return;
            state.page = n;
            loadList();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        };
    });
}

/** 筛选条件变化后重刷列表（保持当前页语义由调用方决定）。 */
export function reload() {
    loadList();
}
