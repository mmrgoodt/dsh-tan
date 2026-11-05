# -*- coding: utf-8 -*-
"""扩展召回要设闸：只靠同义词进来的条目，数量封顶，不许淹掉原词命中"""
import io

p = "hippocampus/retriever.py"
s = io.open(p, encoding="utf-8").read()

old = '''        scored.sort(key=lambda x: -x[0])

        # 实体扩散：命中实体带出关联记忆
        picked = [m for _, m in scored[:limit]]'''

new = '''        # 原词是否直接命中：扩展召回的条目不能和真命中平起平坐，
        # 否则「广告插件」这种查询会被同义词带进来一堆弱相关的，正文直接灌满。
        def _base_hit(m):
            t = ((m.get("content") or "") + " " + (m.get("summary") or "")).lower()
            return any(x.lower() in t for x in terms)

        if term_w is not None:
            scored.sort(key=lambda x: (0 if _base_hit(x[1]) else 1, -x[0]))
            strong = [m for _, m in scored if _base_hit(m)]
            weak = [m for _, m in scored if not _base_hit(m)]
            # 弱相关最多放 strong 的等量条数（且不少于 2 条），留个兜底但不喧宾夺主
            picked = (strong + weak[:max(2, len(strong))])[:limit]
        else:
            scored.sort(key=lambda x: -x[0])
            picked = [m for _, m in scored[:limit]]'''

if old not in s:
    raise SystemExit("MISS: recall tail")
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("OK: recall gated")
