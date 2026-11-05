# -*- coding: utf-8 -*-
"""主题集中度（focus）：这条记忆到底在讲什么

问题：一条 400 字的汇报里顺手提了一次「迅雷」，和一条 100 字专门讲迅雷的，
按"命中/未命中"算出来一样相关，于是噪音全排前面。

focus = 命中词在这条记忆全部词权重里占的比例
  · 「禁用迅雷广告组件」：迅雷/ad/gdt 占 40% 权重 → focus 高
  · 「好，那就这样运行…今天干了不少活：迅雷去广告」：迅雷占 3% → focus 低

于是同样命中，前者排前面。不需要模型，也不算新特征，就是换个分母。
"""
import math
import re

# 延迟导入，避开 retriever <-> focus 的循环依赖
# （retriever 在模块顶层导入 focus）

_CACHE = {}          # mid -> {term: weight}
_DF = {}
_N = 0


def terms_of(text):
    from .retriever import STOPWORDS, STOP_CHARS
    q = (text or "").lower()
    out = []
    for m in re.findall(r"[a-z0-9_\-]{2,}", q):
        if m not in STOPWORDS:
            out.append(m)
    cn = re.sub(r"[^\u4e00-\u9fa5]", " ", q)
    for chunk in cn.split():
        if len(chunk) == 1:
            if chunk not in STOP_CHARS:
                out.append(chunk)
            continue
        for i in range(len(chunk) - 1):
            bg = chunk[i:i + 2]
            if bg in STOPWORDS:
                continue
            if bg[0] in STOP_CHARS and bg[1] in STOP_CHARS:
                continue
            out.append(bg)
        if len(chunk) <= 4 and chunk not in STOPWORDS:
            out.append(chunk)
    return out


def build(rows):
    """rows: [(id, text)] — 预计算每条记忆的词权重分布"""
    global _DF, _N
    _CACHE.clear()
    df = {}
    tfs = {}
    for mid, text in rows:
        g = terms_of(text)
        tf = {}
        for t in g:
            tf[t] = tf.get(t, 0) + 1
        tfs[mid] = tf
        for t in set(g):
            df[t] = df.get(t, 0) + 1
    _DF = df
    _N = max(1, len(rows))

    for mid, tf in tfs.items():
        w = {}
        for t, c in tf.items():
            idf = math.log(1 + _N / (1 + df.get(t, 0)))
            w[t] = (1 + math.log(c)) * idf      # 次线性 tf
        _CACHE[mid] = w
    return len(_CACHE)


def focus(mid, query_terms):
    """命中词在这条记忆里的权重占比，0~1"""
    w = _CACHE.get(mid)
    if not w:
        return 0.0
    total = sum(w.values())
    if total <= 0:
        return 0.0
    hit = 0.0
    for t in query_terms:
        tl = (t or "").lower()
        if not tl:
            continue
        if tl in w:
            hit += w[tl]
        else:
            # 查询词可能是某个 bigram 的一部分
            for k, v in w.items():
                if tl in k:
                    hit += v * 0.5
                    break
    return min(1.0, hit / total)


def coverage(mid, query_terms):
    """查询词有多少比例出现在这条记忆里"""
    w = _CACHE.get(mid)
    if not w or not query_terms:
        return 0.0
    hit = 0
    for t in query_terms:
        tl = (t or "").lower()
        if tl and (tl in w or any(tl in k for k in w)):
            hit += 1
    return hit / max(1, len(query_terms))
