# -*- coding: utf-8 -*-
"""记忆重分层：把「过程播报」降回情景层

大脑里「今天下午三点我改了配置文件」和「这台机器的 Node 在
C:\\Program Files\\nodejs」不是一个量级的东西：
  · 前者是情景，过几天自然淡掉
  · 后者是事实，应该一直在

现在系统里两者都叫 L2，半衰期 180 天，于是「我修好了」这种话
永远占着注入预算，而真正该被记住的事被挤出去。

判定：
  有具体坐标（路径/命令/版本号/配置项）→ 留下，它是事实
  只有「做完了/试过了/改好了」的过程叙述   → 降到 L1

用法: python reclassify.py [--do]
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hippocampus.db import DB

# 有这些说明是可复用的坐标，不是流水账
ANCHOR = re.compile(
    r"[A-Za-z]:\\|/[\w.]+/|```|https?://|\b\d+\.\d+\.\d+\b|"
    r"\b(HKCU|HKLM|HKEY_|Software\\)|"
    r"\b(subprocess|import|def |class |function|SELECT|curl|python|npm|pnpm|git|reg|taskkill|schtasks)\b|"
    r"\.(py|js|json|yml|yaml|md|db|exe|bat|ps1|toml)\b"
)
# 纯过程叙述
PROCESS = re.compile(
    r"(做完了|做好了|改好了|修好了|试过了|跑完了|测完了|检查完|已经完成|"
    r"已经通过|能跑|已推送|已发布|已提交|已清理|已关闭|已删除|已禁用|已卸载)"
)


def classify(m):
    text = (m.get("content") or "") + " " + (m.get("summary") or "")
    has_anchor = bool(ANCHOR.search(text))
    is_process = bool(PROCESS.search((m.get("summary") or "")[:80]))

    if has_anchor:
        return "L2", "有具体坐标，留作事实"
    if is_process:
        return "L1", "过程叙述，降为情景"
    if m.get("layer") in ("L3", "L4", "L5", "L2-"):
        return None, "程序/身份/教训层不动"
    return None, "看不出，保持"


def main():
    do = "--do" in sys.argv
    db = DB()
    rows = [dict(r) for r in db.query(
        "SELECT * FROM memories WHERE status='active' AND source='S3'")]
    plan = []
    for m in rows:
        new_layer, why = classify(m)
        if new_layer and new_layer != m.get("layer"):
            plan.append((m["id"], m.get("layer"), new_layer, why,
                         (m.get("summary") or "")[:52]))

    print("S3（我自己的发言）共 %d 条，需要重分层 %d 条" % (len(rows), len(plan)))
    up = [p for p in plan if p[2] == "L1"]
    print("  其中降为情景 L1：%d 条" % len(up))
    for p in up[:14]:
        print("    %s -> %s  %s" % (p[1], p[2], p[4]))
    if len(up) > 14:
        print("    ...另有 %d 条" % (len(up) - 14))

    if not do:
        print("\n(dry run，加 --do 才写)")
        return

    for mid, old, new, why, _ in plan:
        fields = {"layer": new}
        # 降到情景层配短半衰期，让它自然淡
        if new == "L1":
            fields["half_life_days"] = 3.0
        try:
            db.update_memory(mid, **fields)
        except Exception:
            pass
    print("\n已重分层 %d 条" % len(plan))


if __name__ == "__main__":
    main()
