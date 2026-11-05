# -*- coding: utf-8 -*-
"""Tan 记忆备份：单文件导出 / 导入

文件格式（.tanbak）——纯二进制，单文件，方便存 U 盘、发微信、传网盘：

    偏移   长度   内容
    0      8      魔数 "TANBAK01"
    8      4      元信息长度 N（小端 uint32）
    12     32     payload 的 SHA256（32 字节原始摘要）
    44     N      元信息 JSON（UTF-8）
    44+N   ...    memory.db 的原始字节

导入时会：
    1. 校验魔数 / 版本 / SHA256 —— 文件损坏能立刻发现
    2. 把当前库备份到 backup/before-import-<时间>.db
    3. 用新库替换
"""
import hashlib
import json
import os
import shutil
import struct
import time
from pathlib import Path

MAGIC = b"TANBAK01"
SUFFIX = ".tanbak"
MAX_META = 1024 * 1024  # 元信息最多 1MB，防畸形文件


# ---------------------------------------------------------------- 导出
def export_bytes(db_path, stats=None, config_view=None):
    """把数据库打包成单文件字节流"""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{db_path}")

    payload = db_path.read_bytes()
    digest = hashlib.sha256(payload).digest()

    meta = {
        "app": "tan",
        "format": 1,
        "exported_at": time.time(),
        "exported_at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "db_bytes": len(payload),
        "stats": stats or {},
        "config": config_view or {},
    }
    meta_bytes = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    if len(meta_bytes) > MAX_META:
        raise ValueError("元信息过大")

    return MAGIC + struct.pack("<I", len(meta_bytes)) + digest + meta_bytes + payload


def default_name():
    return "tan-" + time.strftime("%Y%m%d-%H%M") + SUFFIX


# ---------------------------------------------------------------- 解析
def parse(data):
    """解析单文件 → (meta, payload)"""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("不是字节流")
    data = bytes(data)
    if len(data) < 44:
        raise ValueError("文件太小，不是有效的备份")
    if data[:8] != MAGIC:
        raise ValueError("文件头不对——这不是 Tan 的备份文件")

    meta_len = struct.unpack("<I", data[8:12])[0]
    if meta_len <= 0 or meta_len > MAX_META:
        raise ValueError("元信息长度异常，文件可能损坏")

    digest = data[12:44]
    if len(data) < 44 + meta_len:
        raise ValueError("文件被截断")

    meta_bytes = data[44:44 + meta_len]
    payload = data[44 + meta_len:]

    if hashlib.sha256(payload).digest() != digest:
        raise ValueError("SHA256 校验失败——文件损坏或被改过")

    try:
        meta = json.loads(meta_bytes.decode("utf-8"))
    except Exception:
        raise ValueError("元信息解析失败")

    return meta, payload


def inspect(data):
    """只读元信息，不落盘（导入前给用户确认用）"""
    meta, payload = parse(data)
    return {
        "ok": True,
        "meta": meta,
        "payload_bytes": len(payload),
        "summary": _summary(meta),
    }


def _summary(meta):
    st = meta.get("stats") or {}
    parts = [
        f"导出时间 {meta.get('exported_at_str', '?')}",
        f"记忆 {st.get('memories', '?')} 条",
        f"Thread {st.get('threads', '?')} 个",
    ]
    p = st.get("persona") or {}
    if p.get("stage"):
        parts.append(f"人格「{p['stage']}」{p.get('turns', 0)} 轮")
    parts.append(f"大小 {round(meta.get('db_bytes', 0) / 1024, 1)} KB")
    return " · ".join(parts)


# ---------------------------------------------------------------- 导入
def import_bytes(data, db_path, backup_dir=None):
    """校验 → 备份当前 → 替换 → 返回结果"""
    meta, payload = parse(data)
    db_path = Path(db_path)

    # 落盘前先确认 payload 是完整的 SQLite 文件
    if not payload.startswith(b"SQLite format 3\x00"):
        raise ValueError("内嵌的数据库不是 SQLite 文件，拒绝导入")

    backup_dir = Path(backup_dir or (db_path.parent / "backup"))
    backup_dir.mkdir(parents=True, exist_ok=True)

    before = None
    if db_path.exists():
        before = backup_dir / ("before-import-" + time.strftime("%Y%m%d-%H%M%S") + ".db")
        shutil.copy2(db_path, before)

    tmp = db_path.with_suffix(".importing")
    tmp.write_bytes(payload)
    os.replace(tmp, db_path)

    # 顺带把 wal/shm 清掉，避免旧的事务日志干扰
    for ext in ("-wal", "-shm"):
        p = Path(str(db_path) + ext)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    return {
        "ok": True,
        "meta": meta,
        "summary": _summary(meta),
        "restored_bytes": len(payload),
        "backup_of_previous": str(before) if before else None,
    }


# ---------------------------------------------------------------- 本地备份
def list_local(db_dir):
    """列出 backup/ 下的本地备份"""
    d = Path(db_dir) / "backup"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
        st = f.stat()
        out.append({
            "name": f.name,
            "path": str(f),
            "bytes": st.st_size,
            "mtime": st.st_mtime,
            "mtime_str": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
        })
    return out


def make_local_backup(db_path, prefix="manual", keep=7):
    """在 backup/ 下生成一个带时间戳的副本

    keep: 只留最近几个自动备份，不然 3MB × 每天会把盘塞满。
    """
    db_path = Path(db_path)
    d = db_path.parent / "backup"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / (prefix + "-" + time.strftime("%Y%m%d-%H%M%S") + ".db")
    shutil.copy2(db_path, dst)

    # 只清自动备份，manual- 和别的历史文件不动
    try:
        autos = sorted([p for p in d.glob("auto-*.db")], key=lambda p: p.stat().st_mtime)
        for old_file in autos[:-keep]:
            old_file.unlink(missing_ok=True)
    except Exception:
        pass

    return {"ok": True, "path": str(dst), "bytes": dst.stat().st_size}
