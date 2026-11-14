/* 后端接口的唯一入口。页面里不要直接 fetch，统一走这里，方便改地址/加错误处理。 */

async function getJson(url) {
    const res = await fetch(url);
    if (!res.ok && res.status !== 404) {
        throw new Error('HTTP ' + res.status + ' ' + url);
    }
    return res.json();
}

async function sendJson(method, url, body) {
    const res = await fetch(url, {
        method,
        headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    return res.json();
}

/** 文件上传：走 FormData，且失败时要把后端的 error 文案带出来（400/413 也有 JSON 体）。 */
async function sendForm(url, form) {
    const res = await fetch(url, { method: 'POST', body: form });
    let data = {};
    try {
        data = await res.json();
    } catch (err) {
        data = {};
    }
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
}

const dramaPath = (id) => '/api/drama/' + encodeURIComponent(id);

export const api = {
    /** 站点配置：站名 / 页脚链接 / 评论 giscus（没配评论时 giscus 为 null）。 */
    siteConfig: () => getJson('/api/config'),
    stats: () => getJson('/api/stats'),
    facets: () => getJson('/api/facets'),
    dramas: (queryString) => getJson('/api/dramas?' + queryString),
    drama: (id) => getJson(dramaPath(id)),
    reload: () => getJson('/api/reload'),

    /** 覆盖式保存标签：增删改名都提交完整列表。 */
    saveTags: (id, tags) => sendJson('POST', dramaPath(id) + '/tags', { tags }),

    /** 撤销人工编辑，恢复抓取到的原始标签。 */
    resetTags: (id) => sendJson('DELETE', dramaPath(id) + '/tags'),

    /** 收藏 / 取消收藏：返回 {ok, favorite, faved_at, total, item}。 */
    favorite: (id) => sendJson('POST', dramaPath(id) + '/favorite'),
    unfavorite: (id) => sendJson('DELETE', dramaPath(id) + '/favorite'),

    /** 详情页：按这部剧的剧名去 ED2K 链接库找资源。 */
    dramaEd2k: (id) => getJson(dramaPath(id) + '/ed2k'),

    /** 详情页：这部剧刮削匹配到的本地视频（每条带 playable / url）。 */
    dramaVideos: (id) => getJson(dramaPath(id) + '/videos'),

    /* ---------- ED2K 链接库 ---------- */

    /** 上传 TXT：文件字段名必须叫 file（后端只认这个）。 */
    uploadEd2k: (file) => {
        const form = new FormData();
        form.append('file', file, file.name);
        return sendForm('/api/ed2k/upload', form);
    },
    ed2kList: (queryString) => getJson('/api/ed2k?' + queryString),
    ed2kStats: () => getJson('/api/ed2k/stats'),
    ed2kBatches: () => getJson('/api/ed2k/batches'),
    ed2kExport: (queryString) => getJson('/api/ed2k/export?' + queryString),
    ed2kDeleteLink: (hash) => sendJson('DELETE', '/api/ed2k/link/' + encodeURIComponent(hash)),
    ed2kDeleteBatch: (id) => sendJson('DELETE', '/api/ed2k/batch/' + encodeURIComponent(id)),
    ed2kClear: () => sendJson('DELETE', '/api/ed2k/all'),

    /* ---------- 本地视频文件库 ---------- */

    /** 扫描入库：{path} 服务器扫，或 {folder, files:[{path,size}]} 浏览器清单。 */
    videoScan: (payload) => sendJson('POST', '/api/video/scan', payload),
    videoList: (queryString) => getJson('/api/video?' + queryString),
    videoStats: () => getJson('/api/video/stats'),
    videoBatches: () => getJson('/api/video/batches'),
    videoDeleteFile: (id) => sendJson('DELETE', '/api/video/file/' + encodeURIComponent(id)),
    videoDeleteBatch: (id) => sendJson('DELETE', '/api/video/batch/' + encodeURIComponent(id)),
    videoClear: () => sendJson('DELETE', '/api/video/all'),

    /* ---------- 刮削匹配（视频文件名 -> 本地剧库） ---------- */

    /** 预览（dry-run）：{root, force} -> 匹配建议，不写库。 */
    scrapeScan: (payload) => sendJson('POST', '/api/scrape/scan', payload),
    /** 执行入库：{root, force} -> 写 video_file + scrape_batch。 */
    scrapeApply: (payload) => sendJson('POST', '/api/scrape/apply', payload),
    /** 人工修正：{file_id, status: matched|ignored|unmatched, drama_id?}。 */
    scrapeMatch: (payload) => sendJson('POST', '/api/scrape/match', payload),
    scrapeStatus: () => getJson('/api/scrape/status'),
    scrapeReport: (id) => getJson('/api/scrape/report/' + encodeURIComponent(id)),
};
