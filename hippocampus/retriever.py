# -*- coding: utf-8 -*-
"""检索与打分：召回、排序、预算注入"""
import functools
import json
import math
import re
import sqlite3
import threading
import time
import weakref

from . import config
from . import focus as focus_mod
from . import intent as intent_mod


# ---------------- 检索期缓存 ----------------
# /context 每轮对话前都要跑一次，宿主一个 turn 里还可能连调好几次。
# 分词、json 解码、df 统计、概念激活都是「同样的输入必然同样的输出」，
# 每轮重算纯属白烧 —— 实测这几项合起来占了原先延迟的一大半。
@functools.lru_cache(maxsize=8192)
def _json_parsed(raw, kind):
    """json.loads 的缓存版本。entities / provenance 一条记忆要解好几次"""
    if raw is None:
        return {} if kind == "dict" else []
    try:
        v = json.loads(raw)
    except Exception:
        return {} if kind == "dict" else []
    if kind == "dict":
        return v if isinstance(v, dict) else {}
    return v if isinstance(v, list) else []


def _decode_fast(row):
    """和 db._decode_memory 一个意思，只是走缓存、并把结果复制一份

    复制是必须的：缓存里的是共享对象，调用方会改 mem["entities"]。
    """
    if not row:
        return None
    row["entities"] = list(_json_parsed(row.get("entities"), "list"))
    row["tags"] = list(_json_parsed(row.get("tags"), "list"))
    row["provenance"] = dict(_json_parsed(row.get("provenance"), "dict"))
    return row


def _close_quietly(con):
    try:
        con.close()
    except Exception:
        pass


def _mem_text(mem):
    """正文（正文 + 摘要，小写），挂在 mem["_low"] 上复用

    一条记忆在一次查询里会被 bm25、相关度、focus 门槛、意图扫描轮流看，
    每次都重新拼字符串 + lower() 是纯浪费：431 条 × 4 次 × 1.4KB。
    """
    low = mem.get("_low")
    if low is None:
        low = ((mem.get("content") or "") + " " + (mem.get("summary") or "")).lower()
        mem["_low"] = low
    return low


def _days_since(ts):
    if not ts:
        return 0.0
    return max(0.0, (time.time() - ts) / 86400.0)


def decay(mem):
    """衰减系数 = exp(-Δt / 半衰期)"""
    if mem.get("pinned") or mem.get("protected"):
        return 1.0
    hl = max(0.5, float(mem.get("half_life_days") or 3.0))
    return math.exp(-_days_since(mem.get("created")) / hl)


def bm25_score(mem, query_terms, idf=None, avg_len=400, k1=1.4, b=0.72, term_w=None):
    """BM25：比「命中率」准得多

    k1 控制词频饱和，b 控制文档长度惩罚（0=不惩罚，1=完全惩罚）
    term_w: 逐词权重，用来给同义词扩展词降权
    """
    if not query_terms:
        return 0.0
    text = _mem_text(mem)
    if not text:
        return 0.0

    dl = len(text)
    score = 0.0
    for t in query_terms:
        tl = t.lower()
        if tl not in text:
            continue
        tf = text.count(tl)
        w = (idf or {}).get(tl, 1.0) * (term_w or {}).get(tl, 1.0)
        denom = tf + k1 * (1 - b + b * (dl / max(1, avg_len)))
        score += w * (tf * (k1 + 1)) / max(0.001, denom)

    # 归一到 0~1 区间（除以查询词数的一个经验上界）
    norm = max(1.0, len(query_terms) * 0.9)
    return min(1.0, score / norm)


def relevance_of(mem, query_terms, idf=None):
    """相关度：命中词按 IDF 加权——越少见的词权重越高，泛词几乎不加分"""
    if not query_terms:
        return 0.0
    text = _mem_text(mem)

    if idf:
        got = 0.0
        total = 0.0
        for t in query_terms:
            tl = t.lower()
            w = idf.get(tl, 1.0)
            total += w
            if tl in text:
                got += w
        rel = (got / total) if total > 0 else 0.0
    else:
        hit = sum(1 for t in query_terms if t and t.lower() in text)
        rel = hit / max(1, len(query_terms))
    ents = mem.get("entities") or []
    if isinstance(ents, str):
        try:
            ents = json.loads(ents)
        except Exception:
            ents = []
    q = " ".join(query_terms).lower()
    ent_hit = sum(1 for e in ents if e and e.lower() in q)
    if ent_hit:
        rel = min(1.0, rel + 0.3 * ent_hit)
    return rel


def score(mem, query_terms, now_terms=()):
    """综合打分"""
    text = (mem.get("content") or "") + " " + (mem.get("summary") or "")
    text_l = text.lower()

    # 相关度：命中词数 / 查询词数，实体命中加权
    rel = 0.0
    if query_terms:
        hit = sum(1 for term in query_terms if term and term.lower() in text_l)
        rel = hit / max(1, len(query_terms))
    ents = mem.get("entities") or []
    if isinstance(ents, str):
        try:
            ents = json.loads(ents)
        except Exception:
            ents = []
    ent_hit = sum(1 for e in ents if e.lower() in " ".join(query_terms).lower())
    if ent_hit:
        rel = min(1.0, rel + 0.35 * ent_hit)

    d = decay(mem)
    # 负面偏好：挨过骂、出过岔子的经历，比顺利的记得更牢。
    # 这是人和数据库的差别之一 —— 数据库对所有记录一视同仁。
    _val = float(mem.get("valence") or 0.0)
    emo = float(mem.get("emotion") or 1.0)
    if _val < -0.3:
        emo *= 1.4
    elif _val < -0.1:
        emo *= 1.15
    rc = int(mem.get("recall_count") or 0)
    reinforce = 1 + 0.2 * min(rc, 5)
    boost = config.LAYER_BOOST.get(mem.get("layer"), 1.0)
    # 提炼过的结论优先于原始碎片。
    # 结论天生是「综合的」，一条里讲好几件事，focus 天然比单点细节低，
    # 不额外加权的话永远被碎片压着——那就等于白巩固了。
    try:
        prov = mem.get("provenance") or {}
        if isinstance(prov, str):
            import json as _json
            prov = _json.loads(prov)
        if isinstance(prov, dict) and prov.get("by") == "llm":
            boost *= getattr(config, "CONCLUSION_BOOST", 1.6)
    except Exception:
        pass
    conf = float(mem.get("confidence") or 0.7)
    rej = int(mem.get("reject_count") or 0)
    reject_penalty = 1.0 / (1 + 0.5 * rej)

    return rel * d * emo * reinforce * boost * conf * reject_penalty


# ---------- 打分：相关度 × 时间衰减 × 重要度 ----------
# 召回以前只按一个综合数排，看不出「为什么这条排前面」。
# 现在拆成三段可解释的乘积，挂回 mem["score"] / mem["score_parts"]：
#   total = (0.15 + rel) × decay × importance
#   rel        —— 检索相关性（皮层激活 + 字面命中 + 主题集中度，召回里算好传进来）
#   decay      —— 这条自己的 half_life_days 做指数衰减；pinned / protected 不衰减
#   importance —— 层权重 × pinned × 保护 × recall_count × last_recalled 新近 × 被否定 × 置信
RECENCY_BONUS = 0.35    # last_recalled 是刚碰过时的最大加成
RECENCY_SCALE = 14.0    # 半个月不看，加成回落到 0
RECALL_STEP = 0.25      # 每次被召回加 25%，最多叠 8 次
RECALL_CAP = 8
PIN_BONUS = 1.25
PROTECT_BONUS = 1.10
REJECT_PENALTY = 0.6    # 每被否定一次，重要度除以 1+0.6n


def recency_of(mem, now=None):
    """last_recalled 越近加成越高；从没被召回的算 1.0（不奖不罚）"""
    last = mem.get("last_recalled")
    if not last:
        return 1.0
    idle = max(0.0, ((now or time.time()) - float(last)) / 86400.0)
    return 1.0 + RECENCY_BONUS * math.exp(-idle / RECENCY_SCALE)


def importance_of(mem, now=None):
    """重要度与使用：层权重、pinned、保护、召回次数、新近、被否定、置信

    返回 (值, 分项字典) —— 分项是为了能一眼看出某条为什么排前面。
    """
    layer = float(config.LAYER_BOOST.get(mem.get("layer"), 1.0))
    pin = PIN_BONUS if mem.get("pinned") else 1.0
    prot = PROTECT_BONUS if mem.get("protected") else 1.0
    rc = int(mem.get("recall_count") or 0)
    use = 1.0 + RECALL_STEP * min(rc, RECALL_CAP)
    rec = recency_of(mem, now)
    rej = 1.0 / (1.0 + REJECT_PENALTY * int(mem.get("reject_count") or 0))
    conf = float(mem.get("confidence") or 0.7)
    total = layer * pin * prot * use * rec * rej * conf
    return total, {
        "layer": round(layer, 4),
        "pin": pin,
        "protect": prot,
        "recall": round(use, 4),
        "recency": round(rec, 4),
        "reject": round(rej, 4),
        "confidence": round(conf, 4),
    }


def rank_score(mem, rel, now=None):
    """单条候选的总分（可解释）

    rel 由召回阶段算好（皮层激活 + 字面命中 + 主题集中度）。
    衰减用这条记忆自己的 half_life_days —— 情景记忆 3 天，身份层几乎不衰减。
    pinned / protected 由 decay() 直接给 1.0，不参与衰减。
    """
    rel = max(0.0, float(rel or 0.0))
    d = decay(mem)
    imp, parts = importance_of(mem, now)
    total = (0.15 + rel) * d * imp
    return {
        "total": total,
        "rel": round(rel, 6),
        "decay": round(d, 6),
        "importance": round(imp, 6),
        "parts": parts,
    }


# 高频虚词 / 泛词：几乎每条记忆都有，拿它们算相关度只会"什么都命中"
STOPWORDS = set("""
今天 明天 昨天 现在 刚才 时候 什么 怎么 怎样 为什么 如何
帮我 请问 可以 一下 一个 这个 那个 这些 那些 这样 那样
我们 你们 他们 自己 东西 事情 问题 地方 方面 情况 进度
还是 就是 然后 但是 因为 所以 如果 虽然 而且 或者 不过
没有 不是 不能 不要 知道 觉得 认为 需要 应该 可能 一定
已经 正在 一直 曾经 以前 以后 之后 之前
""".split())

STOP_CHARS = set("的了是吗呢吧啊呀哦嗯是在和有与就都也很太更最我你他它这那")


def tokenize(query: str):
    """极简中英文分词：中文 2-gram + 英文单词，并过滤停用词/泛词（带缓存）"""
    if not query:
        return []
    return list(_tokenize_cached(query))


@functools.lru_cache(maxsize=512)
def _tokenize_cached(query: str):
    """分词本体。同一个 query 一次 turn 里会被切好几遍（recall / cortex / 意图），
    切法固定，直接按 query 缓存。返回 tuple 才能进 lru_cache。
    """
    return tuple(_tokenize_raw(query))


def _tokenize_raw(query: str):
    q = query.lower()
    terms = set()

    for m in re.findall(r"[a-z0-9_\-]{2,}", q):
        if m not in STOPWORDS:
            terms.add(m)

    cn = re.sub(r"[^\u4e00-\u9fa5]", " ", q)
    for chunk in cn.split():
        if len(chunk) == 1:
            if chunk not in STOP_CHARS:
                terms.add(chunk)
            continue
        for i in range(len(chunk) - 1):
            bg = chunk[i:i + 2]
            if bg in STOPWORDS:
                continue
            if bg[0] in STOP_CHARS and bg[1] in STOP_CHARS:
                continue
            terms.add(bg)
        if len(chunk) <= 4 and chunk not in STOPWORDS:
            terms.add(chunk)

    return [t for t in terms if t]


# 同义词 / 关联词：查询时把词扩成一组，解决「字面不重合」
# 只放真正容易换着说的，不求全
SYNONYMS = {
    # 网络
    "网络": ["断网", "联网", "上网", "网卡", "dns", "代理", "clash", "tun", "翻墙", "连接"],
    "断网": ["网络", "连不上", "没网", "dns", "代理", "clash"],
    "代理": ["clash", "tun", "fake-ip", "节点", "翻墙", "网络"],
    "网速": ["慢", "卡", "延迟", "带宽"],
    # 修复
    "修": ["解决", "弄好", "处理", "搞定", "恢复", "排查"],
    "解决": ["修", "处理", "搞定", "恢复"],
    "报错": ["错误", "异常", "失败", "崩", "traceback", "error"],
    "崩": ["报错", "异常", "挂", "闪退", "失败"],
    # 配置
    "配置": ["设置", "config", "参数", "改", "调整"],
    "密码": ["口令", "password", "账号", "登录", "凭据"],
    "账号": ["账户", "用户名", "登录", "密码"],
    # 代码
    "代码": ["程序", "脚本", "源码", "code", "实现"],
    "部署": ["上线", "发布", "跑起来", "启动"],
    "测试": ["验证", "试", "跑一下", "检查"],
    # 记忆
    "记忆": ["记住", "记得", "记", "笔记", "备忘"],
    "插件": ["plugin", "扩展", "模块", "组件"],
    "面板": ["界面", "ui", "控制台", "页面", "设置"],
    # 文件
    "文件": ["目录", "路径", "文件夹", "file", "path"],
    "备份": ["backup", "存档", "副本", "导出", "还原"],
    "删": ["删除", "删掉", "清掉", "除去", "remove"],
    # 时间
    "昨天": ["昨天", "昨日", "前一天", "上次"],
    "上次": ["上回", "之前", "以前", "昨天"],
}

MAX_EXPAND = 6          # 一个词最多扩几个
USE_SYNONYM_EXPAND = False   # 同义词扩展开关（实测在中文上稀释相关度，默认关）


def expand_terms(terms):
    """把查询词按同义词表扩一圈（不递归，避免爆炸）"""
    out = set(terms)
    for t in terms:
        tl = t.lower()
        for key, alts in SYNONYMS.items():
            if key in tl or tl in key:
                for a in alts[:MAX_EXPAND]:
                    out.add(a.lower())
    return list(out)


EXPAND_WEIGHT = 0.35   # 扩展词只占原词的三分之一，当线索用，不当主角
EXPAND_GATE = 4        # 原词召回少于这么多条时才扩，避免本来就很准的查询被搅浑


def expand_weighted(terms):
    """返回 (加权词表, {词: 权重})：原词 1.0，扩展词 EXPAND_WEIGHT"""
    base = set(t.lower() for t in terms if t)
    w = {t: 1.0 for t in base}
    for t in terms:
        tl = (t or "").lower()
        if not tl:
            continue
        for key, alts in SYNONYMS.items():
            if key in tl or tl in key:
                for a in alts[:MAX_EXPAND]:
                    al = a.lower()
                    if al in w:
                        continue
                    w[al] = EXPAND_WEIGHT
    return list(w.keys()), w


class Retriever:
    def __init__(self, db):
        self.db = db
        self._idf = None
        self._focus_built = False
        # ---- 检索期缓存 ----
        self._df_cache = {}          # 词 -> 出现在多少条记忆里
        self._df_stamp = None        # 语料指纹，变了就把 df 缓存作废
        self._cx_cache = {}          # query -> (时间, 概念激活字典)
        self._ro = None              # 只读长连接（省掉每次 connect / close）
        self._ro_lock = threading.Lock()
        self._ensure_indexes()

    # ---------- 基础设施 ----------
    def _ensure_indexes(self):
        """补上检索路径上缺的索引（幂等，失败不影响检索）

        EXPLAIN QUERY PLAN 实测：
          · graph.neighbors 的 `source_id IN (..) OR target_id IN (..)` 原本是 SCAN edges
          · events 的 `kind='digest_shown'` 也是整表扫
        这两条每次 /context 都走，所以在这里补 —— 只做 CREATE INDEX IF NOT EXISTS，
        已经有就什么都不干。
        """
        for ddl in getattr(config, "EXTRA_INDEXES", ()):
            try:
                self.db.execute(ddl)
            except Exception:
                pass

    def _rows(self, sql, params=()):
        """走长连接的只读查询

        db.query() 每次 connect + commit + close，一次约 1.2 毫秒；
        一次检索里原来的连接开关有五十多次，光这一点就是几十毫秒。
        读多写少的地方留一个只读长连接更划算（WAL 下读不挡写，每次查询都是新快照）。
        任何异常都退回 db.query()，不影响正确性。
        """
        try:
            with self._ro_lock:
                if self._ro is None:
                    path = str(getattr(self.db, "path", "") or config.DB_PATH)
                    con = sqlite3.connect(path, timeout=10, check_same_thread=False)
                    con.row_factory = sqlite3.Row
                    con.isolation_level = None      # 自动提交：读不挂着事务
                    self._ro = con
                    # Retriever 被回收时关掉连接，免得临时库文件删不掉（测试里会踩）
                    weakref.finalize(self, _close_quietly, con)
                return [dict(r) for r in self._ro.execute(sql, params).fetchall()]
        except Exception:
            try:
                self._ro = None
            except Exception:
                pass
            return self.db.query(sql, params)

    def close(self):
        """显式关掉长连接（一般用不着，weakref 会兜底）"""
        con, self._ro = self._ro, None
        if con is not None:
            _close_quietly(con)

    def _corpus(self):
        """当前活跃记忆（排除 secret）。每次都是新的 dict，调用方随便改"""
        try:
            rows = self._rows(
                "SELECT * FROM memories WHERE status='active' AND sensitivity!='secret'")
        except Exception:
            return []
        return [_decode_fast(r) for r in rows]

    def _corpus_stamp(self, mems):
        """语料指纹：条数 + 最新一条的写入时间"""
        newest = 0.0
        for m in mems:
            c = m.get("created") or 0.0
            if c > newest:
                newest = c
        return (len(mems), newest)

    def _active_count(self):
        """活跃记忆条数（IDF 的分母）。走长连接，别为一次 COUNT 新开连接"""
        try:
            row = self._rows("SELECT COUNT(*) c FROM memories WHERE status='active'")
            return int(row[0]["c"] or 0) or 1
        except Exception:
            return 1

    def _cortex_act(self, query, limit=60):
        """概念激活（带短时缓存）

        激活扩散要沿突触走两跳，几十条 SQL；而同一个 query 在一个 turn 里
        会被问两遍（recall 一次、build_context 一次）。这里缓存 TTL 90 秒，
        有新记忆写入时由 invalidate_focus() 立刻清掉。
        """
        ttl = float(getattr(config, "CORTEX_CACHE_TTL", 90.0) or 0.0)
        now = time.time()
        hit = self._cx_cache.get(query)
        if hit is not None and (now - hit[0]) < ttl:
            return hit[1]        # 只读，不做复制
        act = {}
        try:
            from . import cortex as _cortex
            for mid, sc in _cortex.activate(self.db, query, limit=limit):
                act[mid] = sc
        except Exception:
            act = {}
        cap = int(getattr(config, "CORTEX_CACHE_SIZE", 64) or 64)
        if len(self._cx_cache) >= cap:
            for k in sorted(self._cx_cache, key=lambda k: self._cx_cache[k][0])[:max(1, cap // 2)]:
                self._cx_cache.pop(k, None)
        self._cx_cache[query] = (now, act)
        return act

    def invalidate_focus(self):
        """有新记忆写入时调用：下次查询重建索引，并把缓存全部作废"""
        self._focus_built = False
        self._df_cache.clear()
        self._df_stamp = None
        self._cx_cache.clear()

    def _ensure_focus(self, mems):
        """构建主题集中度索引（一次，之后只加不减）"""
        if self._focus_built:
            return
        try:
            focus_mod.build([
                (m["id"], (m.get("content") or "") + " " + (m.get("summary") or ""))
                for m in mems
            ])
            self._focus_built = True
        except Exception:
            pass

    def _build_idf(self, terms, mems=None):
        """按当前查询词，统计它们在多少条记忆里出现 → 算 IDF 权重

        idf = log(1 + N / (1 + df))，df 越大权重越小；出现超过一半记忆的词压到 0.15。

        以前每个词发一条 `lower(content) LIKE '%词%'`：10 个词 = 10 次全表扫，
        每次还要新开一个连接（实测 15 毫秒）。可语料本来就在内存里
        （recall 已经把活跃记忆全取出来了），直接在 Python 里数字符串就行，
        和 LIKE 的结果完全一致，df 还按词缓存，下一轮同一个词直接命中。
        """
        if not terms:
            return {}
        if mems is None:
            mems = self._corpus()
        n = self._active_count()
        limit_cache = int(getattr(config, "IDF_CACHE", 4096) or 4096)
        idf = {}
        for t in sorted(set(x.lower() for x in terms)):
            df = self._df_cache.get(t)
            if df is None:
                df = 0
                for m in mems:
                    if t in _mem_text(m):
                        df += 1
                if len(self._df_cache) >= limit_cache:
                    self._df_cache.clear()
                self._df_cache[t] = df
            if df <= 0:
                # 库里从没出现过的词，多半是 2-gram 切出来的碎片，
                # 不能因为「罕见」就给它最高分
                idf[t] = 0.0
                continue
            w = math.log(1 + n / (1 + df))
            # 出现在 >50% 记忆里的词，几乎等于泛词，压到很低
            if df > n * 0.5:
                w = 0.15
            idf[t] = max(0.15, round(w, 3))
        return idf

    def _intent_pool(self, it, mems):
        """按意图在语料里挑「这一类」的记忆

        词面检索要求「同一个词」，这里要求「同一个类型」：
        问「我让你别再做」时，一条正文带「别再」的 L2- 记忆就算数，
        跟问句有没有共同实词完全不重要。判定规则全在 intent.py，这里只负责：
        把正文准备好（有缓存）→ 每类各挑几条 → 合并去重。
        """
        if not it or not it.kinds:
            return []
        items = [(m, _mem_text(m)) for m in mems]
        terms = tokenize(it.query)
        pools = []
        # 问句被原样记下来过的，直接算这一类（比任何规则都准）
        pools.append(intent_mod.phrase_hits(it.query, items))
        for kind in it.kinds:
            if kind == intent_mod.KIND_LIST:
                continue          # 清单型不加料，只放宽条数
            pools.append(intent_mod.rank(kind, items, terms=terms))
        if not any(pools):
            return []
        cap = int(getattr(config, "INTENT_SUPPLEMENT_MAX", 6) or 6)
        return intent_mod.merge_pools([p for p in pools if p])[:max(1, cap)]

    def recall(self, query="", current_thread=None, limit=None):
        limit = limit or config.RECALL_CANDIDATES
        terms = tokenize(query)
        # 「可以」「弄吧」「你好」这类查询分词后一个实词都不剩。以前这种会被当成
        # 「无查询」，把全部记忆按衰减排一遍，硬塞 17 条 800 多 token 进上下文。
        # 现在直接返回空，交给 prepare_context 的 build_recent(3) 做兜底。
        if not terms:
            return []

        rows = None
        # 活跃记忆一次取全：下面 df、focus、意图、打分都要用，别重复查库
        mems = self._corpus()
        if not mems:
            return []
        self._ensure_focus(mems)

        # 语料变了（有新写入）就把 df 缓存作废
        stamp = self._corpus_stamp(mems)
        if stamp != self._df_stamp:
            self._df_stamp = stamp
            self._df_cache.clear()

        # 意图：问的是「某类事」还是「某个词」，走的路不一样。
        # 判不出来（普通陈述、「改」「弄吧」这种短命令）就是 None，一切照旧。
        it = None
        if getattr(config, "INTENT_ENABLED", True):
            try:
                it = intent_mod.classify(query, getattr(config, "INTENT_LIST_EXTRA", 6))
            except Exception:
                it = None
        # 清单型问题放宽条数：问「都有哪些」要的不是最高分那三条
        limit_eff = limit + (it.widen if it else 0)

        # 查询信息不足时（「那个呢」「这种怎么弄」），用工作记忆的焦点补全。
        # 人聊天是连续的，程序得知道刚才在聊什么。
        try:
            from . import focusmem as _fm
            _enriched = _fm.enrich_query(self.db, query, tokenize(query))
            if _enriched != query:
                query = _enriched
        except Exception:
            pass

        # 先跑皮层：查询词点亮概念，概念再连到记忆。
        # 这是主信号，后面那些字符匹配只用来补漏。
        # 激活结果有短时缓存（build_context 里还要再要一次，不必重跑扩散）
        cortex_act = self._cortex_act(query)
        _cmax = max(cortex_act.values()) if cortex_act else 0.0

        # 预扫：只对「字面真命中」的记忆算 focus / coverage。
        # 没命中的记忆 focus 恒为 0（focus 的快路径要求词出现在正文里），
        # 以前为了拿 _top_foc 把全库算一遍、主循环里再算一遍，白烧十几毫秒。
        foc_of = {}
        cov_of = {}
        _top_foc = 0.0
        if terms:
            for _m in mems:
                _low = _mem_text(_m)
                _hit = False
                for _t in terms:
                    if _t in _low:
                        _hit = True
                        break
                if not _hit:
                    continue
                _mid = _m["id"]
                _v = focus_mod.focus(_mid, terms)
                foc_of[_mid] = _v
                cov_of[_mid] = focus_mod.coverage(_mid, terms)
                if _v > _top_foc:
                    _top_foc = _v

        scored = []
        # IDF 只算一次（以前在循环里再补算一遍），而且 df 现在是内存里数出来的，
        # 不再每个词发一条 LIKE 全表扫。
        idf = self._build_idf(terms, mems) if terms else {}
        # 先算一次 IDF，把库里不存在的查询词剔掉。
        # 「网络怎么走」会切出「络怎」「么走」，这类碎片会让 related 判断失真。
        if terms:
            real = [t for t in terms if idf.get(t.lower(), 0) > 0]
            # 全被剔掉（例如整句话都很生僻）就保留原样，别让查询变空
            if real:
                terms = real
        # 先只看原词：够准就不扩，不准了才拿同义词补
        base_hits = 0
        if terms:
            for _m in mems:
                _low = _mem_text(_m)
                for _t in terms:
                    if _t in _low:
                        base_hits += 1
                        break
        if terms and base_hits < EXPAND_GATE:
            exp_terms, term_w = expand_weighted(terms)
        else:
            exp_terms, term_w = terms, None
        has_query = bool(query and terms)
        # 意图补充池：先把「属于这一类」的记忆挑出来（纯规则，不查库）。
        # 它们跟问句没有共同词也很正常 —— 那正是要按意图捞的理由。
        intent_pool = []
        intent_w = {}
        intent_scored = {}
        if it:
            try:
                intent_pool = self._intent_pool(it, mems)
            except Exception:
                intent_pool = []
            for _w, _m in intent_pool:
                intent_w[_m["id"]] = _w

        for m in mems:
            # 两路打分取高的：BM25（词频归一）+ 原命中率（兜底）
            r1 = bm25_score(m, exp_terms, idf, term_w=term_w) if exp_terms else 0.0
            r2 = relevance_of(m, terms, idf)
            # 主题集中度：同样命中「迅雷」，专门讲它的和顺带提一句的，要分开
            fc = 1.0
            if terms:
                cov = cov_of.get(m["id"], 0.0)
                foc = foc_of.get(m["id"], 0.0)
                # 用幂函数放大差距：focus 0.05 和 0.35 差 7 倍，
                # 线性加权只有 3 倍，压不住那些"顺带提一句"的长文
                # 0.6 次幂 + 0.08 保护下限：
                # 幂次太狠会把"答案藏在长文里"的那些也压死（找不到 node / 残留文件
                # 这两类直接掉分），太松又压不住噪音。
                fc = cov * max(0.08, foc ** 0.6)
            # 只用 focus 加权，不要再 max(bm25)：
            # bm25_score 里有 min(1.0, ...)，凡是命中的都被顶到 1.0，
            # 长度惩罚形同虚设，一取 max 就把 focus 的区分度全抹平了。
            # 皮层激活值（归一化）为主，focus 为辅。
            # 皮层那层隔了「概念」，比单纯的字面命中更接近记忆的工作方式。
            cn = (cortex_act.get(m["id"], 0.0) / _cmax) if _cmax > 0 else 0.0
            if terms and _cmax > 0:
                rel = 0.72 * cn + 0.28 * (r2 * fc)
            elif terms:
                rel = r2 * fc
            else:
                rel = max(r1, r2)
            # 只是正文里顺带提过一句的，不算命中。
            # 「迅雷」在一条 420 字的汇报里出现一次，focus ≈ 0.005，
            # 这种条目以前照样进正文，占掉一半预算。
            # 注意用原始 foc 判断，不能用 fc —— fc 里有 0.08 的保底，
            # 会把「只提了一次」的长文（foc≈0.004）拉到 0.08，门槛就形同虚设。
            # 被皮层点亮的记忆不再看字面门槛 ——
            # 它可能是通过概念关联进来的，字面上一个字都不重合，但正是该想起来的
            # 相对门槛：低于「最相关那条」的 25% 就算顺带提过。
            # 实测问「迅雷」，真相关 0.011-0.049，噪音 0.003-0.007，中间断得干净。
            # 意图命中的记忆不受下面这两道字面门槛约束 ——
            # 它本来就跟问句没有共同词，靠字面门槛筛就没得玩了。
            # 但也只给一个「地板分」，不许反客为主压过真正的字面命中。
            # 意图命中的记忆不进词面排名 —— 它跟问句没有共同词，
            # 放进 scored 里就会跟真命中抢位置（实测能把一条不相关的 L3 顶到第一）。
            # 这里只给它算一份「地板分」，后面统一走补充通道合并。
            _iw = intent_w.get(m["id"])
            if _iw:
                _floor = min(float(getattr(config, "INTENT_REL_CAP", 0.45)),
                             float(getattr(config, "INTENT_REL_FLOOR", 0.30)) * _iw)
                _p = rank_score(m, max(rel, _floor))
                m["score"] = round(_p["total"], 6)
                m["score_parts"] = _p
                intent_scored[m["id"]] = m
                continue
            if (terms and cn < 0.55 and _top_foc > 0
                    and foc < _top_foc * 0.35
                    and m.get("layer") not in ("L4", "L5")):
                continue
            if has_query:
                # 有查询时，完全不相关的直接丢掉（除非它是身份/程序层）
                # L3（方法）以前是无条件保留的，结果每条查询的第一位
                # 都是「记忆服务在 18765」这种程序性知识，跟问什么都无关。
                # 程序性知识也该看相关度，不该霸着位置。
                if rel <= 0 and m.get("layer") not in ("L4", "L5"):
                    continue
            if current_thread and m.get("thread_id") == current_thread:
                rel = max(rel, 0.3)
            # ------------------------------------------------------------------
            # 总分 = 相关度 × 时间衰减 × 重要度与使用
            # 旧算法只乘了 (0.15+rel)，衰减和「有没有人用」是散的；
            # 现在一条记忆为什么排在前面，看 score_parts 就知道。
            # ------------------------------------------------------------------
            parts = rank_score(m, rel)
            s = parts["total"]
            if current_thread and m.get("thread_id") == current_thread:
                s *= 1.5
                parts["thread"] = 1.5
            if s <= 0:
                continue
            m["score"] = round(s, 6)
            m["score_parts"] = parts
            scored.append((s, m))

        # 身份层 / 教训层保底：库里这两类一共就几条，
        # 但「我最看重什么」「什么事你让我别再做」这类问题全靠它们。
        # 它们跟 query 的字面重合通常是 0，按分数排必然被切掉，所以单独捞。
        anchors = [m for m in mems if m.get("layer") in ("L4", "L5", "L2-")]
        anchor_ids = set(id(m) for m in anchors)

        # 原词是否直接命中：扩展召回的条目不能和真命中平起平坐，
        # 否则「广告插件」这种查询会被同义词带进来一堆弱相关的，正文直接灌满。
        def _base_hit(m):
            return any(x.lower() in _mem_text(m) for x in terms)

        if term_w is not None:
            scored.sort(key=lambda x: (0 if _base_hit(x[1]) else 1, -x[0]))
            strong = [m for _, m in scored if _base_hit(m)]
            weak = [m for _, m in scored if not _base_hit(m)]
            # 弱相关最多放 strong 的等量条数（且不少于 2 条），留个兜底但不喧宾夺主
            picked = (strong + weak[:max(2, len(strong))])[:limit_eff]
        else:
            scored.sort(key=lambda x: -x[0])
            picked = [m for _, m in scored[:limit_eff]]

        # 身份/教训只做「兜底」：只有一条都没召回到时才顶上来。
        # 之前无条件加进去，结果每条查询的正文开头都是那两条性格偏好，
        # 真答案全被挤到后面 —— 「迅雷广告」召不出一停「迅雷广告」。
        if not picked and anchors:
            picked = anchors

        # ---- 意图补充：和词面结果合并去重后一起排序 ----
        # 补充候选走的是同一套 rank_score，所以两者能放在一张表里比。
        # 排序两条规矩：
        #   ① 同一条记忆只出现一次（按 id 去重）
        #   ② 补充候选的总分封顶在「最好的词面命中 × 0.95」
        #      —— 词面能答上来的时候，意图那几条是补位，不是抢位。
        if intent_pool:
            have = set(m.get("id") for m in picked)
            extra = []
            cap = int(getattr(config, "INTENT_SUPPLEMENT_MAX", 6) or 0)
            for w, m in intent_pool:
                if len(extra) >= cap:
                    break
                mid = m.get("id")
                if mid in have:
                    continue
                have.add(mid)
                if not m.get("score_parts"):
                    # 没进过主循环（会被字面门槛挡掉）：现在按地板分补一份
                    floor = min(float(getattr(config, "INTENT_REL_CAP", 0.45)),
                                float(getattr(config, "INTENT_REL_FLOOR", 0.30)) * w)
                    _part = rank_score(m, floor)
                    m["score"] = round(_part["total"], 6)
                    m["score_parts"] = _part
                m["_intent"] = it.kinds if it else ()
                extra.append(m)
            if extra:
                ratio = float(getattr(config, "INTENT_MERGE_RATIO", 0.95))
                _top_lit = 0.0
                for m in picked:
                    v = float(m.get("score") or 0.0)
                    if v > _top_lit:
                        _top_lit = v
                if _top_lit > 0:
                    ceiling = _top_lit * ratio
                    for m in extra:
                        if float(m.get("score") or 0.0) > ceiling:
                            m["score"] = round(ceiling, 6)
                            if isinstance(m.get("score_parts"), dict):
                                m["score_parts"]["intent_capped"] = True
                merged = picked + extra
                # 一起排序：同一条记忆不会出现两次（上面已经按 id 去重）
                merged.sort(key=lambda x: -float(x.get("score") or 0.0))
                picked = merged

        # 联想：顺着图谱的边，把跟命中记忆真正相关的那几条带出来。
        # 比原来按实体硬拉的准 —— 边是建图时按共享实体 + 文本相近算好的，
        # 不会因为某条长文顺手提了一句「迅雷」就被拽进来。
        if picked:
            try:
                from . import graph as _graph
                top_ids = [m["id"] for m in picked[:6] if m.get("id")]
                have = set(m.get("id") for m in picked)
                want = []
                for w, nid in _graph.neighbors(self.db, top_ids, limit=4):
                    if not nid or nid in have:
                        continue
                    have.add(nid)
                    want.append((w, nid))
                if want:
                    # 一次批量取回。以前一条一个 get_memory，每条都新开连接
                    ph = ",".join("?" * len(want))
                    rows = self._rows(
                        f"SELECT * FROM memories WHERE id IN ({ph})",
                        tuple(nid for _w, nid in want))
                    by_id = {}
                    for r in rows:
                        d = _decode_fast(r)
                        if d:
                            by_id[d.get("id")] = d
                    for w, nid in want:
                        nm = by_id.get(nid)
                        if not nm or nm.get("status") != "active":
                            continue
                        nm["_via"] = w          # 标记「这是联想出来的」，注入时可以排在后面
                        # 联想的也带分值，调试时能看出「它靠梯度混进来的，不是命中」
                        _part = rank_score(nm, 0.0)
                        nm["score"] = round(_part["total"], 6)
                        nm["score_parts"] = _part
                        picked.append(nm)
            except Exception:
                pass

        return picked

    # ---------- 预算注入 ----------
    def build_recent(self, limit=3, budget=None):
        """查询没命中时用：取最近 + 最常被用到的几条

        为什么需要：用户说「你好」「在吗」这种短问候，
        tokenize 出来的词在记忆里不存在，召回 0 条 ->
        模型看到的是「我什么都不记得」，体验很差。
        """
        try:
            rows = self.db.query(
                """
                SELECT * FROM memories
                WHERE status = 'active'
                  AND (sensitivity IS NULL OR sensitivity != 'secret')
                ORDER BY pinned DESC, recall_count DESC, created DESC
                LIMIT ?
                """,
                (limit,),
            )
        except Exception:
            return "", [], 0

        if not rows:
            return "", [], 0

        cfg_budget = budget or config.BUDGET
        cap = cfg_budget if isinstance(cfg_budget, int) else 300
        lines, used, total = [], [], 0
        for r in rows:
            m = dict(r)
            text = (m.get("summary") or m.get("content") or "").strip()
            if not text:
                continue
            line = "- " + text[:80]
            n = len(line) // 2
            if total + n > cap:
                break
            lines.append(line)
            used.append(m)
            total += n
        return "\n".join(lines), used, total

    def _update_focus(self, query, mems):
        """把这一轮点亮的概念推进工作记忆

        以前是「每条记忆一个查询」，6 条就是 6 次 connect+close。
        现在用一条 IN 查询取回来，再按记忆分组——顺序和原来一致：
        同一条记忆里 freq 高的在前，每条最多 6 个，总共截到 24 个。
        """
        try:
            from . import focusmem as _fm
            ids = [m.get("id") for m in (mems or [])[:6] if m.get("id")]
            if not ids:
                return
            ph = ",".join("?" * len(ids))
            rows = self._rows(
                "SELECT t.memory_id AS mid, t.concept AS concept FROM traces t "
                "JOIN concepts c ON c.name = t.concept "
                "WHERE t.memory_id IN (%s) AND c.freq >= 4 "
                "ORDER BY t.memory_id, c.freq DESC" % ph, tuple(ids))
            # 只要真正的概念：freq>=4 说明它在多条记忆里都出现，
            # 是「迅雷」「Clash」这种有意义的词；
            # 不过滤的话「第一」「公事」「和结」这类切词碎片会占满焦点。
            per = {}
            for r in rows:
                bucket = per.setdefault(r["mid"], [])
                if len(bucket) < 6:
                    bucket.append(r["concept"])
            cons = []
            for mid in ids:
                cons.extend(per.get(mid, []))
            _fm.touch(self.db, cons[:24], weight=1.0)
        except Exception:
            pass

    def _budget_for(self, query, cortex_act):
        """按问题复杂度决定给几条。

        固定给 6 条是偷懒：问「迅雷」要的就是那两三条，
        问「这个项目整体做到哪一步」才真需要更多上下文。
        该给的地方给足，不该给的一个字都别多。
        """
        base = getattr(config, "INJECT_MAX_ITEMS", 6)
        q = (query or "").strip()
        n_chars = len([c for c in q if not c.isspace()])

        n = base
        if n_chars <= 6:                 # 问得具体，答案就那几条
            n = max(3, base - 2)
        if any(k in q for k in ("整体", "全部", "所有", "哪些", "为什么", "怎么",
                                "哪一步", "过程", "历史", "总结", "梳理")):
            n = base + 2                 # 笼统 / 追根究底，需要拼多块
        if cortex_act and max(cortex_act.values()) < 0.35:
            n += 1                       # 分数低说明在摸索，多给一条兜底

        return max(3, min(9, n))

    def build_context(self, query="", current_thread=None, budget=None):
        """返回 (文本, 使用的记忆列表, token 占用)"""
        budget = budget or dict(config.BUDGET)
        mems = self.recall(query, current_thread)

        # 分桶
        # 不再按「身份/方法/事实/情景」固定顺序装桶。
        # 那样做的后果是：一条跟查询无关的 L3 方法记忆永远排第一，只因为类别靠前。
        # 大脑不是按分类说话的，是想到什么说什么。
        # cortex_act 是 recall 里的局部变量，这里拿不到，重新要一次（有短时缓存，很快）
        _act = {}
        try:
            _act = dict(list(self._cortex_act(query).items())[:6])
        except Exception:
            pass
        max_items = self._budget_for(query, _act)
        # 默认 0：身份记忆不再无条件占前两位。
        # 「我是谁」只在该被想起的时候出现（问说话方式、问偏好），
        # 而不是问「迅雷」也先塞两条「喜欢可爱轻松的语气」。
        ident_quota = getattr(config, "INJECT_IDENTITY_QUOTA", 0)

        used, out_lines, total_tokens = [], [], 0
        budget_left = sum(budget.values()) if isinstance(budget, dict) else 1500

        def _take(m):
            nonlocal total_tokens, budget_left
            text = self._render(m)
            tk = min(config.estimate_tokens(text), config.SINGLE_ITEM_MAX)
            out_lines.append(text)
            used.append(m)
            total_tokens += tk
            budget_left -= tk

        # ① 身份层留固定名额：「我是谁」在任何话题下都成立
        for m in [x for x in mems if x.get("layer") in ("L4", "L5")][:ident_quota]:
            if len(used) >= max_items or budget_left <= 0:
                break
            _take(m)

        # ② 直接命中的，按相关度顺序
        seen = set(u.get("id") for u in used)
        direct = [m for m in mems if not m.get("_via")]
        linked = [m for m in mems if m.get("_via")]

        # 给「顺带想起」留名额，否则直接命中会把 6 个位置全占满，
        # 联想永远出不来（实测：direct 有 10 条候选，前 6 个就填满了）
        direct_cap = max_items - getattr(config, "INJECT_LINKED_MAX", 2)
        for m in direct:
            if len(used) >= direct_cap or budget_left <= 0:
                break
            if m.get("id") in seen:
                continue
            seen.add(m.get("id"))
            _take(m)

        # ③ 顺着图谱边想起的，单独成段。这是「主动」的部分：
        #    用户没问，但它跟当前话题有关，就该自己冒出来。
        asked = getattr(config, "INJECT_LINKED_MAX", 2)
        picked_link = []
        for m in linked:
            if len(picked_link) >= asked or len(used) >= max_items or budget_left <= 0:
                break
            if m.get("id") in seen:
                continue
            seen.add(m.get("id"))
            _take(m)
            picked_link.append(m)

        if picked_link:
            # 给联想的那几条加个小标题，跟直接命中分开
            texts = out_lines[:]
            head = texts[:len(texts) - len(picked_link)]
            tail = texts[len(texts) - len(picked_link):]
            out_lines = head + ["（顺带想起）"] + tail

        self._update_focus(query, used)
        return "\n".join(out_lines), used, total_tokens

    def _render(self, m):
        layer = m.get("layer")
        tag = {"L1": "情景", "L2": "事实", "L2-": "教训", "L3": "方法", "L4": "身份", "L5": "自我"}.get(layer, "")
        days = _days_since(m.get("created"))
        when = config.fuzzy_time(days)
        body = m.get("summary") or m.get("content") or ""
        if m.get("fidelity") == "verbatim":
            body = m.get("content") or body
        # 单条注入上限：预算按 SINGLE_ITEM_MAX 算，正文却按原样塞，就会超支。
        # 一条 800 字的汇报能把整个 semantic 仓吃光，其余真命中全被挤掉。
        #
        # 还要先把换行压成空格：一条记忆如果自带多行（markdown 表格、代码块残留），
        # 渲染出来会变成五六行，每行都算 token，看着像一条实际吃三份钱。
        body = " ".join((body or "").split())
        if len(body) > 200:
            body = body[:199].rstrip() + "…"
        return f"· [{tag}·{when}] {body}"

    def mark_used(self, mems, rejected=False):
        """回写反馈：被用到 → 强化；被否定 → 降权"""
        for m in mems:
            mid = m["id"]
            if rejected:
                mem = self.db.get_memory(mid)
                hl = max(0.3, float(mem["half_life_days"]) * config.REJECT_FACTOR)
                self.db.update_memory(
                    mid, reject_count=int(mem["reject_count"]) + 1,
                    half_life_days=hl, last_recalled=time.time()
                )
            else:
                mem = self.db.get_memory(mid)
                hl = min(config.MAX_HALF_LIFE, float(mem["half_life_days"]) * config.REINFORCE_FACTOR)
                self.db.update_memory(
                    mid, recall_count=int(mem["recall_count"]) + 1,
                    half_life_days=hl, last_recalled=time.time()
                )
