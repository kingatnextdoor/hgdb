/* 顶部统计条。加载失败只降级成一句话，不影响列表。 */
import { el, esc } from './dom.js';
import { api } from './api.js';

export async function loadStats() {
    try {
        const data = await api.stats();
        const s = data.stats || {};
        el('stats').innerHTML = [
            ['共', s.total + ' 部', false],
            ['封面', s.with_cover, false],
            ['新上架', s.new, true],
            ['详情', s.with_detail, false],
            ['收藏', s.favorite, false],
            ['本地视频', s.video, false],
            ['ED2K', s.ed2k, false],
        ].map(([label, value, warn]) =>
            `<span class="chip${warn ? ' chip--warn' : ''}">${label} <b>${esc(value)}</b></span>`
        ).join('');
    } catch (err) {
        el('stats').innerHTML = '<span class="chip">统计加载失败</span>';
    }
}
