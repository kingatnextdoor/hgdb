/* 评论区：giscus —— 评论实体存在 GitHub Discussions 里，本站只负责嵌一个 iframe。
   · 配置从 `GET /api/config` 取（web/config.py 里的 GISCUS_*）；没配就不加载 giscus.app 的脚本
   · 一部剧一条讨论：mapping=specific + term=<term_prefix>:<剧 id>，剧名改了也不会串
   · 高度由 giscus 自己算好、postMessage 通知 client.js 设置，这里不用管家
   · 和本项目的其他跨模块约定一致：详情页只调这里的函数，不直接碰 giscus 的细节 */
import { api } from './api.js';
import { el } from './dom.js';

const GISCUS_SRC = 'https://giscus.app/client.js';

/* giscus 自己用的 localStorage 键名（client.js 里写死的），登录态就存在这里 */
const SESSION_KEY = 'giscus-session';
/* 跳去 GitHub 之前正在看哪部剧：回来要能把弹层重新打开 */
const RETURN_KEY = 'giscus:return-to';

let confPromise = null;      // 配置只取一次（一个页面生命周期内不会变）

/** 登录回来时记住的那部剧。 */
export function rememberLoginReturn(dramaId) {
    try {
        sessionStorage.setItem(RETURN_KEY, String(dramaId));
    } catch (e) { /* 隐私模式存不了，最多是回来不自动弹详情 */ }
}

export function forgetLoginReturn() {
    try {
        sessionStorage.removeItem(RETURN_KEY);
    } catch (e) { /* 同上 */ }
}

/**
 * 收下 GitHub 授权回跳带来的 `?giscus=<session>`，返回"登录前正在看的剧"。
 *
 * 为什么必须自己收：giscus 的「使用 GitHub 登录」是 `<a target="_top">`，
 * 点下去是**整页**跳到 GitHub，授权完由 giscus.app 302 回本页并带上 `?giscus=`。
 * 这个参数原本由页面上的 client.js 读取（读到了才写进 localStorage），
 * 而我们的 client.js 只在打开详情弹层时才注入 —— 回跳落地时弹层是关着的，
 * 没人消费这个参数，登录态就白丢了（表现为：授权成功，但评论区还是「使用 GitHub 登录」）。
 */
export function consumeLoginReturn() {
    let url;
    try {
        url = new URL(location.href);
    } catch (e) {
        return '';
    }
    const session = url.searchParams.get('giscus');
    if (!session) return '';

    try {
        // 与 client.js 的写法完全一致：存 JSON 字符串，否则它 JSON.parse 会失败并清空
        localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    } catch (e) { /* 存不了也继续把地址栏洗干净，别留个带 token 的链接 */ }

    url.searchParams.delete('giscus');
    history.replaceState(null, document.title, url.pathname + url.search + url.hash);
    console.info('[giscus] 已接收登录回跳，登录态已写入 localStorage');

    try {
        const id = sessionStorage.getItem(RETURN_KEY) || '';
        sessionStorage.removeItem(RETURN_KEY);
        return id;
    } catch (e) {
        return '';
    }
}

function loadConfig() {
    if (!confPromise) {
        confPromise = api.siteConfig()
            .catch((err) => ({ giscus: null, error: err.message }));
    }
    return confPromise;
}

/** 详情模板里的占位块；真正的 iframe 由 mountComments 异步塞进去。 */
export function commentsSectionHtml() {
    return `<div class="section" id="comment-section">
        <div class="section__label">评论
            <span class="comments__note">GitHub 账号登录后发言，内容存在讨论区</span></div>
        <div class="comments" id="comment-box"><div class="loading">加载评论…</div></div>
    </div>`;
}

function showHint(html) {
    const box = el('comment-box');
    if (box) box.innerHTML = `<div class="hint">${html}</div>`;
}

/** giscus 建讨论时把 `<meta og:description>` 的内容当讨论描述：写上剧名，
    否则 GitHub 讨论列表里全是 `drama:398` 这种，根本认不出是哪部剧。
    自己加的 meta 关弹层时删掉，页面自带的改完还原，不留下痕迹。 */
function tagDiscussion(dramaId, title) {
    const name = String(title || '').trim();
    if (!name) return;
    let meta = document.querySelector('meta[property="og:description"]');
    if (!meta) {
        meta = document.createElement('meta');
        meta.setAttribute('property', 'og:description');
        meta.dataset.giscusCreated = '1';
        document.head.appendChild(meta);
    } else if (meta.dataset.giscusSaved === undefined) {
        meta.dataset.giscusSaved = meta.getAttribute('content') || '';
    }
    meta.setAttribute('content', `${name}（ID ${dramaId}）`);
}

function restoreMeta() {
    document.querySelectorAll('meta[property="og:description"][data-giscus-created]')
        .forEach((m) => m.remove());
    document.querySelectorAll('meta[property="og:description"][data-giscus-saved]')
        .forEach((m) => {
            m.setAttribute('content', m.dataset.giscusSaved);
            delete m.dataset.giscusSaved;
        });
}

/** 打开某部剧时调用：没配给说明，配了就把 giscus 的 script 插进去。 */
export async function mountComments(dramaId, title) {
    const box = el('comment-box');
    if (!box) return;

    const conf = await loadConfig();
    // 等配置期间可能已经换了剧 / 关了弹层，这时旧容器已经不在文档里
    if (!box.isConnected) return;

    const g = (conf || {}).giscus;
    if (!g) {
        box.innerHTML = `<div class="hint">评论区还没配置：在仓库根的 <code>.env</code> 里填 `
            + '<code>GISCUS_REPO</code> / <code>GISCUS_REPO_ID</code> / '
            + '<code>GISCUS_CATEGORY_ID</code>（值从 '
            + '<a href="https://giscus.app" target="_blank" rel="noreferrer">giscus.app</a> 生成），'
            + '重启服务即可生效。</div>';
        return;
    }

    const prefix = g.term_prefix || 'drama';
    const want = g.mapping === 'specific' ? `${prefix}:${dramaId}` : '';
    // 同一部剧重复渲染时别把已经加载好的 iframe 再冲一遍
    if (want && box.dataset.term === want && box.querySelector('iframe.giscus-frame')) return;

    tagDiscussion(dramaId, title);
    box.dataset.term = want;
    box.innerHTML = '';

    const script = document.createElement('script');
    script.src = GISCUS_SRC;
    script.async = true;
    script.crossOrigin = 'anonymous';
    const attrs = {
        'data-repo': g.repo,
        'data-repo-id': g.repo_id,
        'data-category': g.category || '',
        'data-category-id': g.category_id,
        'data-mapping': g.mapping,
        'data-strict': '0',
        'data-reactions-enabled': '1',
        'data-emit-metadata': '0',
        'data-input-position': 'top',
        'data-theme': g.theme,
        'data-lang': g.lang,
        'data-loading': 'lazy',
    };
    if (want) attrs['data-term'] = want;
    Object.entries(attrs).forEach(([k, v]) => script.setAttribute(k, v));
    script.addEventListener('error', () => {
        if (!box.isConnected) return;
        showHint('giscus 脚本加载失败（giscus.app 连不上？）。检查网络后重新打开这部剧即可。');
    });
    box.appendChild(script);
}

/** 关弹层时调用：清掉 iframe，别让 giscus 在后台继续轮询。 */
export function unmountComments() {
    const box = el('comment-box');
    if (box) {
        box.innerHTML = '';
        delete box.dataset.term;
    }
    restoreMeta();
}
