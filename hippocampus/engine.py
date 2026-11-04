# -*- coding: utf-8 -*-
"""记忆系统主引擎：把各模块串起来，对外提供统一接口"""
import json
import time

from . import config
from .db import DB
from .writer import Writer
from .retriever import Retriever
from .forget import Forgetter
from .persona import Persona
from .loops import Loops
from .proactive import Proactive
from .evolution import Evolution


class Hippocampus:
    def __init__(self, db_path=None):
        self.db = DB(db_path)
        self.writer = Writer(self.db)
        self.retriever = Retriever(self.db)
        self.forgetter = Forgetter(self.db)
        self.persona = Persona(self.db)
        self.loops = Loops(self.db)
        self.proactive = Proactive(self.db, self.loops, self.persona)
        self.evolution = Evolution(self.db, self.retriever)

    # ==================== 对话前：准备上下文 ====================
    def prepare_context(self, query="", include_persona=True):
        """返回 (注入文本, 详情)"""
        parts = []

        # ① 人格块（固定前缀，不占记忆预算）
        if include_persona:
            pb = self.persona.prompt_block()
            if pb:
                parts.append(pb)

        # ② 记忆块
        mem_text, used, tokens = self.retriever.build_context(query)
        if mem_text:
            parts.append("## 我记得的事\n" + mem_text)

        # ③ 未闭合提醒（每轮最多 1 条）
        hints = self.loops.due_for_mention(query)
        inject_loops = []
        if hints:
            lp = hints[0]
            parts.append("## 还欠着的\n· " + self.loops.render_hint(lp))
            inject_loops.append(lp["id"])
            # 标记已问过，否则每轮都提同一条（冷却形同虚设）
            try:
                self.loops.mark_asked(lp["id"])
            except Exception:
                pass

        return "\n\n".join(parts), {
            "memories": len(used),
            "tokens": tokens,
            "loops": inject_loops,
        }

    # ==================== 对话后：写入 ====================
    def after_turn(self, user_text="", assistant_text="", session="", turn=0, context_note=""):
        r = self.writer.ingest_turn(user_text, assistant_text, session, turn, context_note)
        # 人格消化
        self.persona.observe(user_text)
        # 记录用户消息事件（用于主动消息的回访判断）
        if user_text:
            self.db.execute(
                "INSERT INTO events (ts,kind,payload) VALUES (?,?,?)",
                (time.time(), "user_message", json.dumps({"len": len(user_text)}, ensure_ascii=False)),
            )
        # 每天跑一次清理
        self._maybe_daily_maintenance()
        return r

    def _maybe_daily_maintenance(self):
        last = self.db.one("SELECT MAX(ts) t FROM events WHERE kind='daily_sweep'")
        if last and last["t"] and (time.time() - last["t"]) < 20 * 3600:
            return
        self.forgetter.sweep()
        self.db.execute(
            "INSERT INTO events (ts,kind,payload) VALUES (?,?,?)",
            (time.time(), "daily_sweep", "{}"),
        )

    # ==================== 手动操作 ====================
    def remember(self, text, layer="L2", pinned=False):
        return self.writer.remember(text, layer=layer, pinned=pinned)

    def recall(self, query, limit=8):
        mems = self.retriever.recall(query, limit=limit)
        return [
            {
                "id": m["id"],
                "layer": m["layer"],
                "when": config.fuzzy_time((time.time() - (m["created"] or 0)) / 86400),
                "text": m.get("summary") or m.get("content"),
                "pinned": bool(m.get("pinned")),
            }
            for m in mems
        ]

    def mark_rejected(self, mem_ids):
        """用户说「记错了」时调用"""
        for mid in mem_ids:
            m = self.db.get_memory(mid)
            if m:
                self.retriever.mark_used([m], rejected=True)

    def mark_used(self, mem_ids):
        for mid in mem_ids:
            m = self.db.get_memory(mid)
            if m:
                self.retriever.mark_used([m], rejected=False)

    def edit_memory(self, mid, content=None, summary=None):
        """手动改一条记忆（AI 提取错了的地方）"""
        if not mid:
            return False
        fields = {}
        if content is not None:
            fields["content"] = str(content)[:4000]
        if summary is not None:
            fields["summary"] = str(summary)[:200]
        if not fields:
            return False
        self.db.update_memory(mid, **fields)
        try:
            self.db.persona_log("edit", mid, "手动编辑记忆")
        except Exception:
            pass
        return True

    def forget(self, mid, mode="cold"):
        return self.forgetter.forget(mid, mode)

    def pin(self, mid, value=True):
        return self.forgetter.pin(mid, value)

    # ==================== 查询 ====================
    def checklist(self):
        return self.loops.checklist()

    def ask_next_loop(self):
        """主动模式：挑一条该说的（供定时器调用）"""
        ok, reason = self.proactive.should_send()
        if not ok:
            return None, reason
        slot = reason
        text = self.proactive.compose(slot)
        self.db.execute(
            "INSERT INTO events (ts,kind,payload) VALUES (?,?,?)",
            (time.time(), "proactive_sent", json.dumps({"slot": slot, "text": text}, ensure_ascii=False)),
        )
        return text, slot

    def status(self):
        st = self.db.stats()
        p = self.persona.get()
        return {
            "memories": st["memories"],
            "by_layer": st["by_layer"],
            "threads": st["threads"],
            "open_loops": st["open_loops"],
            "persona": {
                "stage": p["stage"] if p else "初识",
                "turns": p["turns"] if p else 0,
            },
            "db_path": str(getattr(self.db, "path", config.DB_PATH)),
        }

    def weekly_report(self):
        return self.evolution.weekly_report()

    def run_golden(self, verbose=False):
        return self.evolution.run_golden(verbose=verbose)

    def snapshot(self, note=""):
        return self.evolution.snapshot(note)

    def rollback(self, version):
        return self.evolution.rollback(version)
