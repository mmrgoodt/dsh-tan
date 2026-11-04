# -*- coding: utf-8 -*-
"""Open Loops：未闭合事项的管理与"开口时接住" """
import json
import time

from . import config

KIND_LABEL = {
    "promise": "答应的事",
    "paused": "半途而废",
    "failed": "没搞成的",
    "todo": "要做的事",
    "known_issue": "已知问题",
}


class Loops:
    def __init__(self, db):
        self.db = db

    # ---------- 查询 ----------
    def list_open(self):
        return self.db.open_loops()

    def due_for_mention(self, query="", limit=None):
        """挑出这一轮值得提的（密度控制：每轮最多 1 条）"""
        limit = limit or config.LOOPS_PER_TURN
        loops = self.db.open_loops()
        if not loops:
            return []

        now = time.time()
        cand = []
        for lp in loops:
            # 冷却：提过 24h 内不再提
            if lp["last_asked"] and (now - lp["last_asked"]) / 3600 < config.LOOP_COOLDOWN_HOURS:
                continue
            # 相关度：标题/详情与当前输入有共同字
            rel = 0.0
            if query:
                q = set(query)
                t = set((lp["title"] or "") + (lp["detail"] or ""))
                if q & t:
                    rel = len(q & t) / max(1, len(q))
            age_days = (now - (lp["created"] or now)) / 86400
            urgency = min(1.0, age_days / 3.0)          # 越久越急
            score = rel * 2.0 + urgency * 0.6 + (0.3 if lp["kind"] in ("promise", "failed") else 0)
            cand.append((score, lp))

        cand.sort(key=lambda x: -x[0])
        return [lp for _, lp in cand[:limit]]

    def mark_asked(self, lid):
        lp = self.db.one("SELECT * FROM open_loops WHERE id=?", (lid,))
        if lp:
            self.db.execute(
                "UPDATE open_loops SET last_asked=?, ask_count=? WHERE id=?",
                (time.time(), (lp["ask_count"] or 0) + 1, lid),
            )

    # ---------- 状态 ----------
    def close(self, lid):
        self.db.execute("UPDATE open_loops SET status='done' WHERE id=?", (lid,))

    def mute(self, lid):
        self.db.execute("UPDATE open_loops SET status='muted' WHERE id=?", (lid,))

    def snooze(self, lid, hours=24):
        self.db.execute(
            "UPDATE open_loops SET last_asked=? WHERE id=?",
            (time.time() + hours * 3600 - config.LOOP_COOLDOWN_HOURS * 3600, lid),
        )

    # ---------- 生成提示语 ----------
    def render_hint(self, loop):
        title = (loop["title"] or "").strip()
        if len(title) > 22:
            title = title[:22] + "…"
        age = (time.time() - (loop["created"] or time.time())) / 86400
        when = "今天" if age < 0.5 else ("昨天" if age < 1.5 else "前几天")
        if loop["kind"] == "promise":
            return f"你{when}说「{title}」，这事我记着呢。"
        if loop["kind"] == "paused":
            return f"{when}那事你说先放一放——「{title}」，现在要接着弄吗？"
        if loop["kind"] == "failed":
            return f"{when}那个「{title}」没弄成，要不要再试一次？"
        if loop["kind"] == "known_issue":
            return f"还有个问题一直挂着：「{title}」，要处理吗？"
        return f"{when}提过「{title}」，弄了吗？"

    # ---------- 对账清单 ----------
    def checklist(self):
        loops = self.db.open_loops()
        if not loops:
            return "📋 未闭合清单：空的，都做完了。"
        lines = [f"📋 未闭合清单（{len(loops)} 件）", ""]
        for i, lp in enumerate(loops, 1):
            age = (time.time() - (lp["created"] or time.time())) / 86400
            kind = KIND_LABEL.get(lp["kind"], "")
            lines.append(f"{i}. 【{kind}·{age:.0f}天前】{lp['title']}")
        return "\n".join(lines)
