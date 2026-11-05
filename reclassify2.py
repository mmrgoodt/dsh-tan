# -*- coding: utf-8 -*-
"""重分层：把「叙述」从「事实」里分出来

现状：360 条记忆里只有 104 条含具体坐标（路径、版本、命令、配置），
剩下 256 条是「我做了什么」的叙述。但这两类都被标成了 L2 事实层，
半衰期 180 天 —— 于是「我重启了一下」和「Node 装在 C:\\Program Files\\nodejs」
一样值钱，一起躺在注入预算里抢位置。

大脑不是这么分的：
  「今天下午三点我重启了服务」  → 情景，几天就淡
  「Node 装在 C:\\Program Files\\nodejs」 → 事实，一直在

判定很硬：
  有具体坐标 → 留 L2
  没有       → 降 L1（3 天半衰期，自然淡出，但还被问得到）
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hippocampus.db import DB

# 可复用的坐标：路径、代码、版本、注册表、URL、扩展名
INFO = re.compile(
    r"[A-Za-z]:\\|/[\w.]{2,}/|```|https?://|\b\d+\.\d+\.\d+\b|"
    r"\b(HKCU|HKLM|HKEY_)\b|\bSoftware\\|"
    r"\.(py|js|json|yml|yaml|md|db|exe|bat|ps1|toml)\b"
)

# 这几层不动：身份、方法、教训、以及 LLM 提炼的结论
KEEP_LAYERS = ("L3", "L4", "L5", "L2-")


def main():
    do = "--do" in sys.argv
    db = DB()
    rows = [dict(r) for r in db.query(
        "SELECT * FROM memories WHERE status='active'")]

    plan = []
    for m in rows:
        if m.get("layer") in KEEP_LAYERS:
            continue
        if m.get("pinned") or m.get("protected"):
            continue
        try:
            import json
            prov = json.loads(m.get("provenance") or "{}")
            if isinstance(prov, dict) and prov.get("by") == "llm":
                continue
        except Exception:
            pass
        body = (m.get("content") or "") + " " + (m.get("summary") or "")
        if INFO.search(body):
            continue
        if m.get("layer") == "L1":
            continue
        plan.append(m)

    print("active %d 条，其中 %d 条是纯叙述、当前却挂在事实层" % (len(rows), len(plan)))
    for m in plan[:10]:
        print("  %s  %s" % (m["layer"], (m.get("summary") or "")[:56]))

    if not do:
        print("\n(dry run，加 --do 才执行)")
        return

    n = 0
    for m in plan:
        try:
            db.update_memory(m["id"], layer="L1", half_life_days=3.0)
            n += 1
        except Exception:
            pass
    print("\n已降为情景层 L1：%d 条（半衰期 3 天，会自然淡出，但仍可检索）" % n)

    from collections import Counter
    after = Counter(r["layer"] for r in db.query(
        "SELECT layer FROM memories WHERE status='active'"))
    print("现在分层:", dict(after))


if __name__ == "__main__":
    main()
