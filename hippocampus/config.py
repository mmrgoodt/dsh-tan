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
CONCLUSION_BOOST = 1.6     # 提炼过的结论，压在原始碎片之上
LAYER_BOOST = {"L4": 1.6, "L5": 1.6, "L3": 1.4, "L2-": 1.35, "L2": 1.0, "L1": 0.9}

# 初始半衰期（天）
HALF_LIFE = {"L1": 3.0, "L2": 180.0, "L2-": 360.0, "L3": 9999.0, "L4": 99999.0, "L5": 99999.0}

# ---------- 可信度 ----------
SOURCE_CONF = {"S1": 1.00, "S2": 0.95, "S3": 0.45, "S4": 0.60, "S5": 0.40}
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
INJECT_MAX_ITEMS = 6      # 一屏最多注入几条，宁可少说别塞废话
INJECT_LINKED_MAX = 2     # 其中最多几条是「顺带想起」的
INJECT_IDENTITY_QUOTA = 0 # 身份层固定名额。0 = 完全按相关度，不搞「每条查询先塞两条性格」     # 其中最多几条是「顺带想起」的      # 一屏最多注入几条，宁可少说别塞废话
RECALL_CANDIDATES = 10   # 候选越多，装进正文的噪音越多
INJECT_TOP_N = 8

# ---------- 意图检索（抽象问句） ----------
# 人问「某类事」，库里存「具体事」，词面对不上。retriever 按意图换一条检索路径，
# 把这些补充候选和词面结果合并去重后再一起排序。
INTENT_ENABLED = True
INTENT_SUPPLEMENT_MAX = 6      # 一条查询最多补几条意图候选（多类合并后的总上限）
INTENT_REL_FLOOR = 0.30        # 补充进来的记忆给个相关度地板，否则 rel=0 直接被门槛筛掉
INTENT_REL_CAP = 0.45          # 地板也有上限：不能反客为主压过真正的字面命中
INTENT_MERGE_RATIO = 0.95      # 合并排序时，补充候选的总分最多到「最好的词面命中」的 95%
INTENT_LIST_EXTRA = 6          # 清单型（都 / 哪些 / 有什么）多给几条候选

# ---------- 检索缓存（把 /context 压到几十毫秒） ----------
TOKENIZE_CACHE = 512           # 分词结果按 query 缓存
JSON_CACHE = 8192              # 记忆里 entities / provenance 的 json.loads 缓存
IDF_CACHE = 4096               # 每个词的 df（出现它的记忆条数）
CORTEX_CACHE_TTL = 90.0        # 概念激活结果的短时缓存（秒）；有写入时立刻作废
CORTEX_CACHE_SIZE = 64

# ---------- 检索用索引 ----------
# 由 Retriever 初始化时补上（CREATE INDEX IF NOT EXISTS，幂等）。
# 这三条是 EXPLAIN QUERY PLAN 实测出来的：edges 的邻居查询原本是 SCAN edges，
# events 的「今天提过归纳没有」原本也是整表扫。
EXTRA_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_tgt ON edges(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts)",
)

# ---------- 遗忘 ----------
REINFORCE_FACTOR = 1.8      # 每次被用，半衰期 ×1.8
REJECT_FACTOR = 0.5         # 被否定，半衰期 ×0.5
MAX_HALF_LIFE = 180.0       # 上限
DORMANT_DECAY = 0.05        # 低于此值转 dormant
NOISE_ARCHIVE_DAYS = 3
SUNSET_DAYS = 90            # 90 天未召回视为沉睡

# ---------- 沉降（settle） ----------
# 遗忘以前只有参数没有任务，从来不真发生。沉降是把「老 + 没人碰」的记忆
# 从 active 改 dormant（不删），把注入预算让给还活着的事。
SETTLE_DAYS = 14            # N 天：created 超过 N 天，且 last_recalled 为空或也超过 N 天
SETTLE_MAX_RECALL = 2       # 召回次数 ≤ 这个数才算「没人用」
SETTLE_BATCH = 2000         # 一次最多扫多少条，防止大库卡住

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
