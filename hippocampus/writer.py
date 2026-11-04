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

    def ingest_turn(self, user_text="", assistant_text="", session="", turn=0, context_note=""):
        """处理一轮对话，返回本次写入的结果"""
        out = {"memories": [], "loops": [], "thread": None, "noise": 0}

        # 1) 用户侧
        if user_text:
            r = self._ingest_one(user_text, "user", session, turn, context_note)
            out["memories"] += r["memories"]
            out["loops"] += r["loops"]
            out["noise"] += r["noise"]
            if r.get("thread"):
                out["thread"] = r["thread"]

        # 2) 助手侧（只记结论/决策，权重低）
        if assistant_text:
            r = self._ingest_one(assistant_text, "assistant", session, turn, context_note)
            out["memories"] += r["memories"]
            out["loops"] += r["loops"]

        return out

    def _ingest_one(self, text, role, session, turn, context_note):
        info = extractor.extract(text, role)
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
            provenance={"session": session, "turn": turn, "quote": text[:200]},
        )
        res["memories"].append(mid)

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
