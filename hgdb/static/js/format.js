/* 格式化：数字、时长、字节数、信息行、标签列表。纯函数，便于单独复用。 */
import { esc } from './dom.js';

const KB = 1024;
const MB = KB * 1024;
const GB = MB * 1024;

export function fmtDuration(sec) {
    if (!sec) return '';
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m} 分 ${s} 秒`;
}

export function fmtNum(n) {
    if (n == null) return '';
    if (n >= 10000) return (n / 10000).toFixed(1) + 'w';
    return String(n);
}

/** 字节数 -> 85.9 MB / 1.20 GB；0 / null 显示 —。 */
export function fmtSize(bytes) {
    if (!bytes) return '—';
    if (bytes >= GB) return (bytes / GB).toFixed(2) + ' GB';
    if (bytes >= MB) return (bytes / MB).toFixed(1) + ' MB';
    return Math.max(1, Math.round(bytes / KB)) + ' KB';
}

export function factHtml(label, value) {
    if (value == null || value === '') return '';
    return `<div class="fact"><b>${esc(label)}</b><span>${esc(value)}</span></div>`;
}

/** 详情页只读态下的标签列表；没有标签时给一句引导。 */
export function tagsView(tags) {
    return (tags || []).length
        ? tags.map((t) => `<a href="/?tag=${esc(t)}"><span class="topic">${esc(t)}</span></a>`).join('')
        : '<span class="hint">暂无标签，点右上「编辑」添加</span>';
}
