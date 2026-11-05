# -*- coding: utf-8 -*-
"""记忆图谱：让记忆之间有连线

大脑和数据库的区别不在存了什么，在**能不能顺着一条线索牵出一串**。

这里建两种边：
  entity  —— 共享实体（都提到「迅雷」「Clash」「鲜果通」），权重 = 交集占比
  similar —— 文本相近但不是同一件事（0.25 ~ 0.78），权重 = 相似度

同一件事（>0.78）不建边，那是重复，入库时已经被去重挡掉了。

检索时：直接命中的记忆只取前几条，剩下的名额给它们的邻居。
于是问「迅雷」能带出「因为迅雷那件事顺手改了 Clash」这类关联，
而不是一万条都提到过迅雷的流水账。
"""
import json
import time

from .atomic import similarity

ENTITY_MIN_SHARE = 1      # 共享 1 个实体就算有关联（权重低，但连得上）
ENTITY_MIN_EACH = 2       # 双方各自至少 2 个实体
SIM_LO = 0.10             # 放宽：中文 3-gram 的 Jaccard 天然偏低，0.10 已经是弱关联了
SIM_HI = 0.78             # 高于这个：同一件事，不算「关联」
MAX_EDGES_PER_NODE = 20   # 每个节点最多留几条边
EDGE_MIN_WEIGHT = 0.08    # 权重低于这个的边是噪声，连上反而干扰联想


def _entities(raw):
    if isinstance(raw, list):
        return [e for e in raw if e]
    try:
        v = json.loads(raw or "[]")
        return [e for e in v if e] if isinstance(v, list) else []
    except Exception:
        return []


def _text(m):
    return ((m.get("content") or "") + " " + (m.get("summary") or "")).strip()


def build_edges(db, verbose=False):
    """全量重建边。259 条记忆两两比较，纯 Python 几秒能跑完。"""
    rows = db.query(
        "SELECT id, content, summary, entities, layer FROM memories WHERE status='active'"
    )
    mems = [dict(r) for r in rows]
    n = len(mems)

    # 实体倒排：先按实体分桶，只在共享实体的记忆之间算，省掉大部分两两比较
    by_entity = {}
    for m in mems:
        for e in set(_entities(m.get("entities"))):
            by_entity.setdefault(e.lower(), []).append(m["id"])

    ent_map = {m["id"]: set(x.lower() for x in _entities(m.get("entities"))) for m in mems}
    text_map = {m["id"]: _text(m) for m in mems}

    edges = {}

    def add(a, b, etype, w):
        if a == b:
            return
        k = tuple(sorted((a, b)))
        if k in edges:
            if w > edges[k][3]:      # 元组是 (a, b, type, weight)，权重在最后
                edges[k] = (a, b, etype, w)
            return
        edges[k] = (a, b, etype, w)

    # ① 实体关联
    for ids in by_entity.values():
        if len(ids) < 2 or len(ids) > 60:      # 太泛的实体（出现 60 次以上）不建边
            continue
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                ea, eb = ent_map[a], ent_map[b]
                # 实体太少的两条（往往是切碎的句子）不配对，否则占比虚高到 1.0
                if len(ea) < ENTITY_MIN_EACH or len(eb) < ENTITY_MIN_EACH:
                    continue
                shared = ea & eb
                if len(shared) < ENTITY_MIN_SHARE:
                    continue
                # 用 Jaccard 而不是 /min：/min 在实体少的时候一律给 1.0，边就没信息量了
                w = len(shared) / len(ea | eb)
                add(a, b, "entity", round(w, 3))

    # ② 文本相近（只在不共享实体的组合上补，省时间）
    if verbose:
        print("  实体边 %d 条，开始补文本边…" % len(edges))
    for i in range(n):
        a = mems[i]
        for j in range(i + 1, n):
            b = mems[j]
            k = tuple(sorted((a["id"], b["id"])))
            if k in edges:
                continue
            # 长度差太多直接跳过，浪费计算
            la, lb = len(text_map[a["id"]]), len(text_map[b["id"]])
            if la == 0 or lb == 0 or max(la, lb) / max(1, min(la, lb)) > 4:
                continue
            s = similarity(text_map[a["id"]], text_map[b["id"]])
            if SIM_LO <= s < SIM_HI:
                add(a["id"], b["id"], "similar", round(s, 3))

    # ③ 每个节点留最强的若干条
    per_node = {}
    for (a, b, et, w) in edges.values():
        per_node.setdefault(a, []).append((w, a, b, et))
        per_node.setdefault(b, []).append((w, a, b, et))

    keep = set()
    for mid, lst in per_node.items():
        lst.sort(reverse=True)
        for w, a, b, et in lst[:MAX_EDGES_PER_NODE]:
            if w < EDGE_MIN_WEIGHT:
                continue
            keep.add(tuple(sorted((a, b))))

    now = time.time()
    db.execute("DELETE FROM edges")
    cnt = 0
    for k in keep:
        a, b, et, w = edges[k]
        try:
            db.execute(
                "INSERT INTO edges (source_id, target_id, edge_type, weight, created) "
                "VALUES (?,?,?,?,?)", (a, b, et, w, now))
            cnt += 1
        except Exception:
            continue
    return {"nodes": n, "edges": cnt}


def link_new(db, mid, limit=8):
    """新记忆入库时增量连边：只跟共享实体或文本相近的连，不全量重算"""
    me = db.get_memory(mid)
    if not me:
        return 0
    my_ent = set(x.lower() for x in _entities(me.get("entities")))
    my_text = _text(me)
    if not my_text:
        return 0

    rows = db.query(
        "SELECT id, content, summary, entities FROM memories "
        "WHERE status='active' AND id!=? ORDER BY created DESC LIMIT 400", (mid,)
    )
    cands = []
    for r in rows:
        other_ent = set(x.lower() for x in _entities(r["entities"]))
        shared = my_ent & other_ent
        if (len(shared) >= ENTITY_MIN_SHARE
                and len(my_ent) >= ENTITY_MIN_EACH
                and len(other_ent) >= ENTITY_MIN_EACH):
            w = len(shared) / len(my_ent | other_ent)
            cands.append((w, r["id"], "entity"))
            continue
        s = similarity(my_text, (r["summary"] or r["content"] or ""))
        if SIM_LO <= s < SIM_HI:
            cands.append((s, r["id"], "similar"))

    # 一条新记忆如果实体不够、文本也不像，就一条边都没有 ——
    # 图谱永远长不大，联想也就无从谈起。
    # 兜底：不管多弱，至少连上最像的 3 条。
    if len(cands) < 3:
        have = set(c[1] for c in cands)
        weak = []
        for r in rows:
            if r["id"] in have:
                continue
            s2 = similarity(my_text, (r["summary"] or r["content"] or ""))
            weak.append((s2, r["id"], "similar"))
        weak.sort(reverse=True)
        cands.extend(weak[:3 - len(cands)])

    cands.sort(reverse=True)
    now = time.time()
    made = 0
    for w, oid, et in cands[:limit]:
        a, b = sorted((mid, oid))
        try:
            db.execute(
                "INSERT INTO edges (source_id, target_id, edge_type, weight, created) "
                "VALUES (?,?,?,?,?)", (a, b, et, round(w, 3), now))
            made += 1
        except Exception:
            pass
    return made


def neighbors(db, mids, limit=6, hop=1):
    """给定一批记忆 id，取它们的邻居（按边权排序）。

    只走 1 跳：2 跳之后相关性衰减太快，等于把整个库都拉进来了。
    """
    mids = [m for m in mids if m]
    if not mids or hop <= 0:
        return []
    ph = ",".join("?" * len(mids))
    rows = db.query(
        f"SELECT source_id, target_id, weight FROM edges "
        f"WHERE source_id IN ({ph}) OR target_id IN ({ph}) "
        f"ORDER BY weight DESC LIMIT ?",
        tuple(mids) + tuple(mids) + (limit * 4,)
    )
    seen = set(mids)
    out = []
    for r in rows:
        for cand in (r["source_id"], r["target_id"]):
            if cand in seen:
                continue
            seen.add(cand)
            out.append((r["weight"], cand))
            if len(out) >= limit:
                return out
    return out
