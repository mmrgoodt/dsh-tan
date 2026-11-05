# -*- coding: utf-8 -*-
"""清理死数据：把「占位但不贡献召回」的记忆下沉，把「AI 正文冒充待办」的 loop 关掉

这个脚本只做数据治理，不改任何架构：
  · 记忆一律**转 dormant**，不删除 —— 还能查到，只是不再进注入预算
  · open_loops 一律标 **done**，不删除 —— 留着能翻
  · 全程一个事务，先备份再动手，可重复执行（第二次跑几乎不动）

四条规则
  1 短垃圾       active + 正文 < 20 字 + 无实体 + 非 pinned → dormant
                 排除：含数字/路径、含偏好字样、L2- 及以上层
                 兜底：被召回过 --min-recalls 次以上的不动 —— 短但真用过，就不是垃圾
                 （「我重启电脑了 将迅雷的开机自启关掉」只有 18 字，召回 57 次，这种不能沉）
  2 陈旧 L1      active + recall_count=0 + L1 + created 超过 --stale-days 天 → dormant
                 L2 及以上不动（那可能是还没被问到的好事实）
  3 待办噪音     open_loops 里标题像 AI 回答正文的 → done
                 判据：以「我/已/这轮/刚才/下面/关键」开头、含 ## | ✅ 排版符号、超过 60 字
                 例外：含「帮我/帮忙/给我/改完/弄完/做完」这种真请求的留着
  4 重复记忆     同层 + content 相似度 > --dup-ratio(difflib) 的成对里，
                 保留 recall_count 更高（同分取 created 更晚）的那条，另一条 → dormant
                 每次丢一条都往 events 记一笔（kind=cleanup），可追溯

用法
  python scripts/cleanup_dead_data.py              # 干跑，只打印不动库
  python scripts/cleanup_dead_data.py --apply      # 备份 + 真写
  python scripts/cleanup_dead_data.py --apply --stale-days 3 --dup-ratio 0.9
"""
import argparse
import difflib
import json
import os
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

# ---------- 路径：跟 hippocampus/config.py 保持一致（不 import，避免牵连引擎） ----------
HOME = Path(os.environ.get("USERPROFILE") or Path.home())
ROOT = Path(os.environ.get("TAN_HOME", HOME / ".dsh" / "tan"))
DB_PATH = ROOT / "memory.db"
BACKUP_DIR = ROOT / "backup"

# ---------- 规则参数 ----------
SHORT_MAX = 20                       # 规则 1：多短算短
MIN_RECALLS = 3                      # 规则 1 兜底：召回次数达到这个数就不当垃圾
NO_TOUCH_LAYERS = {"L2-", "L3", "L4", "L5"}   # 这四层是结论/程序/身份，短也是精华
PREF_WORDS = ("别", "不要", "以后", "不准", "不许", "必须", "禁止", "记住", "记得")
LOOP_MAX_LEN = 60                    # 规则 3：标题超过这么多字，基本是正文
LOOP_HEADS = ("我", "已", "这轮", "刚才", "下面", "关键",
              "Background", "subagent", "Task")          # AI 自述 / 机器生成的子任务
LOOP_MARKS = ("##", "|", "✅", "**", "```", "—", "；")        # 排版符号
# 「真待办」长什么样：对着我说的请求，或他还没做完的事。
# 别把「改完了/做完了」算进来 —— 那是我在汇报进度，正是噪音的特征。
LOOP_KEEP = ("帮我", "帮忙", "给我", "你去", "你帮", "记得", "明天", "下次",
             "回头", "得看", "还得", "要不要", "待会", "等我")


# ---------- 小工具 ----------
def dwidth(s):
    """显示宽度：中文算 2，用来把汇总表对齐"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def pad(s, w):
    s = str(s)
    return s + " " * max(0, w - dwidth(s))


def brief(m, n=48):
    t = (m.get("content") or "").replace("\n", " ").strip()
    return t[:n] + ("…" if len(t) > n else "")


def counts(con):
    """两张表的状态分布，apply 前后各打一次"""
    rows = {}
    for tbl in ("memories", "open_loops"):
        rows[tbl] = dict(con.execute(
            "SELECT status, COUNT(*) FROM %s GROUP BY status ORDER BY 2 DESC" % tbl))
    return rows


def show_counts(tag, c):
    for tbl in ("memories", "open_loops"):
        body = "  ".join("%s=%d" % (k, v) for k, v in c[tbl].items())
        print("  %s %-11s %s" % (tag, tbl, body))


def backup_db():
    """备份：走 sqlite 的 backup API（WAL 也一起带走，比 cp 稳）。
    真实库是 journal_mode=WAL，直接 cp 一个 .db 有可能漏掉 wal 里还没落盘的行。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / ("before-cleanup-%s.db" % time.strftime("%Y%m%d-%H%M%S"))
    src = sqlite3.connect(str(DB_PATH))
    out = sqlite3.connect(str(dst))
    try:
        with out:
            src.backup(out)
    finally:
        src.close()
        out.close()
    # 校验：完整性 + 三张表的行数对得上
    chk = sqlite3.connect(str(dst))
    try:
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
        a = chk.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        chk.close()
    live = sqlite3.connect(str(DB_PATH))
    try:
        b = live.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        live.close()
    return dst, ok, a, b


# ---------- 规则 1：短垃圾 ----------
def rule_short(rows, min_recalls=MIN_RECALLS):
    hit, why = [], {"数字": 0, "路径": 0, "偏好": 0, "高阶层": 0, "被召回": 0}
    for m in rows:
        if m["status"] != "active" or m["pinned"] or m["protected"]:
            continue
        body = m["content"] or ""
        if len(body) >= SHORT_MAX:
            continue
        ents = (m["entities"] or "").strip()
        if ents not in ("", "[]", "null"):        # 有实体的不是垃圾，实体是记忆的抓手
            continue
        if m["layer"] in NO_TOUCH_LAYERS:
            why["高阶层"] += 1
            continue
        if (m["recall_count"] or 0) >= min_recalls:
            why["被召回"] += 1
            continue
        if any(ch.isdigit() for ch in body):
            why["数字"] += 1
            continue
        if ("\\" in body or "/" in body or ":" in body
                or body.lower().endswith((".py", ".js", ".md", ".json", ".db", ".bat"))):
            why["路径"] += 1
            continue
        if any(w in body for w in PREF_WORDS):
            why["偏好"] += 1
            continue
        hit.append(m)
    return hit, why


# ---------- 规则 2：从未召回的陈旧 L1 ----------
def rule_stale_l1(rows, stale_days):
    cutoff = time.time() - stale_days * 86400
    hit, scanned = [], 0
    for m in rows:
        if m["status"] != "active" or m["layer"] != "L1" or m["pinned"] or m["protected"]:
            continue
        scanned += 1
        if (m["recall_count"] or 0) != 0:
            continue
        if (m["created"] or 0) >= cutoff:
            continue
        hit.append(m)
    return hit, scanned


# ---------- 规则 3：待办噪音 ----------
def rule_loops(loops):
    hit, kept_real = [], []
    for lp in loops:
        if lp["status"] != "open":
            continue
        title = (lp["title"] or "").strip()
        if not title:
            continue
        noise = (title.startswith(LOOP_HEADS)
                 or any(k in title for k in LOOP_MARKS)
                 or len(title) > LOOP_MAX_LEN)
        if not noise:
            continue
        if any(k in title for k in LOOP_KEEP):     # 「帮我看看 X」这种是真待办
            kept_real.append(lp)
            continue
        hit.append(lp)
    return hit, kept_real


# ---------- 规则 4：重复记忆 ----------
def rule_dups(rows, ratio_th=0.85, body_cap=800):
    """同层两两比：相似度 > 阈值就归到一簇，簇里留一条，其余下沉"""
    alive = [m for m in rows if m["status"] == "active"]
    by_layer = {}
    for m in alive:
        by_layer.setdefault(m["layer"], []).append(m)

    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    pair_ratio = {}
    for m in alive:
        parent[m["id"]] = m["id"]

    scanned = 0
    for layer, group in by_layer.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                scanned += 1
                a, b = group[i], group[j]
                ta = (a["content"] or "")[:body_cap]
                tb = (b["content"] or "")[:body_cap]
                if not ta or not tb:
                    continue
                # 长度差太多直接跳，省掉大部分比较
                la, lb = len(ta), len(tb)
                if 2.0 * min(la, lb) / (la + lb) < ratio_th:
                    continue
                sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
                if sm.real_quick_ratio() < ratio_th or sm.quick_ratio() < ratio_th:
                    continue
                r = sm.ratio()
                if r > ratio_th:
                    union(a["id"], b["id"])
                    pair_ratio[(a["id"], b["id"])] = r

    clusters = {}
    for m in alive:
        clusters.setdefault(find(m["id"]), []).append(m)

    drops, keeps = [], []
    for root, members in clusters.items():
        if len(members) < 2:
            continue
        # 留：pinned/protected 优先 → recall_count 高 → created 晚 → id 字典序（保证稳定）
        members.sort(key=lambda m: (-(m["pinned"] or 0), -(m["protected"] or 0),
                                    -(m["recall_count"] or 0), -(m["created"] or 0), m["id"]))
        keeps.append(members[0])
        for m in members[1:]:
            best = 0.0
            for (x, y), r in pair_ratio.items():
                if {x, y} == {members[0]["id"], m["id"]}:
                    best = max(best, r)
            drops.append((m, members[0], best))
    return drops, keeps, scanned


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser(description="清理死数据（默认干跑）")
    ap.add_argument("--apply", action="store_true", help="真写库；不加就是 dry-run")
    ap.add_argument("--dry-run", action="store_true", help="显式干跑（默认行为）")
    ap.add_argument("--stale-days", type=float, default=7.0, help="规则 2 的天数门槛，默认 7")
    ap.add_argument("--dup-ratio", type=float, default=0.85, help="规则 4 的相似度门槛，默认 0.85")
    ap.add_argument("--min-recalls", type=int, default=MIN_RECALLS,
                    help="规则 1 兜底：召回次数 ≥ 这么多就不算垃圾，默认 %d" % MIN_RECALLS)
    ap.add_argument("--db", default=str(DB_PATH), help="库路径，默认 ~/.dsh/tan/memory.db")
    ap.add_argument("--no-backup", action="store_true", help="跳过备份（不建议）")
    args = ap.parse_args()

    apply = args.apply and not args.dry_run
    db_path = Path(args.db)
    if not db_path.exists():
        print("库不存在：%s" % db_path)
        return 2

    con = sqlite3.connect(str(db_path), timeout=15)
    con.row_factory = sqlite3.Row

    print("=" * 68)
    print("记忆库清理  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("库：%s  (%.1f MB)" % (db_path, db_path.stat().st_size / 1048576))
    print("模式：%s" % ("APPLY（真写）" if apply else "DRY-RUN（只打印）"))
    print("=" * 68)

    before = counts(con)
    print("\n[动手前] 状态分布")
    show_counts("·", before)

    rows = [dict(r) for r in con.execute("SELECT * FROM memories").fetchall()]
    loops = [dict(r) for r in con.execute("SELECT * FROM open_loops").fetchall()]
    actives = sum(1 for m in rows if m["status"] == "active")
    print("\n扫到 memories %d 条（active %d），open_loops %d 条（open %d）"
          % (len(rows), actives, len(loops),
             sum(1 for lp in loops if lp["status"] == "open")))

    # ---- 规则 1 ----
    short_hit, short_why = rule_short(rows, args.min_recalls)
    print("\n[1] 短垃圾（< %d 字 + 无实体 + 非 pinned → dormant）" % SHORT_MAX)
    print("    扫：active %d 条" % actives)
    print("    动：%d 条" % len(short_hit))
    print("    排除：含数字 %d / 含路径 %d / 偏好似 %d / 高阶层 %d / 被召回过≥%d次 %d"
          % (short_why["数字"], short_why["路径"], short_why["偏好"], short_why["高阶层"],
             args.min_recalls, short_why["被召回"]))
    for m in short_hit[:3]:
        print("      例 %-3s %2d字 %r" % (m["layer"], len(m["content"] or ""), brief(m, 30)))

    # ---- 规则 2 ----
    stale_hit, stale_scanned = rule_stale_l1(rows, args.stale_days)
    age = [(time.time() - (m["created"] or 0)) / 86400 for m in rows if m["created"]]
    print("\n[2] 从未召回的陈旧 L1（recall_count=0 + created > %.0f 天 → dormant）" % args.stale_days)
    print("    扫：L1 active %d 条（全库最老记忆 %.1f 天）"
          % (stale_scanned, max(age) if age else 0))
    print("    动：%d 条%s" % (len(stale_hit),
                             "  ← 库龄不够 %.0f 天，本轮为空，过几天再跑就会命中"
                             % args.stale_days if not stale_hit else ""))
    for m in stale_hit[:3]:
        print("      例 %-3s rc=%d %.1f天 %r" % (m["layer"], m["recall_count"] or 0,
                                              (time.time() - (m["created"] or 0)) / 86400,
                                              brief(m, 30)))

    # ---- 规则 3 ----
    loop_hit, loop_kept = rule_loops(loops)
    n_open = sum(1 for lp in loops if lp["status"] == "open")
    print("\n[3] 待办噪音（标题像 AI 正文 → status=done）")
    print("    扫：open %d 条" % n_open)
    print("    动：%d 条" % len(loop_hit))
    print("    保留真待办 %d 条（对着我说的请求 / 他还没做完的事）" % len(loop_kept))
    for lp in loop_hit[:3]:
        print("      例 %-10s %r" % (lp["kind"], (lp["title"] or "")[:44]))
    if loop_kept:
        print("      留 %-8s %r" % (loop_kept[0]["kind"], (loop_kept[0]["title"] or "")[:44]))

    # ---- 规则 4 ----
    # 已在规则 1 里判死的，不再参与查重，免得同一批数据被两条规则互相干扰
    dead_ids = {m["id"] for m in short_hit} | {m["id"] for m in stale_hit}
    for m in rows:
        if m["id"] in dead_ids:
            m["status"] = "dormant"      # 只是内存里标一下，用来排除出查重池
    dup_drops, dup_keeps, dup_scanned = rule_dups(rows, args.dup_ratio)
    print("\n[4] 重复记忆（同层 + content 相似度 > %.2f → 留一条）" % args.dup_ratio)
    print("    扫：同层两两比 %d 对" % dup_scanned)
    print("    动：%d 条（涉及 %d 组）" % (len(dup_drops), len(dup_keeps)))
    for dead, keep, r in dup_drops[:3]:
        print("      例 %.3f 丢 %-3s %r" % (r, dead["layer"], brief(dead, 26)))
        print("            留 %-3s rc=%d %r" % (keep["layer"], keep["recall_count"] or 0, brief(keep, 26)))

    # ---- 汇总 ----
    total_mem = len(short_hit) + len(stale_hit) + len(dup_drops)
    print("\n" + "-" * 68)
    print("%s %s %s %s" % (pad("规则", 24), pad("扫描", 6), pad("命中", 6), "动作"))
    print("%s %s %s %s" % (pad("1 短垃圾", 24), pad(actives, 6), pad(len(short_hit), 6), "→ dormant"))
    print("%s %s %s %s" % (pad("2 陈旧 L1", 24), pad(stale_scanned, 6), pad(len(stale_hit), 6), "→ dormant"))
    print("%s %s %s %s" % (pad("3 待办噪音", 24), pad(n_open, 6), pad(len(loop_hit), 6), "→ done"))
    print("%s %s %s %s" % (pad("4 重复记忆", 24), pad(dup_scanned, 6), pad(len(dup_drops), 6), "→ dormant"))
    print("-" * 68)
    print("合计：记忆下沉 %d 条，待办关闭 %d 条" % (total_mem, len(loop_hit)))

    if not apply:
        print("\nDRY-RUN：一条都没写。加 --apply 才真动手（会先备份）。")
        print("备份：仅在 --apply 时创建，落在 %s" % BACKUP_DIR)
        con.close()
        return 0

    if not args.no_backup:
        dst, ok, a, b = backup_db()
        print("\n[备份] %s" % dst)
        print("       integrity=%s  行数 备份 %d / 现库 %d %s"
              % (ok, a, b, "✓ 一致" if a == b else "✗ 不一致，已中止"))
        if ok != "ok" or a != b:
            con.close()
            return 3
    else:
        print("\n[备份] 跳过（--no-backup）")

    # ---- 一个事务里做完 ----
    now_ts = time.time()
    try:
        with con:                                  # 成功 commit / 异常 rollback
            for m in short_hit:
                con.execute("UPDATE memories SET status='dormant', updated=? WHERE id=?",
                            (now_ts, m["id"]))
            for m in stale_hit:
                con.execute("UPDATE memories SET status='dormant', updated=? WHERE id=?",
                            (now_ts, m["id"]))
            for dead, keep, r in dup_drops:
                con.execute("UPDATE memories SET status='dormant', updated=? WHERE id=?",
                            (now_ts, dead["id"]))
                con.execute(
                    "INSERT INTO events (ts, kind, payload) VALUES (?,?,?)",
                    (now_ts, "cleanup", json.dumps({
                        "rule": "dedupe", "layer": dead["layer"], "drop": dead["id"],
                        "keep": keep["id"], "ratio": round(r, 4),
                        "drop_brief": (dead["content"] or "")[:60],
                    }, ensure_ascii=False)))
            for lp in loop_hit:
                con.execute("UPDATE open_loops SET status='done' WHERE id=?", (lp["id"],))
            # 一次 tidy：记一条总账，方便以后回查这次动了什么
            con.execute("INSERT INTO events (ts, kind, payload) VALUES (?,?,?)",
                        (now_ts, "cleanup", json.dumps({
                            "rule": "summary", "short": len(short_hit), "stale_l1": len(stale_hit),
                            "loops": len(loop_hit), "dupes": len(dup_drops),
                        }, ensure_ascii=False)))
    except Exception as e:
        con.close()
        print("\n写入失败，已回滚：%s" % e)
        return 4

    after = counts(con)
    print("\n[动手后] 状态分布")
    show_counts("·", after)
    print("\n完成。回滚方式：关掉服务后把备份文件覆盖回 %s" % db_path)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
