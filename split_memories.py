# -*- coding: utf-8 -*-
"""长记忆切块

问题：一条 400 字的汇报被存成单条记忆，里面顺手提了「迅雷」一次。
于是问「迅雷」时它也会命中，还排在有价值的短条目前面 —— 正文塞满了噪音。

修法：按语义边界切开，每块只讲一件事。
  · 有 `## 标题` 的按标题切
  · 没有的按 。；！ 分句，合并到 ~120 字一块
  · 原条目转 dormant（还能查到，不再注入）

用法: python split_memories.py [--do] [--min 180]
"""
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hippocampus import extractor
from hippocampus.db import DB

SPLIT_MIN = 180      # 超过这个长度才切
CHUNK_TARGET = 120   # 每块目标长度
CHUNK_MAX = 180      # 硬上限


def chunks_of(text):
    """把一段长文切成若干语义块"""
    t = (text or "").strip()
    if not t:
        return []

    # ① 先按 markdown 标题切
    if "## " in t:
        parts = re.split(r"\n(?=##\s)", t)
    else:
        parts = [t]

    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= CHUNK_MAX:
            out.append(p)
            continue
        # ② 按句号/分号/换行分句，再贪心合并
        sents = re.split(r"(?<=[。；！？\n])", p)
        buf = ""
        for s in sents:
            s = s.strip()
            if not s:
                continue
            if len(buf) + len(s) <= CHUNK_TARGET:
                buf += s
            else:
                if buf:
                    out.append(buf.strip())
                buf = s
                # 单句就超长的，硬切
                while len(buf) > CHUNK_MAX:
                    out.append(buf[:CHUNK_MAX])
                    buf = buf[CHUNK_MAX:]
        if buf.strip():
            out.append(buf.strip())
    # 去掉过短的碎片和重复
    seen = set()
    clean = []
    for c in out:
        c = c.strip(" ；;。\n")
        if len(c) < 12 or c in seen:
            continue
        seen.add(c)
        clean.append(c)
    return clean


def main():
    do = "--do" in sys.argv
    lo = SPLIT_MIN
    if "--min" in sys.argv:
        lo = int(sys.argv[sys.argv.index("--min") + 1])

    db = DB()
    rows = db.query("SELECT * FROM memories WHERE status='active' AND sensitivity!='secret'")
    plans = []
    total_new = 0
    for r in rows:
        content = r["content"] or ""
        if len(content) <= lo:
            continue
        # protected 只是「别被遗忘机制清掉」，不代表不能拆开存。
        # 之前把它也跳过，结果几条 350/550 字的长报告一条没切，噪音原样留着。
        if r["pinned"] or r["layer"] in ("L4", "L5"):
            continue          # 钉住的和身份层不切
        parts = chunks_of(content)
        if len(parts) <= 1:
            continue
        plans.append((r, parts))
        total_new += len(parts)

    print("命中 %d 条长记忆，将切成 %d 块" % (len(plans), total_new))
    for r, parts in plans[:6]:
        print("\n--- %s  %d 字 → %d 块" % (r["id"], len(r["content"] or ""), len(parts)))
        for p in parts[:4]:
            print("    · " + p[:76].replace("\n", " "))
    if len(plans) > 6:
        print("\n...另有 %d 条" % (len(plans) - 6))

    if not do:
        print("\n(dry run，加 --do 才真的切)")
        return

    made = 0
    for r, parts in plans:
        prov = {}
        try:
            prov = json.loads(r["provenance"] or "{}")
        except Exception:
            pass
        for i, p in enumerate(parts):
            try:
                sub = {
                    "session": prov.get("session"),
                    "turn": prov.get("turn"),
                    "split_from": r["id"],
                    "part": i,
                }
                db.add_memory(
                    layer=r["layer"],
                    thread_id=r["thread_id"],
                    content=p,
                    summary=extractor.summarize(p, 70),
                    fidelity="summary",
                    source=r["source"],
                    emotion=float(r["emotion"] or 1.0),
                    valence=float(r["valence"] or 0.0),
                    created=r["created"],
                    half_life_days=float(r["half_life_days"] or 180.0),
                    entities=extractor.extract_entities(p),
                    tags=["split"],
                    provenance=sub,
                )
                made += 1
            except Exception:
                continue
        db.update_memory(r["id"], status="dormant")

    print("\n完成：原 %d 条转 dormant，新写入 %d 块" % (len(plans), made))


if __name__ == "__main__":
    main()
