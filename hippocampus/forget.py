# -*- coding: utf-8 -*-
"""遗忘：衰减检查、三出口、容量控制"""
import time

from . import config


class Forgetter:
    def __init__(self, db):
        self.db = db

    def sweep(self):
        """定期跑一次：噪音归档、自然沉降、容量控制"""
        stat = {"archived_noise": 0, "dormant": 0, "trimmed": 0}
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
