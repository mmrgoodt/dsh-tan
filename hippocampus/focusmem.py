# -*- coding: utf-8 -*-
"""工作记忆：注意力焦点

人聊天是连续的。「迅雷广告弄一下」→「再关掉开机自启」→「那个呢」，
第三句单看什么都指不出来，但人在场就知道说的是迅雷。

程序做不到，因为每轮都是独立检索。于是：
  · 用户说得越短，召回越乱
  · 「那种事怎么弄」永远查不到东西

这里维护一个短期焦点：最近几轮被点亮过的概念，带衰减。
当查询本身信息不足时，用焦点补充。

这是工作记忆——容量小、几秒到几分钟、不写进长期记忆。
"""
import time

DECAY_TAU = 900          # 焦点半衰期 15 分钟：聊过一会儿就该淡
MAX_FOCUS = 12
FOCUS_MIN = 0.25


def ensure_schema(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS working_memory (
            concept TEXT PRIMARY KEY,
            strength REAL DEFAULT 1.0,
            updated REAL
        )""")


def touch(db, concepts, weight=1.0):
    """把概念推入焦点。被点亮的概念强度累加，其余随时间衰减"""
    if not concepts:
        return
    ensure_schema(db)
    now = time.time()
    with db.conn() as con:
        con.execute("DELETE FROM working_memory WHERE updated < ?", (now - DECAY_TAU * 6,))
        for c in list(concepts)[:40]:
            if not c:
                continue
            row = con.execute(
                "SELECT strength FROM working_memory WHERE concept=?", (c,)).fetchone()
            if row:
                con.execute(
                    "UPDATE working_memory SET strength = strength + ?, updated=? WHERE concept=?",
                    (weight, now, c))
            else:
                con.execute(
                    "INSERT INTO working_memory (concept, strength, updated) VALUES (?,?,?)",
                    (c, weight, now))


def focus(db, limit=MAX_FOCUS):
    """当前焦点：强度 × 时间衰减，取最强的几个"""
    ensure_schema(db)
    now = time.time()
    rows = db.query("SELECT concept, strength, updated FROM working_memory")
    out = []
    for r in rows:
        age = now - (r["updated"] or now)
        s = float(r["strength"] or 1.0) * pow(0.5, age / DECAY_TAU)
        if s >= FOCUS_MIN:
            out.append((s, r["concept"]))
    out.sort(reverse=True)
    return [c for _, c in out[:limit]]


def clear(db):
    ensure_schema(db)
    db.execute("DELETE FROM working_memory")


def enrich_query(db, query, terms):
    """查询信息不足时，用焦点补全

    判据：分词后实词少于 2 个，或者词都很短（「那个」「这种」）。
    这时把焦点概念拼进来，让检索知道上下文在聊什么。
    """
    t = (query or "").strip()
    if not t:
        return query
    # 判据用「字数」而不是「词数」：
    # 「那个呢」会被切成 ['那个呢','个呢'] 两个词，词数判断根本拦不住，
    # 但这句话本身没有任何检索锚点，只能靠上下文。
    core = "".join(ch for ch in t if "一" <= ch <= "龥" or ch.isalnum())
    if len(core) > 4:
        return query
    f = focus(db)
    if not f:
        return query
    # 只补最近最亮的 3 个，补多了反而稀释
    return t + " " + " ".join(f[:3])
