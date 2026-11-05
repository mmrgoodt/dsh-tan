# -*- coding: utf-8 -*-
"""给每条记忆算「主题词」——这是区分「主题是迅雷」和「顺带提到迅雷」的关键

做法：纯 TF-IDF，不用模型。
  · 一条记忆里出现越多、在其他记忆里越少见的词 → 越能代表它的主题
  · 「迅雷」在「禁用迅雷广告组件」里出现 3 次、别处少见 → 进主题词
  · 「迅雷」在「好，那就先这样运行着…今天干了不少活：迅雷去广告」里出现 1 次
    且整段主题是别的 → 权重被长度和低词频压下去，进不了主题词

检索时：query 命中主题词的，比只命中正文的，相关度高一个档次。
"""
import io
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hippocampus.db import DB
from hippocampus.retriever import STOPWORDS, STOP_CHARS

TOP_K = 6          # 每条记忆取几个主题词
MIN_DF = 2          # 只出现过 1 次的多半是切出来的碎片


def grams(text):
    """中文 2-gram + 英文单词（和检索端同一套切法，保证对得上）"""
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


def _pmi_filter(docs):
    """用点互信息砍掉「项名」「装插」这种伪词

    纯 2-gram 切法会产出大量碎片：它们的两个单字各自很常见，凑一起没有意义。
    PMI = log(P(bg) / (P(c1)P(c2)))，越低越像碎片。
    """
    uni = {}
    bi = {}
    total = 0
    for g in docs.values():
        for t in g:
            if len(t) == 2 and all("一" <= ch <= "龥" for ch in t):
                bi[t] = bi.get(t, 0) + 1
                uni[t[0]] = uni.get(t[0], 0) + 1
                uni[t[1]] = uni.get(t[1], 0) + 1
        total += max(1, len(g))
    if not total:
        return set()
    bad = set()
    for bg, c in bi.items():
        p_bg = c / total
        p_1 = uni.get(bg[0], 1) / total
        p_2 = uni.get(bg[1], 1) / total
        pmi = math.log(p_bg / max(1e-9, p_1 * p_2))
        # 出现次数少 + PMI 低 → 碎片
        if pmi < 1.0 and c < 3:
            bad.add(bg)
    return bad


def build_keywords(rows):
    """rows: [(id, content)] -> {id: [关键词]}"""
    # ① 文档频率
    df = {}
    docs = {}
    for mid, text in rows:
        g = grams(text)
        docs[mid] = g
        for t in set(g):
            df[t] = df.get(t, 0) + 1
    n = max(1, len(rows))
    junk = _pmi_filter(docs)

    # ② 每条算 TF-IDF，取 top-k
    out = {}
    for mid, g in docs.items():
        if not g:
            out[mid] = []
            continue
        tf = {}
        for t in g:
            tf[t] = tf.get(t, 0) + 1
        total = sum(tf.values()) or 1
        scored = []
        for t, c in tf.items():
            if df.get(t, 0) < MIN_DF:
                continue
            if t in junk:
                continue
            idf = math.log(1 + n / (1 + df[t]))
            # 归一化词频 × idf；长度长的文档要除一下，避免长文天然占优
            score = (c / total) * idf * math.sqrt(len(g))
            scored.append((score, t))
        scored.sort(reverse=True)
        # 去重：如果一个词是另一个词的一部分，只留长的
        picked = []
        for s, t in scored:
            if any(t != p and t in p for p in picked):
                continue
            picked.append(t)
            if len(picked) >= TOP_K:
                break
        out[mid] = picked
    return out


def main():
    do = "--do" in sys.argv
    db = DB()
    rows = db.query(
        "SELECT id, content, summary FROM memories WHERE status='active'"
    )
    data = []
    for r in rows:
        text = (r["content"] or "") + " " + (r["summary"] or "")
        data.append((r["id"], text))

    kws = build_keywords(data)
    print("算出 %d 条记忆的主题词" % len(kws))
    for mid, k in list(kws.items())[:8]:
        r = db.one("SELECT summary FROM memories WHERE id=?", (mid,))
        print("  %-24s %s" % ("/".join(k)[:44], (r["summary"] or "")[:44]))

    if not do:
        print("\n(dry run)")
        return

    db.execute("CREATE TABLE IF NOT EXISTS memory_keywords ("
               "id TEXT PRIMARY KEY, keywords TEXT)")
    db.execute("DELETE FROM memory_keywords")
    import json
    for mid, k in kws.items():
        db.execute("INSERT INTO memory_keywords (id, keywords) VALUES (?,?)",
                   (mid, json.dumps(k, ensure_ascii=False)))
    print("已写入 %d 条主题词" % len(kws))


if __name__ == "__main__":
    main()
