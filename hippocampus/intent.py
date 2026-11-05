# -*- coding: utf-8 -*-
"""问句意图：纯规则判「这句话问的是哪一类事」

问题出在哪
----------
记忆里存的是「具体事」——「别老给我列表格」「git 用的是这个邮箱」；
人问的是「某类事」——「什么事我让你别再做」「我的代码提交用什么身份」。
两句话字面上一个实词都不重合，2-gram 索引天然接不上。
这是词面检索的天花板，不是参数没调好，加同义词也救不了：
「别再做」和「别老给我列表格」之间没有可以查表的词对应关系。

所以先判意图，再换一条检索路径：
  · 问「别再做」      → 去 L3 / L2- 层、正文带禁令词的那批里捞
  · 问「做到哪一步」  → 去最近 7 天的 L1 / L2 里捞带进度词的
  · 问「上次为什么不满意」 → 捞 valence 负的、带「不满意 / 推翻 / 换掉」的
  · 问「用什么身份 / 在哪」 → 捞带路径、域名、email、user.name、引号值的
  · 问「都有哪些」    → 放宽条数，不只取最高分那几条

判据只有正则和关键词：一毫秒量级，不调模型。
而且只在问句（外加明确的禁令句）上生效 —— 「改」「弄吧」这种短命令、
普通陈述句一律返回 None，照旧走词面检索，不污染原有排序。

这个模块只做判定，不碰数据库：筛记忆由 retriever 拿着这里给的规则去跑，
判定逻辑才好单测。
"""
import re
import time

# ---------------- 意图种类 ----------------
KIND_BAN = "ban"             # 否定 / 禁令：我让你别再做的那类
KIND_PROGRESS = "progress"   # 进度：做到哪一步、下一步
KIND_REVIEW = "review"       # 回顾 / 不满：上次为什么不行
KIND_CONFIG = "config"       # 配置 / 身份：用什么身份、配在哪
KIND_LIST = "list"           # 清单：都有哪些、几个
KIND_ROUTINE = "routine"     # 例行：定期要做的事

ALL_KINDS = (KIND_BAN, KIND_PROGRESS, KIND_REVIEW, KIND_CONFIG, KIND_LIST, KIND_ROUTINE)

# 中文里「别」作实词的时候很多（识别 / 特别 / 区别 / 分别 / 性别 / 告别 / 个别），
# 前置断言把它们排除掉，不然「识别准确率」也会被当成禁令。
_BAN_CHAR = r"(?<![识特区分类性告个])别"

# ---------------- 查询侧：怎么认出这是哪一类问题 ----------------
# 问句标记。一个都没有、又不是明确的禁令句，就当陈述 / 短命令，走原来的路。
_QUESTION = re.compile(
    r"[?？]|什么|啥|哪|怎么|怎样|为什么|为啥|如何|多少|几个|几点|是否|是不是|"
    r"有没有|吗|呢|谁|干什么|做什么"
)

_Q_BAN = re.compile(
    _BAN_CHAR + r"(?:再|老|总|用|写|搞|那么|给|做)|不要|不用再|不许|不准|禁止|"
    r"以后(?:别|不要|不用)|我让你别|让你别|别再"
)

_Q_PROGRESS = re.compile(
    r"做到哪|到哪一?步|到哪了|进度|下一步|现在到|到什么程度|做到什么|"
    r"卡在哪|还差|做完没|做完了"
)

_Q_REVIEW = re.compile(
    r"不满意|不认可|不买账|后悔|推翻|被否|换掉|重做|重新选|"
    r"(?:上次|上回|之前|刚才|昨天).{0,10}(?:为什么|为啥|怎么|哪|不满|不行|不对|没用)"
)

_Q_CONFIG = re.compile(
    r"用什么身份|什么身份|什么邮箱|怎么配|怎么设置|怎么设|在哪|哪里|哪儿|"
    r"地址|路径|目录|文件夹|端口|身份|配置|账号|密钥|密码|邮箱|域名|"
    r"user\.name|email|git"
)

_Q_LIST = re.compile(r"都|哪些|有什么|有啥|几个|清单|列一下|所有|全部|依次|从头")

_Q_ROUTINE = re.compile(r"定期|每周|每天|每月|周期|例行|习惯|定时|频次")

# ---------------- 记忆侧：这一类长什么样 ----------------
_PAT_BAN = re.compile(
    _BAN_CHAR + r"(?:再|老|总|用|写|搞|那么|给|做)?|不要|不用再|不许|不准|禁止|以后"
)
_PAT_PROGRESS = re.compile(
    r"完成|改成|换成|迁移|进度|下一步|pending|阶段|上线|发布|进行中|待办|todo|"
    r"已落地|还没|收尾|待定"
)
_PAT_REVIEW = re.compile(r"不满意|推翻|换掉|重做|回滚|改回|踩坑|坑|否决|不认可|退掉")
# 配置 / 身份型：特征不是某个词，而是「一个具体的取值」。
#   · 强证据：盘符路径、URL、email、user.name、域名 —— 有就是有
#   · 弱证据：反引号 / 引号包起来的值、密钥/token/端口这类字眼 —— 有也说明不了什么
#     （「`patch: entry "xxx" not found`」这种日志也带引号，但跟配置无关）
# 正则的路子和 distill.value_tokens 一样（那边判断「结论变没变」，
# 这边判断「这条里有没有实打实的取值」），按需复制过来，不改 distill。
_PAT_CONFIG_STRONG = re.compile(
    r"[A-Za-z]:[\\/]|https?://|[\w.+-]+@[\w-]+\.[A-Za-z]{2,}|user\.name|"
    r"(?:\b[a-z0-9-]+\.)+(?:com|cn|net|org|io|dev|top|xyz|local)\b"
)
_PAT_CONFIG_WEAK = re.compile(
    r"[`\"“”'][^`\"“”'\n]{2,60}[`\"“”']|密钥|token|端口|配置文件|环境变量"
)
_PAT_ROUTINE = re.compile(r"定期|每周|每天|每月|周期|例行|习惯|定时|提醒|日程")

_PATTERNS = {
    KIND_BAN: _PAT_BAN,
    KIND_PROGRESS: _PAT_PROGRESS,
    KIND_REVIEW: _PAT_REVIEW,
    KIND_ROUTINE: _PAT_ROUTINE,
}

# 层偏好：问「别再做」最该看的是教训层（L2-）和方法层（L3）；
# 问进度最该看的是最近的 L1 / L2。层对不上不是不能进，只是权重低一点。
_LAYER_BONUS = {
    KIND_BAN: {"L2-": 0.35, "L3": 0.30, "L4": 0.10},
    KIND_PROGRESS: {"L1": 0.25, "L2": 0.20},
    KIND_REVIEW: {"L2-": 0.20, "L1": 0.10},
    KIND_CONFIG: {"L3": 0.15, "L4": 0.10},
    KIND_ROUTINE: {"L3": 0.15, "L2": 0.10},
}

# 每类在补充池里最多几条（retriever 还会按自己的上限再压一次）
MAX_PER_KIND = {
    KIND_BAN: 4,
    KIND_PROGRESS: 4,
    KIND_REVIEW: 4,
    KIND_CONFIG: 3,
    KIND_ROUTINE: 4,
}

PROGRESS_MAX_AGE_DAYS = 7.0     # 进度型只捞最近一周的，更早的「完成」是历史不是现状


class Intent:
    """一次判定的结果。kinds 为空就是没命中，走原来的检索路径"""

    __slots__ = ("query", "kinds", "is_question", "widen")

    def __init__(self, query, kinds, is_question, widen=0):
        self.query = query
        self.kinds = tuple(kinds)
        self.is_question = is_question
        self.widen = int(widen or 0)

    def __bool__(self):
        return bool(self.kinds)

    def __repr__(self):
        return "Intent(%r, kinds=%s, q=%s, widen=%d)" % (
            self.query[:20], ",".join(self.kinds), self.is_question, self.widen)


def classify(query, list_extra=6):
    """判意图。没命中返回 None —— 这一步必须是便宜的，所以只有正则"""
    q = (query or "").strip()
    if not q:
        return None
    is_q = bool(_QUESTION.search(q))

    kinds = []
    # 禁令句本身就是信号，不必非得是问句：「以后别用表格」也是在讲同一类事
    if _Q_BAN.search(q):
        kinds.append(KIND_BAN)
    # 其余四类要求是问句，否则「改」「弄吧」「进度」这种短命令也会被当成提问
    if is_q:
        if _Q_PROGRESS.search(q):
            kinds.append(KIND_PROGRESS)
        if _Q_REVIEW.search(q):
            kinds.append(KIND_REVIEW)
        if _Q_CONFIG.search(q):
            kinds.append(KIND_CONFIG)
        if _Q_LIST.search(q):
            kinds.append(KIND_LIST)
        if _Q_ROUTINE.search(q):
            kinds.append(KIND_ROUTINE)

    if not kinds:
        return None
    widen = int(list_extra) if KIND_LIST in kinds else 0
    return Intent(q, kinds, is_q, widen)


def score(kind, text_low, layer="", age_days=0.0, valence=0.0):
    """这条记忆有多像「kind 这一类」，0~1；0 表示不进补充池

    text_low 必须是已经小写过的正文（retriever 里有缓存，别在这里再 lower 一次）。
    """
    if not text_low:
        return 0.0
    if kind != KIND_CONFIG and kind not in _PATTERNS:
        return 0.0

    if kind == KIND_PROGRESS:
        # 超过一周的「完成 / 迁移」是旧账，问进度要的是现状
        if age_days > PROGRESS_MAX_AGE_DAYS:
            return 0.0

    if kind == KIND_CONFIG:
        # 强证据和弱证据分开算：路径 / URL / email / user.name 一条顶一条，
        # 引号里的东西和「端口」这种字眼只算三成 —— 日志里满是引号，那不叫配置。
        strong = len(set(_PAT_CONFIG_STRONG.findall(text_low)))
        weak = len(_PAT_CONFIG_WEAK.findall(text_low))
        v = min(strong, 2) * 1.0 + min(weak, 3) * 0.3
        if v < 0.6:
            return 0.0
        base = 0.42 + 0.16 * min(v, 3.0)
    else:
        pat = _PATTERNS[kind]
        hits = len(pat.findall(text_low))
        if kind == KIND_BAN:
            # 禁令型：行里没有禁令词，那只有教训层 / 方法层还算数
            if hits == 0 and layer not in ("L2-", "L3"):
                return 0.0
            base = 0.45 + 0.15 * min(hits, 4)
        elif kind == KIND_PROGRESS:
            if hits == 0:
                return 0.0
            base = 0.45 + 0.12 * min(hits, 4)
            # 越新越像「现在的事」
            base *= 1.0 - 0.45 * min(1.0, max(0.0, age_days) / PROGRESS_MAX_AGE_DAYS)
        elif kind == KIND_REVIEW:
            if hits == 0 and valence >= -0.05:
                return 0.0
            base = 0.40 + 0.12 * min(hits, 4)
        else:
            if hits == 0:
                return 0.0
            base = 0.45 + 0.12 * min(hits, 4)

    w = base + _LAYER_BONUS.get(kind, {}).get(layer or "", 0.0)
    # 回顾 / 不满型：情绪是负的，多半就是那条「不满意」的记忆
    if kind == KIND_REVIEW and valence < -0.3:
        w += 0.20
    elif kind == KIND_REVIEW and valence < -0.05:
        w += 0.10
    return max(0.0, min(1.0, w))


def rank(kind, items, max_n=None, now=None, terms=()):
    """在候选池里挑出「这一类」的前几条

    items: [(mem, text_low)]，text_low 由调用方缓存好，这里不再重复 lower。
    terms: 这次查询的词，用来做同分时的「同题」优先 ——
           「代码提交用什么身份」里，讲到 git 交身份的那条本来就该比重提一句
           `"xxx" not found` 的那条靠前。
    返回 [(权重, mem)]，按权重降序；权重相同看和问句的实词重合，再看谁更新。
    """
    if kind != KIND_CONFIG and kind not in _PATTERNS:
        return []
    max_n = int(max_n or MAX_PER_KIND.get(kind, 4))
    now = now or time.time()
    tl = [t for t in (terms or ()) if t]

    out = []
    for mem, low in items:
        if not low:
            continue
        created = mem.get("created") or 0.0
        age = max(0.0, (now - created) / 86400.0) if created else 999.0
        w = score(kind, low,
                  layer=mem.get("layer") or "",
                  age_days=age,
                  valence=float(mem.get("valence") or 0.0))
        if w > 0:
            overlap = 0
            for t in tl:
                if t in low:
                    overlap += 1
            out.append((w, overlap, created, mem))
    if not out:
        return []
    out.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return [(w, m) for w, _o, _c, m in out[:max_n]]


def phrase_hits(query, items):
    """原话命中：有没有哪条记忆里原样记着这句问话

    这一条是给「问句被存下来过」的情况留的。问句往往很长，被一条长记忆摊薄
    之后 focus 极低，按词面分数永远排不进前几名，可它恰恰就是答案本身。
    只做整句包含判断（去标点），不做模糊匹配 —— 便宜，也不会乱认。
    """
    q = (query or "").strip()
    if not q:
        return []
    norm = re.sub(r"[\s，。！？、；：「」『』（）()\[\]\"'`~!?,.;:_—-]+", "", q.lower())
    if len(norm) < 6:
        return []
    cands = {q.lower(), norm}
    out = []
    for mem, low in items:
        if not low:
            continue
        for c in cands:
            if c and c in low:
                out.append((1.0, mem))
                break
    out.sort(key=lambda x: -(x[1].get("created") or 0.0))
    return out[:3]


def merge_pools(pools):
    """多类意图的补充结果合并：同一条记忆只留权重最高的那次

    pools: [[(权重, mem), ...], ...]
    """
    best = {}
    order = []
    for pool in pools:
        for w, m in pool:
            mid = m.get("id")
            if mid is None:
                continue
            if mid not in best:
                best[mid] = (w, m)
                order.append(mid)
            elif w > best[mid][0]:
                best[mid] = (w, m)
    out = [best[mid] for mid in order]
    out.sort(key=lambda x: -x[0])
    return out
