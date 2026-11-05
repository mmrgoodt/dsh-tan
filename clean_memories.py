# -*- coding: utf-8 -*-
"""记忆清洗：把回补进来的"对话水分"下沉为 dormant

回补之后库里从 66 条涨到 285 条，评测命中率反而从 60% 掉到 55% ——
因为一批寒暄、确认、过程播报也一起进来了，挤掉了 top-3 里的真命中。

关键区分：
  · 用户说的话（source=S1）短也是事实。「将迅雷的开机自启关掉」只有 11 个字，
    但它是真实发生过的事，不能当水分。
  · 我自己的回复（source=S3）如果是「好嘞，那就停这儿～」这种，才是水分。

用法: python clean_memories.py [--do]
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hippocampus.db import DB

# 用户侧：纯确认词，没有任何请求内容
CONFIRM_ONLY = set("""1 2 3 好 嗯 哦 行 可以 对 是 不是 没有 在吗 你好 谢谢 收到 ok
? ？ / \\ . 。 … 早 晚安 哈哈 嗯嗯 好的 行吧 可以啊 弄吧 就这样 对呀 是的 没问题
不用啦 不用 算了 知道了 明白 我看看 试试 继续 然后呢 咋了""".split())

# 助手侧：应答开头，后面基本没有可复用的结论
CHAT_HEAD = re.compile(
    r"^(好嘞|好的|好呀|收到|明白|了解|嗯|哦|咦|哈|对|是|没错|没问题|谢谢|不客气|"
    r"那就|行|可以|OK|ok|Alright|Sure)"
)

# 助手侧：有信息量的痕迹
INFO_MARK = re.compile(
    r"[A-Za-z]:\\|/[\w.]{2,}/|```|https?://|\b\d+(\.\d+)?\s*(条|个|次|秒|ms|MB|GB|tok|%)|"
    r"\.(py|js|json|yml|yaml|md|db|exe|bat|ps1)|(HKCU|HKLM|HKEY_)|"
    r"\b(subprocess|import|def |class |function|SELECT|curl|python|npm|pnpm|git|reg|taskkill)\b"
)


def score(m):
    """越负越像水分"""
    content = (m.get("content") or "")
    summary = (m.get("summary") or "")
    text = (summary or content).strip()
    full = content or summary
    src = (m.get("source") or "")

    # 用户侧
    if src == "S1":
        bare = re.sub(r"[\s\W_]", "", text)
        if not bare:
            return -3                    # 只有一个符号
        if text in CONFIRM_ONLY:
            return -3                    # 纯确认词
        if len(bare) <= 2:
            return -2
        if len(bare) <= 6 and not INFO_MARK.search(full):
            return -1                    # 太短又没具体内容
        return 0

    # 助手侧
    s = 0
    if INFO_MARK.search(full):
        s += 2
    if len(full) >= 200:
        s += 1
    if len(full) < 40:
        s -= 1
    # 带标题/表格的，说明有结构化的实际内容，不再按寒暄处理
    structured = ("## " in full) or ("|---" in full) or ("| 项目 |" in full)
    if structured:
        return max(0, s)
    if CHAT_HEAD.match(text):
        s -= 2
    if len(re.sub(r"[\s\W_]", "", full)) < 12:
        s -= 2
    if not INFO_MARK.search(full) and len(full) < 120:
        s -= 1
    return s


def main():
    do = "--do" in sys.argv
    db = DB()
    rows = db.query("SELECT * FROM memories WHERE status='active'")
    victims = []
    for r in rows:
        if r["pinned"] or r["protected"] or r["layer"] in ("L4", "L5", "L3", "L2-"):
            continue
        # 带表格/标题的正文一律不动：那里面通常有具体的操作记录
        body = (r["content"] or "") + (r["summary"] or "")
        if "|" in body or "## " in body:
            continue
        sc = score(r)
        if sc <= -1:
            victims.append((r["id"], r["layer"], r.get("source"), sc, (r["summary"] or "")[:70]))

    print("active 总数 %d，判定为水分 %d 条" % (len(rows), len(victims)))
    for mid, layer, src, sc, txt in victims[:30]:
        print("  %-3s %-3s %+d  %s" % (layer, src, sc, txt))
    if len(victims) > 30:
        print("  ...另有 %d 条" % (len(victims) - 30))
    if not do:
        print("\n(dry run，加 --do 才真的执行)")
        return
    for mid, _, _, _, _ in victims:
        db.update_memory(mid, status="dormant")
    print("已下沉 %d 条为 dormant（可查询，不再注入）" % len(victims))


if __name__ == "__main__":
    main()
