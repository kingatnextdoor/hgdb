/* 快捷筛选开关条：多选，点几个就是几个条件同时满足（交集）。
   与 tagbar 的差别：选项是固定的（写在 index.html 里、判定在后端 ONLY_FILTERS），
   不需要请求接口，所以这里不导出 load 之类的东西，只有绑定和同步两份逻辑。 */
import { el } from './dom.js';
import { state } from './state.js';
import { reload } from './list.js';

/** 按 state.only 刷新按钮选中态；从 URL 进来时也要调一次。 */
export function syncOnlyBar() {
    const bar = el('onlybar');
    if (!bar) return;
    bar.querySelectorAll('[data-only]').forEach((btn) => {
        const on = state.only.includes(btn.dataset.only);
        btn.classList.toggle('is-on', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
}

/** 绑定点击：切一个开关 -> 回第一页、重算列表。 */
export function initOnlyBar() {
    const bar = el('onlybar');
    if (!bar) return;
    bar.querySelectorAll('[data-only]').forEach((btn) => {
        btn.onclick = () => {
            const key = btn.dataset.only;
            const i = state.only.indexOf(key);
            if (i >= 0) state.only.splice(i, 1); else state.only.push(key);
            state.page = 1;
            syncOnlyBar();
            reload();
        };
    });
    syncOnlyBar();
}
