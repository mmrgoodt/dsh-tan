# -*- coding: utf-8 -*-
"""自动巩固：找出「攒够了、该提炼」的话题

人不用被提醒「该总结了」——脑子会自己觉得某件事想通了。
程序做不到自动想通，但可以做到**自动发现该想了**。

这里每天收盘时问三个问题：
  1. 最近这几天新进来的记忆，集中在哪几个话题上？
  2. 哪个话题已经攒了 4 条以上、却还没有一条结论？
  3. 那些碎片里，有没有互相矛盾的？

答案不写进记忆（写进去需要真的想清楚），而是变成一条「该整理了」的提示，
在下一次对话开头交给我。等我在对话里真的提炼完，再标记完成。

这样巩固不是靠人记得做，是靠系统催。
"""
import json
import time

from .cortex import ensure_schema as cortex_schema

RECENT_DAYS = 14         # 只看最近几天的（话题本来就分散，窗口太短会一个都找不出来）

# 2-gram 切出来的虚词组合，不是话题。
# 不过滤的话「还要」「用什」「的话」会当上话题，提炼出来的结论毫无意义。
STOP_EDGE = set("还用的了是在有和与就都也这那你我他它们")
NOISE = set("""还要 用什 么办 什么 那个 这个 的话 了的 是的 就是 不是 没有 可以
需要 应该 可能 已经 现在 时候 东西 事情 问题 情况 方面 地方 我们 你们
他们 自己 这样 那样 这些 那些 一些 一个 一样 因为 所以 但是 如果 虽然
而且 或者 不过 然后 开始 结束 之后 之前 上面 下面 里面 外面 出来 起来""".split())


def _is_topic(c):
    """判断一个概念够不够格当话题"""
    if not c or len(c) < 2 or len(c) > 12:
        return False
    if c in NOISE:
        return False
    # 纯英文短词（api/token 这类）也当不了话题候选，容易聚成噪音
    if c.isascii() and len(c) <= 3:
        return False
    # 2 字词不能以虚词开头或结尾：「还要」「用什」「的话」
    if len(c) == 2 and (c[0] in STOP_EDGE or c[1] in STOP_EDGE):
        return False
    return True
MIN_CLUSTER = 3          # 少于 3 条不值得提炼
MAX_TASKS = 3            # 一次最多提醒几件
COOLDOWN_HOURS = 20      # 同一话题多久内不重复提醒


def ensure_schema(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS digest_tasks (
            topic TEXT PRIMARY KEY,
            asked_at REAL,
            done_at REAL,
            size INTEGER
        )""")


def _recent(db, days=RECENT_DAYS):
    since = time.time() - days * 86400
    return db.query(
        "SELECT id, content, summary, layer, provenance, created "
        "FROM memories WHERE status='active' AND created >= ?", (since,))


def _covered_topics(db):
    """已经有结论覆盖的话题：LLM 写过的东西算已巩固"""
    out = set()
    for r in db.query(
        "SELECT provenance, content FROM memories "
        "WHERE status='active' AND provenance LIKE '%\"by\": \"llm\"%'"
    ):
        text = (r["content"] or "")
        out.add(text[:40])
    return out


def candidates(db, days=RECENT_DAYS, min_size=MIN_CLUSTER):
    """找出值得巩固的记忆簇

    聚类不靠图（图会连成一坨），靠「这条记忆的主导概念」——
    也就是它 traces 里 freq 中等的那几个概念。
    """
    ensure_schema(db)
    recent = _recent(db, days)
    if len(recent) < min_size:
        return []

    # 用 entities 聚类，不用 2-gram 概念。
    # 实测 2-gram 会切出「闭浏」「用什」这种碎片当话题，
    # 提炼出来的结论毫无意义；entities 是正则抽的专名，质量高得多。
    clusters = {}
    for r in recent:
        try:
            ents = json.loads(r["entities"] or "[]")
        except Exception:
            ents = []
        for e in ents:
            if _is_topic(e):
                clusters.setdefault(e, set()).add(r["id"])

    # 已经问过的，冷却期内不再问
    asked = {}
    for r in db.query("SELECT topic, asked_at, done_at FROM digest_tasks"):
        asked[r["topic"]] = (r["asked_at"], r["done_at"])
    now = time.time()

    out = []
    for c, mids in clusters.items():
        if len(mids) < min_size or not _is_topic(c):
            continue
        a, d = asked.get(c, (0, 0))
        if d and (now - d) < COOLDOWN_HOURS * 3600 * 3:
            continue
        if a and (now - a) < COOLDOWN_HOURS * 3600:
            continue
        out.append((len(mids), c, sorted(mids)))
    out.sort(reverse=True)

    # 去掉被更大簇包含的（同一个话题只提一次）
    picked = []
    seen = set()
    for n, c, mids in out:
        if len(picked) >= MAX_TASKS:
            break
        if any(c in x[1] or x[1] in c for x in picked):
            continue
        if set(mids) <= seen:
            continue
        picked.append((n, c, mids))
        seen |= set(mids)
    return picked


def unsummarized(db, days=RECENT_DAYS, limit=8):
    """最近攒下、但还没被任何结论覆盖的记忆

    不强行聚类 —— 实测按 2-gram 聚会出「闭浏」这种碎片话题，
    按 entities 聚又打不出簇。与其猜测，不如把原料直接摆出来：
    归纳这件事本来就该由看得懂内容的一方做。
    """
    recent = _recent(db, days)
    if not recent:
        return []

    # 已经被 LLM 结论提到过的内容，说明归纳过了
    claimed = []
    for r in db.query(
        "SELECT content FROM memories "
        "WHERE status='active' AND provenance LIKE '%\"by\": \"llm\"%'"
    ):
        claimed.append((r["content"] or "")[:60])

    out = []
    for r in recent:
        body = (r["summary"] or r["content"] or "")[:70]
        if not body:
            continue
        if any(body[:20] in c or c[:20] in body for c in claimed):
            continue
        out.append((r["created"], body))
    out.sort(reverse=True)
    return [b for _, b in out[:limit]]


def render(db, days=RECENT_DAYS):
    """给注入用的一段提示。没有该整理的就返回空串。"""
    lines = []

    # ① 有明确话题簇的，优先提
    try:
        tasks = candidates(db, days)
    except Exception:
        tasks = []
    for n, c, mids in tasks[:2]:
        lines.append("关于「%s」攒了 %d 条，还没归纳成结论。" % (c, n))

    # ② 剩下的一律摆原料
    try:
        rest = unsummarized(db, days, limit=6 if lines else 8)
    except Exception:
        rest = []
    if rest:
        lines.append("最近攒下这些，还没归纳：")
        for b in rest:
            lines.append("  · " + b.replace("\n", " "))

    return "\n".join(lines)


def mark_asked(db, topic, size):
    ensure_schema(db)
    db.execute(
        "INSERT OR REPLACE INTO digest_tasks (topic, asked_at, size) VALUES (?,?,?)",
        (topic, time.time(), size))


def mark_done(db, topic):
    ensure_schema(db)
    db.execute("UPDATE digest_tasks SET done_at=? WHERE topic=?", (time.time(), topic))


def auto_mark_done(db):
    """如果某个话题下已经出现了 LLM 结论，就自动标记完成"""
    ensure_schema(db)
    llm_topics = set()
    for r in db.query(
        "SELECT content, tags FROM memories "
        "WHERE status='active' AND provenance LIKE '%\"by\": \"llm\"%'"
    ):
        try:
            tags = json.loads(r["tags"] or "[]")
        except Exception:
            tags = []
        for t in tags:
            if isinstance(t, str):
                llm_topics.add(t)
        body = (r["content"] or "")
        for row in db.query("SELECT topic FROM digest_tasks"):
            if row["topic"] in body:
                llm_topics.add(row["topic"])

    n = 0
    for topic in llm_topics:
        r = db.one("SELECT done_at FROM digest_tasks WHERE topic=?", (topic,))
        if r and not r["done_at"]:
            mark_done(db, topic)
            n += 1
    return n
