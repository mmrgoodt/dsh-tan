# -*- coding: utf-8 -*-
"""全局参数与常量"""
import os
from pathlib import Path

# ---------- 路径 ----------
HOME = Path(os.environ.get("USERPROFILE") or Path.home())
ROOT = Path(os.environ.get("TAN_HOME", HOME / ".dsh" / "tan"))
DB_PATH = ROOT / "memory.db"
RAW_DIR = ROOT / "raw"
BACKUP_DIR = ROOT / "backup"
SNAPSHOT_DIR = ROOT / "snapshots"
CONFIG_PATH = ROOT / "config.json"

for _d in (ROOT, RAW_DIR, BACKUP_DIR, SNAPSHOT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------- 记忆层 ----------
LAYERS = ("L1", "L2", "L2-", "L3", "L4", "L5")

LAYER_LABEL = {
    "L1": "情景（今天说了什么）",
    "L2": "语义（事实与偏好）",
    "L2-": "反例（什么不该再做）",
    "L3": "程序（怎么做某事）",
    "L4": "身份（你是谁）",
    "L5": "自我（我是谁）",
}

# 层加成（参与打分）
LAYER_BOOST = {"L4": 1.6, "L5": 1.6, "L3": 1.4, "L2-": 1.35, "L2": 1.0, "L1": 0.9}

# 初始半衰期（天）
HALF_LIFE = {"L1": 3.0, "L2": 180.0, "L2-": 360.0, "L3": 9999.0, "L4": 99999.0, "L5": 99999.0}

# ---------- 可信度 ----------
SOURCE_CONF = {"S1": 1.00, "S2": 0.95, "S3": 0.70, "S4": 0.60, "S5": 0.40}
SOURCE_LABEL = {
    "S1": "用户亲口",
    "S2": "工具实测",
    "S3": "我推断",
    "S4": "网上查",
    "S5": "我猜测",
}

# ---------- 保护伞（免衰减） ----------
PROTECTED_LAYERS = {"L3", "L4", "L5", "L2-"}

# ---------- 预算（token） ----------
BUDGET = {
    "identity": 200,   # L4 + L5 固定
    "procedure": 350,  # L3
    "counter": 150,    # L2-
    "semantic": 500,   # L2
    "episodic": 300,   # L1 + Thread 摘要
}
BUDGET_TOTAL = sum(BUDGET.values())  # 1500
SINGLE_ITEM_MAX = 200

# ---------- 检索 ----------
RECALL_CANDIDATES = 20
INJECT_TOP_N = 8

# ---------- 遗忘 ----------
REINFORCE_FACTOR = 1.8      # 每次被用，半衰期 ×1.8
REJECT_FACTOR = 0.5         # 被否定，半衰期 ×0.5
MAX_HALF_LIFE = 180.0       # 上限
DORMANT_DECAY = 0.05        # 低于此值转 dormant
NOISE_ARCHIVE_DAYS = 3
SUNSET_DAYS = 90            # 90 天未召回视为沉睡

# ---------- Open Loops ----------
LOOP_COOLDOWN_HOURS = 24
LOOPS_PER_TURN = 1

# ---------- 主动消息 ----------
PROACTIVE_MAX_PER_DAY = 4
PROACTIVE_QUIET_START = 23   # 23:00
PROACTIVE_QUIET_END = 7      # 07:00
PROACTIVE_SLOTS = [
    (8, 9, "morning"),
    (12, 13, "noon"),
    (19, 21, "evening"),
]
PROACTIVE_BACKOFF_DAYS = 3   # 连续 3 天没回 → 降到 1 条

# ---------- 进化 ----------
AUTO_TUNE_MAX_STEP = 0.20    # 单次调整幅度 ≤20%
AUTO_TUNE_MAX_PER_DAY = 1
GOLDEN_PASS_LINE = 16        # 20 题里至少 16

# ---------- 人格 ----------
PERSONA_STAGES = [
    (0, 20, "初识", "话少、观察、不主动开玩笑"),
    (20, 100, "熟悉", "找到节奏，开始有偏好"),
    (100, 500, "默契", "有判断，敢说'这个你不需要'"),
    (500, 10 ** 9, "老友", "有脾气，有默契，有共同的梗"),
]

# ---------- 时间模糊化 ----------
def fuzzy_time(days: float) -> str:
    if days < 0.5:
        return "今天"
    if days < 1.5:
        return "昨天"
    if days < 3:
        return "这两天"
    if days < 8:
        return "前几天"
    if days < 31:
        return "上个月"
    if days < 121:
        return "前阵子"
    return "挺久以前"


# ---------- 工具 ----------
def estimate_tokens(text: str) -> int:
    """粗略估算 token：中文按 1.6 字/token，英文按 4 字符/token"""
    if not text:
        return 0
    cn = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cn
    return int(cn / 1.6 + other / 4) + 1
