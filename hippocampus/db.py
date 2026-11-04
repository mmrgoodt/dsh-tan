# -*- coding: utf-8 -*-
"""SQLite 存储层：表结构、连接、基础读写"""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import config
from . import migrate

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS memories (
    id              TEXT PRIMARY KEY,
    layer           TEXT NOT NULL,
    thread_id       TEXT,
    content         TEXT NOT NULL,
    summary         TEXT,
    fidelity        TEXT DEFAULT 'summary',   -- verbatim / summary / gist
    source          TEXT DEFAULT 'S3',        -- S1..S5
    confidence      REAL DEFAULT 0.7,
    provenance      TEXT,                     -- JSON {session,turn,quote}
    valence         REAL DEFAULT 0.0,         -- -1..1
    emotion         REAL DEFAULT 1.0,         -- 0.5..2.0
    context_note    TEXT,                     -- 当时的场景
    created         REAL NOT NULL,            -- unix ts
    last_recalled   REAL,
    recall_count    INTEGER DEFAULT 0,
    reject_count    INTEGER DEFAULT 0,
    half_life_days  REAL DEFAULT 3.0,
    protected       INTEGER DEFAULT 0,
    pinned          INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'active',    -- active/dormant/archived/superseded
    sensitivity     TEXT DEFAULT 'normal',    -- normal/private/secret
    superseded_by   TEXT,
    entities        TEXT DEFAULT '[]',        -- JSON array
    tags            TEXT DEFAULT '[]',        -- JSON array
    tokens          INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mem_layer  ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_mem_thread ON memories(thread_id);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created);

CREATE TABLE IF NOT EXISTS threads (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    kind        TEXT DEFAULT 'task',          -- task / chat
    status      TEXT DEFAULT 'doing',         -- doing/paused/done/dropped
    anchors     TEXT DEFAULT '[]',
    summary     TEXT,
    started     REAL,
    ended       REAL,
    distilled_to TEXT
);

CREATE TABLE IF NOT EXISTS missions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    stage       TEXT DEFAULT 'design',        -- design/build/test/ship
    decisions   TEXT DEFAULT '[]',
    open_questions TEXT DEFAULT '[]',
    rounds      INTEGER DEFAULT 0,
    created     REAL,
    updated     REAL
);

CREATE TABLE IF NOT EXISTS open_loops (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,                -- promise/paused/failed/todo/known_issue
    title       TEXT NOT NULL,
    detail      TEXT,
    thread_id   TEXT,
    created     REAL,
    due         REAL,
    status      TEXT DEFAULT 'open',          -- open/done/muted/snoozed
    last_asked  REAL,
    ask_count   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS edges (
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    edge_type   TEXT NOT NULL,                -- thread/entity/causal/semantic
    weight      REAL DEFAULT 1.0,
    created     REAL,
    PRIMARY KEY (source_id, target_id, edge_type)
);

CREATE TABLE IF NOT EXISTS snapshots (
    version     TEXT PRIMARY KEY,
    created     REAL,
    params      TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS golden_cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    keywords    TEXT NOT NULL,                -- JSON array，命中即算对
    last_score  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS persona (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    name        TEXT DEFAULT '',
    pronoun     TEXT DEFAULT '他',
    core        TEXT DEFAULT '',              -- 底色（JSON array）
    traits      TEXT DEFAULT '[]',            -- 长出来的（JSON array of {text,evidence,ts})
    speech      TEXT DEFAULT '[]',            -- 说话习惯
    pacts       TEXT DEFAULT '[]',            -- 我们的约定
    judgments   TEXT DEFAULT '[]',            -- 我学会的判断
    mood        TEXT DEFAULT '{}',            -- 当前心情（JSON）
    stage       TEXT DEFAULT '初识',
    turns       INTEGER DEFAULT 0,
    created     REAL,
    updated     REAL
);

CREATE TABLE IF NOT EXISTS persona_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL,
    kind        TEXT,                         -- trait/speech/pact/judgment/stage/mood
    text        TEXT,
    reason      TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL,
    kind        TEXT,
    payload     TEXT
);
"""


def new_id(prefix="m"):
    return f"{prefix}-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"


def now():
    return time.time()


class DB:
    def __init__(self, path=None):
        self.path = str(path or config.DB_PATH)
        self._init()
        # schema 升级：老库自动补字段，不用删库
        try:
            con = sqlite3.connect(self.path)
            migrate.ensure(con)
            con.close()
        except Exception:
            pass

    def _init(self):
        con = sqlite3.connect(self.path)
        con.executescript(SCHEMA)
        # 单行 persona
        cur = con.execute("SELECT COUNT(*) FROM persona")
        if cur.fetchone()[0] == 0:
            con.execute(
                "INSERT INTO persona (id, created, updated) VALUES (1, ?, ?)",
                (now(), now()),
            )
        con.commit()
        con.close()

    @contextmanager
    def conn(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # ---------- 通用 ----------
    def execute(self, sql, params=()):
        with self.conn() as c:
            cur = c.execute(sql, params)
            return cur.lastrowid

    def query(self, sql, params=()):
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---------- 记忆 ----------
    def add_memory(self, **kw):
        m = {
            "id": kw.get("id") or new_id(),
            "layer": kw["layer"],
            "thread_id": kw.get("thread_id"),
            "content": kw["content"],
            "summary": kw.get("summary") or kw["content"][:60],
            "fidelity": kw.get("fidelity", "summary"),
            "source": kw.get("source", "S3"),
            "confidence": kw.get("confidence", config.SOURCE_CONF.get(kw.get("source", "S3"), 0.7)),
            "provenance": json.dumps(kw.get("provenance") or {}, ensure_ascii=False),
            "valence": kw.get("valence", 0.0),
            "emotion": kw.get("emotion", 1.0),
            "context_note": kw.get("context_note"),
            "created": kw.get("created") or now(),
            "half_life_days": kw.get("half_life_days", config.HALF_LIFE[kw["layer"]]),
            "protected": 1 if (kw.get("protected") or kw["layer"] in config.PROTECTED_LAYERS) else 0,
            "pinned": 1 if kw.get("pinned") else 0,
            "sensitivity": kw.get("sensitivity", "normal"),
            "entities": json.dumps(kw.get("entities") or [], ensure_ascii=False),
            "tags": json.dumps(kw.get("tags") or [], ensure_ascii=False),
            "tokens": config.estimate_tokens(kw["content"]),
        }
        cols = ",".join(m)
        ph = ",".join("?" * len(m))
        self.execute(f"INSERT INTO memories ({cols}) VALUES ({ph})", tuple(m.values()))
        return m["id"]

    def get_memory(self, mid):
        row = self.one("SELECT * FROM memories WHERE id=?", (mid,))
        return _decode_memory(row) if row else None

    def update_memory(self, mid, **fields):
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE memories SET {sets} WHERE id=?", tuple(fields.values()) + (mid,))

    # ---------- Thread ----------
    def add_thread(self, title, kind="task", anchors=None):
        tid = new_id("th")
        self.execute(
            "INSERT INTO threads (id,title,kind,status,anchors,started) VALUES (?,?,?,?,?,?)",
            (tid, title, kind, "doing", json.dumps(anchors or [], ensure_ascii=False), now()),
        )
        return tid

    def find_thread_by_anchor(self, text):
        rows = self.query("SELECT * FROM threads WHERE status IN ('doing','paused') ORDER BY started DESC")
        for r in rows:
            anchors = json.loads(r["anchors"] or "[]")
            if any(a and a in text for a in anchors):
                return r
        return None

    def close_thread(self, tid, summary=None):
        self.execute(
            "UPDATE threads SET status='done', ended=?, summary=COALESCE(?,summary) WHERE id=?",
            (now(), summary, tid),
        )

    # ---------- Open Loops ----------
    def add_loop(self, kind, title, detail="", thread_id=None, due=None):
        lid = new_id("lp")
        self.execute(
            "INSERT INTO open_loops (id,kind,title,detail,thread_id,created,due,status) VALUES (?,?,?,?,?,?,?,?)",
            (lid, kind, title, detail, thread_id, now(), due, "open"),
        )
        return lid

    def open_loops(self):
        return self.query("SELECT * FROM open_loops WHERE status='open' ORDER BY created DESC")

    # ---------- edges ----------
    def link(self, a, b, edge_type="semantic", weight=1.0):
        self.execute(
            "INSERT OR REPLACE INTO edges (source_id,target_id,edge_type,weight,created) VALUES (?,?,?,?,?)",
            (a, b, edge_type, weight, now()),
        )

    # ---------- persona ----------
    def persona(self):
        row = self.one("SELECT * FROM persona WHERE id=1")
        if not row:
            return None
        for k in ("core", "traits", "speech", "pacts", "judgments", "mood"):
            try:
                row[k] = json.loads(row[k] or ("[]" if k != "mood" else "{}"))
            except Exception:
                row[k] = [] if k != "mood" else {}
        return row

    def save_persona(self, **fields):
        if not fields:
            return
        for k in ("core", "traits", "speech", "pacts", "judgments", "mood"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        fields["updated"] = now()
        sets = ",".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE persona SET {sets} WHERE id=1", tuple(fields.values()))

    def persona_log(self, kind, text, reason=""):
        self.execute(
            "INSERT INTO persona_log (ts,kind,text,reason) VALUES (?,?,?,?)",
            (now(), kind, text, reason),
        )

    # ---------- 统计 ----------
    def stats(self):
        total = self.one("SELECT COUNT(*) c FROM memories WHERE status='active'")["c"]
        by_layer = self.query(
            "SELECT layer, COUNT(*) c FROM memories WHERE status='active' GROUP BY layer"
        )
        loops = self.one("SELECT COUNT(*) c FROM open_loops WHERE status='open'")["c"]
        threads = self.one("SELECT COUNT(*) c FROM threads")["c"]
        return {
            "memories": total,
            "by_layer": {r["layer"]: r["c"] for r in by_layer},
            "open_loops": loops,
            "threads": threads,
        }


def _decode_memory(row):
    if not row:
        return None
    for k in ("entities", "tags"):
        try:
            row[k] = json.loads(row[k] or "[]")
        except Exception:
            row[k] = []
    try:
        row["provenance"] = json.loads(row["provenance"] or "{}")
    except Exception:
        row["provenance"] = {}
    return row
