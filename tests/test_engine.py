# -*- coding: utf-8 -*-
"""engine 集成测试：after_turn 写入、prepare_context 注入、status 统计"""
from pathlib import Path

import pytest

from hippocampus import config


# ---------------------------------------------------------------- 写入
def test_after_turn_adds_memory(engine):
    """空库跑一轮对话 → memories 计数增加"""
    before = engine.status()["memories"]
    assert before == 0

    result = engine.after_turn(user_text="我在做一个记忆插件")
    assert result["memories"], "after_turn 应该至少写入一条记忆"

    after = engine.status()["memories"]
    assert after == before + 1
    assert engine.status()["by_layer"] == {"L1": 1}


def test_after_turn_pact_memory_is_L5(engine):
    """人格约定落进 L5，并且 protected"""
    result = engine.after_turn(user_text="以后别叫我老师，叫我小满就行")
    assert len(result["memories"]) == 1
    mem = engine.db.get_memory(result["memories"][0])
    assert mem["layer"] == "L5"
    assert mem["protected"] == 1
    assert mem["content"] == "以后别叫我老师，叫我小满就行"


def test_after_turn_creates_thread_for_real_content(engine):
    """有意义的内容会开一个 thread"""
    engine.after_turn(user_text="以后别叫我老师，叫我小满就行")
    assert engine.status()["threads"] == 1


def test_after_turn_records_persona_turn(engine):
    engine.after_turn(user_text="我在做一个记忆插件")
    assert engine.status()["persona"]["turns"] == 1


def test_after_turn_creates_exactly_one_loop(engine):
    """一句话里两个信号，只落 1 条未闭合事项"""
    result = engine.after_turn(user_text="我明天还得看下报表，先这样吧")
    assert len(result["loops"]) == 1
    assert engine.status()["open_loops"] == 1


def test_after_turn_assistant_text_also_ingested(engine):
    before = engine.status()["memories"]
    engine.after_turn(assistant_text="这个配置我已经改成 8080 端口了")
    assert engine.status()["memories"] == before + 1


def test_after_turn_empty_turn_writes_nothing(engine):
    result = engine.after_turn()
    assert result["memories"] == []
    assert engine.status()["memories"] == 0


# ---------------------------------------------------------------- 读回
def test_prepare_context_contains_identity_block(engine):
    """prepare_context 的文本里必须有「我是谁」或「我记得」"""
    text, detail = engine.prepare_context("记忆")
    assert isinstance(text, str)
    assert ("我是谁" in text) or ("我记得" in text)
    assert set(detail) == {"memories", "tokens", "loops"}


def test_prepare_context_injects_written_memory(engine):
    engine.after_turn(user_text="我在做一个记忆插件")
    text, detail = engine.prepare_context("记忆")
    assert "记忆插件" in text
    assert detail["memories"] >= 1
    assert detail["tokens"] > 0


def test_prepare_context_empty_db_has_persona_only(engine):
    text, detail = engine.prepare_context("随便聊聊")
    assert "我是谁" in text
    assert detail["memories"] == 0


def test_recall_returns_written_memory(engine):
    engine.after_turn(user_text="以后别叫我老师，叫我小满就行")
    hits = engine.recall("小满")
    assert hits
    assert hits[0]["layer"] == "L5"
    assert "小满" in hits[0]["text"]


def test_edit_memory(engine):
    result = engine.after_turn(user_text="以后别叫我老师，叫我小满就行")
    mid = result["memories"][0]
    assert engine.edit_memory(mid, content="叫我小满") is True
    assert engine.db.get_memory(mid)["content"] == "叫我小满"


def test_pin_and_forget(engine):
    result = engine.after_turn(user_text="我在做一个记忆插件")
    mid = result["memories"][0]
    assert engine.pin(mid, True) is True
    assert engine.db.get_memory(mid)["pinned"] == 1
    assert engine.forget(mid, "cold") is True
    assert engine.db.get_memory(mid)["status"] == "dormant"
    assert engine.status()["memories"] == 0        # dormant 不计入 active


def test_checklist_renders_open_loop(engine):
    engine.after_turn(user_text="我明天还得看下报表，先这样吧")
    assert "未闭合清单" in engine.checklist()
    assert "报表" in engine.checklist()


def test_prepare_context_injects_at_most_one_loop(engine):
    """密度控制：库里两条 loop，每轮只提一条"""
    engine.db.add_loop("promise", "看报表", detail="原话一")
    engine.db.add_loop("todo", "改配置", detail="原话二")
    text, detail = engine.prepare_context("报表")
    assert len(detail["loops"]) == 1
    assert "还欠着的" in text


def test_prepare_context_does_not_repeat_same_loop(engine):
    engine.after_turn(user_text="我明天还得看下报表，先这样吧")
    first, _ = engine.prepare_context("随便")
    second, _ = engine.prepare_context("随便")
    assert "还欠着的" in first
    assert "还欠着的" not in second


# ---------------------------------------------------------------- 已知 bug 捕获
def test_remember_explicit(engine):
    mid = engine.remember("我的项目路径是 D:\\work\\tan", layer="L2")
    assert engine.db.get_memory(mid)["content"]


def test_status_reports_instance_db_path(engine, tmp_path):
    assert Path(engine.status()["db_path"]) == tmp_path / "engine.db"


# ---------------------------------------------------------------- 人格（写侧依赖）
def test_persona_detect_preferences_address():
    from hippocampus.persona import detect_preferences

    got = detect_preferences("以后别叫我老师，叫我小满就行")
    assert got
    kind, text, why = got[0]
    assert kind == "pact"
    assert "小满" in text or "别叫我老师" in text
    assert why


def test_persona_detect_preferences_speech():
    from hippocampus.persona import detect_preferences

    got = detect_preferences("说话简短点，别啰嗦")
    assert got and got[0][0] == "speech"


def test_persona_detect_preferences_empty():
    from hippocampus.persona import detect_preferences

    assert detect_preferences("") == []


def test_persona_prompt_block_has_default_core(engine):
    block = engine.persona.prompt_block()
    assert "## 我是谁" in block
    assert "底色：" in block
