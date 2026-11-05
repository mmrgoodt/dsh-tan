# -*- coding: utf-8 -*-
"""用左右邻熵把碎片从概念里剔出去

问题：`鲜果通项目` 被 2-gram 切成 `鲜果|果通|通项|项目`，
于是图上出现了「通项」「果通」「个插」「忆插」这种看不懂的标签。
`鲜果`（他的核心项目）连着的全是对面三个字的碎片，这个概念等于废了。

判断依据是「邻字熵」：
    真词 `记忆`  → 右边可以是 里/系统/库/了/的…… 分布宽，熵高
    碎片 `了一`  → 右边几乎总是接着同一个名词，熵低

这是新词发现里的标准做法。熵低于阈值的当碎片，不建突触。
"""
import collections
import math
import re
import sqlite3
import sys

DB = r"C:\Users\admin\.dsh\tan\memory.db"

# 2-gram 只在中文里切
NON_CJK = re.compile(r"[^\u4e00-\u9fff]")


def entropy(counter):
    n = sum(counter.values())
    if n <= 1:
        return 0.0
    return -sum((c / n) * math.log(c / n, 2) for c in counter.values() if c > 0)


def analyze(texts, min_freq=2):
    """返回 {gram: (left_ent, right_ent, freq)}"""
    left = collections.defaultdict(collections.Counter)
    right = collections.defaultdict(collections.Counter)
    freq = collections.Counter()

    for t in texts:
        if not t:
            continue
        s = NON_CJK.sub("〇", t)      # 非中文换成占位符，保持位置连续（写反过一次）
        L = len(s)
        for i in range(L - 1):
            g = s[i:i + 2]
            if "〇" in g:
                continue
            freq[g] += 1
            if i > 0:
                left[g][s[i - 1]] += 1
            if i + 2 < L:
                right[g][s[i + 2]] += 1

    out = {}
    for g, f in freq.items():
        if f < min_freq:
            continue
        out[g] = (entropy(left[g]), entropy(right[g]), f)
    return out


def main():
    apply = "--apply" in sys.argv
    con = sqlite3.connect(DB)
    texts = [r[0] or "" for r in con.execute(
        "select coalesce(summary,'') || ' ' || coalesce(content,'') from memories where status='active'")]
    print("扫了 %d 条记忆" % len(texts))

    stats = analyze(texts, min_freq=2)
    print("出现的 2-gram: %d" % len(stats))

    # 阈值：任一侧熵太低就是碎片。1.2 是试出来的 ——
    # 真词两侧基本都在 1.5 以上，碎片总有一侧贴近 0。
    # 判据一：左熵低 → 它是别人尾巴上的碎片。
    #   果通（总跟在「鲜」后面）、开鲜（总在「先」后面）、忆插（总在「记」后面）
    # 判据二：虚词黑名单 → 高频但没意义。
    #
    # 特意**不**用「右熵低」判碎片：鲜果 右边永远是「通」（鲜果通项目），右熵为 0，
    # 但它明明是他的核心项目。专有名词天然就是固定搭配，右熵在这里不是好指标。
    TH = 0.6
    FUNC = set("了的在是我你他她它这那就都也还很更最把被给和与或但因为所以"
               "了一这那地得着过吗呢吧啊呀哦嗯个件条次些么什怎样多少")
    frags = set()
    for g, (l, r, f) in stats.items():
        if l < TH:                       # 别人尾巴
            frags.add(g)
        elif len(g) == 2 and (g[0] in FUNC or g[1] in FUNC) and f < 40:
            frags.add(g)                 # 虚词拼出来的
    # 白名单：他自己的核心的东西，宁可留着也不删
    PROTECT = {"鲜果", "先开", "项目", "插件", "记忆", "密码", "账号", "小程序",
               "公司", "个体户", "检索", "写入", "备份", "图谱", "概念", "突触"}
    frags -= PROTECT
    print("判为碎片: %d" % len(frags))

    sample = ["通项", "果通", "开鲜", "个插", "忆插", "了一", "这一", "记忆", "插件", "鲜果"]
    print("\n抽样看判得准不准：")
    for g in sample:
        if g in stats:
            l, r, f = stats[g]
            verdict = "碎片" if g in frags else "真词"
            print("  %-4s 左熵%.2f 右熵%.2f 频%d → %s" % (g, l, r, f, verdict))
        else:
            print("  %-4s 没出现" % g)

    if not apply:
        print("\n（只看不写。加 --apply 才清理）")
        return

    names = [r[0] for r in con.execute("select name from concepts")]
    drop = [n for n in names if n in frags]
    print("\n库里概念 %d 个，其中碎片 %d 个" % (len(names), len(drop)))

    if drop:
        qs = ",".join("?" * len(drop))
        con.execute("delete from synapses where a in (%s) or b in (%s)" % (qs, qs), drop + drop)
        con.execute("delete from concepts where name in (%s)" % qs, drop)
        con.commit()
    left_n = con.execute("select count(*) from concepts").fetchone()[0]
    left_e = con.execute("select count(*) from synapses").fetchone()[0]
    print("清完：概念 %d 个，突触 %d 条" % (left_n, left_e))

    print("\n鲜果 现在连着的：")
    for a, b, w in con.execute(
            "select a,b,weight from synapses where a='鲜果' or b='鲜果' order by weight desc limit 10"):
        print("   %-10s %.2f" % (b if a == "鲜果" else a, w))


if __name__ == "__main__":
    main()
