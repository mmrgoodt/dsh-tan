# -*- coding: utf-8 -*-
"""api.py 补丁：查询净化 + 强化回写"""
import io

p = "api.py"
s = io.open(p, encoding="utf-8").read()


def sub1(text, old, new, tag):
    if old not in text:
        raise SystemExit("MISS: " + tag)
    if text.count(old) != 1:
        raise SystemExit("AMBIGUOUS(%d): %s" % (text.count(old), tag))
    return text.replace(old, new)


s = sub1(
    s,
    "import json\nimport os\nimport secrets\nimport threading\nimport time",
    "import json\nimport os\nimport re\nimport secrets\nimport threading\nimport time",
    "import_re",
)
s = sub1(
    s,
    "from hippocampus import backup as hip_backup",
    "from hippocampus import backup as hip_backup\nfrom hippocampus.retriever import tokenize",
    "import_tokenize",
)

sanitize = '''
# ==================== 查询净化 + 强化回写 ====================
# 宿主的 system-prompt/assemble 会把「最近一条 user/message」当检索词发过来，
# 但有些事件看着像用户消息、其实是宿主塞的：运行时快照、系统提醒、操作者锚点。
# 拿这些去检索记忆，等于用一段英文模板匹配中文记忆，只会召回到垃圾。
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
# system-prompt/assemble 一个 turn 里会被调好几次，不去重等于刷分。
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

s = sub1(s, "class Handler(BaseHTTPRequestHandler):", sanitize.strip() + "\n\n\nclass Handler(BaseHTTPRequestHandler):", "anchor")

s = sub1(
    s,
    '''        if path == "/context":
            q = body.get("query") or ""
            with LOCK:
                text, meta = H.prepare_context(q)''',
    '''        if path == "/context":
            q = sanitize_query(body.get("query") or "")
            with LOCK:
                text, meta = H.prepare_context(q)
                # 强化回路：只有真正进了注入正文的记忆才计一次
                meta["reinforced"] = reinforce_used(meta.get("ids") or [])''',
    "context_endpoint",
)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("OK: api.py patched")
