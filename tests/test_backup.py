# -*- coding: utf-8 -*-
"""backup 测试：魔数、往返解析、SHA256 防篡改、非法输入"""
import hashlib
import json
import struct
from pathlib import Path

import pytest

from hippocampus import backup
from hippocampus.db import DB

MAGIC = b"TANBAK01"


def _make_file(payload: bytes, meta: dict | None = None) -> bytes:
    """按格式规范手搓一个备份文件，用于构造非法/边界输入"""
    meta_bytes = json.dumps(meta or {"app": "tan", "format": 1}).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return MAGIC + struct.pack("<I", len(meta_bytes)) + digest + meta_bytes + payload


# ---------------------------------------------------------------- 导出
def test_export_magic_prefix(db, db_path):
    db.add_memory(layer="L2", content="备份里的一条记忆")
    data = backup.export_bytes(db_path)
    assert isinstance(data, bytes)
    assert data[:8] == MAGIC
    assert len(data) > 44


def test_export_missing_db_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.export_bytes(tmp_path / "not-here.db")


def test_export_meta_fields(db, db_path):
    db.add_memory(layer="L2", content="一条记忆")
    data = backup.export_bytes(db_path, stats={"memories": 1})
    meta, payload = backup.parse(data)
    assert meta["app"] == "tan"
    assert meta["format"] == 1
    assert meta["db_bytes"] == len(payload)
    assert meta["stats"]["memories"] == 1
    assert "exported_at_str" in meta


# ---------------------------------------------------------------- 往返
def test_export_then_parse_roundtrip(db, db_path):
    """export_bytes → parse 必须还原出同样的 payload"""
    db.add_memory(layer="L2", content="往返测试")
    data = backup.export_bytes(db_path)
    meta, payload = backup.parse(data)
    assert payload == Path(db_path).read_bytes()
    assert meta["db_bytes"] == len(payload)
    assert len(data) == 44 + len(json.dumps(meta, ensure_ascii=False).encode("utf-8")) + len(payload)


def test_parse_accepts_bytearray(db, db_path):
    data = backup.export_bytes(db_path)
    meta, payload = backup.parse(bytearray(data))
    assert payload == Path(db_path).read_bytes()


def test_inspect_returns_summary(db, db_path):
    db.add_memory(layer="L2", content="看一眼元信息")
    info = backup.inspect(backup.export_bytes(db_path, stats={"memories": 1, "threads": 0}))
    assert info["ok"] is True
    assert info["payload_bytes"] > 0
    assert "记忆 1 条" in info["summary"]


# ---------------------------------------------------------------- 防篡改
def test_tampered_payload_byte_raises(db, db_path):
    """改掉 payload 里一个字节 → SHA256 校验必须拦住"""
    db.add_memory(layer="L2", content="要被改坏的备份")
    data = backup.export_bytes(db_path)
    bad = bytearray(data)
    bad[-1] ^= 0xFF
    with pytest.raises(ValueError) as exc:
        backup.parse(bytes(bad))
    assert "SHA256" in str(exc.value)
    # 改中间一个字节也一样
    bad2 = bytearray(data)
    bad2[len(data) // 2] ^= 0x01
    with pytest.raises(ValueError):
        backup.parse(bytes(bad2))


def test_truncated_file_raises(db, db_path):
    data = backup.export_bytes(db_path)
    with pytest.raises(ValueError) as exc:
        backup.parse(data[:60])
    assert "截断" in str(exc.value) or "长度" in str(exc.value)


def test_valid_payload_but_broken_meta_json_raises():
    """payload 摘要对得上，但元信息不是 JSON → 也要报 ValueError"""
    payload = b"SQLite format 3\x00" + b"\x00" * 100
    # 破坏 meta 区（digest 只校验 payload，所以只能靠 JSON 解析拦）
    meta_bytes = b"{not json"
    data = MAGIC + struct.pack("<I", len(meta_bytes)) + hashlib.sha256(payload).digest() + meta_bytes + payload
    with pytest.raises(ValueError) as exc:
        backup.parse(data)
    assert "元信息" in str(exc.value)


# ---------------------------------------------------------------- 非法输入
@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"x" * 43, id="too-short"),
        pytest.param(b"NOTMAGI1" + b"\x00" * 100, id="wrong-magic"),
        pytest.param(MAGIC + b"\x00" * 36, id="zero-meta-len"),
        pytest.param(MAGIC + struct.pack("<I", 999) + b"\x00" * 32, id="meta-len-beyond-eof"),
        pytest.param(MAGIC + struct.pack("<I", 0xFFFFFFFF) + b"\x00" * 32, id="meta-len-too-big"),
        pytest.param("这是字符串不是字节", id="str-input"),
        pytest.param(12345, id="int-input"),
        pytest.param(None, id="none-input"),
    ],
)
def test_parse_invalid_input_raises_value_error(bad):
    with pytest.raises(ValueError):
        backup.parse(bad)


# ---------------------------------------------------------------- 导入
def test_import_restores_into_new_db(db, db_path, tmp_path):
    mid = db.add_memory(layer="L2", content="导入后还在的记忆")
    data = backup.export_bytes(db_path)
    dst = tmp_path / "restored.db"
    res = backup.import_bytes(data, dst, backup_dir=tmp_path / "bk")
    assert res["ok"] is True
    assert res["restored_bytes"] == len(backup.parse(data)[1])
    db2 = DB(dst)
    assert db2.get_memory(mid)["content"] == "导入后还在的记忆"


def test_import_over_existing_db_keeps_backup(db, db_path, tmp_path):
    db.add_memory(layer="L2", content="旧库里的")
    data = backup.export_bytes(db_path)
    dst = tmp_path / "target.db"
    DB(dst)                                  # 先有一个旧库
    res = backup.import_bytes(data, dst, backup_dir=tmp_path / "bk")
    assert res["backup_of_previous"]
    assert Path(res["backup_of_previous"]).exists()


def test_import_rejects_non_sqlite_payload(tmp_path):
    data = _make_file(b"this is not a database")
    with pytest.raises(ValueError):
        backup.import_bytes(data, tmp_path / "nope.db", backup_dir=tmp_path / "bk")
    assert not (tmp_path / "nope.db").exists()
