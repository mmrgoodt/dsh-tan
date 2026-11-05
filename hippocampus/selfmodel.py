# -*- coding: utf-8 -*-
"""自我模型：知道自己知道什么

现在问一件没记过的事，系统的表现是「沉默」——只返回空列表。
人和它的区别在于：人会先意识到「这块我没印象」，然后才可能说「我不确定」。

这里给它一个粗糙的自我模型：
  · 库里有哪几个主题、每个主题有多少条、最近一次是什么时候
  · 检索没命中时，能说出「我记得的和什么有关」

不做成一条记忆（会污染检索），做成一个随时可查的概览。
"""
import json
import time

MIN_TOPIC_FREQ = 5       # 低于这个：太偶然，不算主题
MAX_TOPIC_FREQ = 50      # 高于这个：太泛（「记忆」「插件」），是背景不是主题
MAX_TOPICS = 24

# 2-gram 切出来的通用组合，不是主题。
# 不过滤的话「自动」「直接」「了一」「一件事」会占满目录，
# 真正该被看见的「迅雷」「Clash」反而被挤出去。
NOISE_TOPICS = set("""自动 直接 一次 一件 件事 说明 状态 内容 文件 系统 项目 打开
方式 方法 时候 进行 完成 处理 情况 问题 东西 地方 方面 我们 你们 他们
这个 那个 这些 那些 这样 那样 什么 怎么 现在 已经 可以 需要 应该 可能
上面 下面 里面 外面 之前 之后 开始 结束 结果 原因 时间 数据 部分 全部
本身 其实 只是 还是 就是 但是 因为 所以 如果 虽然 而且 或者 不过
不是 没有 不会 知道 一个 一些 一样 我们 他们
了一 出来 一条 必须 上面 下面 已经 现在 可能 之后 进行 处理
说明 情况 方式 部分 全部 自己 没有 这个 那个 就是 还是 因为
""".split())


def _days(ts):
    if not ts:
        return None
    return max(0, int((time.time() - ts) / 86400))


def topic_map(db):
    """返回 `{topics: [...], total, domains}`"""
    rows = db.query(
        """
        SELECT t.concept AS name,
               COUNT(*) AS n,
               MAX(m.created) AS last
          FROM traces t
          JOIN memories m ON m.id = t.memory_id
         WHERE m.status = 'active'
         GROUP BY t.concept
        HAVING COUNT(*) >= ? AND COUNT(*) <= ?
         ORDER BY n DESC
         LIMIT ?
        """,
        (MIN_TOPIC_FREQ, MAX_TOPIC_FREQ, 300),
    )
    topics = []
    for r in rows:
        name = r["name"]
        if name in NOISE_TOPICS or len(name) < 2:
            continue
        if not any(ch.isalpha() or "一" <= ch <= "龥" for ch in name):
            continue
        topics.append({
            "name": name,
            "count": r["n"],
            "last_days": _days(r["last"]),
        })
        if len(topics) >= MAX_TOPICS:
            break

    total = db.one("SELECT COUNT(*) c FROM memories WHERE status='active'")["c"] or 0
    layers = {}
    for r in db.query(
        "SELECT layer, COUNT(*) c FROM memories WHERE status='active' GROUP BY layer"
    ):
        layers[r["layer"]] = r["c"]

    return {"total": total, "layers": layers, "topics": topics}


def render_map(db, limit=16):
    """人能读的版本"""
    m = topic_map(db)
    lines = ["我一共记得 %d 条东西。" % m["total"]]
    ln = "；".join("%s %d 条" % (k, v) for k, v in sorted(m["layers"].items()))
    if ln:
        lines.append("按层：%s" % ln)
    if m["topics"]:
        lines.append("常出现的主题：" + "；".join(
            "%s（%d 条）" % (t["name"], t["count"]) for t in m["topics"][:limit]))
    return "\n".join(lines)


def why_empty(db, query, terms):
    """检索没命中时说人话：这块为什么是空的，我大致记得什么

    比直接返回空列表有用得多 —— 至少能看出是「没记过」还是「记过但没找到」。
    """
    m = topic_map(db)
    if not m["total"]:
        return "记忆库还是空的。"

    # 有没有相关的主题？用查询里的字去撞主题名
    near = []
    for t in m["topics"]:
        name = t["name"]
        if any(x and (x in name or name in x) for x in terms):
            near.append(t)
    if near:
        return "没找到直接相关的，但我记得这些相近的主题：" + "；".join(
            "%s（%d 条）" % (t["name"], t["count"]) for t in near[:5])

    return ("这件事我没印象。我一共记得 %d 条，主要是这些主题：%s"
            % (m["total"], "、".join(t["name"] for t in m["topics"][:8])))
