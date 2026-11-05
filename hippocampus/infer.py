# -*- coding: utf-8 -*-
"""推理：从已有的关系推出新关系

现在的系统只会联想：A 连着 B，B 连着 C，激活时能从 A 走到 C。
但那只是「走」过去，脑子里并没有建立 A 和 C 的联系 —— 下次还得重走一遍。

人不是这样。知道「迅雷要用 gdt」「gdt 是广点通」，就会直接得出
「迅雷那个广告来自广点通」，不需要每次重新想。

这一步叫**关系传递**：
    A ——(w1)—— B ——(w2)—— C   ⇒   建一条 A ——(w1·w2·λ)—— C

只推强的。弱关系传递会爆炸，而且推出来的东西没有意义。

另外做矛盾检测：同一条事实被两次说成不同的样子，得能发现。
"""
import json
import time

INFER_MIN_EDGE = 1.5      # 实测突触权重中位数在 2 附近，3.0 一条都传不出来
INFER_DECAY = 0.35        # 传递后打折，推出来的关系本来就比直接的弱
MAX_INFERRED_PER_NODE = 3 # 每个节点最多新增几条推理边，防止爆炸
CONTRADICT_MIN_SIM = 0.32 # 阈值太高会一对都查不出来


def transitive(db, dry=False):
    """关系传递：A-B-C → A-C"""
    rows = db.query("SELECT a, b, weight FROM synapses WHERE weight >= ?",
                    (INFER_MIN_EDGE,))
    adj = {}
    for r in rows:
        adj.setdefault(r["a"], []).append((r["b"], r["weight"]))
        adj.setdefault(r["b"], []).append((r["a"], r["weight"]))

    existing = set()
    for r in db.query("SELECT a, b FROM synapses"):
        existing.add((r["a"], r["b"]))
        existing.add((r["b"], r["a"]))

    inferred = {}
    per_node = {}
    for b, nbrs in adj.items():
        if len(nbrs) < 2:
            continue
        nbrs = sorted(nbrs, key=lambda x: -x[1])[:6]   # 只从最强的几条传
        for i in range(len(nbrs)):
            for j in range(i + 1, len(nbrs)):
                a, wa = nbrs[i]
                c, wc = nbrs[j]
                if a == c or (a, c) in existing:
                    continue
                if per_node.get(a, 0) >= MAX_INFERRED_PER_NODE:
                    continue
                if per_node.get(c, 0) >= MAX_INFERRED_PER_NODE:
                    continue
                w = wa * wc * INFER_DECAY / 10.0
                if w < 1.0:
                    continue
                k = tuple(sorted((a, c)))
                if k in inferred and inferred[k][2] >= w:
                    continue
                inferred[k] = (k[0], k[1], round(w, 3))
                per_node[k[0]] = per_node.get(k[0], 0) + 1
                per_node[k[1]] = per_node.get(k[1], 0) + 1

    if dry:
        return list(inferred.values())

    now = time.time()
    made = 0
    with db.conn() as con:
        for a, b, w in inferred.values():
            try:
                con.execute(
                    "INSERT OR IGNORE INTO synapses (a, b, weight, cooc, last) "
                    "VALUES (?,?,?,?,?)", (a, b, w, 0, now))
                made += 1
            except Exception:
                pass
    return made


def contradictions(db, limit=8):
    """矛盾检测：像但不同，且都声称是事实

    人记错的时候会有这种感觉：「这事我记得是 A，好像又说过 B」。
    程序里对应的是：两条 L2 事实层记忆相似度不低，但关键细节冲突。

    纯规则判不了「谁对」，只能把候选挑出来交给上层判断。
    """
    from .atomic import similarity

    rows = db.query(
        "SELECT id, content, summary FROM memories "
        "WHERE status='active' AND layer='L2' ORDER BY created DESC LIMIT 200")
    if len(rows) < 2:
        return []

    out = []
    for i in range(len(rows)):
        a = rows[i]
        ta = (a["summary"] or a["content"] or "")
        if len(ta) < 12:
            continue
        for j in range(i + 1, len(rows)):
            b = rows[j]
            tb = (b["summary"] or b["content"] or "")
            if len(tb) < 12:
                continue
            s = similarity(ta, tb)
            if s < CONTRADICT_MIN_SIM:
                continue
            # 像，但数字/路径不同 → 大概率是前后不一致
            import re
            na = set(re.findall(r"\d+", ta))
            nb = set(re.findall(r"\d+", tb))
            if na and nb and na != nb:
                out.append((round(s, 2), a["id"], b["id"], ta[:48], tb[:48]))
                if len(out) >= limit:
                    return out
    return out


def render_contradictions(db, limit=4):
    cs = contradictions(db, limit)
    if not cs:
        return ""
    lines = ["以下几条我记得不一致，可能是前后说法变了："]
    for s, a, b, ta, tb in cs:
        lines.append("  · %s" % ta.replace("\n", " "))
        lines.append("    另有说法：%s" % tb.replace("\n", " "))
    return "\n".join(lines)
