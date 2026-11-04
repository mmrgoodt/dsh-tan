# -*- coding: utf-8 -*-
"""检索与打分：召回、排序、预算注入"""
import json
import math
import re
import time

from . import config
from .db import _decode_memory


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


def bm25_score(mem, query_terms, idf=None, avg_len=400, k1=1.4, b=0.72):
    """BM25：比「命中率」准得多

    k1 控制词频饱和，b 控制文档长度惩罚（0=不惩罚，1=完全惩罚）
    """
    if not query_terms:
        return 0.0
    text = ((mem.get("content") or "") + " " + (mem.get("summary") or "")).lower()
    if not text:
        return 0.0

    dl = len(text)
    score = 0.0
    for t in query_terms:
        tl = t.lower()
        if tl not in text:
            continue
        tf = text.count(tl)
        w = (idf or {}).get(tl, 1.0)
        denom = tf + k1 * (1 - b + b * (dl / max(1, avg_len)))
        score += w * (tf * (k1 + 1)) / max(0.001, denom)

    # 归一到 0~1 区间（除以查询词数的一个经验上界）
    norm = max(1.0, len(query_terms) * 0.9)
    return min(1.0, score / norm)


def relevance_of(mem, query_terms, idf=None):
    """相关度：命中词按 IDF 加权——越少见的词权重越高，泛词几乎不加分"""
    if not query_terms:
        return 0.0
    text = ((mem.get("content") or "") + " " + (mem.get("summary") or "")).lower()

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
    emo = float(mem.get("emotion") or 1.0)
    rc = int(mem.get("recall_count") or 0)
    reinforce = 1 + 0.2 * min(rc, 5)
    boost = config.LAYER_BOOST.get(mem.get("layer"), 1.0)
    conf = float(mem.get("confidence") or 0.7)
    rej = int(mem.get("reject_count") or 0)
    reject_penalty = 1.0 / (1 + 0.5 * rej)

    return rel * d * emo * reinforce * boost * conf * reject_penalty


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
    """极简中英文分词：中文 2-gram + 英文单词，并过滤停用词/泛词"""
    if not query:
        return []
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


class Retriever:
    def __init__(self, db):
        self.db = db
        self._idf = None

    def _build_idf(self, terms):
        """按当前查询词，统计它们在多少条记忆里出现 → 算 IDF 权重

        idf = log(1 + N / (1 + df))，df 越大权重越小；出现超过一半记忆的词压到 0.15。
        """
        if not terms:
            return {}
        try:
            row = self.db.one("SELECT COUNT(*) c FROM memories WHERE status='active'")
            n = (row["c"] if row else 0) or 1
        except Exception:
            n = 1
        idf = {}
        for t in set(x.lower() for x in terms):
            try:
                r = self.db.one(
                    "SELECT COUNT(*) c FROM memories WHERE status='active' "
                    "AND (lower(content) LIKE ? OR lower(summary) LIKE ?)",
                    (f"%{t}%", f"%{t}%"))
                df = (r["c"] if r else 0) or 0
            except Exception:
                df = 0
            import math
            w = math.log(1 + n / (1 + df))
            # 出现在 >50% 记忆里的词，几乎等于泛词，压到很低
            if df > n * 0.5:
                w = 0.15
            idf[t] = max(0.15, round(w, 3))
        return idf

    def recall(self, query="", current_thread=None, limit=None):
        limit = limit or config.RECALL_CANDIDATES
        terms = tokenize(query)

        rows = self.db.query(
            "SELECT * FROM memories WHERE status='active' AND sensitivity!='secret'"
        )
        mems = [_decode_memory(r) for r in rows]

        scored = []
        idf = None
        exp_terms = expand_terms(terms) if (terms and USE_SYNONYM_EXPAND) else terms
        has_query = bool(query and terms)
        for m in mems:
            if idf is None:
                idf = self._build_idf(terms)
            # 两路打分取高的：BM25（词频归一）+ 原命中率（兜底）
            r1 = bm25_score(m, exp_terms, idf) if exp_terms else 0.0
            r2 = relevance_of(m, terms, idf)
            rel = max(r1, r2)
            s = score(m, terms)
            if current_thread and m.get("thread_id") == current_thread:
                s *= 1.5
                rel = max(rel, 0.3)
            if has_query:
                # 有查询时，完全不相关的直接丢掉（除非它是身份/程序层）
                if rel <= 0 and m.get("layer") not in ("L4", "L5", "L3"):
                    continue
                s = s * (0.15 + rel)      # 相关度主导
            else:
                s = decay(m) * config.LAYER_BOOST.get(m.get("layer"), 1.0)
            if s <= 0:
                continue
            scored.append((s, m))

        scored.sort(key=lambda x: -x[0])

        # 实体扩散：命中实体带出关联记忆
        picked = [m for _, m in scored[:limit]]
        if terms:
            qtext = " ".join(terms)
            seeds = []
            for m in picked[:5]:
                for e in (m.get("entities") or [])[:6]:
                    if e and e.lower() in qtext:
                        seeds.append(e)
            if seeds:
                for m in mems:
                    if m in picked:
                        continue
                    if any(e in (m.get("entities") or []) for e in set(seeds)):
                        picked.append(m)
                    if len(picked) >= limit + 3:
                        break

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

    def build_context(self, query="", current_thread=None, budget=None):
        """返回 (文本, 使用的记忆列表, token 占用)"""
        budget = budget or dict(config.BUDGET)
        mems = self.recall(query, current_thread)

        # 分桶
        buckets = {"identity": [], "procedure": [], "counter": [], "semantic": [], "episodic": []}
        for m in mems:
            layer = m.get("layer")
            if layer in ("L4", "L5"):
                buckets["identity"].append(m)
            elif layer == "L3":
                buckets["procedure"].append(m)
            elif layer == "L2-":
                buckets["counter"].append(m)
            elif layer == "L2":
                buckets["semantic"].append(m)
            else:
                buckets["episodic"].append(m)

        used = []
        lines = []
        total_tokens = 0

        for name, cap in (("identity", budget["identity"]), ("procedure", budget["procedure"]),
                          ("counter", budget["counter"]), ("semantic", budget["semantic"]),
                          ("episodic", budget["episodic"])):
            room = cap
            for m in buckets[name]:
                if room <= 0:
                    break
                text = self._render(m)
                tk = min(config.estimate_tokens(text), config.SINGLE_ITEM_MAX)
                if tk > room and used:
                    continue
                lines.append(text)
                used.append(m)
                room -= tk
                total_tokens += tk

        return "\n".join(lines), used, total_tokens

    def _render(self, m):
        layer = m.get("layer")
        tag = {"L1": "情景", "L2": "事实", "L2-": "教训", "L3": "方法", "L4": "身份", "L5": "自我"}.get(layer, "")
        days = _days_since(m.get("created"))
        when = config.fuzzy_time(days)
        body = m.get("summary") or m.get("content") or ""
        if m.get("fidelity") == "verbatim":
            body = m.get("content") or body
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
