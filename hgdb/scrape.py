# -*- coding: utf-8 -*-
"""刮削匹配引擎：文件主名 -> 本地数据集里的剧，纯函数，不碰库也不碰 HTTP。

「刮削」在这个项目里是对内匹配：本地数据集本来就有 1317 部剧的
标题 / 简介 / 评分 / 标签，把视频文件名对上去即可，不请求任何外部源。

匹配为什么不能只靠相等
----------------------
文件名几乎从来不会和剧名一模一样::

    www.98T.la@50元买断人妻-011.mp4        <- 站点水印 + 集数
    神医毒妃（2026）【4KHDR 国语】.mp4      <- 括号备注 + 画质词
    女尊之妖男 chambre 01.mp4               <- 隔壁语言混进来的词

所以按「先严格后宽松」走四级，每级命中即停：

    1. exact     清洗后与剧名相等（lower 比较）        0.95 自动
    2. contains  一方包含另一方（都 >= 2 字）          0.80 自动
    3. fuzzy     difflib 相似度 0.85 以上              自动
    4. fuzzy     相似度 0.60 ~ 0.85                    pending 待人工确认
    5. 都没戏                                          unmatched

集数解析直接复用 ed2k.split_name()：ED2K 文件名和本地视频文件名的
集数写法是同一批人起的习惯，没必要写两份正则。
"""
import difflib
import re

from hgdb import ed2k

# 自动确认的最低置信度：>= 它直接写 matched，以下进 pending 等人工点头
AUTO_SCORE = 0.85
# 模糊匹配的下限：再低就当没看见，否则「XX lovers」能匹配到任何两个字
FUZZY_MIN = 0.60
# contains 匹配时两边的最小长度：单字剧名/单字文件名包含关系太容易误伤
CONTAIN_MIN = 2
# 人工确认时最多给几个候选
MAX_CANDIDATES = 5

# 画质/语言/放送等噪音词：整词去掉。顺序无关，反复套用到稳定
_NOISE = re.compile(
    r'(?:4k|8k|2160p?|1080p?|720p?|480p?|uhd|hdr(?:10\+?)?|sdr|h\.?26[45]|'
    r'x26[45]|hevc|avc|aac|flac|60fps?|杜比|全景声|高清|超清|蓝光|国语|粤语|'
    r'英语|中字|双字|国粤双语|中英双字|日语|无水印|修复版?|内部版|抢鲜版)',
    re.I)
# 括号备注：（2026）【国语】【4K】—— 内容一律不要
_BRACKETS = re.compile(r'[（(【\[][^）)】\]]*[）)】\]]')
# 清洗完尾巴上残留的分隔符
_TAIL_CLEAN = re.compile(r'[\s\-_.·：:,，]+$')


def clean_stem(stem):
    """把文件主名洗成「更像剧名」的串：去水印/画质词/括号备注/首尾分隔符。"""
    text = ed2k.SITE_PREFIX.sub('', str(stem or '').strip())
    text = _BRACKETS.sub(' ', text)
    # 噪音词反复套到稳定：「1080P 国语 4K」要洗两次才能干净
    for _ in range(3):
        before = text
        text = _NOISE.sub(' ', text)
        text = text.strip(' \t-_.·：:,，')
        if text == before:
            break
    return _TAIL_CLEAN.sub('', text).strip()


def parse_episode(stem):
    """从文件主名拆出 (剧名候选, 起始集号, 集数标记)。

    直接借用 ED2K 的解析（第X集 / -01 / .EP03 / 01-10 合集 都认），
    集数标记里的空格顺手去掉，跟 ed2k 表的存法一致。
    """
    title, episode, label = ed2k.split_name(str(stem or ''))
    return title, episode, re.sub(r'\s+', '', label)


class TitleMatcher:
    """拿一份数据集的剧名列表，回答「这个文件名最像哪部剧」。

    dramas 传数据集的 items（至少有 id / title）。构建时把剧名 lower
    一次，后面所有比较都基于小写；1317 部的规模下暴力比对毫无压力。
    """

    def __init__(self, dramas):
        self.entries = []          # (lower_title, drama_id, title)
        self.by_lower = {}
        for item in dramas or ():
            title = (item.get('title') or '').strip()
            if not title:
                continue
            low = title.lower()
            self.entries.append((low, str(item.get('id') or ''), title))
            self.by_lower.setdefault(low, []).append((str(item.get('id') or ''), title))

    # ---- 各级匹配 ----

    def _exact(self, clean):
        hits = self.by_lower.get(clean.lower())
        if not hits:
            return None
        if len(hits) > 1:
            # 同名剧有多部：不敢替用户做主，转 pending
            return self._result(None, '', 0.95, 'exact', 'pending',
                                self._cands([(did, t, 0.95) for did, t in hits]))
        return self._result(hits[0][0], hits[0][1], 0.95, 'exact', 'matched', [])

    def _contains(self, clean):
        low = clean.lower()
        if len(low) < CONTAIN_MIN:
            return None
        hits = []
        for title_low, did, title in self.entries:
            if title_low == low:
                continue                      # exact 那级已经处理过
            if len(title_low) >= CONTAIN_MIN and (title_low in low or low in title_low):
                hits.append((did, title))
        if not hits:
            return None
        if len(hits) > 1:
            return self._result(None, '', 0.80, 'contains', 'pending',
                                self._cands([(did, t, 0.80) for did, t in hits]))
        return self._result(hits[0][0], hits[0][1], 0.80, 'contains', 'matched', [])

    def _fuzzy(self, clean):
        low = clean.lower()
        if len(low) < CONTAIN_MIN or not self.entries:
            return None
        scored = []
        for title_low, did, title in self.entries:
            # real_quick_ratio 便宜，先拿它把明显不像的挡掉再算完整 ratio
            sm = difflib.SequenceMatcher(None, low, title_low)
            if sm.real_quick_ratio() < FUZZY_MIN:
                continue
            ratio = sm.ratio()
            if ratio >= FUZZY_MIN:
                scored.append((ratio, did, title))
        if not scored:
            return None
        scored.sort(key=lambda row: (-row[0], row[1]))
        best_ratio, best_id, best_title = scored[0]
        score = round(min(best_ratio, 0.85), 4)   # 模糊匹配封顶 0.85，别冒充 exact
        status = 'matched' if best_ratio >= AUTO_SCORE else 'pending'
        cands = self._cands(
            [(did, t, round(min(r, 0.85), 4)) for r, did, t in scored[:MAX_CANDIDATES]])
        return self._result(best_id, best_title, score, 'fuzzy', status, cands)

    # ---- 结果组装 ----

    @staticmethod
    def _cands(rows):
        return [{'drama_id': did, 'title': title, 'score': score}
                for did, title, score in rows]

    @staticmethod
    def _result(drama_id, title, score, level, status, candidates):
        return {
            'drama_id': drama_id,
            'title': title,
            'score': round(float(score), 4),
            'level': level,
            'status': status,
            'candidates': candidates,
        }

    def match(self, stem):
        """匹配一个文件主名，返回完整决策 dict（含集数解析）。"""
        raw_title, episode, label = parse_episode(stem)
        clean = clean_stem(raw_title)
        out = self._result(None, '', 0.0, 'none', 'unmatched', [])
        if clean:
            for level in (self._exact, self._contains, self._fuzzy):
                found = level(clean)
                if found is not None:
                    out = found
                    break
        out['cleaned'] = clean
        out['episode'] = episode
        out['episode_label'] = label
        return out


def match_files(stems, dramas):
    """批量匹配：stems -> [match 结果]，dramas 每个文件共用一份 matcher。"""
    matcher = TitleMatcher(dramas)
    return [dict(matcher.match(stem), stem=str(stem or '')) for stem in stems]
