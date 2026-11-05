# -*- coding: utf-8 -*-
"""回补历史时保留原始时间戳

补进来的对话如果全用 now() 当 created，时间线就全挤在今天：
「昨天干了什么」会答错，衰减也算错。给 writer/engine 开一个 ts 参数。
"""
import io


def sub1(path, old, new, tag):
    s = io.open(path, encoding="utf-8").read()
    if old not in s:
        raise SystemExit("MISS: " + tag)
    if s.count(old) != 1:
        raise SystemExit("AMBIGUOUS(%d): %s" % (s.count(old), tag))
    io.open(path, "w", encoding="utf-8", newline="\n").write(s.replace(old, new, 1))


# ---------- writer.py ----------
sub1(
    "hippocampus/writer.py",
    '''    def ingest_turn(self, user_text="", assistant_text="", session="", turn=0, context_note=""):
        """处理一轮对话，返回本次写入的结果"""
        out = {"memories": [], "loops": [], "thread": None, "noise": 0}

        # 1) 用户侧
        if user_text:
            r = self._ingest_one(user_text, "user", session, turn, context_note)''',
    '''    def ingest_turn(self, user_text="", assistant_text="", session="", turn=0,
                    context_note="", ts=None):
        """处理一轮对话，返回本次写入的结果

        ts: 这一轮的真实时间（回补历史时用，缺省就取当前时间）
        """
        out = {"memories": [], "loops": [], "thread": None, "noise": 0}

        # 1) 用户侧
        if user_text:
            r = self._ingest_one(user_text, "user", session, turn, context_note, ts)''',
    "writer/ingest_turn",
)

sub1(
    "hippocampus/writer.py",
    '''        if assistant_text:
            r = self._ingest_one(assistant_text, "assistant", session, turn, context_note)''',
    '''        if assistant_text:
            r = self._ingest_one(assistant_text, "assistant", session, turn, context_note, ts)''',
    "writer/assistant_call",
)

sub1(
    "hippocampus/writer.py",
    '''    def _ingest_one(self, text, role, session, turn, context_note):''',
    '''    def _ingest_one(self, text, role, session, turn, context_note, ts=None):''',
    "writer/_ingest_one_sig",
)

# 噪音分支也带时间
sub1(
    "hippocampus/writer.py",
    '''                source="S1" if role == "user" else "S3",
                emotion=1.0,
                context_note=context_note,
                entities=[],
                tags=["noise"],
            )''',
    '''                source="S1" if role == "user" else "S3",
                emotion=1.0,
                created=ts,
                context_note=context_note,
                entities=[],
                tags=["noise"],
            )''',
    "writer/noise_created",
)

sub1(
    "hippocampus/writer.py",
    '''            entities=info["entities"],
            tags=[info["layer"]],
            provenance={"session": session, "turn": turn, "quote": text[:200]},
        )''',
    '''            entities=info["entities"],
            tags=[info["layer"]],
            created=ts,
            provenance={"session": session, "turn": turn, "quote": text[:200]},
        )''',
    "writer/created",
)

# ---------- engine.py ----------
sub1(
    "hippocampus/engine.py",
    '''    def after_turn(self, user_text="", assistant_text="", session="", turn=0, context_note=""):
        r = self.writer.ingest_turn(user_text, assistant_text, session, turn, context_note)''',
    '''    def after_turn(self, user_text="", assistant_text="", session="", turn=0, context_note="", ts=None):
        r = self.writer.ingest_turn(user_text, assistant_text, session, turn, context_note, ts)''',
    "engine/after_turn",
)

print("OK: ts passthrough")
