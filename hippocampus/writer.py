# -*- coding: utf-8 -*-
"""写入流程：提取 → 打标 → 归层 → 归 Thread → 落库"""
import json
import time

from . import config
from . import distill as distill_mod
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
        out = {"memories": [], "loops": [], "thread": None, "noise": 0,
               "distill": {"dropped": [], "kinds": {}}, "superseded": []}

        # 1) 用户侧
        if user_text:
            r = self._ingest_one(user_text, "user", session, turn, context_note, ts)
            out["memories"] += r["memories"]
            out["loops"] += r["loops"]
            out["noise"] += r["noise"]
            out["superseded"] += r.get("superseded") or []
            self._merge_distill(out, r)
            if r.get("thread"):
                out["thread"] = r["thread"]

        # 2) 助手侧（只记结论/决策，权重低）
        if assistant_text:
            r = self._ingest_one(assistant_text, "assistant", session, turn, context_note, ts)
            out["memories"] += r["memories"]
            out["loops"] += r["loops"]
            out["superseded"] += r.get("superseded") or []
            self._merge_distill(out, r)

        return out

    @staticmethod
    def _merge_distill(out, r):
        """把单侧的提炼统计并进整轮结果（调试/自测看这里）"""
        d = r.get("distill") or {}
        out["distill"]["dropped"] += d.get("dropped") or []
        for k, v in (d.get("kinds") or {}).items():
            out["distill"]["kinds"][k] = out["distill"]["kinds"].get(k, 0) + v

    def _ingest_one(self, text, role, session, turn, context_note, ts=None):
        # 0) 提炼：一轮话里大半是过程叙述（「我修好了」「已经推送了」「做了四件事」），
        # 先按规则压成「事实 / 偏好 / 教训」三类，再走后面的提取。
        # 全被丢掉 = 这轮没有可记的东西：不建 Thread、不占预算、不留流水账。
        d = distill_mod.distill(text, role)
        distilled = {"dropped": d["dropped"], "kinds": d["kinds"]}
        if not d["text"]:
            return {"memories": [], "loops": [], "noise": 0, "thread": None,
                    "distill": distilled, "superseded": []}
        text = d["text"]

        info = extractor.extract(text, role)
        # 只留有价值的：有具体坐标，或者明确是决策/教训。
        # 不加这道闸，360 条里 256 条是流水账，抢的是真事的预算。
        try:
            from .gate import should_keep
            # noise 也要过闸。之前排除 noise 是个漏洞：
            # 「好的没问题」被判为噪音后走了 noise 分支，照样入库。
            if info.get("keep"):
                if not should_keep(info.get("content") or text, info.get("layer")):
                    return {"memories": [], "loops": [], "noise": 0, "thread": None,
                            "distill": distilled, "superseded": []}
        except Exception:
            pass
        res = {"memories": [], "loops": [], "noise": 0, "thread": None,
               "distill": distilled, "superseded": []}
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

        # 冲突覆盖：同一主题的新结论进来，把旧的那条标 superseded，
        # 不留两条互相打架的记录（路径 A、路径 B 同时 active 是最糟的状态）。
        res["superseded"] = self._supersede(mid, info.get("content") or text, info["layer"], ts)

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

    def _supersede(self, new_id, text, layer, ts=None):
        """冲突覆盖：新结论盖掉旧结论，旧的标 status='superseded'

        判据（保守，判错了比不判更糟）：
          · 主题词重合 ≥2，且重合占较少一侧的一半以上
          · 新记忆 created 更晚（回补历史时按 ts 算）
          · 旧记忆没被 pinned（人手动钉住的不许自动覆盖）
          · 取值确实变了（路径/端口/版本/数字不同），或者同层态度反了

        旧的只改 status + superseded_by，内容和召回次数都留着 —— 能翻案。
        """
        try:
            # 只扫 active 且没 pinned 的：pinned 是「我说的算」，不参与自动覆盖
            rows = self.db.query(
                "SELECT id, content, summary, layer, created FROM memories "
                "WHERE status='active' AND pinned=0 AND id!=?", (new_id,))
        except Exception:
            return []
        if not rows:
            return []

        # 先用便宜的字面预筛：新记忆的主题词至少要有 2 个出现在旧正文里，
        # 才值得去算完整的分词重合。471 条活跃记忆时这一步把 tokenize 的
        # 调用次数从「每条一次」降到「几乎没有」，写一轮的开销保持在毫秒级。
        new_tokens = distill_mod.topic_tokens(text)
        if len(new_tokens) < 2:
            return []
        new_created = ts or time.time()
        out = []
        for r in rows:
            old_created = r["created"] or 0
            if old_created > new_created:
                continue
            old_text = (r["content"] or "") + " " + (r["summary"] or "")
            low = old_text.lower()
            if sum(1 for t in new_tokens if t in low) < 2:
                continue
            if not distill_mod.conflict(old_text, text,
                                        old_layer=r["layer"], new_layer=layer):
                continue
            try:
                self.db.update_memory(r["id"], status="superseded", superseded_by=new_id)
            except Exception:
                continue
            out.append(r["id"])
        return out[:5]

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
