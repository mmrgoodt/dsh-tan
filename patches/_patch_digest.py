# -*- coding: utf-8 -*-
"""长报告别只留第一行

原来 is_report_text 判为「我的长篇报告」后直接 t.split('\n')[0][:80]，
等于把我写出来的方案、命令、验证结果全砍成一句开场白。
下次问「上次那个是怎么改的」，库里只剩一句没有信息量的话。
改成压大纲：保留标题 + 结论句 + 要点行，丢掉代码块和表格。
"""
import io

p = "hippocampus/extractor.py"
s = io.open(p, encoding="utf-8").read()

old = '''    # 我自己的长篇报告：只留开头一句结论
    is_report = is_report_text(t, role)
    if is_report:
        t = t.strip()
        head = t.split(chr(10))[0][:80]
        t = head'''

new = '''    # 我自己的长篇报告：压成大纲，不是砍成一句话
    is_report = is_report_text(t, role)
    if is_report:
        t = report_digest(t)'''

if old not in s:
    raise SystemExit("MISS: report branch")
s = s.replace(old, new, 1)

# 插入 report_digest
helper = '''

def report_digest(text, limit=420):
    """长篇输出 → 大纲式记忆

    留：markdown 标题、首段结论、要点行（·/-/数字序号）
    丢：代码块、表格、纯装饰行
    目标：留住「结论是什么、改了哪几处」，而不是只剩一句「先说结论」。
    """
    lines = []
    in_code = False
    seen = set()
    for raw in str(text or "").splitlines():
        s = raw.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not s:
            continue
        if s.startswith("|") or set(s) <= set("-|: "):
            continue
        if s.startswith("#"):
            item = s.lstrip("# ").strip()
        elif s[0] in "·-*" or s[:2] in ("1.", "2.", "3.", "4.", "5."):
            item = s.lstrip("·-* ").strip()[:100]
        elif not lines:
            item = s[:120]
        else:
            continue
        if item and item not in seen:
            seen.add(item)
            lines.append(item)
        if len(lines) >= 14:
            break
    out = "；".join(lines)
    return out[:limit] if out else str(text or "").strip()[:80]

'''

anchor = "def is_report_text(text, role):"
if anchor not in s:
    raise SystemExit("MISS: anchor")
s = s.replace(anchor, helper.strip() + "\n\n\n" + anchor, 1)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("OK: report_digest")
