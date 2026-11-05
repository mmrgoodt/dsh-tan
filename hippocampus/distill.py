# -*- coding: utf-8 -*-
"""提炼器：把一轮对话压成「事实 / 偏好 / 教训」三类

为什么要有这一步：
writer.ingest_turn 以前是整段塞库 —— 一段汇报里「我修好了」「已经推送了」
「做了四件事」全进去了，真事反而抢不到注入预算（360 条里 256 条流水账）。

这一步纯规则，不调模型。判据是句式：
  · 过程叙述 —— 完成态、汇报口吻、第一人称「我做了某事」 → 丢
  · 事实     —— 有坐标（路径 / 端口 / 数字 / 版本）或陈述句（我在做 / 改成 / 路径是）→ 留
  · 偏好     —— 以后 / 别 / 不要 / 喜欢 / 记住 / 叫我 …… → 留
  · 教训     —— 坑 / 教训 / 别再 / 千万 / 导致 …… → 留

切句只按句末标点（。！？；换行），不按逗号切：
「我明天还得看下报表，先这样吧」这种一句里混着事实和收尾口气，
按逗号切开会把碎片单独判断，反而把整句拆散。
判断时按小句分类，只要有一个小句属于三类之一，整句原样留下（不改写原话）。
"""
import re

# ---------- 过程叙述：完成态 / 汇报口吻 ----------
_PROCESS = re.compile(
    r"((修好|弄好|搞好|改好|跑通|搞定|弄完|做完|改完|写完|装好|更新完|重建完|整理完)"
    r"|(已经|刚刚|刚才|已)(推送|提交|合并|部署|上线|同步|备份|重启|安装|发出去|发过去|跑完|改完)"
    r"|(推送|提交|合并|部署|上线|跑完|装好)了"
    r"|(做|改|弄|写|修|跑|测|试|看)了(一|两|三|四|五|六|七|八|九|十|\d+)?(件|步|遍|次|处|个)"
    r"|(全部|都)(通过|跑完|跑过|绿了|好了)"
    r"|(第[一二三四五六七八九十\d]+步|接下来|然后我|综上|以上|先说结论|汇报一下|复盘一下))"
)

# ---------- 事实：坐标或陈述句 ----------
_FACT = re.compile(
    r"(\d|[A-Za-z]:[\\/]|https?://|`[^`]+`|"
    r"路径|目录|文件夹|端口|版本|配置|参数|默认值|地址|域名|账号|密码|密钥|token|"
    r"字段|表结构|数据库|接口|函数|模块|插件|脚本|进程|服务|内存|磁盘|日志|"
    r"用的是|跑在|装在|存在|放在|保存在|改成|换成|设成|设到|指向|"
    r"我在|我正在|我是|我的|我们要|现在用|目前用|之前是|其实是|结论|原因是|因为|说明|意味着)",
    re.I,
)

# ---------- 偏好：要求 / 喜好 / 说法 ----------
_PREF = re.compile(
    r"(以后|下次|每次|别再|不要|别用|别写|别搞|别那么|不用|记住|记着|"
    r"我(喜欢|不喜欢|讨厌|习惯|想要|希望|偏好)|"
    r"叫我|称我|喊我|语气|说话|回复|风格|格式|排版|简洁|详细|简短|正式|随意|可爱|啰嗦|"
    r"要求|必须|千万)"
)

# ---------- 教训：踩过的坑 ----------
_LESSON = re.compile(
    r"(坑|教训|别再|下次注意|下次记得|记住|千万|一定|导致|踩过|血的|"
    r"注意|否则|会挂|会崩|会丢|会炸|根因|原因就是)"
)

# ---------- 纯口气词 ----------
_FILLER = re.compile(
    r"^(好的?|好吧|嗯+|哦+|啊+|行|可以|没问题|收到|谢谢|多谢|哈哈+|ok|okay|"
    r"先这样|就这样|没事|算了|在吗|你好|晚安|好的呢)[。.!！?？~\s]*$",
    re.I,
)

_SENT_END = "。！？!?；;\n"
_CLAUSE_SEP = "，,、"


def split_sentences(text):
    """按句末标点切句，标点跟着前一句走（拼回去能一模一样）"""
    out, buf = [], ""
    for ch in text or "":
        buf += ch
        if ch in _SENT_END:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def _clauses(sentence):
    """一句话再切成小句，只用于定性，不用来拼正文"""
    parts, buf = [], ""
    for ch in sentence or "":
        if ch in _CLAUSE_SEP:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        parts.append(buf)
    return [p for p in parts if p.strip()]


def classify(clause):
    """给一个小句定性：fact / preference / lesson / process / None

    None = 既不是三类之一，也不是过程叙述（口气词、问句碎片）
    """
    c = (clause or "").strip(" \t\r\n，,。；;！!？?…")
    if not c:
        return None
    if _FILLER.match(c):
        return None
    # 教训优先：一句里既像偏好又像教训的（「以后别再这么干」），按教训算
    if _LESSON.search(c):
        return "lesson"
    if _PREF.search(c):
        return "preference"
    if _FACT.search(c):
        return "fact"
    if _PROCESS.search(c):
        return "process"
    # 既没坐标也没完成态：短句当口气词丢掉，长句按事实留（宁可多留一条，别丢真事）
    if len(c) < 8:
        return None
    return "fact"


def distill(text, role="user"):
    """提炼一轮文本，返回 {text, kept, dropped, kinds}

    text    —— 提炼后的正文（原话拼接，不改写；全被丢掉时为空串）
    kept    —— 留下的小句列表
    dropped —— 丢掉的小句列表（过程叙述 / 口气词），便于自测和调试
    kinds   —— {fact: n, preference: n, lesson: n}
    """
    raw = (text or "").strip()
    res = {"text": raw, "kept": [], "dropped": [], "kinds": {}}
    if not raw:
        return res

    kept_sent, dropped = [], []
    for sent in split_sentences(raw):
        kinds = [k for k in (classify(c) for c in _clauses(sent)) if k]
        real = [k for k in kinds if k != "process"]
        if real:
            kept_sent.append(sent)
            for k in set(real):
                res["kinds"][k] = res["kinds"].get(k, 0) + 1
        else:
            dropped.append(sent.strip())

    res["kept"] = kept_sent
    res["dropped"] = [d for d in dropped if d]
    if res["dropped"]:
        # 有东西被丢掉时才重拼，避免把没丢的原话改了一个标点
        res["text"] = "".join(kept_sent).strip().rstrip("，,；;、 \n")
    return res


def is_process_narrative(text):
    """整段都是过程叙述（没有事实 / 偏好 / 教训）"""
    t = (text or "").strip()
    if not t:
        return False
    kinds = [k for k in (classify(c) for c in _clauses(t)) if k]
    if not kinds:
        return True
    return all(k == "process" for k in kinds)


# ---------- 冲突判定：主题词重合 + 结论变了 ----------
_NUM = re.compile(r"\b\d{2,}(?:\.\d+)*\b")
_PATH = re.compile(r"[A-Za-z]:[\\/][^\s，。；,;、）)]+|(?<![\w])/(?:[\w.\-]+/)+[\w.\-]+")
_URL = re.compile(r"https?://[^\s，。；,;、）)]+")
_NEG = re.compile(r"(别|不要|不用|不再|停止|取消|去掉|删掉)")


def topic_tokens(text):
    """主题词：用检索那套分词，保证和召回路子一致（中英混排 + 停用词）"""
    try:
        from .retriever import tokenize

        return set(tokenize(text or ""))
    except Exception:
        t = (text or "").lower()
        return set(re.findall(r"[a-z]{3,}", t)) | set(re.findall(r"[\u4e00-\u9fa5]{2}", t))


def value_tokens(text):
    """取值：路径 / URL / 数字。结论「变没变」看的就是这些"""
    t = text or ""
    return (
        set(m.lower() for m in _PATH.findall(t))
        | set(m.lower() for m in _URL.findall(t))
        | set(_NUM.findall(t))
    )


def conflict(old_text, new_text, old_layer=None, new_layer=None):
    """新记忆是否覆盖旧记忆的结论（路径 A → B、端口 8080 → 8090）

    保守三条件：
      1. 主题词重合 ≥2，且重合占较少一侧的一半以上（同一件事，不是路过）
      2. 两边都带取值，且取值不同 —— 这是「改了」而不是「又说了一遍」
      3. 或者：同一层里一边明确否定、另一边没否定 —— 算结论相反
    只是换个说法的（取值相同）不算冲突。
    """
    old_text = old_text or ""
    new_text = new_text or ""
    ot, nt = topic_tokens(old_text), topic_tokens(new_text)
    if len(ot) < 2 or len(nt) < 2:
        return False
    inter = ot & nt
    if len(inter) < 2:
        return False
    if len(inter) < 0.5 * min(len(ot), len(nt)):
        return False

    ov, nv = value_tokens(old_text), value_tokens(new_text)
    if ov and nv and ov != nv:
        return True

    if old_layer and new_layer and old_layer == new_layer:
        if bool(_NEG.search(old_text)) != bool(_NEG.search(new_text)):
            return True
    return False
