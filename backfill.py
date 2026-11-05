# -*- coding: utf-8 -*-
"""从 DSH 会话文件回补记忆：找出没入库的对话轮，走 extractor 写进去

用法:
  python backfill.py --dry            只看要补多少轮
  python backfill.py --do             真写
  python backfill.py --do --limit 20  最多补 20 轮
"""
import argparse
import datetime
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from compression import zstd  # Python 3.14 标准库

from hippocampus.db import DB

SESSIONS_ROOT = os.path.expanduser(r"~\.dsh\sessions")

# 宿主注入块：这些出现在 user/message 里，但不是人说的话
HOST_MARKS = (
    "<system-reminder>",
    "<available_skills>",
    "<project_instructions>",
    "Current runtime context.",
    "[OPERATOR ANCHOR",
    "## Tools\n",
)


def is_host_noise(txt):
    if not txt:
        return True
    head = txt[:500]
    return any(m in head for m in HOST_MARKS)


def load_events(path):
    raw = zstd.decompress(open(path, "rb").read()).decode("utf-8", errors="replace")
    out = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def text_of(ev):
    """assistant/message 的正文在 data.message.content，user/message 在 data.content"""
    data = ev.get("data") or {}
    msg = data.get("message") if isinstance(data.get("message"), dict) else data
    parts = []
    for c in (msg.get("content") or []):
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text") or "")
    return "\n".join(p for p in parts if p).strip()


def turns_of(events):
    """按 turn 收集 (turn, user_text, assistant_text, ts)

    每个 turn 可能有很多 step，assistant/message 也发很多次；
    只留最后一条有正文的，那才是这一轮的最终答复。
    """
    buckets = {}
    order = []
    for ev in events:
        t = ev.get("type")
        d = ev.get("data") or {}
        turn = d.get("turn")
        if t == "turn/start":
            if isinstance(turn, int):
                if turn not in buckets:
                    buckets[turn] = {"user": "", "assistant": "", "ts": ev.get("time")}
                    order.append(turn)
        elif not order:
            continue
        elif t == "user/message":
            if not isinstance(turn, int):
                turn = order[-1]
            if turn not in buckets:
                continue
            src = (d.get("source") or {}).get("kind")
            txt = text_of(ev)
            if src != "user" or is_host_noise(txt):
                continue
            buckets[turn]["user"] = txt
        elif t == "assistant/message":
            if not isinstance(turn, int):
                turn = order[-1]
            if turn not in buckets:
                continue
            txt = text_of(ev)
            if txt:
                buckets[turn]["assistant"] = txt
    return [(t, buckets[t]["user"], buckets[t]["assistant"], buckets[t]["ts"]) for t in order]


def done_turns(db, session_id):
    """已经入库过的 (session, turn)"""
    got = set()
    for r in db.query("SELECT provenance FROM memories WHERE provenance IS NOT NULL"):
        try:
            p = json.loads(r["provenance"] or "{}")
        except Exception:
            continue
        if p.get("session") == session_id and isinstance(p.get("turn"), int):
            got.add(p["turn"])
    return got


# 整轮没有记忆价值的：寒暄、确认、误触、空输入
CHITCHAT = set("""1 2 3 好 嗯 哦 行 可以 对 是 不 没有 在吗 你好 您好 谢谢 收到 ok okk
? ？ / \\ . 。 … 早 晚安 哈哈 嗯嗯 好的 行吧 可以啊 那就这样""".split())


def worth_remembering(user, assistant):
    """这一轮值不值得进记忆库

    历史里有一大堆「1」「/」「算了」这种，配上我一段寒暄回复。
    原样写进去就是纯噪音：占预算、拉低召回精度，还挤掉真正有用的记忆。
    """
    u = (user or "").strip()
    a = (assistant or "").strip()
    if not u and not a:
        return False
    if u.lower() in CHITCHAT:
        return False
    # 用户没说什么、我也没展开什么 —— 整轮没有信息增量
    if len(u) < 8 and len(a) < 150:
        return False
    # 只有符号
    if u and not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fa5" for ch in u):
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="session-01fa62b3-a616-49be-8657-88fb8a7e5eed")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--do", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    path = None
    for root, dirs, files in os.walk(SESSIONS_ROOT):
        if os.path.basename(root) == args.session and "session.v4.jsonl.zstd" in files:
            path = os.path.join(root, "session.v4.jsonl.zstd")
            break
    if not path:
        raise SystemExit("找不到会话文件: " + args.session)

    events = load_events(path)
    turns = turns_of(events)
    db = DB()
    done = done_turns(db, args.session)

    raw_todo = [t for t in turns if t[0] not in done and (t[1] or t[2])]
    todo = [t for t in raw_todo if worth_remembering(t[1], t[2])]
    skipped = len(raw_todo) - len(todo)
    print("会话 %s" % args.session)
    print("总轮数 %d，已入库 %d，待补 %d（另有 %d 轮是寒暄/误触，跳过）"
          % (len(turns), len(done & set(t[0] for t in turns)), len(todo), skipped))
    if todo:
        t0, u0, a0, ts0 = todo[0]
        t1 = todo[-1]
        fmt = lambda ms: datetime.datetime.fromtimestamp((ms or 0) / 1000).strftime("%m-%d %H:%M")
        print("区间 %s ~ %s" % (fmt(todo[0][3]), fmt(todo[-1][3])))
        print("样例 turn=%s  用户: %s" % (t0, (u0 or "")[:60].replace("\n", " ")))
        print("             助手: %s" % ((a0 or "")[:60].replace("\n", " ")))
    if not args.do:
        return

    from hippocampus.engine import Hippocampus
    H = Hippocampus()
    n = 0
    mems = 0
    for turn, user, assistant, ts in todo:
        if args.limit and n >= args.limit:
            break
        r = H.after_turn(user_text=user, assistant_text=assistant,
                         session=args.session, turn=turn,
                         ts=(ts / 1000.0) if ts else None)
        mems += len(r.get("memories") or [])
        n += 1
        if n % 10 == 0:
            print("  ...已补 %d 轮 / %d 条" % (n, mems))
    print("完成：补了 %d 轮，写入 %d 条记忆" % (n, mems))


if __name__ == "__main__":
    main()
