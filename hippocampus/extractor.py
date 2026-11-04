# -*- coding: utf-8 -*-
"""从对话里提取记忆（纯规则，不用模型）"""
import re

from . import config

# ---------- 信号词 ----------
PROMISE_RE = re.compile(r"(我(明天|后天|回头|晚点|等会儿?|一会)[^。，,；;]{0,20}(给你|帮你|去|弄|看|查|问|确认))|"
                        r"(我(答应|保证))")
PAUSE_RE = re.compile(r"(先这样|等有空|回头再说|改天|先放一放|先不弄|暂缓|以后再说)")
FAILED_RE = re.compile(r"(还是不行|又失败|没成功|报错了|搞不定|弄不了|起不来|打不开|连不上)")
TODO_RE = re.compile(r"(要(改|加|做|弄|修|换)|得(改|加|做|弄)|需要(改|加|做)|准备(改|加|做)|记得(改|加|做))")
ISSUE_RE = re.compile(r"(这里(有|存在)?(问题|漏洞|bug)|有个(问题|漏洞)|先不管|暂时不管|以后再说)")

# ---------- 事实/偏好信号 ----------
FACT_RE = re.compile(r"(是|叫|在|有|用了|装了|改成|变成|地址|电话|账号|密码|编号|版本|型号|路径)")
PREF_RE = re.compile(r"(我(喜欢|不喜欢|讨厌|习惯|想要|希望|觉得|认为)|以后(别|要|不用)|下次(别|要))")
DECISION_RE = re.compile(r"(就(这么|这样|用)|决定|定了|确定用|改成|换成|统一用)")
LESSON_RE = re.compile(r"(原来|其实|教训|注意|记住|坑|以后(记得|别)|下次(记得|别))")
CRED_RE = re.compile(r"(密码|密钥|key|token|账号|地址|电话|身份证|信用代码|AppID|Secret)", re.I)

# ---------- 情绪 ----------
EMO_STRONG_NEG = ("太累了", "烦", "崩溃", "气死", "受不了", "难受", "无语", "算了", "不想")
EMO_STRONG_POS = ("太好了", "哈哈", "舒服", "爽", "搞定", "成了", "厉害", "漂亮", "完美")


def _clean(text: str) -> str:
    return (text or "").strip()


MAX_CONTENT = 600
MIN_CONTENT = 3


def looks_like_junk(text):
    """无信息量的垃圾：重复字符刷屏、纯符号、太短"""
    t = (text or "").strip()
    if len(t) < MIN_CONTENT:
        return True
    core = "".join(t.split())
    # 不同字符太少 → 重复刷屏（"啊啊啊啊啊啊" 6 个字只有 1 种字符）
    if core and len(set(core)) <= max(2, len(core) // 8):
        return True
    if not re.search(r"[一-龥A-Za-z0-9]", t):
        return True
    return False


def clip_content(text, limit=MAX_CONTENT):
    """过长截断，尽量断在句号/换行处（结果保证 <= limit）"""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    # 给省略号留一个位
    cut = t[:max(1, limit - 1)]
    for mark in ("。", "！", "？", chr(10), "；", ". ", "; "):
        i = cut.rfind(mark)
        if i > limit * 0.5:
            head = cut[:i + len(mark)].rstrip()
            return (head[:max(1, limit - 1)]) + "…"
    return cut.rstrip()[:max(1, limit - 1)] + "…"


def detect_noise(text: str) -> bool:
    """判断是不是没营养的闲聊"""
    t = _clean(text)
    if len(t) <= 4:
        return True
    if re.fullmatch(r"[嗯哦啊哈好的行对是嗯嗯哦哦okOK，。！？\s]+", t):
        return True
    if len(t) < 10 and not any(k in t for k in ("？", "?", "改", "加", "弄", "查")):
        return True
    return False


def valence_of(text: str) -> float:
    t = text or ""
    neg = sum(1 for w in EMO_STRONG_NEG if w in t)
    pos = sum(1 for w in EMO_STRONG_POS if w in t)
    if neg and not pos:
        return -min(1.0, 0.4 + 0.2 * neg)
    if pos and not neg:
        return min(1.0, 0.4 + 0.2 * pos)
    return 0.0


def emotion_of(text: str) -> float:
    v = abs(valence_of(text))
    if v >= 0.8:
        return 1.8
    if v >= 0.4:
        return 1.4
    return 1.0


def extract_entities(text: str, known=None) -> list:
    """抽取实体：大写词、路径、域名、引号内、已知实体"""
    t = text or ""
    ents = set()
    # 已知实体命中
    for e in (known or []):
        if e and e in t:
            ents.add(e)
    # 路径
    for m in re.findall(r"[A-Za-z]:\\[^\s，,。；;）)]+", t):
        ents.add(m)
    # 域名
    for m in re.findall(r"\b[a-z0-9-]+\.(?:com|cn|net|org|io|dev)\b", t, re.I):
        ents.add(m)
    # 反引号 / 引号内的短词
    for m in re.findall(r"`([^`]{2,24})`", t):
        ents.add(m.strip())
    # 连续英文/数字标识（≥4）
    for m in re.findall(r"\b[A-Za-z][A-Za-z0-9_\-]{3,}\b", t):
        if m.lower() not in ("this", "that", "with", "from", "true", "false", "null", "none"):
            ents.add(m)
    return list(ents)[:20]


def extract(text: str, role: str = "user") -> dict:
    """分析一段话，返回候选记忆描述（不写库）

    返回:
      {
        "keep": bool,            # 是否值得记
        "noise": bool,
        "layer": "L1"/"L2"/...,
        "content": 摘要,
        "loops": [(kind, title)],
        "entities": [...],
        "valence": float, "emotion": float,
        "protected": bool,
      }
    """
    t = _clean(text)
    res = {
        "keep": False, "noise": False, "layer": "L1",
        "content": t, "loops": [], "entities": [],
        "valence": 0.0, "emotion": 1.0, "protected": False,
    }
    if not t:
        return res

    # 垃圾内容直接当噪音处理，不进长期记忆
    if looks_like_junk(t):
        res["noise"] = True
        res["layer"] = "L1"
        res["keep"] = True
        res["content"] = clip_content(t)
        return res

    res["valence"] = valence_of(t)
    res["emotion"] = emotion_of(t)
    res["entities"] = extract_entities(t)

    # 未闭合事项（同一句只取优先级最高的一个）
    PRIORITY = ["promise", "failed", "todo", "known_issue", "paused"]
    found = set()
    for kind, rx in (("promise", PROMISE_RE), ("paused", PAUSE_RE),
                     ("failed", FAILED_RE), ("todo", TODO_RE), ("known_issue", ISSUE_RE)):
        if rx.search(t):
            found.add(kind)
    if found:
        for kind in PRIORITY:
            if kind in found:
                title = re.sub(r"\s+", " ", t)[:40]
                res["loops"].append((kind, title))
                break

    # 噪音
    if detect_noise(t):
        res["noise"] = True
        res["layer"] = "L1"
        res["keep"] = True
        res["content"] = clip_content(t)
        return res

    # 人格约定（"以后别叫我X"）优先级最高，不进反例
    PACT_RE = re.compile(r"(以后(别|不要)(叫我|称我|喊我)|叫我[^，。]{1,8}(就行|吧|就好))")
    if PACT_RE.search(t) and role == "user":
        res["layer"] = "L5"
        res["protected"] = True
        res["fidelity"] = "verbatim"
        res["keep"] = True
        res["content"] = clip_content(t)
        return res

    # 分层判定（优先级：反例 > 程序 > 身份 > 语义 > 情景）
    if LESSON_RE.search(t) and role == "user":
        res["layer"] = "L2-"          # 教训类 → 反例记忆
    elif DECISION_RE.search(t):
        res["layer"] = "L2"
        res["protected"] = True
    elif CRED_RE.search(t):
        res["layer"] = "L2"
        res["protected"] = True
    elif PREF_RE.search(t):
        res["layer"] = "L2"
    elif FACT_RE.search(t) and len(t) > 12:
        res["layer"] = "L2"
    else:
        res["layer"] = "L1"

    res["keep"] = True
    # 保真度：含凭证/路径的逐字保存
    res["fidelity"] = "verbatim" if (CRED_RE.search(t) or "\\" in t) else "summary"
    # 最后一道：正文长度收口
    res["content"] = clip_content(res.get("content") or t)
    return res


def summarize(text: str, limit: int = 60) -> str:
    t = re.sub(r"\s+", " ", _clean(text))
    if len(t) <= limit:
        return t
    # 尽量在标点处截断
    cut = t[:limit]
    for sep in ("。", "；", "，", ",", "."):
        i = cut.rfind(sep)
        if i > limit * 0.5:
            return cut[:i + 1]
    return cut + "…"
