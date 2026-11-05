# -*- coding: utf-8 -*-
"""修 sanitize：宿主块的闭合标签经常根本没有"""
import io

p = "api.py"
s = io.open(p, encoding="utf-8").read()

old = '''_HOST_BLOCK = re.compile(
    r"<system-reminder>.*?</system-reminder>"
    r"|<available_skills>.*?</available_skills>"
    r"|<project_instructions>.*?</project_instructions>"
    r"|Current runtime context\\..*?(?=\\n\\n|\\Z)"
    r"|\\[OPERATOR ANCHOR.*?\\]"
    r"|## Tools\\s.*?(?=\\n\\n\\n|\\Z)",
    re.S | re.I,
)'''

new = '''_HOST_BLOCK = re.compile(
    # 成对的先剥成对；但在 DSH 里这些块实测经常没有闭合标签
    # （system-reminder 就直接接正文到消息结尾），所以退化成「剥到文末」
    r"<system-reminder>.*?(?:</system-reminder>|\\Z)"
    r"|<available_skills>.*?(?:</available_skills>|\\Z)"
    r"|<project_instructions>.*?(?:</project_instructions>|\\Z)"
    r"|<available-tools>.*?(?:</available-tools>|\\Z)"
    r"|Current runtime context\\..*?(?=\\n\\n|\\Z)"
    r"|\\[OPERATOR ANCHOR.*?\\]"
    r"|## Tools\\s.*?(?=\\n\\n\\n|\\Z)",
    re.S | re.I,
)'''

if old not in s:
    raise SystemExit("MISS: _HOST_BLOCK")
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("OK")
