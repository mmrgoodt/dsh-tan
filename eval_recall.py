# -*- coding: utf-8 -*-
"""记忆召回评测：跑 golden_cases，看 top-k 命中率

用法: python eval_recall.py [k]
判定：某条召回的正文里覆盖了该 case 至少一半的关键词，就算命中。
"""
import io
import json
import sys

sys.path.insert(0, ".")
from hippocampus.db import DB
from hippocampus.retriever import Retriever

K = int(sys.argv[1]) if len(sys.argv) > 1 else 3

db = DB()
r = Retriever(db)
cases = db.query("SELECT * FROM golden_cases ORDER BY id")

hit = 0
total = 0
detail = []
for c in cases:
    q = c["question"]
    try:
        kws = json.loads(c["keywords"] or "[]")
    except Exception:
        kws = []
    if not q or not kws:
        continue
    total += 1
    # 跟真实体验对齐：看最终注入上下文的文字，而不是 recall 的裸排名
    text, used, _tok = r.build_context(q)
    mems = used
    blob = text
    need = max(1, (len(kws) + 1) // 2)
    got = sum(1 for k in kws if k and k in blob)
    ok = got >= need
    if ok:
        hit += 1
    detail.append((ok, q, kws, got, need, len(mems)))

print("golden_cases 召回评测  top-%d" % K)
print("-" * 68)
for ok, q, kws, got, need, n in detail:
    print("%s %-26s 命中 %d/%d 需 %d  召回 %d 条" % ("PASS" if ok else "miss", q[:26], got, len(kws), need, n))
print("-" * 68)
print("命中率 %d/%d = %.1f%%" % (hit, total, 100.0 * hit / max(1, total)))
