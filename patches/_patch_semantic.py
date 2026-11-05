# -*- coding: utf-8 -*-
"""语义召回补强：同义词扩展改成加权，原词 1.0 / 扩展词 0.35

原来 USE_SYNONYM_EXPAND=False，注释写「实测在中文上稀释相关度」——
问题不在扩展本身，在于扩展词和原词等权，泛词一多就把真正的命中压下去了。
按惯例给扩展词降权到 0.35，只当"锦上添花"，不动原词的地位。
"""
import io

# ---------- retriever.py ----------
p = "hippocampus/retriever.py"
s = io.open(p, encoding="utf-8").read()


def sub1(text, old, new, tag):
    if old not in text:
        raise SystemExit("MISS: " + tag)
    if text.count(old) != 1:
        raise SystemExit("AMBIGUOUS(%d): %s" % (text.count(old), tag))
    return text.replace(old, new)


# 1) bm25 支持逐词权重
old_bm25 = '''def bm25_score(mem, query_terms, idf=None, avg_len=400, k1=1.4, b=0.72):
    """BM25：比「命中率」准得多

    k1 控制词频饱和，b 控制文档长度惩罚（0=不惩罚，1=完全惩罚）
    """
    if not query_terms:
        return 0.0'''
new_bm25 = '''def bm25_score(mem, query_terms, idf=None, avg_len=400, k1=1.4, b=0.72, term_w=None):
    """BM25：比「命中率」准得多

    k1 控制词频饱和，b 控制文档长度惩罚（0=不惩罚，1=完全惩罚）
    term_w: 逐词权重，用来给同义词扩展词降权
    """
    if not query_terms:
        return 0.0'''
s = sub1(s, old_bm25, new_bm25, "bm25_sig")

old_w = '''        tf = text.count(tl)
        w = (idf or {}).get(tl, 1.0)'''
new_w = '''        tf = text.count(tl)
        w = (idf or {}).get(tl, 1.0) * (term_w or {}).get(tl, 1.0)'''
s = sub1(s, old_w, new_w, "bm25_weight")

# 2) 加加权扩词函数
old_expand = '''def expand_terms(terms):
    """把查询词按同义词表扩一圈（不递归，避免爆炸）"""
    out = set(terms)
    for t in terms:
        tl = t.lower()
        for key, alts in SYNONYMS.items():
            if key in tl or tl in key:
                for a in alts[:MAX_EXPAND]:
                    out.add(a.lower())
    return list(out)'''
new_expand = '''def expand_terms(terms):
    """把查询词按同义词表扩一圈（不递归，避免爆炸）"""
    out = set(terms)
    for t in terms:
        tl = t.lower()
        for key, alts in SYNONYMS.items():
            if key in tl or tl in key:
                for a in alts[:MAX_EXPAND]:
                    out.add(a.lower())
    return list(out)


EXPAND_WEIGHT = 0.35   # 扩展词只占原词的三分之一，当线索用，不当主角
EXPAND_GATE = 4        # 原词召回少于这么多条时才扩，避免本来就很准的查询被搅浑


def expand_weighted(terms):
    """返回 (加权词表, {词: 权重})：原词 1.0，扩展词 EXPAND_WEIGHT"""
    base = set(t.lower() for t in terms if t)
    w = {t: 1.0 for t in base}
    for t in terms:
        tl = (t or "").lower()
        if not tl:
            continue
        for key, alts in SYNONYMS.items():
            if key in tl or tl in key:
                for a in alts[:MAX_EXPAND]:
                    al = a.lower()
                    if al in w:
                        continue
                    w[al] = EXPAND_WEIGHT
    return list(w.keys()), w'''
s = sub1(s, old_expand, new_expand, "expand_weighted")

# 3) recall 里用加权扩展
old_recall = '''        scored = []
        idf = None
        exp_terms = expand_terms(terms) if (terms and USE_SYNONYM_EXPAND) else terms
        has_query = bool(query and terms)
        for m in mems:
            if idf is None:
                idf = self._build_idf(terms)
            # 两路打分取高的：BM25（词频归一）+ 原命中率（兜底）
            r1 = bm25_score(m, exp_terms, idf) if exp_terms else 0.0'''
new_recall = '''        scored = []
        idf = None
        # 先只看原词：够准就不扩，不准了才拿同义词补
        base_hits = sum(
            1 for m in mems
            if any(t.lower() in ((m.get("content") or "") + (m.get("summary") or "")).lower()
                   for t in terms)
        ) if terms else 0
        if terms and base_hits < EXPAND_GATE:
            exp_terms, term_w = expand_weighted(terms)
        else:
            exp_terms, term_w = terms, None
        has_query = bool(query and terms)
        for m in mems:
            if idf is None:
                idf = self._build_idf(terms)
            # 两路打分取高的：BM25（词频归一）+ 原命中率（兜底）
            r1 = bm25_score(m, exp_terms, idf, term_w=term_w) if exp_terms else 0.0'''
s = sub1(s, old_recall, new_recall, "recall_weighted")

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("OK: weighted synonym expansion")
