/* 页面状态 + 地址栏同步。
   筛选条件全部保留在 URL 上，复制链接就能分享同一份筛选结果。 */
import { el } from './dom.js';

export const DEFAULT_SIZE = 48;

export const SORTS = {
    update: 1, score: 1, episode: 1, views: 1, title: 1, added: 1, changed: 1,
};

export const state = {
    q: '',
    tags: [],
    sort: 'update',
    order: 'desc',
    only: [],          // 快捷筛选开关，多选（与 tags 同理，走逗号分隔的 URL）
    min_score: '',
    size: DEFAULT_SIZE,
    page: 1,
};

export const DEFAULT_QUERY = {
    sort: 'update', order: 'desc', size: DEFAULT_SIZE, page: 1,
};

export function readUrl() {
    const p = new URLSearchParams(location.search);
    state.q = p.get('q') || '';
    state.tags = (p.get('tag') || '').split(',').filter(Boolean);
    state.sort = SORTS[p.get('sort')] ? p.get('sort') : 'update';
    state.order = p.get('order') === 'asc' ? 'asc' : 'desc';
    state.only = (p.get('only') || '').split(',').filter(Boolean);
    state.min_score = p.get('min_score') || '';
    state.size = Number(p.get('size')) || DEFAULT_SIZE;
    state.page = Number(p.get('page')) || 1;
}

/** 写回地址栏：只写与默认值不同的项，链接更短。 */
export function writeUrl() {
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    if (state.tags.length) p.set('tag', state.tags.join(','));
    if (state.sort !== DEFAULT_QUERY.sort) p.set('sort', state.sort);
    if (state.order !== DEFAULT_QUERY.order) p.set('order', state.order);
    if (state.only.length) p.set('only', state.only.join(','));
    if (state.min_score) p.set('min_score', state.min_score);
    if (state.size !== DEFAULT_QUERY.size) p.set('size', String(state.size));
    if (state.page !== DEFAULT_QUERY.page) p.set('page', String(state.page));
    const qs = p.toString();
    history.replaceState(null, '', qs ? '?' + qs : location.pathname);
}

/** 把 state 反映到工具栏控件（从 URL 进来时要调一次）。
    快捷筛选开关不在这里：它们是按钮，由 onlybar.js 的 syncOnlyBar() 同步。 */
export function syncControls() {
    el('q').value = state.q;
    el('sort').value = state.sort;
    el('order').value = state.order;
    el('min_score').value = state.min_score;
    el('size').value = String(state.size);
}

/** 列表接口的查询串：与 writeUrl 不同，这里默认值也要显式带上。 */
export function queryString() {
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    if (state.tags.length) p.set('tag', state.tags.join(','));
    p.set('sort', state.sort);
    p.set('order', state.order);
    if (state.only.length) p.set('only', state.only.join(','));
    if (state.min_score) p.set('min_score', state.min_score);
    p.set('size', String(state.size));
    p.set('page', String(state.page));
    return p.toString();
}
