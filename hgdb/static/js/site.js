/* 站点身份：站名、文档标题、页脚链接。
   全部来自 `GET /api/config` 的 site 段（hgdb/config.py 的 SITE_NAME / SITE_TAGLINE /
   SITE_LINKS）。HTML 里只写中性占位文字，这样同一份静态文件能部署成不同的站，
   开源版本也不至于带着别人的站名跑。

   取不到配置就保持占位文字，不报错、不阻塞页面 —— 页脚只是一个链接条。 */
import { el } from './dom.js';
import { api } from './api.js';

/** 重建页脚：SITE_LINKS 为空就整块收起来（不留一条空白的横线）。 */
function renderFooter(links) {
    const footer = el('site-footer');
    footer.textContent = '';
    if (!links.length) {
        footer.hidden = true;
        return;
    }
    links.forEach((link, index) => {
        if (index) {
            const sep = document.createElement('span');
            sep.setAttribute('aria-hidden', 'true');
            sep.textContent = '·';
            footer.appendChild(sep);
        }
        const a = document.createElement('a');
        a.href = link.url;
        a.target = '_blank';
        a.rel = 'noopener';
        a.textContent = link.label;
        footer.appendChild(a);
    });
    footer.hidden = false;
}

export async function loadSite() {
    let site = null;
    try {
        site = (await api.siteConfig()).site;
    } catch (err) {
        return;                     // 配置接口挂了：保留占位文字
    }
    if (!site) return;

    if (site.name) {
        document.querySelectorAll('[data-site-name]').forEach((node) => {
            node.textContent = site.name;
        });
        document.title = site.tagline ? `${site.name} · ${site.tagline}` : site.name;
    }
    renderFooter(site.links || []);
}
