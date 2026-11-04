# -*- coding: utf-8 -*-
"""数据库迁移：让 schema 能升级，不用删库

用 SQLite 内置的 PRAGMA user_version 记录版本号。
每次启动时检查，低于当前版本就逐步跑迁移。

以后加字段只做两件事：
  1. SCHEMA_VERSION 加 1
  2. MIGRATIONS 里加一条对应版本的 SQL

老用户的库会自动升级，数据不丢。
"""
import sqlite3
import time

SCHEMA_VERSION = 2

# 版本号 → [(SQL, 说明), ...]
# 注意：SQL 要写成可重复执行的（用 try 兜住 ALTER 已存在的情况）
MIGRATIONS = {
    # v1 = 最初版本，没有迁移
    1: [],

    # v2：记忆支持记录「最后一次手动修改时间」
    #     以前只有 created，改过内容后看不出来
    2: [
        ("ALTER TABLE memories ADD COLUMN updated REAL", "memories 加 updated"),
        ("CREATE INDEX IF NOT EXISTS idx_mem_updated ON memories(updated)",
         "updated 加索引"),
    ],
}


def get_version(conn):
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    except Exception:
        return 0


def set_version(conn, v):
    conn.execute("PRAGMA user_version = %d" % int(v))


def migrate(conn, verbose=True):
    """把库升到 SCHEMA_VERSION，返回做了哪些事"""
    done = []
    cur = get_version(conn)

    if cur == 0:
        # 全新的库，或很早以前建的（没有版本号）
        # 判断是不是新库：看有没有 memories 表
        has_table = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()[0]
        if not has_table:
            # 真·新库：建表后直接标成最新版
            set_version(conn, SCHEMA_VERSION)
            conn.commit()
            return ["新库，直接标为 v%d" % SCHEMA_VERSION]
        # 老库（没有版本号）：当成 v1 处理，继续往下跑
        cur = 1
        set_version(conn, 1)
        done.append("老库识别为 v1")

    if cur >= SCHEMA_VERSION:
        return done

    for v in range(cur + 1, SCHEMA_VERSION + 1):
        steps = MIGRATIONS.get(v, [])
        for sql, desc in steps:
            try:
                conn.execute(sql)
                done.append("v%d: %s" % (v, desc))
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    done.append("v%d: %s（已存在，跳过）" % (v, desc))
                else:
                    done.append("v%d: %s 失败 → %s" % (v, desc, e))
        set_version(conn, v)
        conn.commit()

    if verbose and done:
        print("[migrate] 数据库升级到 v%d：%s" % (SCHEMA_VERSION, "；".join(done)))
    return done


def ensure(conn):
    """启动时调用一次，静默处理异常（迁移失败不能挡住服务启动）"""
    try:
        return migrate(conn, verbose=False)
    except Exception:
        return []
