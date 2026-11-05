# -*- coding: utf-8 -*-
"""一次性补丁：接通强化回路 + 查询净化 + 空查询不再灌满预算"""
import io, re, sys

P = lambda p: io.open(p, encoding="utf-8").read()
W = lambda p, s: io.open(p, "w", encoding="utf-8", newline="\n").write(s)


def sub1(text, old, new, tag):
    if old not in text:
        raise SystemExit("MISS: " + tag)
    if text.count(old) != 1:
        raise SystemExit("AMBIGUOUS(%d): %s" % (text.count(old), tag))
    return text.replace(old, new)


# ---------- 1) engine.py：把真正注入的记忆 id 带出去 ----------
s = P("hippocampus/engine.py")
s = sub1(
    s,
    """        # ② 记忆块
        mem_text, used, tokens = self.retriever.build_context(query)
        if not mem_text:""",
    """        # ② 记忆块
        used_ids = []
        mem_text, used, tokens = self.retriever.build_context(query)
        if not mem_text:""",
    "engine/used_ids_decl",
)
s = sub1(
    s,
    """        if mem_text:
            parts.append("## 我记得的事\\n" + mem_text)""",
    """        if mem_text:
            parts.append("## 我记得的事\\n" + mem_text)
            used_ids = [m["id"] for m in used if m.get("id")]""",
    "engine/used_ids_fill",
)
s = sub1(
    s,
    """            "loops": inject_loops,
        }""",
    """            "loops": inject_loops,
            # 真正进了正文的记忆 id：调用方据此回写强化，否则 recall_count 永远为 0
            "ids": used_ids,
        }""",
    "engine/meta_ids",
)
W("hippocampus/engine.py", s)

# ---------- 2) retriever.py：查询词全被过滤掉时不要全量兜底 ----------
s = P("hippocampus/retriever.py")
s = sub1(
    s,
    """    def recall(self, query="", current_thread=None, limit=None):
        limit = limit or config.RECALL_CANDIDATES
        terms = tokenize(query)
""",
    """    def recall(self, query="", current_thread=None, limit=None):
        limit = limit or config.RECALL_CANDIDATES
        terms = tokenize(query)
        # 「可以」「弄吧」「你好」这类查询分词后一个实词都不剩。以前这种会被当成
        # 「无查询」，把全部记忆按衰减排一遍，硬塞 17 条 800 多 token 进上下文。
        # 现在直接返回空，交给 prepare_context 的 build_recent(3) 做兜底。
        if not terms:
            return []
""",
    "retriever/empty_terms",
)
W("hippocampus/retriever.py", s)

# ---------- 3) api.py：净化查询 + 回写强化 ----------
s = P("api.py")

sanitize = '''
# ==================== 查询净化 ====================
# 宿主的 system-prompt/assemble 会把「最近一条 user/message」当检索词发过来，
# 但有些事件看着像用户消息、其实是宿主塞的：运行时快照、系统提醒、操作者锚点。
# 拿这些东西去检索记忆，等于用一段英文模板去匹配中文记忆，只会召回到垃圾。
_HOST_BLOCK = re.compile(
    r"<system-reminder>.*?</system-reminder>"
    r"|<available_skills>.*?</available_skills>"
    r"|<project_instructions>.*?</project_instructions>"
    r"|Current runtime context\\..*?(?=\\n\\n|\\Z)"
    r"|\\[OPERATOR ANCHOR.*?\\]"
    r"|## Tools\\s.*?(?=\\n\\n\\n|\\Z)",
    re.S | re.I,
)

# 强化回写去重：同一批 id 在短窗口内只记一次。
# 原因：system-prompt/assemble 一个 turn 里会被调好几次，不去重等于刷分。
_MARK_SEEN = {}
_MARK_WINDOW = 6.0


def sanitize_query(q):
    """去掉宿主注入块；剩下的如果没有实词，返回空串（交给 build_recent 兜底）"""
    if not q:
        return ""
    q = _HOST_BLOCK.sub(" ", q)
    q = re.sub(r"\\s+", " ", q).strip()
    if not q:
        return ""
    try:
        if not tokenize(q):
            return ""
    except Exception:
        pass
    return q[:400]


def reinforce_used(ids):
    """被注入正文的记忆 → 强化一次（半衰期拉长、recall_count+1）"""
    if not ids:
        return 0
    now = time.time()
    fresh = []
    for mid in ids:
        if not mid:
            continue
        if now - _MARK_SEEN.get(mid, 0.0) < _MARK_WINDOW:
            continue
        _MARK_SEEN[mid] = now
        fresh.append(mid)
    if not fresh:
        return 0
    try:
        H.mark_used(fresh)
        return len(fresh)
    except Exception:
        return 0

'''

s = sub1(
    s,
    "import json\nimport os\nimport secrets\nimport threading\nimport time",
    "import json\nimport os\nimport re\nimport secrets\nimport threading\nimport time",
    "api/import_re",
)
s = sub1(
    s,
    "from hippocampus import backup as hip_backup",
    "from hippocampus import backup as hip_backup\nfrom hippocampus.retriever import tokenize",
    "api/import_tokenize",
)

anchor = "class ApiHandler"
if anchor not in s:
    raise SystemExit("MISS: class ApiHandler")
s = s.replace(anchor, sanitize.strip() + "\n\n\n" + anchor, 1)

old_ctx = '''        if path == "/context":
            q = body.get("query") or ""
            with LOCK:
                text, meta = H.prepare_context(q)'''
new_ctx = '''        if path == "/context":
            q = sanitize_query(body.get("query") or "")
            with LOCK:
                text, meta = H.prepare_context(q)
                # 强化回路：只有真正进了注入正文的记忆才计一次
                meta["reinforced"] = reinforce_used(meta.get("ids") or [])'''
s = sub1(s, old_ctx, new_ctx, "api/context_endpoint")

W("api.py", s)
print("OK: engine.py / retriever.py / api.py patched")
