/* 标签筛选条：多选，选中即筛选。
   收起态由 CSS 裁到两行，「展开全部」按钮在栏外（html 里），所以永远点得到。 */
import { el, esc } from './dom.js';
import { api } from './api.js';
import { state } from './state.js';
import { reload } from './list.js';

const COLLAPSE_THRESHOLD = 40;   // 超过这个数量才显示「展开全部」

export async function loadTags() {
    try {
        const data = await api.facets();
        const tags = data.tags || [];
        const bar = el('tagbar');
        const more = el('tag-more');

        bar.innerHTML = tags.map((t) =>
            `<button class="tag${state.tags.includes(t.name) ? ' is-on' : ''}" data-tag="${esc(t.name)}">`
            + `${esc(t.name)} <span>${t.count}</span></button>`
        ).join('');

        bar.querySelectorAll('[data-tag]').forEach((btn) => {
            btn.onclick = () => {
                const name = btn.dataset.tag;
                const i = state.tags.indexOf(name);
                if (i >= 0) state.tags.splice(i, 1); else state.tags.push(name);
                state.page = 1;
                btn.classList.toggle('is-on');
                reload();
            };
        });

        if (more) {
            more.hidden = tags.length <= COLLAPSE_THRESHOLD;
            more.textContent = '展开全部 ' + tags.length;
            more.onclick = () => {
                const open = bar.classList.toggle('is-open');
                more.textContent = open ? '收起' : '展开全部 ' + tags.length;
            };
        }
    } catch (err) { /* 标签加载失败不影响列表 */ }
}
