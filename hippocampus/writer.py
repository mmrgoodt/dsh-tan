# -*- coding: utf-8 -*-
"""写入流程：提取 → 打标 → 归层 → 归 Thread → 落库"""
import json
import time

from . import config
from . import extractor
from .db import new_id, now
from .extractor import clip_content


class Writer:
    def __init__(self, db):
        self.db = db

    def ingest_turn(self, user_text="", assistant_text="", session="", turn=0,
                    context_note="", ts=None):
        """处理一轮对话，返回本次写入的结果

        ts: 这一轮的真实时间（回补历史时用，缺省就取当前时间）
        """
        out = {"memories": [], "loops": [], "thread": None, "noise": 0}

        # 1) 用户侧
        if user_text:
            r = self._ingest_one(user_text, "user", session, turn, context_note, ts)
            out["memories"] += r["memories"]
            out["loops"] += r["loops"]
            out["noise"] += r["noise"]
            if r.get("thread"):
                out["thread"] = r["thread"]

        # 2) 助手侧（只记结论/决策，权重低）
        if assistant_text:
            r = self._ingest_one(assistant_text, "assistant", session, turn, context_note, ts)
            out["memories"] += r["memories"]
            out["loops"] += r["loops"]

        return out

    def _ingest_one(self, text, role, session, turn, context_note, ts=None):
        info = extractor.extract(text, role)
        # 只留有价值的：有具体坐标，或者明确是决策/教训。
        # 不加这道闸，360 条里 256 条是流水账，抢的是真事的预算。
        try:
            from .gate import should_keep
            # noise 也要过闸。之前排除 noise 是个漏洞：
            # 「好的没问题」被判为噪音后走了 noise 分支，照样入库。
            if info.get("keep"):
                if not should_keep(info.get("content") or text, info.get("layer")):
                    return {"memories": [], "loops": [], "noise": 0, "thread": None}
        except Exception:
            pass
        res = {"memories": [], "loops": [], "noise": 0, "thread": None}
        if not info["keep"]:
            return res

        if info["noise"]:
            res["noise"] = 1
            # 噪音也存，但不建实体、不建 Thread，3 天后归档
            mid = self.db.add_memory(
                layer="L1",
                content=info.get("content") or text,
                summary=extractor.summarize(text, 40),
                source="S1" if role == "user" else "S3",
                emotion=1.0,
                created=ts,
                context_note=context_note,
                entities=[],
                tags=["noise"],
            )
            res["memories"].append(mid)
            return res

        # Thread 归属
        thread = self.db.find_thread_by_anchor(text)
        if not thread:
            title = extractor.summarize(text, 18)
            kind = "chat" if info["layer"] == "L1" and not info["loops"] else "task"
            tid = self.db.add_thread(title, kind=kind, anchors=info["entities"][:5])
            thread = {"id": tid, "title": title}
            res["thread"] = tid
        tid = thread["id"] if isinstance(thread, dict) else thread

        # 去重：同一件事说过就不再存第二份，只强化旧的那条。
        # 库里已经出现过「我在飞书里怎么看上下文」存两条这种，白占预算。
        try:
            from .atomic import similarity
            body = info.get("content") or text
            recent = self.db.query(
                "SELECT id, content, summary FROM memories "
                "WHERE status='active' AND layer=? ORDER BY created DESC LIMIT 80",
                (info["layer"],),
            )
            for r in recent:
                if similarity(body, (r["summary"] or r["content"] or "")) >= 0.82:
                    old_m = self.db.get_memory(r["id"])
                    self.db.update_memory(
                        r["id"],
                        recall_count=int(old_m["recall_count"] or 0) + 1,
                        last_recalled=time.time(),
                    )
                    res["memories"].append({"dup_of": r["id"]})
                    return res
        except Exception:
            pass

        mid = self.db.add_memory(
            layer=info["layer"],
            thread_id=tid,
            content=info.get("content") or text,
            summary=extractor.summarize(text, 70),
            fidelity=info.get("fidelity", "summary"),
            source="S1" if role == "user" else "S3",
            valence=info["valence"],
            emotion=info["emotion"],
            context_note=context_note,
            protected=info["protected"],
            entities=info["entities"],
            tags=[info["layer"]],
            created=ts,
            provenance={"session": session, "turn": turn, "quote": text[:200]},
        )
        res["memories"].append(mid)

        # 新记忆立刻连边，否则图谱永远是今天的样子
        try:
            from . import graph as _graph
            _graph.link_new(self.db, mid)
        except Exception:
            pass

        # 未闭合事项
        for kind, title in info["loops"]:
            lid = self.db.add_loop(kind, title, detail=text[:300], thread_id=tid)
            res["loops"].append(lid)

        return res

    # ---------- 显式记忆 ----------
    def remember(self, text, layer="L2", tags=None, entities=None, pinned=False, source="S1"):
        """用户说"记下来"时调用"""
        mid = self.db.add_memory(
            layer=layer,
            content=clip_content(text),
            summary=extractor.summarize(text, 70),
            fidelity="verbatim",
            source=source,
            pinned=pinned,
            protected=True if layer in config.PROTECTED_LAYERS else False,
            entities=entities or extractor.extract_entities(text),
            tags=tags or ["manual"],
        )
        return mid

    def close_open_loops(self, thread_id=None):
        if thread_id:
            self.db.execute(
                "UPDATE open_loops SET status='done' WHERE thread_id=? AND status='open'",
                (thread_id,),
            )
        else:
            self.db.execute("UPDATE open_loops SET status='done' WHERE status='open'")
