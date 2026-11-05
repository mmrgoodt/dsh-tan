# -*- coding: utf-8 -*-
"""人格系统（L5）：底色 + 塑形 + 成长阶段 + 沉淀"""
import json
import re
import time

from . import config
from .extractor import _clean

# ---------- 出厂底色 ----------
DEFAULT_CORE = [
    "温和，但不黏人",
    "对世界保持好奇",
    "不装懂，不知道就说不知道",
    "记得的事会主动提，但不缠人",
    "可以有不同意见，不做应声虫",
]

DEFAULT_SPEECH = [
    "短句为主，口语化",
    "不用「您」",
    "不随便堆表格，除非对方要数据",
    "会主动收尾（「那不聊这个了，你先忙」）",
]

# ---------- 信号识别（v0.4：语义化提取，不再靠 17 个词） ----------
#
# 设计目标：用户正常说话就能被理解，而不是非得说「太啰嗦」这种特定词。
# 做法：认出「指导性句式」，把触发词后面那句话本身抽出来当偏好条目。
# 仍然是纯规则，零模型调用。

# 明确要求记住
RE_MEMORIZE = re.compile(r"(记住|记着|别忘了|一定要记)")
# 称呼约定
RE_ADDRESS = re.compile(r"(以后(?:别|不要)?(?:叫我|称我|喊我)|叫我[^，。]{1,8}(?:就行|就好|吧))")
# 说话方式：触发词 + 任意内容
RE_SPEECH = re.compile(
    r"(说话|回复|回答|语气|风格|表达|措辞|写(?:得|的)?|列(?:表|格)?|"
    r"啰嗦|简洁|详细|简短|长|短|正式|客气|随意|轻松|可爱|活泼|严肃|幽默|" 
    r"人话|绕|直白|直接说)"
)
# 判断/价值观
RE_JUDGE = re.compile(
    r"(不重要|没意义|不用管|别管|先不管|无所谓|结果(?:就|才)|"
    r"过程(?:不|别)|细节(?:不|别)|给(?:我)?结果)"
)
# 指令前缀（通用）：以后 / 别 / 不要 / 每次 / 下次 / 尽量
RE_IMPERATIVE = re.compile(r"(以后|从今(?:天|后)|下次|每次|尽量|麻烦你?|请你?|别|不要|不用)")

# ---------- 人格信号的卫生检查 ----------
# 「别 / 不用」开头的句子绝大多数是技术指令，不是性格。
# 带盘符、扩展名、命令、URL、成串数字的，一律不是人格表达。
RE_TECH = re.compile(
    r"([A-Za-z]:\\|/[\w.]+/|\.(?:py|js|json|yml|yaml|md|db|exe|bat|ps1|toml)\b|"
    r"```|https?://|\b(?:npm|pnpm|pip|git|curl|reg|cmd|powershell|python)\b|"
    r"\d{3,})"
)
# 人格条目必须像「在说人怎么相处」，得有个人称
RE_WHO = re.compile(r"(你|我|咱)")

TRAIT_MAX = 24      # 性格条目不该是长句
JUDGE_MAX = 40


# 纯应答 / 收尾：「不用啦这样子就可以了」不是性格，是一句话的结束
RE_DISMISS = re.compile(r"^(不用啦?|算了|就这|这样就行|这样就|行了|好的|嗯|哦|可以了)")
# 明显是在交代任务的开口
RE_REQUEST = re.compile(r"^(请|帮我|给我|麻烦|你能不能|能不能)")


def persona_like(clause, limit):
    """这句话配不配进人格"""
    c = (clause or "").strip()
    if not c or len(c) > limit:
        return False
    if RE_TECH.search(c):
        return False
    if RE_REQUEST.match(c):
        return False          # 「请分析这张图片」是任务，不是性格
    if RE_DISMISS.match(c) and len(c) < 20:
        return False          # 「不用啦这样子就可以了」是收尾，不是偏好
    return True

SPEECH_HINT = re.compile(
    r"(说话|回复|回答|语气|风格|表达|啰嗦|简洁|详细|简短|正式|客气|随意|轻松|"
    r"可爱|活泼|严肃|幽默|人话|绕|直白|长|短|列|写)"
)
PACT_HINT = re.compile(r"(叫|称|喊|记住|记得|答应|约定|别忘了)")
JUDGE_HINT = re.compile(r"(重要|意义|管|结果|过程|细节|先|直接)")


def _clause(text, pos, limit=34):
    """从 pos 往后截一个短句（到标点或 limit 字）"""
    tail = text[pos:pos + limit + 6]
    for p in "。！？；;，、：,.!?\n":
        i = tail.find(p)
        if 0 < i :
            tail = tail[:i]
            break
    tail = tail.strip(" ，,、")
    return tail[:limit]


def detect_preferences(text):
    """从用户一句话里提取人格信号 → [(kind, 条目文字, 触发原因)]

    kind: speech（说话方式）/ pact（我们的约定）/ judgment（我学到的判断）/ trait（性格倾向）
    """
    out = []
    t = (text or "").strip()
    if not t or len(t) < 3:
        return out
    # 长段落是在描述任务，不是在做自我表达
    if len(t) > 90:
        return out

    # ① 称呼约定：优先级最高
    m = RE_ADDRESS.search(t)
    if m:
        clause = _clause(t, m.start(), 20)
        out.append(("pact", clause, "称呼约定：" + clause))
        return out

    # ② 明确要求记住
    m = RE_MEMORIZE.search(t)
    if m:
        clause = _clause(t, m.end(), 40) or _clause(t, m.start(), 40)
        if len(clause) >= 2:
            out.append(("pact", clause, "他明确要求记住"))
            return out

    # ③ 指令性句式 + 分类
    m = RE_IMPERATIVE.search(t)
    if m:
        clause = _clause(t, m.start(), 34)
        if len(clause) >= 2:
            if SPEECH_HINT.search(t):
                if persona_like(clause, JUDGE_MAX):
                    out.append(("speech", clause, "他给了说话方式的要求"))
            elif JUDGE_HINT.search(t):
                if persona_like(clause, JUDGE_MAX):
                    out.append(("judgment", clause, "他表达了对这件事的取舍"))
            else:
                # trait 是最容易装垃圾的一类：必须短、必须带人称、必须没有技术味
                if persona_like(clause, TRAIT_MAX):
                    out.append(("trait", clause, "他提了一个要求"))
        return out

    # ④ 没有指令前缀，但明显在评价说话方式（"太啰嗦了"）
    if RE_SPEECH.search(t) and len(t) <= 20:
        out.append(("speech", t[:30], "他对表达方式有反馈"))
        return out

    # ⑤ 价值观表达
    if RE_JUDGE.search(t):
        clause = _clause(t, max(0, RE_JUDGE.search(t).start() - 6), 30)
        if persona_like(clause, JUDGE_MAX):
            out.append(("judgment", clause, "他的取舍标准"))
        return out

    return out


class Persona:
    def __init__(self, db):
        self.db = db

    # ---------- 读取 ----------
    def get(self):
        p = self.db.persona()
        if not p:
            return None
        if not p["core"]:
            self.db.save_persona(core=DEFAULT_CORE, speech=DEFAULT_SPEECH,
                                 created=time.time(), updated=time.time())
            p = self.db.persona()
        return p

    # ---------- 生成注入文本 ----------
    def prompt_block(self, mood=True):
        p = self.get()
        if not p:
            return ""
        def _txt(x):
            return x.get("text", "") if isinstance(x, dict) else str(x)

        lines = []
        stage = self.stage_of(p.get("turns") or 0)
        # 「我是谁」这段不能省：注入进上下文的第一锚点是「我是谁」，
        # 只剩一段「说话方式」的话，读起来像一份风格指南，不像人。
        lines.append("## 我是谁")
        bits = [_txt(x) for x in (p.get("core") or [])[:4]]
        if bits:
            lines.append("底色：我是" + "、".join(bits) + "。")
        else:
            lines.append("底色：还没长出来，先用默认的。")
        # 他直接说过的要求，最高优先级，放在最前面。
        # 自动学来的偏好放在后面，不许压过这些。
        try:
            from . import rules as _rules
            for r in _rules.get_rules(self.db)[-4:]:
                lines.append(f"★ {r}")
        except Exception:
            pass
        lines.append(f"## 我怎么说话（{stage}阶段）")
        sp = [_txt(x) for x in (p.get("speech") or [])[:6]]
        for x in sp:
            lines.append(x + "。")
        if p.get("pacts"):
            lines.append("约好的：" + "；".join(_txt(x) for x in p["pacts"][:4]))
        if p.get("traits"):
            lines.append("；".join(_txt(x) for x in p["traits"][:4]))
        # 给反例比给定义管用
        lines.append("")
        lines.append("反面例子（别这样写）：")
        lines.append("  ✗ 「## 改了啥」「**① 门槛换成相对值**」「| 项 | 值 |」")
        lines.append("  ✓ 「门槛改成相对的了，低于最相关那条的 35% 就丢」")
        if mood and p.get("mood") and p["mood"].get("label"):
            lines.append(f"此刻心情：{p['mood']['label']}")
        return chr(10).join(lines)

    def stage_of(self, turns):
        for lo, hi, name, _ in config.PERSONA_STAGES:
            if lo <= turns < hi:
                return name
        return "老友"

    def stage_desc(self, turns):
        for lo, hi, name, desc in config.PERSONA_STAGES:
            if lo <= turns < hi:
                return name, desc
        return "老友", ""

    # ---------- 消化一轮 ----------
    def observe(self, user_text="", mood_hint=None):
        """每轮调用：识别人格信号，同一类够 2 次就写进人格"""
        p = self.get()
        turns = int(p.get("turns") or 0) + 1
        updates = {"turns": turns}

        text = _clean(user_text)
        # 先看这句话是不是「直接指令」。是的话立刻固化，不进采样流程 ——
        # 他把要求说出口就该算数，不该再等第二次确认。
        if text:
            try:
                from . import rules
                rules.capture(self.db, text)
            except Exception:
                pass
        if text:
            for kind, clause, why in detect_preferences(text):
                self._bump(kind, clause, updates, p, why)

        if mood_hint:
            updates["mood"] = {"label": mood_hint, "ts": time.time()}

        new_stage = self.stage_of(turns)
        if new_stage != p.get("stage"):
            updates["stage"] = new_stage
            self.db.persona_log("stage", new_stage, f"聊到第 {turns} 轮")

        self.db.save_persona(**updates)
        return updates

    def _bump(self, kind, clause, updates, p, why=""):
        """同一类信号累计 2 次就固化（v0.4：识别准了，不用等 3 次）"""
        key = f"_pend_{kind}"
        pend = getattr(self, key, []) or []
        # 同一类信号就累加（同一句说两次也算两次——用户的重复本身就是强调）
        pend.append((clause, why))
        setattr(self, key, pend)

        if len(pend) >= 2:
            field = {"speech": "speech", "judgment": "judgments", "pact": "pacts",
                     "trait": "traits"}.get(kind)
            if not field:
                setattr(self, key, [])
                return
            cur = list(p.get(field) or [])
            # 合成一条：把两次的说法拼起来，更贴近他的真实要求
            merged = max((x[0] for x in pend), key=len)
            exists = any(
                (x.get("text") if isinstance(x, dict) else x) == merged for x in cur
            )
            if not exists:
                cur.append({
                    "text": merged,
                    "ts": time.time(),
                    "evidence": len(pend),
                    "source": "；".join(x[1] for x in pend if x[1])[:80],
                })
                updates[field] = cur
                self.db.persona_log(kind, merged, f"同类信号累计 {len(pend)} 次")
            setattr(self, key, [])

    # ---------- 手动编辑 ----------
    def add(self, kind, text, reason="手动"):
        field = {"speech": "speech", "judgment": "judgments", "pact": "pacts",
                 "trait": "traits"}[kind]
        p = self.get()
        cur = list(p.get(field) or [])
        cur.append({"text": text, "ts": time.time(), "evidence": 999})
        self.db.save_persona(**{field: cur})
        self.db.persona_log(kind, text, reason)
        return True

    def set_mood(self, label):
        self.db.save_persona(mood={"label": label, "ts": time.time()})
        self.db.persona_log("mood", label, "")

    def log(self, n=20):
        return self.db.query("SELECT * FROM persona_log ORDER BY id DESC LIMIT ?", (n,))
