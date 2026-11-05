# -*- coding: utf-8 -*-
"""清洗已有的 persona 脏数据

新的识别规则只挡住以后的输入，已经写进 persona 表的还得手动过一遍。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hippocampus.db import DB
from hippocampus.persona import persona_like, TRAIT_MAX, JUDGE_MAX

# 明确要删的：技术指令、任务描述、一次性收尾语 —— 具体条目点名清
DROP = (
    "不用啦这样子就可以了",
    "不用做这个我需要你能更改设备的燃烧时间",
    "每次打开软件开启代理后可以正常访问外网",
    "请分析这张图片",
    "你没把名字和使用方式和方法写出来吗",
    "写个文档吧我看看",
)


def _txt(x):
    return x.get("text", "") if isinstance(x, dict) else str(x)


def main():
    do = "--do" in sys.argv
    db = DB()
    p = db.persona()
    if not p:
        print("没有 persona 数据")
        return

    changes = {}
    for field, limit in (("traits", TRAIT_MAX), ("judgments", JUDGE_MAX), ("speech", JUDGE_MAX)):
        cur = p.get(field) or []
        if isinstance(cur, str):
            try:
                cur = json.loads(cur)
            except Exception:
                cur = []
        keep, drop = [], []
        for item in cur:
            t = _txt(item)
            ok = not any(t.startswith(d) for d in DROP)
            (keep if ok else drop).append((item, t))
        changes[field] = (keep, drop)

    for field, (keep, drop) in changes.items():
        print("== %s：留 %d / 删 %d" % (field, len(keep), len(drop)))
        for _, t in keep:
            print("   留  " + t[:52])
        for _, t in drop:
            print("   删  " + t[:52])

    if not do:
        print("\n(dry run，加 --do 才写入)")
        return

    for field, (keep, _) in changes.items():
        db.execute("UPDATE persona SET %s=? WHERE id=(SELECT id FROM persona LIMIT 1)" % field,
                   (json.dumps([k for k, _ in keep], ensure_ascii=False),))
    print("已更新 persona")


if __name__ == "__main__":
    main()
