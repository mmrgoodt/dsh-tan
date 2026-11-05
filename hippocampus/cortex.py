# -*- coding: utf-8 -*-
"""皮层：概念网络 + 激活扩散（第二版）

第一版把每条记忆都拿去跟全部概念比对（1400 × 268），跑了十分钟没出来。
本质是没建倒排：概念是从文本里切出来的，那就该在切的时候顺手记下
「这个概念出现在哪几条记忆里」，而不是反过来去每条记忆里找。

三层：
  concepts  概念节点
  synapses  突触（PMI 加权，惩罚「两个都常见」的偶然共现）
  traces    概念 → 记忆的线索

检索是激活扩散：命中概念 → 沿突触走两跳 → 反查记忆。
"""
import json
import math
import re
import time
from collections import Counter

from .retriever import tokenize, STOPWORDS, STOP_CHARS

MAX_HOP = 2
HOP_DECAY = 0.45
MIN_ACTIVATION = 0.06
K_WINNERS = 28           # 一次最多点亮几个概念：皮层也是稀疏的
INHIBIT_GAMMA = 1.7      # 侧抑制强度，越大越只留最强的几个
CONCEPT_MIN_DF = 3        # 跨 3 条以上才算概念：真词会重复，碎片不会


def ensure_schema(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS concepts (
            name TEXT PRIMARY KEY, freq INTEGER DEFAULT 0, last_seen REAL)""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS synapses (
            a TEXT, b TEXT, weight REAL DEFAULT 0, cooc INTEGER DEFAULT 0, last REAL,
            PRIMARY KEY (a, b))""")
    db.execute("""
        CREATE TABLE IF NOT EXISTS traces (
            concept TEXT, memory_id TEXT, salience REAL DEFAULT 1.0,
            PRIMARY KEY (concept, memory_id))""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_syn_a ON synapses(a)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_syn_b ON synapses(b)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tr_c ON traces(concept)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_tr_m ON traces(memory_id)")


def _ents(m):
    raw = m.get("entities")
    if isinstance(raw, list):
        return [e.strip() for e in raw if e and len(e.strip()) >= 2]
    try:
        v = json.loads(raw or "[]")
        return [e.strip() for e in v if e and len(e.strip()) >= 2] if isinstance(v, list) else []
    except Exception:
        return []


def _grams(text):
    """这条文本里的中文 2-gram + 英文词"""
    q = (text or "").lower()
    out = set(re.findall(r"[a-z0-9_\-]{2,}", q))
    cn = re.sub(r"[^\u4e00-\u9fa5]", " ", q)
    for chunk in cn.split():
        for i in range(len(chunk) - 1):
            bg = chunk[i:i + 2]
            if bg in STOPWORDS:
                continue
            if bg[0] in STOP_CHARS and bg[1] in STOP_CHARS:
                continue
            out.add(bg)
    return out


def build(db, verbose=False):
    ensure_schema(db)
    rows = [dict(r) for r in db.query(
        "SELECT id, content, summary, entities, layer, confidence "
        "FROM memories WHERE status='active'")]
    n = max(1, len(rows))

    # ① 每条记忆的 gram（顺便统计 df），一次遍历完成
    per_mem = {}
    df = Counter()
    for m in rows:
        body = ((m.get("content") or "") + " " + (m.get("summary") or ""))
        g = _grams(body)
        per_mem[m["id"]] = g
        for t in g:
            df[t] += 1

    auto = set(t for t, c in df.items() if c >= CONCEPT_MIN_DF)

    # ② 每条记忆的概念集合 = 实体 ∪ (自己的 gram 里被认定为概念的)
    ents_of = {}
    freq = Counter()
    for m in rows:
        e = set(x for x in _ents(m) if 2 <= len(x) <= 24)
        e |= (per_mem[m["id"]] & auto)
        ents_of[m["id"]] = e
        for x in e:
            freq[x] += 1

    # ③ 突触：只对「同一条记忆里」的概念建对
    pair = Counter()
    for mid, e in ents_of.items():
        lst = sorted(e)
        if len(lst) > 40:      # 概念太多的记忆（长报告）不建对，否则一炸一堆
            lst = lst[:40]
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                pair[(lst[i], lst[j])] += 1

    now = time.time()

    # 批量写在一个事务里。db.execute() 每次都要 connect + commit + close，
    # 几千条 INSERT 就是几千次 fsync —— 之前 build 跑十分钟跑不完，就卡在这。
    c_rows = [(e, c, now) for e, c in freq.items()]

    s_rows = []
    for (a, b), co in pair.items():
        if co < 2:
            continue
        p_ab = co / n
        p_a = freq.get(a, 1) / n
        p_b = freq.get(b, 1) / n
        pmi = math.log(max(1e-9, p_ab / (p_a * p_b)))
        if pmi <= 0:
            continue
        s_rows.append((a, b, round(pmi * math.log(1 + co), 3), co, now))

    t_rows = []
    for m in rows:
        e = ents_of[m["id"]]
        if not e:
            continue
        conf = float(m.get("confidence") or 0.7)
        sal = conf / max(1.0, len(e) ** 0.5)
        for x in e:
            t_rows.append((x, m["id"], round(sal, 4)))

    with db.conn() as con:
        con.execute("DELETE FROM concepts")
        con.executemany("INSERT INTO concepts (name, freq, last_seen) VALUES (?,?,?)", c_rows)
        con.execute("DELETE FROM synapses")
        con.executemany(
            "INSERT INTO synapses (a, b, weight, cooc, last) VALUES (?,?,?,?,?)", s_rows)
        con.execute("DELETE FROM traces")
        con.executemany(
            "INSERT OR REPLACE INTO traces (concept, memory_id, salience) VALUES (?,?,?)", t_rows)

    made = len(s_rows)
    tr = len(t_rows)

    if verbose:
        print("  概念 %d / 突触 %d / 线索 %d" % (len(freq), made, tr))
    return {"concepts": len(freq), "synapses": made, "traces": tr}


def _lookup(word, known):
    w = word.lower()
    if w in known:
        return [w]
    return [k for k in known if w in k or k in w][:6]


def activate(db, query, limit=8, hops=MAX_HOP):
    ensure_schema(db)
    terms = tokenize(query)
    if not terms:
        return []

    known = {r["name"].lower(): r["freq"] for r in db.query("SELECT name, freq FROM concepts")}
    # 初始激活按概念的稀有度加权：
    # 「记忆」「系统」这种到处都有的概念，点亮它等于没点亮；
    # 「迅雷」「gdt」这种专门的词才是真信号。
    # 这就是 IDF 的思想，只不过用在概念上。
    import math as _m

    def _idf(freq):
        return 1.0 / (1.0 + _m.log(max(1.0, freq)))

    act = {}
    for t in terms:
        for c in _lookup(t, known):
            a = _idf(known.get(c, 1))
            if a > act.get(c, 0.0):
                act[c] = a
    if not act:
        return []
    # 别名：命中「迅雷」时把「thunder」一起点亮，反之亦然。
    # 这是解决「不懂语义」最直接的一招 —— 同一件东西的两种叫法，
    # 靠共现统计推不出来，但可以从种子词典里拿到。
    try:
        from . import aliases as _al
        _a = _al.expand(db, list(act.keys()))
        _base = max(act.values()) if act else 0
        for c in _a:
            if c not in act and c in known:
                # 别名权重必须低。0.85 实测会把噪音一起拉进来：
                # 问「崩了」经过报错/失败/异常扩散，能扯到 B 站视频上去。
                # 0.35 相当于「顺带想到」，而不是「等价于」。
                act[c] = _base * 0.35
    except Exception:
        pass

    # 归一化到 1.0，方便后面按比例扩散
    _mx = max(act.values())
    if _mx > 0:
        act = {c: a / _mx for c, a in act.items()}

    frontier = dict(act)
    for _ in range(hops):
        nxt = {}
        for c, a in frontier.items():
            if a < MIN_ACTIVATION:
                continue
            for r in db.query(
                "SELECT a, b, weight FROM synapses WHERE a=? OR b=? "
                "ORDER BY weight DESC LIMIT 24", (c, c)
            ):
                other = r["b"] if r["a"] == c else r["a"]
                gain = a * min(1.0, r["weight"] / 4.0) * HOP_DECAY
                if gain > nxt.get(other, 0.0):
                    nxt[other] = gain
        for c, a in nxt.items():
            if a > act.get(c, 0.0):
                act[c] = a
        frontier = nxt
        if not frontier:
            break

    # —— 侧抑制 ——
    # 不加这一步的话，扩散会把几百个概念都带上分，反查出来是一锅粥。
    # 大脑同一时刻只让一小撮神经元放电，其余被压制（lateral inhibition）。
    if act:
        items = sorted(act.items(), key=lambda kv: -kv[1])[:K_WINNERS]
        top = items[0][1] if items else 0.0
        act = {}
        for c, a in items:
            if a < MIN_ACTIVATION:
                continue
            # 非线性压缩：强的保留，弱的迅速掉下去，差距拉开
            act[c] = (a / top) ** INHIBIT_GAMMA if top > 0 else 0.0

    scores = {}
    for c, a in act.items():
        if a < MIN_ACTIVATION:
            continue
        for r in db.query("SELECT memory_id, salience FROM traces WHERE concept=?", (c,)):
            scores[r["memory_id"]] = scores.get(r["memory_id"], 0.0) + a * float(r["salience"] or 1.0)

    return sorted(scores.items(), key=lambda x: -x[1])[:limit]


def reinforce(db, memory_ids, factor=1.1):
    now = time.time()
    for mid in memory_ids:
        try:
            db.execute(
                "UPDATE synapses SET weight = weight * ?, last = ? "
                "WHERE a IN (SELECT concept FROM traces WHERE memory_id=?) "
                "   OR b IN (SELECT concept FROM traces WHERE memory_id=?)",
                (factor, now, mid, mid))
        except Exception:
            pass
