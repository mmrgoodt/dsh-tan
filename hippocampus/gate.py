# -*- coding: utf-8 -*-
"""收紧 /turn 的自动抽取

问题：360 条记忆里 256 条是「我重启了一下」「我觉得还要优化」这种流水账，
全都挂在事实层抢注入预算。数据库大不等于记得多。

现在 memory_save 工具已经通了，真正重要的东西由我主动记。
所以 /turn 这条自动通道可以收紧：**只留带具体坐标的、或者明确是决策/教训的**。

判据（任一命中才存）：
  · 有路径 / 代码 / 版本号 / 注册表 / URL
  · 是明确的决定（决定、以后都、不用了、改成）
  · 是教训（别再、坑、注意）
  · 用户明确要求记（记住、记着）

其余的丢掉。宁可漏，不要塞。
"""
import io
import re

# 具体坐标
INFO = re.compile(
    r"[A-Za-z]:\\|/[\w.]{2,}/|```|https?://|\b\d+\.\d+\.\d+\b|"
    r"\b(HKCU|HKLM|HKEY_)\b|\bSoftware\\|"
    r"\.(py|js|json|yml|yaml|md|db|exe|bat|ps1|toml)\b"
)
# 决定 / 教训 / 明确要求记住
DECISION = re.compile(
    r"(决定|以后都|以后就|改成|换成|不用了|不再|别再|不要了|定下来|就这么办|"
    r"记住|记着|坑|教训|注意|小心|必须|千万不要)"
)


def should_keep(text, layer):
    """这条值不值得进长期记忆"""
    if layer in ("L2-", "L3", "L4", "L5"):
        return True          # 教训 / 方法 / 身份，本来就稀有
    t = text or ""
    if len(t) < 8:
        return False         # 太短，没信息
    if INFO.search(t):
        return True          # 有具体坐标
    if DECISION.search(t):
        return True          # 是决定或教训
    return False


def install_gate():
    """给 writer 装上这道闸"""
    p = "hippocampus/writer.py"
    s = io.open(p, encoding="utf-8").read()
    if "should_keep" in s:
        print("闸已经装过了")
        return False

    old = '''    def _ingest_one(self, text, role, session, turn, context_note, ts=None):
        info = extractor.extract(text, role)'''
    new = '''    def _ingest_one(self, text, role, session, turn, context_note, ts=None):
        info = extractor.extract(text, role)
        # 只留有价值的：有具体坐标，或者明确是决策/教训。
        # 不加这道闸，360 条里 256 条是流水账，抢的是真事的预算。
        try:
            from .gate import should_keep
            if info.get("keep") and not info.get("noise"):
                if not should_keep(info.get("content") or text, info.get("layer")):
                    return {"memories": [], "loops": [], "noise": 0, "thread": None}
        except Exception:
            pass'''
    if old not in s:
        raise SystemExit("MISS: writer._ingest_one")
    s = s.replace(old, new, 1)
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)
    print("闸已装到 writer")
    return True


if __name__ == "__main__":
    install_gate()
