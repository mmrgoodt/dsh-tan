# -*- coding: utf-8 -*-
"""写入闸：只留有价值的进长期记忆

/turn 原来是「什么都记」，360 条里 256 条是流水账，抢的是真事的注入预算。
现在 memory_save 由我主动写重要的，所以这条自动通道可以收紧。

判据（任一命中才存）：
  · 有路径 / 代码 / 版本 / 注册表 / URL / 扩展名
  · 提到目录、路径、文件夹（短句才算）
  · 是明确的决定（决定、以后都、改成、别再）
  · 是教训 / 明确要求记住

写成函数而不是一条大正则 —— 之前那条正则里 \\b\\d+\\.\\d+\\.\\d+\\b
连 v24.22.1 都匹配不到（v 和 2 都是 \\w，中间没有词边界），3.15 又只有两段。
拆开写反而不会出错。
"""
import re

_EXT = re.compile(r"\.(py|js|json|yml|yaml|md|db|exe|bat|ps1|toml|txt|log)\b")
_VER = re.compile(r"\d+\.\d+")
_PATH = re.compile(r"[A-Za-z]:[\\/]")
_CNNAME = re.compile(r"[\w\u4e00-\u9fa5]{2,}_[\w\u4e00-\u9fa5]{2,}")
_DIRHINT = re.compile(r"(目录|路径|文件夹|保存在|存在哪|放在哪)")
_DECISION = re.compile(
    r"(决定|以后都|以后就|改成|换成|不用了|不再|别再|不要了|定下来|就这么办|"
    r"记住|记着|坑|教训|必须|千万不要)"
)


def has_info(text):
    """有没有可复用的坐标"""
    t = text or ""
    if not t:
        return False
    if _PATH.search(t):
        return True
    if "http://" in t or "https://" in t or "```" in t:
        return True
    if "HKCU" in t or "HKLM" in t or "HKEY_" in t:
        return True
    if _EXT.search(t):
        return True
    if _CNNAME.search(t):
        return True
    if _VER.search(t):
        return True
    if _DIRHINT.search(t) and len(t) < 60:
        return True
    return False


def should_keep(text, layer):
    """这条值不值得进长期记忆"""
    if layer in ("L2-", "L3", "L4", "L5"):
        return True
    t = (text or "").strip()
    if len(t) < 8:
        return False
    if has_info(t):
        return True
    if _DECISION.search(t):
        return True
    return False


def install_gate():
    """给 writer 装上这道闸（幂等）"""
    import io
    p = "hippocampus/writer.py"
    s = io.open(p, encoding="utf-8").read()
    if "should_keep" in s:
        print("闸已经装过了")
        return False
    old = "    def _ingest_one(self, text, role, session, turn, context_note, ts=None):\n        info = extractor.extract(text, role)"
    new = (
        "    def _ingest_one(self, text, role, session, turn, context_note, ts=None):\n"
        "        info = extractor.extract(text, role)\n"
        "        # 只留有价值的：有具体坐标，或者明确是决策/教训。\n"
        "        # noise 也要过闸，否则「好的没问题」会从后门进来。\n"
        "        try:\n"
        "            from .gate import should_keep\n"
        "            if info.get(\"keep\"):\n"
        "                if not should_keep(info.get(\"content\") or text, info.get(\"layer\")):\n"
        "                    return {\"memories\": [], \"loops\": [], \"noise\": 0, \"thread\": None}\n"
        "        except Exception:\n"
        "            pass"
    )
    if old not in s:
        raise SystemExit("MISS")
    io.open(p, "w", encoding="utf-8", newline="\n").write(s.replace(old, new, 1))
    print("闸已装到 writer")
    return True
