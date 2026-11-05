# -*- coding: utf-8 -*-
"""概念别名：让「迅雷」和「Thunder」变成同一个东西

现在这套系统最大的短板是不懂语义。最典型的表现：
「迅雷」和「Thunder」是两个毫不相干的概念节点，问中文召不出英文那半边。

完全靠模型解决需要外部服务，但有一半可以从数据里推出来：
**同一批记忆里总是成对出现的「纯英文词」和「中文词」，多半是一回事。**

    D:\\APP\\Thunder   禁用迅雷广告组件   ← 一条记忆里同时出现
    gdt / 广点通                        ← 同上

做法：
  1. 扫所有记忆，统计「英文概念 ↔ 中文概念」在**同一条记忆**里的共现
  2. 共现 ≥2 次，且双方都是「专名长相」（不是通用词），判为别名
  3. 建 aliases 表，激活一个时同时点亮另一个

不追求百分之百准。多认错几个别名，代价是偶发误召回；
认不出来，代价是永远想不起另一半。
"""
import json
import re

from .cortex import ensure_schema as cortex_schema

CJK = re.compile(r"[\u4e00-\u9fa5]")
ASCII_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{1,}$")

# 太通用的词不能当别名锚点：它们跟谁共现都正常
GENERIC_EN = {
    "app", "appdata", "api", "config", "data", "file", "files", "index", "json",
    "main", "new", "node", "npm", "path", "program", "run", "script", "src",
    "temp", "test", "tmp", "user", "users", "win", "windows", "www", "html",
    "true", "false", "null", "none", "error", "info", "log", "logs", "bin",
}
GENERIC_CN = {"本机", "这台", "这个", "那个", "东西", "问题", "系统", "文件", "目录"}


# 种子别名：这些是语义知识，靠共现统计永远推不出来，直接写。
SEED = [
    ("迅雷", "thunder"), ("迅雷", "xlgame"), ("广告", "gdt"), ("广点通", "gdt"),
    ("网盘", "onedrive"), ("飞书", "feishu"), ("微信", "wechat"), ("钉钉", "dingtalk"),
    ("代理", "clash"), ("代理", "tun"), ("虚拟网卡", "meta"),
    ("记忆", "memory"), ("提示词", "prompt"), ("会话", "session"),
    ("插件", "plugin"), ("工作区", "workspace"), ("公钥", "ssh"),
    ("版本库", "git"), ("仓库", "github"), ("接口", "api"), ("令牌", "token"),
    ("端口", "port"), ("配置", "config"), ("环境变量", "path"),
    ("开机自启", "startup"), ("密码", "password"), ("账号", "account"),
    ("显卡", "gpu"), ("内存", "ram"), ("硬盘", "disk"),
]


def ensure_schema(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS aliases (
            a TEXT, b TEXT, score REAL DEFAULT 0, PRIMARY KEY (a, b)
        )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_alias_a ON aliases(a)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_alias_b ON aliases(b)")


def _ents(m):
    raw = m.get("entities")
    if isinstance(raw, list):
        return [e for e in raw if e]
    try:
        v = json.loads(raw or "[]")
        return [e for e in v if e] if isinstance(v, list) else []
    except Exception:
        return []


def discover(db):
    """扫出候选别名对"""
    ensure_schema(db)
    rows = db.query(
        "SELECT id, content, summary, entities FROM memories WHERE status='active'")

    pair = {}
    for r in rows:
        # 从实体 + 正文里的英文词一起找
        body = ((r.get("content") or "") + " " + (r.get("summary") or ""))
        cn = set(e for e in _ents(r) if CJK.search(e) and e not in GENERIC_CN)
        en = set(x for x in _ents(r) if ASCII_WORD.match(x) and x.lower() not in GENERIC_EN)
        for w in re.findall(r"[A-Za-z][A-Za-z0-9_.\-]{2,}", body):
            if w.lower() not in GENERIC_EN:
                en.add(w)

        if not cn or not en:
            continue
        # 同一条记忆里，每个中文概念都跟每个英文词算一次共现
        for c in cn:
            for e in en:
                # 太长的英文（整条路径）不当别名
                if len(e) > 20 or chr(92) in e or "/" in e or "." in e:
                    continue
                if len(c) > 12:
                    continue
                k = (c, e)
                pair[k] = pair.get(k, 0) + 1

    out = []
    for (c, e), n in pair.items():
        if n < 2:
            continue
        # 分数 = 共现次数，但英文词如果跟太多中文概念共现，说明它不是专名
        spread = sum(1 for (cc, ee), mm in pair.items() if ee == e and mm >= 2)
        if spread > 6:
            continue
        score = n / (1.0 + spread * 0.3)
        out.append((score, c, e))
    out.sort(reverse=True)

    db.execute("DELETE FROM aliases")
    written = 0
    with db.conn() as con:
        for c, e in SEED:
            a, b = sorted((c, e))
            con.execute("INSERT OR REPLACE INTO aliases (a, b, score) VALUES (?,?,?)",
                        (a, b, 9.0))
            written += 1
        for score, c, e in out[:400]:
            a, b = sorted((c, e))
            con.execute("INSERT OR REPLACE INTO aliases (a, b, score) VALUES (?,?,?)",
                        (a, b, round(score, 3)))
            written += 1
    return {"candidates": len(out), "written": written}


def expand(db, concepts):
    """给一批概念补上它们的别名"""
    if not concepts:
        return concepts
    ensure_schema(db)
    out = set(concepts)
    ph = ",".join("?" * len(concepts))
    for r in db.query(
        f"SELECT a, b FROM aliases WHERE a IN ({ph}) OR b IN ({ph})",
        tuple(concepts) + tuple(concepts)
    ):
        out.add(r["a"])
        out.add(r["b"])
    return out
