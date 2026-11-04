# -*- coding: utf-8 -*-
"""retriever 测试：分词、衰减、相关度、召回"""
import time

import pytest

from hippocampus import retriever
from hippocampus.retriever import Retriever, decay, relevance_of, tokenize


def _mem(days_ago: float, half_life=3.0, **kw):
    """构造一条 N 天前写入的记忆字典"""
    base = {
        "created": time.time() - days_ago * 86400.0,
        "half_life_days": half_life,
        "protected": 0,
        "pinned": 0,
        "content": "",
        "summary": "",
        "entities": [],
        "layer": "L1",
        "emotion": 1.0,
        "confidence": 0.7,
        "recall_count": 0,
        "reject_count": 0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- tokenize
def test_tokenize_drops_stopwords():
    """「今天/怎么」这类泛词必须被过滤，实词要留下"""
    terms = tokenize("今天天气怎么样")
    assert "今天" not in terms
    assert "怎么" not in terms
    assert "天气" in terms


def test_tokenize_no_empty_terms():
    assert all(t.strip() for t in tokenize("今天天气怎么样"))


def test_tokenize_empty_query():
    assert tokenize("") == []
    assert tokenize(None) == []


def test_tokenize_english_and_stopwords():
    terms = tokenize("帮我查一下 memory plugin")
    assert "帮我" not in terms
    assert "memory" in terms
    assert "plugin" in terms


# ---------------------------------------------------------------- decay
def test_decay_monotonic_and_non_negative():
    """天数越大衰减越小，且永远不为负"""
    values = [decay(_mem(d)) for d in (0, 1, 3, 7, 30, 365)]
    assert all(v >= 0.0 for v in values)
    assert all(a > b for a, b in zip(values, values[1:]))


def test_decay_fresh_memory_near_one():
    assert decay(_mem(0)) == pytest.approx(1.0, abs=1e-3)


def test_decay_matches_half_life():
    """3 天半衰期 → 3 天后约 e^-1"""
    assert decay(_mem(3, 3.0)) == pytest.approx(0.3679, abs=0.01)


def test_decay_protected_and_pinned_never_decay():
    old = time.time() - 3650 * 86400.0
    assert decay({"created": old, "half_life_days": 1.0, "protected": 1}) == 1.0
    assert decay({"created": old, "half_life_days": 1.0, "pinned": 1}) == 1.0


def test_decay_half_life_floor():
    """half_life_days 为 0/None 时不应出现除零或负数"""
    v = decay({"created": time.time() - 86400, "half_life_days": 0, "protected": 0, "pinned": 0})
    assert 0.0 < v < 1.0


# ---------------------------------------------------------------- relevance_of
def test_relevance_zero_for_unrelated():
    mem = _mem(1, content="我明天得看报表", summary="我明天得看报表")
    assert relevance_of(mem, tokenize("推荐一部电影")) == 0.0


def test_relevance_positive_for_hit():
    mem = _mem(1, content="我在做一个记忆插件", summary="记忆插件")
    assert relevance_of(mem, tokenize("记忆插件")) > 0.0


# ---------------------------------------------------------------- recall
def test_recall_empty_db_returns_no_results(db):
    """空库里查任何东西都是 0 条"""
    r = Retriever(db)
    assert r.recall("推荐一部电影") == []
    assert r.recall("") == []


def test_recall_prefers_relevant_memory(db):
    hit = db.add_memory(layer="L2", content="我在做一个记忆插件，用 SQLite 存")
    db.add_memory(layer="L2", content="周末去爬山，带相机")
    picked = Retriever(db).recall("记忆插件", limit=5)
    assert picked
    assert picked[0]["id"] == hit


def test_recall_unrelated_query_drops_plain_memory(db):
    db.add_memory(layer="L2", content="周末去爬山，带相机")
    assert Retriever(db).recall("推荐一部电影") == []


def test_recall_skips_secret_sensitivity(db):
    mid = db.add_memory(layer="L2", content="密码是 abc123", sensitivity="secret")
    picked = Retriever(db).recall("密码")
    assert all(m["id"] != mid for m in picked)


def test_recall_respects_limit(db):
    for i in range(6):
        db.add_memory(layer="L2", content=f"记忆插件第{i}版说明")
    picked = Retriever(db).recall("记忆插件", limit=2)
    assert 0 < len(picked) <= 2


def test_build_context_renders_memory(db):
    db.add_memory(layer="L5", content="我是小满")
    text, used, tokens = Retriever(db).build_context("小满")
    assert "我是小满" in text
    assert used
    assert tokens > 0


def test_build_context_empty_db(db):
    text, used, tokens = Retriever(db).build_context("随便问问")
    assert text == ""
    assert used == []
    assert tokens == 0
