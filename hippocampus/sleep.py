# -*- coding: utf-8 -*-
"""睡眠：离线重放、突触修剪、记忆重构

人睡着的时候海马体会把白天的片段高速重放一遍，跟皮层的记忆一起激活。
反复一起激活的连接变强（Hebbian：fire together, wire together），
从来不用的连接慢慢剪掉。醒来看世界，重点自然就清楚了。

这套东西现在完全没有。所以库里：
  · 每条记忆是等权的，说过一句和说过十遍一样重
  · 从来没被召回过的连接，和天天用的连接一样粗
  · 碎片永远是碎片，不会自己坍缩成结论

睡眠干三件事：
  1. **重放**：随机采样记忆，把它们的概念一起激活，强化共现路径
  2. **修剪**：权重低于阈值的突触删掉；从没被走过的路径优先剪
  3. **重构**：一对记忆之间突触足够强，说明它们讲的是同一件事，
     合并成一条——这是碎片坍缩成结论的那一步

跑法：每天维护时跑一次，或者手动 `python sleep.py`。
"""
import json
import math
import random
import time

PRUNE_WEIGHT = 0.6        # 低于这个权重的突触，睡一觉就没了
REPLAY_ROUNDS = 60        # 每次睡眠重放多少条记忆
HEBB_FACTOR = 1.04        # 一起激活一次，连接增强 4%
FLOOR_WEIGHT = 0.35       # 再弱也不至于完全归零（可能只是暂时想不起来）
MERGE_WEIGHT = 9.0        # 一对记忆间所有突触加起来超过这个值，就合并（5.0 太松，实测会误并）


def _ents(m):
    raw = m.get("entities")
    if isinstance(raw, list):
        return [e for e in raw if e]
    try:
        v = json.loads(raw or "[]")
        return [e for e in v if e] if isinstance(v, list) else []
    except Exception:
        return []


def replay(db, rounds=REPLAY_ROUNDS, rng=None):
    """离线重放：随机取记忆，强化它们内部的突触

    采样不是均匀的：最近的和常被召回的多取几次（记忆在睡眠里也是按重要性重放）。
    """
    rng = rng or random.Random()
    rows = db.query(
        "SELECT id, recall_count, created FROM memories WHERE status='active'")
    if not rows:
        return 0

    # 权重：召回次数 + 新近度
    now = time.time()
    pool = []
    for r in rows:
        recency = 1.0 / (1.0 + (now - (r["created"] or now)) / 86400.0 / 30.0)
        w = 1.0 + min(4.0, (r["recall_count"] or 0)) + recency
        pool.append((w, r["id"]))

    total = sum(w for w, _ in pool)
    picked = []
    for _ in range(rounds):
        x = rng.uniform(0, total)
        acc = 0.0
        for w, mid in pool:
            acc += w
            if acc >= x:
                picked.append(mid)
                break

    now = time.time()
    hit = 0
    with db.conn() as con:
        for mid in picked:
            cons = [r["concept"] for r in con.execute(
                "SELECT concept FROM traces WHERE memory_id=?", (mid,))]
            if len(cons) < 2:
                continue
            cons = cons[:40]
            for i in range(len(cons)):
                for j in range(i + 1, len(cons)):
                    a, b = sorted((cons[i], cons[j]))
                    cur = con.execute(
                        "SELECT weight FROM synapses WHERE a=? AND b=?", (a, b)).fetchone()
                    if cur:
                        con.execute(
                            "UPDATE synapses SET weight = weight * ?, last=? WHERE a=? AND b=?",
                            (HEBB_FACTOR, now, a, b))
                        hit += 1
    return hit


def prune(db, threshold=PRUNE_WEIGHT):
    """突触修剪：长期没走过、又弱的连接，删掉

    大脑不能无限长连接，否则信号全糊在一起。删掉弱的，留下的才清晰。
    """
    with db.conn() as con:
        before = con.execute("SELECT COUNT(*) FROM synapses").fetchone()[0]
        con.execute("DELETE FROM synapses WHERE weight < ?", (threshold,))
        con.execute(
            "UPDATE synapses SET weight = ? WHERE weight < ?", (FLOOR_WEIGHT, FLOOR_WEIGHT))
        # 权重归一化，防止重放让它无限增长
        con.execute("UPDATE synapses SET weight = ? WHERE weight > ?", (20.0, 20.0))
        after = con.execute("SELECT COUNT(*) FROM synapses").fetchone()[0]
    return {"before": before, "after": after, "pruned": before - after}


def reconstruct(db, threshold=MERGE_WEIGHT):
    """记忆重构：讲同一件事的两条记忆，合并成一条

    怎么判断：两条记忆的所有概念之间，突触权重加起来很高 ——
    说明它们在概念空间里几乎重叠，只是当时分两次说了。
    合并后旧的两条标 dormant，新的继承全部线索。
    """
    from .atomic import similarity

    # 找概念高度重叠的记忆对
    con_rows = db.query(
        "SELECT memory_id, concept FROM traces")
    mem_cons = {}
    for r in con_rows:
        mem_cons.setdefault(r["memory_id"], set()).add(r["concept"])

    syn = {}
    for r in db.query("SELECT a, b, weight FROM synapses"):
        syn[(r["a"], r["b"])] = r["weight"]
        syn[(r["b"], r["a"])] = r["weight"]

    # 受保护的记忆不参与合并：
    #   · LLM 提炼的结论（provenance.by == "llm"）—— 合并会把结论的独立性吃掉，
    #     实测「本机网络走 Clash TUN」这条结论被并掉后，问网络直接召不出来
    #   · 身份/方法/教训层 —— 这三层本来就是一条一件事
    protect = {}
    for r in db.query(
        "SELECT id, layer, provenance FROM memories WHERE status='active'"):
        keep = r["layer"] in ("L3", "L4", "L5", "L2-")
        try:
            pv = json.loads(r["provenance"] or "{}")
            if isinstance(pv, dict) and pv.get("by") == "llm":
                keep = True
        except Exception:
            pass
        protect[r["id"]] = keep

    ids = [i for i in mem_cons if not protect.get(i)]
    cands = []
    for i in range(len(ids)):
        a = ids[i]
        ca = mem_cons[a]
        if len(ca) < 3:
            continue
        for j in range(i + 1, len(ids)):
            b = ids[j]
            cb = mem_cons[b]
            if len(cb) < 3:
                continue
            # 先看概念重合度，够高再算突触总和
            inter = ca & cb
            if len(inter) < max(2, int(0.4 * min(len(ca), len(cb)))):
                continue
            w = sum(syn.get((x, y), 0.0) for x in inter for y in inter if x != y)
            if w >= threshold:
                cands.append((w, a, b))

    cands.sort(reverse=True)
    merged = 0
    done = set()
    for w, a, b in cands[:12]:
        if a in done or b in done:
            continue
        ma, mb = db.get_memory(a), db.get_memory(b)
        if not ma or not mb:
            continue
        # 合并：内容取长的，线索取并集，signature 记在 provenance
        longer, shorter = (ma, mb) if len(ma.get("content") or "") >= len(mb.get("content") or "") else (mb, ma)
        body = longer.get("content") or ""
        other = (shorter.get("content") or "").strip()
        if other and similarity(body, other) < 0.95:
            body = body.rstrip() + "\n\n（同一件事的另一段记录）" + other
        prov = longer.get("provenance") or "{}"
        try:
            p = json.loads(prov) if isinstance(prov, str) else dict(prov)
        except Exception:
            p = {}
        p["merged_from"] = [shorter["id"]]
        # 用更长的 content 建新记忆，layers 取更高的那个
        layer_order = {"L5": 0, "L4": 1, "L3": 2, "L2-": 3, "L2": 4, "L1": 5}
        layer = min((longer.get("layer"), shorter.get("layer")),
                    key=lambda x: layer_order.get(x, 9))
        try:
            new_id = db.add_memory(
                layer=layer,
                content=body[:2000],
                summary=(longer.get("summary") or "")[:70],
                fidelity="verbatim",
                source=longer.get("source") or "S3",
                confidence=min(1.0, float(longer.get("confidence") or 0.7) + 0.1),
                entities=longer.get("entities"),
                tags=["merged"],
                created=longer.get("created"),
                provenance=p,
            )
            # 线索并过去
            with db.conn() as con:
                for mid in (a, b):
                    con.execute(
                        "INSERT OR IGNORE INTO traces (concept, memory_id, salience) "
                        "SELECT concept, ?, salience FROM traces WHERE memory_id=?", (new_id, mid))
            db.update_memory(a, status="dormant", superseded_by=new_id)
            db.update_memory(b, status="dormant", superseded_by=new_id)
            done.update((a, b))
            merged += 1
        except Exception:
            continue
    return merged


def sleep(db, verbose=False):
    """一次完整睡眠"""
    t0 = time.time()
    r = {"replayed": 0, "pruned": 0, "merged": 0}

    # 概念表可能在空库里还没建（sleep 直接跑会 no such table: synapses）。
    # 建表本身是幂等的，先补上再动重放和修剪。
    try:
        from . import cortex as _cortex0

        _cortex0.ensure_schema(db)
    except Exception:
        pass

    r["replayed"] = replay(db)

    p = prune(db)
    r["pruned"] = p["pruned"]

    # 合并先关掉：实测它会把高价值记忆并没，收益远小于风险。
    # 留着入口，等有更好的判据再开。
    r["merged"] = 0

    # 沉降：老且没人碰的记忆转 dormant（不删）。
    # 遗忘以前只有参数没有任务，睡眠跑完也没人动状态，等于没遗忘。
    try:
        from .forget import settle as _settle

        r["settled"] = _settle(db).get("settled", 0)
    except Exception:
        r["settled"] = 0

    # 重建概念网（合并后线索变了）
    try:
        from . import cortex
        cortex.build(db)
    except Exception:
        pass

    r["ms"] = int((time.time() - t0) * 1000)
    if verbose:
        print("睡眠完成：重放 %d 次 / 剪掉 %d 条突触 / 合并 %d 对记忆 / 沉降 %d 条 / %dms"
              % (r["replayed"], r["pruned"], r["merged"], r.get("settled", 0), r["ms"]))
    return r


def main(argv=None):
    """命令行入口

      python -m hippocampus.sleep --settle        只跑一次沉降（老记忆转 dormant）
      python -m hippocampus.sleep --settle --days 7 --dry-run
      python -m hippocampus.sleep                 跑一次完整睡眠（含沉降）
    """
    import argparse
    import os
    import sys

    ap = argparse.ArgumentParser(
        prog="python -m hippocampus.sleep",
        description="睡眠维护：重放 / 修剪 / 沉降")
    ap.add_argument("--settle", action="store_true",
                    help="只跑沉降，不跑重放和修剪")
    ap.add_argument("--days", type=float, default=None,
                    help="沉降天数阈值（默认读 config.SETTLE_DAYS，14 天）")
    ap.add_argument("--max-recall", type=int, default=None,
                    help="召回次数 ≤ 这个数才算没人用（默认 config.SETTLE_MAX_RECALL）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不改状态")
    ap.add_argument("--verbose", "-v", action="store_true", help="打印明细")
    args = ap.parse_args(argv)

    # 允许直接 `python hippocampus/sleep.py` 跑（此时没有包上下文）
    if __package__ in (None, ""):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from hippocampus.db import DB  # type: ignore
        from hippocampus.forget import settle as _settle  # type: ignore
    else:
        from .db import DB
        from .forget import settle as _settle

    db = DB()
    if args.settle:
        st = _settle(db, days=args.days, max_recall=args.max_recall, dry_run=args.dry_run)
        print("沉降%s：扫描 %d 条 active / 转 dormant %d 条（阈值 %g 天，召回 ≤%d）"
              % ("（dry-run）" if args.dry_run else "",
                 st["scanned"], st["settled"], st["days"], st["max_recall"]))
        if args.verbose:
            for mid in st["ids"]:
                mem = db.get_memory(mid) or {}
                print("  · %s [%s] %s" % (mid, mem.get("layer"),
                                          (mem.get("summary") or mem.get("content") or "")[:40]))
        return 0

    r = sleep(db, verbose=True)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
