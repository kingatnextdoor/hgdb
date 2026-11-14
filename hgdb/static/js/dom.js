/* DOM 与转义小工具。所有拼接 HTML 的地方都必须过 esc()。 */

export const el = (id) => document.getElementById(id);

const ESCAPES = {
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
};

export function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}
