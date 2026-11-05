# -*- coding: utf-8 -*-
"""他说一次就该算数

现在的毛病：人格靠 `detect_preferences` 从每句话里「猜」，
而且同一类信号要攒够 2 次才固化。于是：

    他说「以后跟我说话可爱一点」
    → 系统当成一条普通偏好，攒着
    → 下次我又写成运维报告
    → 他得再说一遍

这不是记不住，是**机制错了**。他把要求说出口的那一刻，就是指令，
不是「样本」，不需要再采样两次确认。

改法：
  1. 认出「元指令」——他在直接规定我该怎么说话（含 以后/一直/每次 + 语气/说话/别/不要）
  2. 认出来就**立刻**写进 persona 的 rules 字段，不等第二次
  3. rules 有最高优先级：渲染时排最前面，自动学到的东西不许覆盖它
  4. 只有他明确说「改掉/换成 X」时才更新 rules
"""
import io
import re
import time

# 元指令：直接规定我怎么说话
_META = re.compile(
    r"(以后|从今|今后|一直|每次都|永远|接下来|记住|记着|要记得)"
    r".{0,24}"
    r"(说话|语气|跟我说|跟我讲|回复|回答|表达|风格|可爱|正式|僵硬|语气词)"
)
# 或者：直接说「跟我说话要 X」
_META2 = re.compile(r"(跟我|和我)说话.{0,12}(要|别|不要|尽量|可爱|正式|轻松|直接)")
# 明确的「改掉」
# 只认明确的撤销。「别那么正式」是提要求，不是取消要求 ——
# 之前把「别那么」也算撤销，一句正常要求直接被当成清空指令。
_REVOKE = re.compile(r"(改掉这条|取消这条|撤销|当我没说|忘掉这个要求)")


def is_meta_command(text):
    t = (text or "").strip()
    if len(t) < 6 or len(t) > 120:
        return False
    return bool(_META.search(t) or _META2.search(t))


def ensure_column(db):
    try:
        # db.query 返回的是 dict，用 r[1] 会 KeyError，然后被 except 吞掉 ——
        # 结果表结构一直没加成功，capture 每次都静默失败。
        cols = [r["name"] for r in db.query("PRAGMA table_info(persona)")]
        if "rules" not in cols:
            db.execute("ALTER TABLE persona ADD COLUMN rules TEXT DEFAULT '[]'")
    except Exception:
        pass


def _load(db):
    import json
    r = db.one("SELECT rules FROM persona WHERE id=(SELECT id FROM persona LIMIT 1)")
    if not r or not r.get("rules"):
        return []
    try:
        v = json.loads(r["rules"])
        return v if isinstance(v, list) else []
    except Exception:
        return []


def capture(db, text):
    """他刚说的话里有没有直接指令。有就立刻固化，不等第二次。

    返回 True 表示「这句话被当成指令收下了」。
    """
    if not is_meta_command(text):
        return False
    ensure_column(db)
    import json

    rules = _load(db)
    now = time.time()

    # 说「改掉」→ 清空旧的
    if _REVOKE.search(text):
        rules = [{"text": "（他取消了一条旧要求）", "ts": now, "explicit": True}]

    # 同一条不重复加；新说的覆盖旧的说法
    body = text.strip()[:120]
    rules = [r for r in rules if not (isinstance(r, dict) and body[:16] in (r.get("text") or ""))]
    rules.append({"text": body, "ts": now, "explicit": True})
    rules = rules[-8:]          # 最多记 8 条，别堆成山

    db.execute(
        "UPDATE persona SET rules=? WHERE id=(SELECT id FROM persona LIMIT 1)",
        (json.dumps(rules, ensure_ascii=False),))
    return True


def get_rules(db):
    ensure_column(db)
    return [r.get("text", "") for r in _load(db) if isinstance(r, dict) and r.get("text")]


def install():
    """把 capture 接进 observe：人格学习之前先看是不是指令"""
    p = "hippocampus/persona.py"
    s = io.open(p, encoding="utf-8").read()
    if "from . import rules" in s:
        print("已经接过了")
        return False
    old = '''        text = _clean(user_text)
        if text:
            for kind, clause, why in detect_preferences(text):
                self._bump(kind, clause, updates, p, why)'''
    new = '''        text = _clean(user_text)
        # 先看这句话是不是「直接指令」。是的话立刻固化，不进采样流程 ——
        # 他把要求说出口就该算数，不该再等第二次确认。
        if text:
            try:
                from . import rules
                rules.capture(self.db, text)
            except Exception:
                pass
        if text:
            for kind, clause, why in detect_preferences(text):
                self._bump(kind, clause, updates, p, why)'''
    if old not in s:
        raise SystemExit("MISS: observe")
    io.open(p, "w", encoding="utf-8", newline="\n").write(s.replace(old, new, 1))
    print("capture 接进 observe")
    return True


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hippocampus.db import DB
    db = DB()
    ensure_column(db)
    install()
    for t in ["以后跟我说话都可爱一点，别那么正式",
              "记住，说话别太生硬",
              "这个东西挺好的",
              "以后都走 TUN 模式"]:
        print(("收下 " if is_meta_command(t) else "不是 "), t)
