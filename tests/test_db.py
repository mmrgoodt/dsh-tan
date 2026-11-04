# -*- coding: utf-8 -*-
"""db 层测试：建表、增查改、统计"""
from pathlib import Path

import pytest

from hippocampus import config
from hippocampus.db import DB, new_id

REQUIRED_TABLES = {
    "memories",
    "threads",
    "open_loops",
    "edges",
    "persona",
    "golden_cases",
}


# ---------------------------------------------------------------- 建库
def test_tables_exist_after_init(db):
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert REQUIRED_TABLES <= names


def test_indexes_exist(db):
    names = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_mem_layer" in names
    assert "idx_mem_status" in names


def test_db_file_is_in_tmp(db, tmp_path):
    """测试库必须落在 tmp_path 里，不是真实库"""
    assert Path(db.path).parent == tmp_path


def test_init_is_idempotent(db_path):
    """重复打开同一个库不会重复插入 persona 行"""
    DB(db_path)
    d2 = DB(db_path)
    assert d2.one("SELECT COUNT(*) c FROM persona")["c"] == 1


def test_new_id_prefix():
    mid = new_id("lp")
    assert mid.startswith("lp-")


# ---------------------------------------------------------------- 记忆 CRUD
def test_add_memory_then_get_by_id(db):
    mid = db.add_memory(
        layer="L2",
        content="以后别叫我老师",
        source="S1",
        entities=["tan"],
        tags=["pref"],
    )
    m = db.get_memory(mid)
    assert m is not None
    assert m["id"] == mid
    assert m["content"] == "以后别叫我老师"
    assert m["layer"] == "L2"
    assert m["source"] == "S1"
    assert m["status"] == "active"
    assert m["summary"] == "以后别叫我老师"
    assert m["entities"] == ["tan"]        # JSON 字段已解码成 list
    assert m["tags"] == ["pref"]
    assert m["tokens"] > 0
    assert 0 < m["confidence"] <= 1


def test_add_memory_uses_layer_defaults(db):
    mid = db.add_memory(layer="L5", content="我是小满")
    m = db.get_memory(mid)
    assert m["half_life_days"] == config.HALF_LIFE["L5"]
    assert m["protected"] == 1              # L5 在保护伞里


def test_add_memory_short_summary_default(db):
    text = "这是一段超过六十个字符的很长很长的记忆内容" * 3
    mid = db.add_memory(layer="L2", content=text)
    assert len(db.get_memory(mid)["summary"]) <= 60


def test_get_memory_missing_returns_none(db):
    assert db.get_memory("m-not-a-real-id") is None


def test_update_memory_content(db):
    mid = db.add_memory(layer="L2", content="旧内容")
    db.update_memory(mid, content="新内容")
    m = db.get_memory(mid)
    assert m["content"] == "新内容"
    assert m["id"] == mid


def test_update_memory_multiple_fields(db):
    mid = db.add_memory(layer="L2", content="旧内容")
    db.update_memory(mid, content="新内容", status="dormant", pinned=1)
    m = db.get_memory(mid)
    assert m["content"] == "新内容"
    assert m["status"] == "dormant"
    assert m["pinned"] == 1


def test_update_memory_without_fields_is_noop(db):
    mid = db.add_memory(layer="L2", content="旧内容")
    db.update_memory(mid)                    # 不应该抛异常
    assert db.get_memory(mid)["content"] == "旧内容"


def test_update_memory_unknown_id_does_not_raise(db):
    db.update_memory("m-missing", content="无所谓")
    assert db.get_memory("m-missing") is None


# ---------------------------------------------------------------- 统计 / thread / loop
def test_stats_counts_active_memories(db):
    db.add_memory(layer="L2", content="第一条记忆")
    db.add_memory(layer="L2", content="第二条记忆")
    db.add_memory(layer="L1", content="第三条记忆")
    st = db.stats()
    assert st["memories"] == 3
    assert st["by_layer"] == {"L2": 2, "L1": 1}
    assert st["open_loops"] == 0


def test_stats_ignores_dormant(db):
    mid = db.add_memory(layer="L2", content="会被冷存的")
    db.add_memory(layer="L2", content="留下的")
    db.update_memory(mid, status="dormant")
    assert db.stats()["memories"] == 1


def test_thread_roundtrip(db):
    tid = db.add_thread("写测试", kind="task", anchors=["pytest"])
    row = db.one("SELECT * FROM threads WHERE id=?", (tid,))
    assert row["title"] == "写测试"
    assert row["status"] == "doing"
    db.close_thread(tid, summary="做完了")
    row = db.one("SELECT * FROM threads WHERE id=?", (tid,))
    assert row["status"] == "done"
    assert row["summary"] == "做完了"


def test_find_thread_by_anchor(db):
    tid = db.add_thread("写测试", anchors=["pytest"])
    found = db.find_thread_by_anchor("今天用 pytest 跑一下")
    assert found and found["id"] == tid
    assert db.find_thread_by_anchor("完全无关的一句话") is None


def test_open_loops_roundtrip(db):
    lid = db.add_loop("promise", "明天看报表", detail="原话")
    loops = db.open_loops()
    assert [lp["id"] for lp in loops] == [lid]
    assert loops[0]["kind"] == "promise"
    db.execute("UPDATE open_loops SET status='done' WHERE id=?", (lid,))
    assert db.open_loops() == []


def test_persona_row_is_single_and_decoded(db):
    p = db.persona()
    assert p["id"] == 1
    assert isinstance(p["core"], list)
    assert isinstance(p["mood"], dict)
    db.save_persona(name="小满", core=["温和"])
    p = db.persona()
    assert p["name"] == "小满"
    assert p["core"] == ["温和"]


def test_link_upsert_edge(db):
    db.link("m-1", "m-2", "semantic", 0.5)
    db.link("m-1", "m-2", "semantic", 0.9)
    rows = db.query("SELECT * FROM edges WHERE source_id='m-1'")
    assert len(rows) == 1
    assert rows[0]["weight"] == 0.9


def test_persona_log_insert(db):
    db.persona_log("trait", "更直接", "两次信号")
    row = db.one("SELECT * FROM persona_log ORDER BY id DESC LIMIT 1")
    assert row["kind"] == "trait"
    assert row["text"] == "更直接"
