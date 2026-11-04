# -*- coding: utf-8 -*-
"""Tan 命令行工具

用法:
  python cli.py status                查看状态
  python cli.py say "用户说的话"       模拟一轮对话（写入）
  python cli.py ask "问题"            模拟对话前检索
  python cli.py remember "要记的事"    手动记一条
  python cli.py checklist             看未闭合清单
  python cli.py gold                  跑黄金测试
  python cli.py report                周报
  python cli.py sweep                 手动跑一次清理
  python cli.py snapshot              存快照
  python cli.py demo                  一键演示全流程
"""
import json
import sys
import time

from hippocampus import Hippocampus, config


def p(obj):
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(obj)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    h = Hippocampus()

    if cmd == "status":
        st = h.status()
        print(f"📦 记忆库: {st['db_path']}")
        print(f"   记忆 {st['memories']} 条 / Thread {st['threads']} 个 / 未闭合 {st['open_loops']} 件")
        print(f"   分层: {st['by_layer']}")
        print(f"   人格: {st['persona']['stage']} 阶段，已聊 {st['persona']['turns']} 轮")

    elif cmd == "say":
        text = args[1] if len(args) > 1 else "今天把那个软件的开屏广告去掉了，改了两个目录"
        r = h.after_turn(user_text=text, session="cli", turn=1)
        print("写入结果：")
        p(r)

    elif cmd == "ask":
        q = args[1] if len(args) > 1 else "某软件"
        text, meta = h.prepare_context(q)
        print(f"—— 注入内容（{meta['tokens']} token，命中 {meta['memories']} 条）——\n")
        print(text or "（空）")

    elif cmd == "remember":
        text = args[1] if len(args) > 1 else "测试记忆"
        mid = h.remember(text)
        print(f"✅ 已记住：{mid}")

    elif cmd == "checklist":
        print(h.checklist())

    elif cmd == "gold":
        r = h.run_golden(verbose=True)
        print(f"黄金测试：{r['passed']}/{r['total']}（{r['score']}%）")
        for d in r["details"]:
            print(f"  {'✅' if d['ok'] else '❌'} {d['q']}  (命中 {d['hit']})")

    elif cmd == "report":
        print(h.weekly_report())

    elif cmd == "sweep":
        p(h.forgetter.sweep())

    elif cmd == "snapshot":
        p(h.snapshot("手动"))

    elif cmd == "demo":
        demo(h)

    else:
        print(__doc__)


def demo(h):
    print("=" * 60)
    print("Tan 演示")
    print("=" * 60)

    turns = [
        "帮我把那个软件的开屏广告去掉，两个目录改了就行",
        "我明天还得看下项目报表，先这样吧",
        "以后别叫我老师，叫我小满就行",
        "算了，太累了，今天不想弄了",
        "我的代码托管在 GitHub，账号是 myname",
    ]

    for i, t in enumerate(turns, 1):
        print(f"\n[第 {i} 轮] 用户: {t}")
        r = h.after_turn(user_text=t, assistant_text="", session="demo", turn=i)
        print(f"   → 写入 {len(r['memories'])} 条，新增未闭合 {len(r['loops'])} 件")

    print("\n" + "=" * 60)
    print("【对话前】问：那个软件怎么弄的？")
    print("=" * 60)
    text, meta = h.prepare_context("软件 广告")
    print(text)
    print(f"\n（占用 {meta['tokens']} token，命中 {meta['memories']} 条）")

    print("\n" + "=" * 60)
    print("【未闭合清单】")
    print("=" * 60)
    print(h.checklist())

    print("\n" + "=" * 60)
    print("【人格】")
    print("=" * 60)
    print(h.persona.prompt_block())

    print("\n" + "=" * 60)
    print("【状态】")
    print("=" * 60)
    p(h.status())


if __name__ == "__main__":
    main()
