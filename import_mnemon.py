# -*- coding: utf-8 -*-
"""从 mnemon 导入已有记忆"""
import json
import sqlite3
import sys
import time
from pathlib import Path

from hippocampus import Hippocampus, config

MNEMON_DB = Path.home() / ".mnemon" / "data" / "default" / "mnemon.db"

CAT_TO_LAYER = {
    "fact": "L2",
    "insight": "L2-",     # 洞察/教训 → 反例层
    "context": "L2",
    "decision": "L2",
    "preference": "L2",
}


def main():
    if not MNEMON_DB.exists():
        print(f"找不到 mnemon 数据库：{MNEMON_DB}")
        return

    con = sqlite3.connect(str(MNEMON_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, content, category, importance, tags, entities, stored_at, created_at "
        "FROM insights WHERE deleted_at IS NULL"
    ).fetchall()
    con.close()

    print(f"mnemon 里有 {len(rows)} 条记忆\n")

    h = Hippocampus()
    imported = 0
    for r in rows:
        try:
            tags = json.loads(r["tags"] or "[]")
        except Exception:
            tags = []
        try:
            ents = json.loads(r["entities"] or "[]")
        except Exception:
            ents = []
        layer = CAT_TO_LAYER.get(r["category"], "L2")
        ts = r["created_at"] or r["stored_at"]
        if isinstance(ts, str):
            try:
                ts = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                ts = time.time()

        mid = h.db.add_memory(
            layer=layer,
            content=r["content"],
            summary=(r["content"] or "")[:70],
            fidelity="summary",
            source="S1",
            created=float(ts or time.time()),
            protected=True if layer in ("L3", "L4", "L5", "L2-") else False,
            entities=ents[:20],
            tags=(tags or []) + ["migrated"],
        )
        imported += 1
        print(f"  ✅ [{layer}] {(r['content'] or '')[:40]}…")

    print(f"\n导入完成：{imported} 条")
    print(f"新库位置：{config.DB_PATH}")


if __name__ == "__main__":
    main()
