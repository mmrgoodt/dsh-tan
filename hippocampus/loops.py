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

# ---------- 噪音判定：AI 的回答正文被整段当成了待办 ----------
# 抽取侧不看说话人，我回一大段话时，开头那句常被写成 todo，
# 然后每轮都去「提醒」他一件根本没答应过的事。
#
# 这里只拦**强信号**，判得比 scripts/cleanup_dead_data.py 的批量清理松：
# 「我明天还得看下报表」是用户亲口的承诺，也以「我」开头，
# 运行时误伤一条真待办，比漏掉十条噪音的代价大。
NOISE_MARKS = ("##", "|", "✅", "**", "```", "——")
NOISE_HEADS = ("已", "这轮", "刚才", "下面", "关键", "Background", "subagent", "Task")
NOISE_MAX_LEN = 60
# 「真待办」长什么样：对着我说的请求，或者他还没做完的事。
# 注意别把「改完了/做完了」算进来 —— 那是**我**在汇报进度，正是噪音的特征。
REAL_TODO = ("帮我", "帮忙", "给我", "你去", "你帮", "记得", "明天", "下次",
             "回头", "得看", "还得", "要不要", "待会", "等我")


def is_noise_title(title):
    """标题像不像 AI 的正文（而不是一件真待办）"""
    t = (title or "").strip()
    if not t:
        return False
    if any(k in t for k in REAL_TODO):        # 先认「真待办」的信号
        return False
    if len(t) > NOISE_MAX_LEN:
        return True
    if any(k in t for k in NOISE_MARKS):      # 带排版符号的必然是我写的正文
        return True
    if t.startswith(NOISE_HEADS):
        return True
    if t.startswith("我") and ("：" in t or len(t) > 30):
        return True
    return False


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
            # 噪音：AI 正文冒充的待办，永远不提（真待办不受影响）
            if is_noise_title(lp["title"]):
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
