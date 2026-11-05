# -*- coding: utf-8 -*-
"""预测：不是等你问，而是猜你接下来要问什么

大脑和检索引擎最本质的差别在这里：
  检索引擎 = 你给 query，它去找
  大脑     = 一直在预测下一个输入，只有预测错了才「注意」

实现不了真的预测模型，但有一半可以从对话序列里统计出来：
**你问完 A 之后，通常接着问 B。** 这就是一阶马尔可夫链。

做法：
  1. 把每条记忆按 (session, turn) 排好，取每轮的主导概念
  2. 统计相邻两轮的概念转移：A → B 出现多少次
  3. 存成 predictions 表，归一化成概率

用途：
  · 检索时，如果当前概念有强后继，把那条记忆提前放进候选
  · 主动消息时，用「你上次聊到 X」而不是随机挑一条

注意：这个表要够大才有意义。现在库里只有几十个会话轮次，
先建起来，让它随着对话慢慢长。
"""
import json
import time
from collections import Counter

PRED_MIN_COUNT = 1       # 数据还少，先按 1 次记；等轮次多了再提到 2
PRED_TOP_K = 3           # 每个概念最多留几个后继


def ensure_schema(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            src TEXT, dst TEXT, count INTEGER, prob REAL, updated REAL,
            PRIMARY KEY (src, dst)
        )""")


# 2-gram 碎片，不能当话题
_STOP_EDGE = set("还用的了是在有和与就都也这那你我他它们把被从对向以")
_NOISE = set("""还要 用什 么办 什么 那个 这个 的话 了的 是的 就是 不是 没有 可以
需要 应该 可能 已经 现在 时候 东西 事情 问题 情况 方面 地方 我们 你们
他们 自己 这样 那样 这些 那些 一些 一个 一样 因为 所以 但是 如果 虽然
而且 或者 不过 然后 开始 结束 之后 之前 上面 下面 里面 外面 出来 起来
改成 话了 话的 话上 个会 到了 还会 喊我 不用 一次 一直
""".split())


def _ok(c):
    if not c or not (2 <= len(c) <= 12):
        return False
    if c.lower() in _NOISE or c.isdigit():
        return False
    if len(c) == 2 and (c[0] in _STOP_EDGE or c[1] in _STOP_EDGE):
        return False
    return True


def _dominant(db, memory_id, k=3):
    """一条记忆的几个主导概念

    优先用 entities（正则抽的专名，质量好）；
    traces 只作补充，而且必须过 _ok 过滤 —— 按最低频取会挑出一堆
    「喊我」「改成」这种 2-gram 碎片，转移表全是噪音。
    """
    out = []
    r = db.one("SELECT entities FROM memories WHERE id=?", (memory_id,))
    if r:
        try:
            for e in json.loads(r["entities"] or "[]"):
                if _ok(e):
                    out.append(e)
                if len(out) >= k:
                    return out
        except Exception:
            pass
    if len(out) < k:
        rows = db.query(
            "SELECT t.concept AS c FROM traces t JOIN concepts co ON co.name=t.concept "
            "WHERE t.memory_id=? AND co.freq BETWEEN 3 AND 80 "
            "ORDER BY co.freq DESC LIMIT ?", (memory_id, k * 3))
        for row in rows:
            if _ok(row["c"]) and row["c"] not in out:
                out.append(row["c"])
            if len(out) >= k:
                break
    return out


def build(db, verbose=False):
    ensure_schema(db)
    rows = db.query(
        "SELECT id, provenance, created FROM memories "
        "WHERE status='active' AND provenance LIKE '%session%' "
        "ORDER BY created"
    )

    # 按 session 分组，按 turn 排序
    sessions = {}
    for r in rows:
        try:
            p = json.loads(r["provenance"] or "{}")
        except Exception:
            continue
        s, t = p.get("session"), p.get("turn")
        if not s or not isinstance(t, int):
            continue
        dom = _dominant(db, r["id"])
        if not dom:
            continue
        sessions.setdefault(s, []).append((t, dom))

    trans = Counter()
    turns_seen = 0
    for s, lst in sessions.items():
        lst.sort()
        turns_seen += len(lst)
        for i in range(len(lst) - 1):
            for a in lst[i][1]:
                for b in lst[i + 1][1]:
                    if a != b:
                        trans[(a, b)] += 1

    now = time.time()
    db.execute("DELETE FROM predictions")
    n = 0
    # 按源概念归一化
    by_src = {}
    for (a, b), c in trans.items():
        by_src.setdefault(a, []).append((c, b))
    with db.conn() as con:
        for a, lst in by_src.items():
            lst.sort(reverse=True)
            total = sum(c for c, _ in lst) or 1
            for c, b in lst[:PRED_TOP_K]:
                if c < PRED_MIN_COUNT:
                    continue
                con.execute(
                    "INSERT INTO predictions (src, dst, count, prob, updated) "
                    "VALUES (?,?,?,?,?)", (a, b, c, round(c / total, 3), now))
                n += 1

    if verbose:
        print("  会话 %d 个 / 轮次 %d / 规律 %d 条" % (len(sessions), turns_seen, n))
    return {"sessions": len(sessions), "turns": turns_seen, "rules": n}


def predict(db, concepts, top=3):
    """给一批当前概念，猜接下来会涉及哪些"""
    ensure_schema(db)
    if not concepts:
        return []
    ph = ",".join("?" * len(concepts))
    rows = db.query(
        f"SELECT dst, SUM(prob) AS p, SUM(count) AS c FROM predictions "
        f"WHERE src IN ({ph}) GROUP BY dst ORDER BY p DESC LIMIT ?",
        tuple(concepts) + (top,))
    return [(r["dst"], round(float(r["p"]), 3)) for r in rows]


def render_prediction(db, concepts, limit=2):
    """一句话：你接下来可能会问到什么"""
    ps = predict(db, concepts, limit)
    if not ps:
        return ""
    return "你接下来可能还会问：" + "、".join(f"{d}" for d, _ in ps)


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hippocampus.db import DB
    db = DB()
    r = build(db, verbose=True)
    print(r)
    for probe in (["迅雷"], ["密码"], ["clash"]):
        print(probe, "->", predict(db, probe))
