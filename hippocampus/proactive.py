# -*- coding: utf-8 -*-
"""主动消息：时段选择、频率控制、内容生成（不调用模型）"""
import json
import time
from datetime import datetime

from . import config


class Proactive:
    def __init__(self, db, loops=None, persona=None):
        self.db = db
        self.loops = loops
        self.persona = persona

    # ---------- 静默判断 ----------
    def in_quiet_hours(self, dt=None):
        dt = dt or datetime.now()
        h = dt.hour
        s, e = config.PROACTIVE_QUIET_START, config.PROACTIVE_QUIET_END
        if s > e:                     # 23 → 7 跨天
            return h >= s or h < e
        return s <= h < e

    def slot_now(self, dt=None):
        dt = dt or datetime.now()
        for start, end, name in config.PROACTIVE_SLOTS:
            if start <= dt.hour < end:
                return name
        return None

    # ---------- 频率控制 ----------
    def sent_today(self):
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        row = self.db.one(
            "SELECT COUNT(*) c FROM events WHERE kind='proactive_sent' AND ts>=?", (day_start,)
        )
        return row["c"] if row else 0

    def quiet_days(self):
        """连续几天没互动"""
        row = self.db.one(
            "SELECT MAX(ts) t FROM events WHERE kind='user_message'"
        )
        if not row or not row["t"]:
            return 0
        return int((time.time() - row["t"]) / 86400)

    def daily_limit(self):
        if self.quiet_days() >= config.PROACTIVE_BACKOFF_DAYS:
            return 1                    # 连续不理我 → 降到 1 条
        return config.PROACTIVE_MAX_PER_DAY

    # ---------- 决定要不要发 ----------
    def should_send(self, now=None):
        now = now or datetime.now()
        if self.in_quiet_hours(now):
            return False, "静默时段"
        slot = self.slot_now(now)
        if not slot:
            return False, "不在计划时段"
        if self.sent_today() >= self.daily_limit():
            return False, "今天额度已满"
        # 这个时段今天已经发过？
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        rows = self.db.query(
            "SELECT payload FROM events WHERE kind='proactive_sent' AND ts>=?", (day_start,)
        )
        for r in rows:
            try:
                pl = json.loads(r["payload"] or "{}")
                if pl.get("slot") == slot:
                    return False, f"{slot} 已发过"
            except Exception:
                pass
        return True, slot

    # ---------- 生成内容 ----------
    def compose(self, slot):
        """按人格 + 未闭合清单 + 记忆，组装一条消息（纯模板，不调模型）"""
        p = self.persona.get() if self.persona else None
        name = (p.get("name") if p else "") or ""

        # 有正事优先说正事
        if self.loops:
            due = self.loops.due_for_mention()
            if due and slot in ("morning", "noon"):
                lp = due[0]
                self.loops.mark_asked(lp["id"])
                return self.loops.render_hint(lp)

        # 否则按人格闲聊
        stage = p.get("stage") if p else "初识"
        if slot == "morning":
            base = random_pick([
                "起了没？今天想先弄哪块？",
                "早。今天有什么要紧事吗？",
                "早上好，今天别又熬到半夜。",
            ])
        elif slot == "noon":
            base = random_pick([
                "中午吃了吗？",
                "这个点还不歇会儿？",
                "午饭别对付。",
            ])
        else:
            base = random_pick([
                "今天就到这儿吧，别熬。",
                "晚上还弄吗？不弄就早点歇。",
                "今天累不累？",
            ])

        if stage in ("初识", "熟悉"):
            return base
        return base


def random_pick(items):
    import random
    return random.choice(items)
