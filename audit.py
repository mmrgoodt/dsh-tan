# -*- coding: utf-8 -*-
"""全面体检：把所有能想到的地方跑一遍，看哪里真的有问题

不是跑测试用例看它「通过」，是拿真实问题看它「答对没有」。
每一项都打死分数，不通过就列出来。
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hippocampus.db import DB
from hippocampus.retriever import Retriever, tokenize

D = DB()
R = Retriever(D)
issues = []


def h(t):
    print("\n" + "=" * 56)
    print(t)
    print("=" * 56)


# ---------- 1. 检索：真实问题 ----------
h("1. 检索 —— 拿真问题问，看第一条对不对")

CASES = [
    ("迅雷广告怎么搞的", ["Thunder", "gdt", "广告组件"]),
    ("密码清单在哪", ["密码清单", "<项目目录>"]),
    ("Clash 是怎么配的", ["TUN", "Clash"]),
    ("node 找不到", ["nodejs", "Program Files"]),
    ("GitHub 怎么连的", ["SSH", "<github-user>", "GitHub"]),
    ("OneDrive 卸载干净了吗", ["OneDrive", "卸载"]),
    ("记忆系统用什么库", ["memory.db", "SQLite", "记忆"]),
    ("我让你说话可爱一点", ["可爱", "语气"]),
    ("开机自启关了没", ["开机自启", "启动"]),
    ("这套东西装在哪", ["dsh-tan", "D:\\dsh-tan"]),
    ("thunder", ["Thunder", "迅雷"]),
    ("gdt", ["gdt", "广告"]),
]

ok = 0
for q, expect in CASES:
    d = None
    try:
        from hippocampus.cortex import activate
        res = activate(D, q, limit=3)
        texts = []
        for mid, sc in res:
            m = D.get_memory(mid)
            if m:
                texts.append((m.get("summary") or "") + (m.get("content") or "")[:120])
        blob = " ".join(texts)
        hit = any(e.lower() in blob.lower() for e in expect)
        if hit:
            ok += 1
        else:
            issues.append("检索没命中：「%s」想找 %s" % (q, expect))
        print("  %s %-18s %s" % ("✓" if hit else "✗", q,
                                 (texts[0][:40] if texts else "(空)")))
    except Exception as e:
        issues.append("检索异常：%s -> %s" % (q, e))
        print("  ✗ %-18s 异常 %s" % (q, e))
print("\n  命中 %d/%d" % (ok, len(CASES)))

# ---------- 2. 写入闸 ----------
h("2. 写入闸 —— 该留的留，该丢的丢")

from hippocampus.gate import should_keep

GATE = [
    ("我觉得还是要继续优化一下", False),
    ("好的没问题", False),
    ("嗯", False),
    ("就这样吧", False),
    ("以后都走 TUN 模式，别再切回系统代理", True),
    ("配置在 D:/dsh-tan/config.json", True),
    ("记住：仓库必须私有", True),
    ("那个坑别再踩了，必须先关 TUN", True),
    ("Node v24.22.1 装在 Program Files", True),
    ("我重启了一下服务", False),
    ("今天天气不错", False),
    ("密码清单在 <账号目录> 目录", True),
]
gok = 0
for t, want in GATE:
    got = should_keep(t, "L2")
    if got == want:
        gok += 1
    else:
        issues.append("写入闸判错：「%s」该%s，实际%s" % (t[:20], "留" if want else "丢", "留" if got else "丢"))
    print("  %s %-32s → %s" % ("✓" if got == want else "✗", t[:30], "留" if got else "丢"))
print("\n  正确 %d/%d" % (gok, len(GATE)))

# ---------- 3. 分层 ----------
h("3. 分层 —— 事实和情景有没有分开")

from collections import Counter
layers = Counter(r["layer"] for r in D.query(
    "SELECT layer FROM memories WHERE status='active'"))
print("  分布:", dict(layers))
total = sum(layers.values())
if layers.get("L2", 0) > total * 0.75:
    issues.append("L2 占比 %.0f%%，事实层太胖，分层没起作用" % (100.0 * layers["L2"] / total))
if layers.get("L1", 0) == 0:
    issues.append("L1 情景层是空的，说明没有东西在淡出")

# 抽查：L2 里有多少真的含坐标
INFO = re.compile(r"[A-Za-z]:\\|/[\w.]{2,}/|```|https?://|\b\d+\.\d+\.\d+\b|\.(py|js|json|md|db|exe|bat)\b")
l2 = [r for r in D.query("SELECT content,summary FROM memories WHERE status='active' AND layer='L2'")]
bad = [r for r in l2 if not INFO.search((r["content"] or "") + (r["summary"] or ""))]
print("  L2 里没坐标的: %d/%d" % (len(bad), len(l2)))
if len(bad) > len(l2) * 0.4:
    issues.append("L2 里 %d 条没具体坐标，事实层混进了叙述" % len(bad))

# ---------- 4. 预测 ----------
h("4. 预测表 —— 推出来的东西像不像话")

from hippocampus import predict
BAD_PRED = re.compile(r"^(spm|https?|com|www|\d+$)")
pbad = 0
prows = D.query("SELECT src, dst, count FROM predictions ORDER BY count DESC LIMIT 40")
for r in prows:
    if BAD_PRED.match(r["dst"]) or len(r["dst"]) < 2:
        pbad += 1
print("  前 40 条规律里，目标明显是垃圾的: %d 条" % pbad)
for r in prows[:6]:
    print("    %-10s → %-12s %d" % (r["src"][:10], r["dst"][:12], r["count"]))
if pbad > 6:
    issues.append("预测表前 40 条里有 %d 条目标词是垃圾，质量不合格" % pbad)

# ---------- 5. 性能 ----------
h("5. 性能")

t = time.time()
for _ in range(10):
    R.recall("迅雷", limit=6)
dt = (time.time() - t) / 10
print("  recall 平均 %.3f 秒" % dt)
if dt > 0.15:
    issues.append("recall 平均 %.2f 秒，太慢" % dt)

from hippocampus import cortex
t = time.time()
cortex.build(D)
print("  cortex.build %.2f 秒" % (time.time() - t))

# ---------- 6. 边界 ----------
h("6. 边界情况")

EDGE = ["", "   ", "a", "啊" * 200, "?!@#$%", "迅雷" * 50, "SELECT * FROM memories"]
for q in EDGE:
    try:
        r = R.recall(q, limit=3)
        print("  %-24s → %d 条" % ((q[:20] or "(空)"), len(r)))
    except Exception as e:
        issues.append("边界输入崩了：「%s」%s" % (q[:12], e))
        print("  %-24s → 崩 %s" % (q[:20], e))

# ---------- 7. 数据一致性 ----------
h("7. 数据一致性")

orphan = D.one("SELECT COUNT(*) c FROM traces WHERE memory_id NOT IN (SELECT id FROM memories)")
print("  孤立的 traces（指向不存在的记忆）:", orphan["c"])
if orphan["c"] > 0:
    issues.append("有 %d 条 traces 指向已不存在的记忆" % orphan["c"])

dead_syn = D.one(
    "SELECT COUNT(*) c FROM synapses WHERE a NOT IN (SELECT name FROM concepts) "
    "OR b NOT IN (SELECT name FROM concepts)")
print("  指向不存在概念的突触:", dead_syn["c"])
if dead_syn["c"] > 0:
    issues.append("有 %d 条突触连着不存在的概念" % dead_syn["c"])

nosum = D.one("SELECT COUNT(*) c FROM memories WHERE status='active' AND (summary IS NULL OR summary='')")
print("  active 但没有 summary 的记忆:", nosum["c"])
if nosum["c"] > 0:
    issues.append("有 %d 条记忆没有摘要，注入时会是空的" % nosum["c"])

# ---------- 汇总 ----------
h("体检结果")
if not issues:
    print("  没发现问题。")
else:
    print("  发现 %d 个问题：" % len(issues))
    for i, x in enumerate(issues, 1):
        print("  %2d. %s" % (i, x))
