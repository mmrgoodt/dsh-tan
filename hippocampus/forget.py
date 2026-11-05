# -*- coding: utf-8 -*-
"""遗忘：衰减检查、三出口、容量控制、沉降"""
import json
import time

from . import config


def _overrides():
    """config.json 里的覆盖项（可选）。读不到就当没有，不影响默认值。"""
    try:
        p = config.CONFIG_PATH
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def settle_params():
    """沉降参数：(天数, 召回次数上限)

    默认从 config 读（SETTLE_DAYS / SETTLE_MAX_RECALL），
    也可以在 ~/.dsh/tan/config.json 里用 settle_days / settle_max_recall 覆盖。
    """
    ov = _overrides()
    try:
        days = float(ov.get("settle_days", config.SETTLE_DAYS))
    except Exception:
        days = float(config.SETTLE_DAYS)
    try:
        max_recall = int(ov.get("settle_max_recall", config.SETTLE_MAX_RECALL))
    except Exception:
        max_recall = int(config.SETTLE_MAX_RECALL)
    return days, max_recall


def settle(db, days=None, max_recall=None, now=None, dry_run=False):
    """沉降：老且没人碰的记忆，从 active 转 dormant（不删）

    三条同时满足才动：
      · created 超过 N 天
      · last_recalled 为空，或者也超过 N 天（最近被想起的不动）
      · recall_count ≤ max_recall（用得多的不动）
    另外：pinned / protected 一律不动 —— 钉住的和受保护的是「不许自动遗忘」。

    为什么是 dormant 而不是删：遗忘要可逆。休眠的还能被捞回来，
    删了就真没了。superseded 是另一回事（被新结论盖掉），这里不碰。
    """
    now = now or time.time()
    cfg_days, cfg_max = settle_params()
    days = cfg_days if days is None else float(days)
    max_recall = cfg_max if max_recall is None else int(max_recall)
    window = days * 86400.0

    try:
        rows = db.query(
            "SELECT id, created, last_recalled, recall_count FROM memories "
            "WHERE status='active' AND pinned=0 AND protected=0 "
            "ORDER BY created ASC LIMIT ?",
            (int(config.SETTLE_BATCH),),
        )
    except Exception:
        return {"scanned": 0, "settled": 0, "days": days, "max_recall": max_recall, "ids": []}

    ids = []
    for m in rows:
        created = m["created"] or now
        if (now - created) < window:
            continue
        last = m["last_recalled"]
        if last is not None and (now - last) < window:
            continue
        if int(m["recall_count"] or 0) > max_recall:
            continue
        if not dry_run:
            try:
                db.update_memory(m["id"], status="dormant")
            except Exception:
                continue
        ids.append(m["id"])

    stat = {
        "scanned": len(rows),
        "settled": len(ids),
        "days": days,
        "max_recall": max_recall,
        "ids": ids,
        "dry_run": bool(dry_run),
    }
    if not dry_run and ids:
        # 记一笔事件，事后能查「什么时候沉的、沉了哪些」
        try:
            db.execute(
                "INSERT INTO events (ts,kind,payload) VALUES (?,?,?)",
                (now, "settle", json.dumps(
                    {"settled": len(ids), "days": days, "max_recall": max_recall},
                    ensure_ascii=False)),
            )
        except Exception:
            pass
    return stat


class Forgetter:
    def __init__(self, db):
        self.db = db

    def settle(self, **kw):
        """手动/定时跑一次沉降"""
        return settle(self.db, **kw)

    def sweep(self):
        """定期跑一次：噪音归档、自然沉降、容量控制、按时沉降"""
        stat = {"archived_noise": 0, "dormant": 0, "trimmed": 0, "settled": 0}
        now = time.time()

        # ① 噪音 3 天后归档
        rows = self.db.query(
            "SELECT id, created, tags FROM memories WHERE status='active' AND tags LIKE '%noise%'"
        )
        for r in rows:
            if (now - r["created"]) / 86400 >= config.NOISE_ARCHIVE_DAYS:
                self.db.update_memory(r["id"], status="archived")
                stat["archived_noise"] += 1

        # ② 自然沉降：衰减低于阈值且长期未召回
        rows = self.db.query(
            "SELECT * FROM memories WHERE status='active' AND protected=0 AND pinned=0"
        )
        for m in rows:
            hl = max(0.5, float(m["half_life_days"] or 3.0))
            days = (now - (m["created"] or now)) / 86400
            d = pow(2.718281828, -days / hl)
            last = m["last_recalled"] or m["created"] or now
            idle = (now - last) / 86400
            if d < config.DORMANT_DECAY and idle > 30:
                self.db.update_memory(m["id"], status="dormant")
                stat["dormant"] += 1

        # ③ 容量控制：每层超限时，最旧的低分转 dormant
        cap = {"L1": 5000, "L2": 2000, "L2-": 200, "L3": 300, "L4": 200, "L5": 200}
        for layer, limit in cap.items():
            row = self.db.one(
                "SELECT COUNT(*) c FROM memories WHERE layer=? AND status='active'", (layer,)
            )
            if row and row["c"] > limit:
                excess = row["c"] - limit
                victims = self.db.query(
                    "SELECT id FROM memories WHERE layer=? AND status='active' "
                    "AND protected=0 AND pinned=0 ORDER BY recall_count ASC, created ASC LIMIT ?",
                    (layer, excess),
                )
                for v in victims:
                    self.db.update_memory(v["id"], status="dormant")
                    stat["trimmed"] += 1

        # ④ 沉降：老且没人碰的记忆转 dormant。
        # 这一步以前只有参数，没有任务 —— 遗忘从来没真发生。
        try:
            st = settle(self.db, now=now)
            stat["settled"] = st.get("settled", 0)
            stat["settle_days"] = st.get("days")
        except Exception:
            stat["settled"] = 0
        return stat

    # ---------- 用户手动操作 ----------
    def forget(self, mid, mode="cold"):
        """mode: mute（静音）/ cold（冷存）/ delete（真删）"""
        if mode == "delete":
            self.db.update_memory(mid, status="archived", content="[已删除]")
        elif mode == "cold":
            self.db.update_memory(mid, status="dormant")
        else:
            self.db.update_memory(mid, pinned=0, status="active", half_life_days=1.0)
        return True

    def pin(self, mid, value=True):
        mem = self.db.get_memory(mid)
        if not mem:
            return False
        self.db.update_memory(mid, pinned=1 if value else 0)
        return True
