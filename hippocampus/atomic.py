# -*- coding: utf-8 -*-
"""原子记忆：LLM 主动写入时的落库逻辑

和正则抽取的区别：
  · 正则只能猜「这句话像不像偏好」，模型能直接说「这是一条偏好，内容是什么」
  · 同一件事反复说，不该存十条，应该强化那一条
  · 新事实推翻旧事实时，旧的要标 superseded，不是留着两份打架

对齐 Mem0 / Letta 的写入流程：
  ADD    —— 库里没有相近的，插一条
  UPDATE —— 有相近的，用新的覆盖旧的（旧条目 superseded_by 指向新的）
  NOOP   —— 已经记过了，只做一次强化
"""
import json
import math
import time

from . import extractor
from .db import now

# kind → 记忆层
KIND_LAYER = {
    "fact": "L2",        # 事实：环境、配置、结论
    "preference": "L4",  # 偏好：怎么跟我说话、喜欢什么
    "lesson": "L2-",     # 教训：踩过的坑
    "method": "L3",      # 方法：可复用的步骤
    "identity": "L5",    # 身份：我是谁
    "episode": "L1",     # 情景：某天发生了什么
}
PROTECTED_KINDS = ("preference", "identity", "lesson", "method")

DUP_THRESHOLD = 0.82    # 高于这个：已经记过
CONFLICT_THRESHOLD = 0.55


def _shingles(text, n=3):
    """字符 n-gram 集合，用来算 Jaccard"""
    t = "".join(ch for ch in (text or "").lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fa5")
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def similarity(a, b):
    """字符 3-gram Jaccard —— 对中文短句够用，不需要模型"""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return inter / max(1, len(sa | sb))


def find_similar(db, text, limit=40):
    """粗筛候选：先按字面召回，再用 Jaccard 精排"""
    try:
        rows = db.query(
            "SELECT id, content, summary, layer FROM memories WHERE status='active' "
            "ORDER BY created DESC LIMIT ?", (limit,)
        )
    except Exception:
        return []
    scored = []
    for r in rows:
        body = (r["summary"] or r["content"] or "")
        s = similarity(text, body)
        if s > 0.15:
            scored.append((s, dict(r)))
    scored.sort(key=lambda x: -x[0])
    return scored


class AtomicWriter:
    def __init__(self, db):
        self.db = db

    def save(self, text, kind="fact", about="", session="", source="S-llm"):
        """返回 {"action": add|update|noop, "id": ..., "similar": 0.83}"""
        text = (text or "").strip()
        if len(text) < 4:
            return {"action": "skip", "reason": "太短"}

        layer = KIND_LAYER.get(kind, "L2")
        cands = find_similar(self.db, text)
        best = cands[0] if cands else None

        # ① 已经记过：不重复插，强化一次
        if best and best[0] >= DUP_THRESHOLD:
            mid = best[1]["id"]
            try:
                old = self.db.get_memory(mid)
                self.db.update_memory(
                    mid,
                    recall_count=int(old["recall_count"] or 0) + 1,
                    last_recalled=time.time(),
                    content=text if len(text) > len(old["content"] or "") else old["content"],
                )
            except Exception:
                pass
            return {"action": "noop", "id": mid, "similar": round(best[0], 3),
                    "reason": "已经记过，做了强化"}

        # ② 相近但不同：新条目覆盖旧的
        superseded = None
        if best and best[0] >= CONFLICT_THRESHOLD:
            superseded = best[1]["id"]

        extra = {}
        if kind in ("preference", "identity"):
            extra["half_life_days"] = 99999.0
        mid = self.db.add_memory(
            layer=layer,
            content=text,
            summary=extractor.summarize(text, 70),
            fidelity="verbatim",
            source=source,
            confidence=1.0,
            protected=1 if kind in PROTECTED_KINDS else 0,
            pinned=1 if kind == "identity" else 0,
            entities=extractor.extract_entities(text),
            tags=[kind] + ([about] if about else []),
            provenance={"session": session, "by": "llm", "kind": kind, "about": about},
            **extra
        )

        if superseded:
            try:
                self.db.update_memory(superseded, superseded_by=mid, status="superseded")
            except Exception:
                pass

        return {"action": "update" if superseded else "add", "id": mid,
                "similar": round(best[0], 3) if best else 0.0,
                "superseded": superseded}
