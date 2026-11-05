# -*- coding: utf-8 -*-
"""两处修：人格别乱学 + 身份/教训层保底注入

① 人格：RE_IMPERATIVE 只认「别 / 不要 / 不用 / 请你」这几个词，
   于是「不用做这个我需要你能更改设备的燃烧时间」整句被当成性格，
   「请分析这张图片」被当成价值观。人格块每一轮都注入，错一次错每一轮。

② 召回：L4 身份 / L2- 教训只有 2 条，query 相关度算出来接近 0，
   在 20 条候选里排不到前面就被切掉 —— 所以「我最看重什么」永远召不到。
   给这两层做保底：不管 query 是什么，都进候选。
"""
import io
import json
import sys


def sub1(path, old, new, tag):
    s = io.open(path, encoding="utf-8").read()
    if old not in s:
        raise SystemExit("MISS: " + tag)
    if s.count(old) != 1:
        raise SystemExit("AMBIGUOUS(%d): %s" % (s.count(old), tag))
    io.open(path, "w", encoding="utf-8", newline="\n").write(s.replace(old, new, 1))


# ==================== ① persona.py ====================
P = "hippocampus/persona.py"

sub1(
    P,
    '''# 指令前缀（通用）：以后 / 别 / 不要 / 每次 / 下次 / 尽量
RE_IMPERATIVE = re.compile(r"(以后|从今(?:天|后)|下次|每次|尽量|麻烦你?|请你?|别|不要|不用)")''',
    '''# 指令前缀（通用）：以后 / 别 / 不要 / 每次 / 下次 / 尽量
RE_IMPERATIVE = re.compile(r"(以后|从今(?:天|后)|下次|每次|尽量|麻烦你?|请你?|别|不要|不用)")

# ---------- 人格信号的卫生检查 ----------
# 「别 / 不用」开头的句子绝大多数是技术指令，不是性格。
# 带盘符、扩展名、命令、URL、成串数字的，一律不是人格表达。
RE_TECH = re.compile(
    r"([A-Za-z]:\\\\|/[\w.]+/|\\.(?:py|js|json|yml|yaml|md|db|exe|bat|ps1|toml)\\b|"
    r"```|https?://|\\b(?:npm|pnpm|pip|git|curl|reg|cmd|powershell|python)\\b|"
    r"\\d{3,})"
)
# 人格条目必须像「在说人怎么相处」，得有个人称
RE_WHO = re.compile(r"(你|我|咱)")

TRAIT_MAX = 24      # 性格条目不该是长句
JUDGE_MAX = 40


def persona_like(clause, limit):
    """这句话配不配进人格"""
    c = (clause or "").strip()
    if not c or len(c) > limit:
        return False
    if RE_TECH.search(c):
        return False
    return bool(RE_WHO.search(c))''',
    "persona/hygiene",
)

sub1(
    P,
    '''    out = []
    t = (text or "").strip()
    if not t or len(t) < 3:
        return out''',
    '''    out = []
    t = (text or "").strip()
    if not t or len(t) < 3:
        return out
    # 长段落是在描述任务，不是在做自我表达
    if len(t) > 90:
        return out''',
    "persona/length_gate",
)

sub1(
    P,
    '''            if SPEECH_HINT.search(t):
                out.append(("speech", clause, "他给了说话方式的要求"))
            elif JUDGE_HINT.search(t):
                out.append(("judgment", clause, "他表达了对这件事的取舍"))
            else:
                out.append(("trait", clause, "他提了一个要求"))''',
    '''            if SPEECH_HINT.search(t):
                if persona_like(clause, JUDGE_MAX):
                    out.append(("speech", clause, "他给了说话方式的要求"))
            elif JUDGE_HINT.search(t):
                if persona_like(clause, JUDGE_MAX):
                    out.append(("judgment", clause, "他表达了对这件事的取舍"))
            else:
                # trait 是最容易装垃圾的一类：必须短、必须带人称、必须没有技术味
                if persona_like(clause, TRAIT_MAX):
                    out.append(("trait", clause, "他提了一个要求"))''',
    "persona/trait_gate",
)

sub1(
    P,
    '''        clause = _clause(t, max(0, RE_JUDGE.search(t).start() - 6), 30)
        out.append(("judgment", clause or t[:30], "他的取舍标准"))''',
    '''        clause = _clause(t, max(0, RE_JUDGE.search(t).start() - 6), 30)
        if persona_like(clause, JUDGE_MAX):
            out.append(("judgment", clause, "他的取舍标准"))''',
    "persona/judge_gate",
)

# ==================== ② retriever.py 保底 ====================
R = "hippocampus/retriever.py"

sub1(
    R,
    '''        # 原词是否直接命中：扩展召回的条目不能和真命中平起平坐，''',
    '''        # 身份层 / 教训层保底：库里这两类一共就几条，
        # 但「我最看重什么」「什么事你让我别再做」这类问题全靠它们。
        # 它们跟 query 的字面重合通常是 0，按分数排必然被切掉，所以单独捞。
        anchors = [m for m in mems if m.get("layer") in ("L4", "L5", "L2-")]
        anchor_ids = set(id(m) for m in anchors)

        # 原词是否直接命中：扩展召回的条目不能和真命中平起平坐，''',
    "retriever/anchor_decl",
)

sub1(
    R,
    '''        if term_w is not None:
            scored.sort(key=lambda x: (0 if _base_hit(x[1]) else 1, -x[0]))
            strong = [m for _, m in scored if _base_hit(m)]
            weak = [m for _, m in scored if not _base_hit(m)]
            # 弱相关最多放 strong 的等量条数（且不少于 2 条），留个兜底但不喧宾夺主
            picked = (strong + weak[:max(2, len(strong))])[:limit]
        else:
            scored.sort(key=lambda x: -x[0])
            picked = [m for _, m in scored[:limit]]''',
    '''        if term_w is not None:
            scored.sort(key=lambda x: (0 if _base_hit(x[1]) else 1, -x[0]))
            strong = [m for _, m in scored if _base_hit(m)]
            weak = [m for _, m in scored if not _base_hit(m)]
            # 弱相关最多放 strong 的等量条数（且不少于 2 条），留个兜底但不喧宾夺主
            picked = (strong + weak[:max(2, len(strong))])[:limit]
        else:
            scored.sort(key=lambda x: -x[0])
            picked = [m for _, m in scored[:limit]]

        # 保底条目放在最前面：身份和教训先于"这条问句碰巧命中了什么"
        if anchors:
            picked = anchors + [m for m in picked if id(m) not in anchor_ids]
        picked = picked[:limit + len(anchors)]''',
    "retriever/anchor_merge",
)

print("OK: persona hygiene + layer anchors")
