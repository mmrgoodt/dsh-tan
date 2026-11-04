# -*- coding: utf-8 -*-
"""Tan HTTP API + 控制台后端

启动：python api.py [--port 18765]
控制台：http://127.0.0.1:18765/

接口：
  GET  /health /status /checklist /report /graph /loops /gold
  GET  /config         读参数
  GET  /persona        读人格
  GET  /memories       记忆列表（?layer=&q=&limit=&status=）
  GET  /threads        Thread 列表
  GET  /edges          关系统计
  POST /context        对话前拿注入文本
  POST /turn           对话后写入
  POST /recall         检索
  POST /remember       手动记一条
  POST /proactive      取一条主动消息
  POST /gold           跑黄金测试
  POST /reject         标记记错
  POST /config         保存参数
  POST /persona        保存人格
  POST /memory/pin     钉住
  POST /memory/forget  遗忘
  POST /memory/reinforce 强化
  POST /loop/close     关闭未闭合
  POST /loop/mute      静音
  POST /sweep          手动清理
"""
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from hippocampus import Hippocampus, config
from hippocampus import backup as hip_backup

H = Hippocampus()
LOCK = threading.Lock()
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = config.ROOT / "config.json"

TOKEN_FILE = config.ROOT / "token.txt"


def _load_token():
    """读 token；不存在就生成一个（32 字节 URL-safe）"""
    try:
        if TOKEN_FILE.exists():
            t = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if len(t) >= 16:
                return t
        t = secrets.token_urlsafe(32)
        TOKEN_FILE.write_text(t, encoding="utf-8")
        return t
    except Exception:
        return secrets.token_urlsafe(32)


AUTH_TOKEN = _load_token()

# 不需要 token 的路径（看门狗探活、静态页面本身）
PUBLIC_PATHS = {"/health", "/", "/console.html", "/index.html",
                "/neuron3d.html", "/favicon.ico",
                "/auth/login"}   # 登录接口本身要能访问，否则死锁


TUNABLE = {
    "half_life_l1": ("HALF_LIFE", "L1"),
    "half_life_l2": ("HALF_LIFE", "L2"),
    "reinforce_factor": ("REINFORCE_FACTOR", None),
    "reject_factor": ("REJECT_FACTOR", None),
    "budget_identity": ("BUDGET", "identity"),
    "budget_semantic": ("BUDGET", "semantic"),
    "budget_episodic": ("BUDGET", "episodic"),
    "proactive_max_per_day": ("PROACTIVE_MAX_PER_DAY", None),
    "proactive_quiet_start": ("PROACTIVE_QUIET_START", None),
    "proactive_quiet_end": ("PROACTIVE_QUIET_END", None),
    "loops_per_turn": ("LOOPS_PER_TURN", None),
    "loop_cooldown_hours": ("LOOP_COOLDOWN_HOURS", None),
    "noise_archive_days": ("NOISE_ARCHIVE_DAYS", None),
}


def load_overrides():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def config_view():
    ov = load_overrides()
    out = {}
    for key, (attr, sub) in TUNABLE.items():
        if key in ov:
            out[key] = ov[key]
        elif sub is not None and isinstance(getattr(config, attr, None), dict):
            out[key] = getattr(config, attr)[sub]
        else:
            out[key] = getattr(config, attr, None)
    return out


def save_config(patch):
    ov = load_overrides()
    applied = {}
    for key, val in (patch or {}).items():
        if key not in TUNABLE:
            continue
        attr, sub = TUNABLE[key]
        try:
            if sub is not None:
                cur = getattr(config, attr)
                if isinstance(cur, dict):
                    nv = float(val)
                    cur[sub] = int(nv) if nv == int(nv) and "." not in str(val) else nv
                    setattr(config, attr, cur)
                    applied[key] = cur[sub]
                else:
                    continue
            else:
                cur = getattr(config, attr)
                nv = float(val)
                nv = int(nv) if isinstance(cur, int) else nv
                setattr(config, attr, nv)
                applied[key] = nv
            ov[key] = applied[key]
        except Exception:
            continue
    try:
        CONFIG_FILE.write_text(json.dumps(ov, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return applied


def json_resp(handler, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Expose-Headers", "X-Backup-Name, X-Backup-Bytes")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Tan-Source, Authorization")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def html_resp(handler, text, code=200):
    body = text.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Expose-Headers", "X-Backup-Name, X-Backup-Bytes")
    handler.end_headers()
    handler.wfile.write(body)


MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".map": "application/json; charset=utf-8",
}


def serve_static(handler, name):
    """从 visual/ 目录发静态文件（只允许扁平文件名，防目录穿越）。"""
    safe = os.path.basename(name)
    f = os.path.join(HERE, "visual", safe)
    if not os.path.isfile(f):
        return json_resp(handler, {"error": "not found", "path": safe}, 404)
    ext = os.path.splitext(safe)[1].lower()
    ctype = MIME.get(ext, "application/octet-stream")
    try:
        with open(f, "rb") as fh:
            body = fh.read()
    except Exception as e:
        return json_resp(handler, {"error": str(e)}, 500)
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Expose-Headers", "X-Backup-Name, X-Backup-Bytes")
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "Tan/0.2"

    def _authorized(self):
        """检查 token：header / query / cookie 三种带法都认"""
        try:
            p = urlparse(self.path)
            if p.path in PUBLIC_PATHS:
                return True
            if p.path.startswith("/.well-known"):
                return True

            # 0) DSH 面板专用 header（面板在桌面应用里，算可信环境）
            if (self.headers.get("X-Tan-Source") or "") == "dsh-plugin":
                return True

            # 1) Authorization: Bearer xxx
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                if secrets.compare_digest(auth[7:].strip(), AUTH_TOKEN):
                    return True

            # 2) ?token=xxx
            qt = parse_qs(p.query).get("token", [""])[0]
            if qt and secrets.compare_digest(qt, AUTH_TOKEN):
                return True

            # 3) Cookie: tan_token=xxx
            cookie = self.headers.get("Cookie") or ""
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("tan_token="):
                    if secrets.compare_digest(part[10:], AUTH_TOKEN):
                        return True
            return False
        except Exception:
            return False

    def _deny(self):
        body = json.dumps({
            "ok": False,
            "error": "需要 token",
            "hint": "在 ~/.dsh/tan/token.txt 里，或访问 /?token=<token>",
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Tan-Source, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ================= GET =================
    def do_GET(self):
        if not self._authorized():
            return self._deny()
        p = urlparse(self.path)
        path = p.path
        qs = parse_qs(p.query)

        # 首页 → 控制台
        if path in ("/", "/console", "/index.html"):
            return serve_static(self, "console.html")

        # 其余静态文件（neuron3d.html / *.png / *.css / *.js）
        if path.startswith("/") and path.count("/") == 1:
            name = path[1:]
            if name and ("." in name) and not name.startswith("."):
                return serve_static(self, name)

        if path == "/health":
            return json_resp(self, {"ok": True, "version": "0.2", "db": str(config.DB_PATH)})
        if path == "/status":
            return json_resp(self, H.status())
        if path == "/checklist":
            return json_resp(self, {"text": H.checklist()})
        if path == "/report":
            return json_resp(self, {"text": H.weekly_report()})
        if path == "/graph":
            return json_resp(self, graph_data(int(qs.get("limit", ["80"])[0])))
        if path == "/loops":
            return json_resp(self, {"loops": H.db.open_loops()})
        if path == "/gold":
            with LOCK:
                return json_resp(self, H.run_golden(verbose=True))
        if path == "/config":
            return json_resp(self, {"config": config_view()})

        # ---- 备份 ----
        if path == "/export":
            with LOCK:
                st = H.status()
                data = hip_backup.export_bytes(config.DB_PATH, stats=st, config_view=config_view())
            name = hip_backup.default_name()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Backup-Name", name)
            self.send_header("X-Backup-Bytes", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/backups":
            return json_resp(self, {"backups": hip_backup.list_local(config.ROOT)})
        if path == "/persona":
            return json_resp(self, {"persona": persona_view()})
        if path == "/memories":
            return json_resp(self, memories_view(
                layer=qs.get("layer", [None])[0] or None,
                keyword=qs.get("q", [""])[0],
                limit=int(qs.get("limit", ["80"])[0]),
                status=qs.get("status", ["active"])[0],
            ))
        if path == "/threads":
            rows = H.db.query(
                "SELECT t.*, (SELECT COUNT(*) FROM memories m WHERE m.thread_id=t.id) AS n "
                "FROM threads t ORDER BY t.started DESC LIMIT 60")
            return json_resp(self, {"threads": rows})
        if path == "/edges":
            return json_resp(self, {"edges": H.db.query(
                "SELECT edge_type, COUNT(*) c FROM edges GROUP BY edge_type")})

        json_resp(self, {"error": "not found"}, 404)

    # ================= POST =================
    def do_POST(self):
        if not self._authorized():
            return self._deny()
        global H
        p = urlparse(self.path)
        path = p.path

        # ---- 二进制上传（必须在 _body() 之前读，否则请求体被消耗掉）----
        if path in ("/import", "/import/preview"):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n > 0 else b""
            if not raw:
                return json_resp(self, {"ok": False, "error": "没有收到文件内容"}, 400)
            if path == "/import/preview":
                try:
                    return json_resp(self, hip_backup.inspect(raw))
                except Exception as e:
                    return json_resp(self, {"ok": False, "error": str(e)}, 400)
            # /import
            try:
                with LOCK:
                    r = hip_backup.import_bytes(raw, config.DB_PATH, config.BACKUP_DIR)
                H = Hippocampus()          # 库换了，重建引擎
                with LOCK:
                    st = H.status()
                return json_resp(self, {**r, "stats": st})
            except Exception as e:
                return json_resp(self, {"ok": False, "error": str(e)}, 400)

        body = self._body()

        if path == "/auth/login":
            tk = (body.get("token") or "").strip()
            ok = bool(tk) and secrets.compare_digest(tk, AUTH_TOKEN)
            if ok:
                r = json_resp(self, {"ok": True})
                return
            return json_resp(self, {"ok": False, "error": "token 不对"}, 401)

        if path == "/context":
            q = body.get("query") or ""
            with LOCK:
                text, meta = H.prepare_context(q)
            # 调试日志：看不同工作区的请求都带了什么
            try:
                import time as _t
                _line = "%s  q=%r  -> %d 条/%d tok" % (
                    _t.strftime("%m-%d %H:%M:%S"), q[:40],
                    meta.get("memories", 0), meta.get("tokens", 0))
                if not text:
                    _line += "  [空]"
                with open(config.ROOT / "context.log", "a", encoding="utf-8") as f:
                    f.write(_line + chr(10))
            except Exception:
                pass
            return json_resp(self, {"text": text, **meta})
        if path == "/turn":
            with LOCK:
                r = H.after_turn(
                    user_text=body.get("user") or "",
                    assistant_text=body.get("assistant") or "",
                    session=body.get("session") or "",
                    turn=int(body.get("turn") or 0),
                    context_note=body.get("note") or "")
            return json_resp(self, {"ok": True, **r})
        if path == "/recall":
            with LOCK:
                mems = H.recall(body.get("query") or "", limit=int(body.get("limit") or 8))
            return json_resp(self, {"memories": mems})
        if path == "/remember":
            with LOCK:
                mid = H.remember(body.get("text") or "", layer=body.get("layer", "L2"),
                                 pinned=bool(body.get("pinned")))
            return json_resp(self, {"ok": True, "id": mid})
        if path == "/proactive":
            with LOCK:
                text, slot = H.ask_next_loop()
            return json_resp(self, {"text": text, "slot": slot})
        if path == "/gold":
            with LOCK:
                return json_resp(self, H.run_golden(verbose=True))
        if path == "/reject":
            with LOCK:
                H.mark_rejected(body.get("ids") or [])
            return json_resp(self, {"ok": True})
        if path == "/import/preview":
            raw = b""
            n = int(self.headers.get("Content-Length") or 0)
            if n > 0:
                raw = self.rfile.read(n)
            try:
                return json_resp(self, hip_backup.inspect(raw))
            except Exception as e:
                return json_resp(self, {"ok": False, "error": str(e)}, 400)

        if path == "/backup":
            with LOCK:
                return json_resp(self, hip_backup.make_local_backup(config.DB_PATH))

        if path == "/config":
            with LOCK:
                applied = save_config(body)
            return json_resp(self, {"ok": True, "applied": applied, "config": config_view()})
        if path == "/persona":
            with LOCK:
                save_persona(body)
            return json_resp(self, {"ok": True, "persona": persona_view()})
        if path == "/memory/edit":
            with LOCK:
                ok = H.edit_memory(
                    body.get("id"),
                    content=body.get("content"),
                    summary=body.get("summary"),
                )
            return json_resp(self, {"ok": bool(ok)})

        if path == "/memory/pin":
            with LOCK:
                H.pin(body.get("id"), bool(body.get("value", True)))
            return json_resp(self, {"ok": True})
        if path == "/memory/forget":
            with LOCK:
                H.forget(body.get("id"), body.get("mode", "cold"))
            return json_resp(self, {"ok": True})
        if path == "/memory/reinforce":
            with LOCK:
                H.mark_used([body.get("id")])
            return json_resp(self, {"ok": True})
        if path == "/loop/close":
            with LOCK:
                H.loops.close(body.get("id"))
            return json_resp(self, {"ok": True})
        if path == "/loop/mute":
            with LOCK:
                H.loops.mute(body.get("id"))
            return json_resp(self, {"ok": True})
        if path == "/sweep":
            with LOCK:
                return json_resp(self, H.forgetter.sweep())

        json_resp(self, {"error": "not found"}, 404)


def persona_view():
    p = H.persona.get() or {}
    return {
        "name": p.get("name") or "",
        "pronoun": p.get("pronoun") or "他",
        "stage": p.get("stage") or "初识",
        "turns": p.get("turns") or 0,
        "core": p.get("core") or [],
        "speech": p.get("speech") or [],
        "pacts": p.get("pacts") or [],
        "judgments": p.get("judgments") or [],
        "traits": p.get("traits") or [],
        "mood": p.get("mood") or {},
        "log": H.persona.log(30),
    }


def save_persona(body):
    fields = {}
    for k in ("name", "pronoun"):
        if k in body:
            fields[k] = str(body[k])[:40]
    for k in ("core", "speech", "pacts", "judgments", "traits"):
        if k in body and isinstance(body[k], list):
            fields[k] = [x if isinstance(x, dict) else
                         {"text": str(x), "ts": time.time(), "evidence": 999}
                         for x in body[k]][:40]
    if fields:
        H.db.save_persona(**fields)
        H.db.persona_log("edit", "手动编辑", "控制台")


def memories_view(layer=None, keyword="", limit=80, status="active"):
    sql = "SELECT * FROM memories WHERE status=?"
    args = [status]
    if layer:
        sql += " AND layer=?"
        args.append(layer)
    if keyword:
        sql += " AND (content LIKE ? OR summary LIKE ?)"
        args += [f"%{keyword}%", f"%{keyword}%"]
    sql += " ORDER BY created DESC LIMIT ?"
    args.append(max(1, min(500, limit)))
    out = []
    for r in H.db.query(sql, tuple(args)):
        try:
            ents = json.loads(r["entities"] or "[]")
        except Exception:
            ents = []
        out.append({
            "id": r["id"], "layer": r["layer"], "summary": r["summary"],
            "content": (r["content"] or "")[:400],
            "age_days": round((time.time() - (r["created"] or 0)) / 86400, 1),
            "recall_count": r["recall_count"], "reject_count": r["reject_count"],
            "half_life": r["half_life_days"], "pinned": bool(r["pinned"]),
            "protected": bool(r["protected"]), "status": r["status"],
            "source": r["source"], "confidence": r["confidence"],
            "entities": ents[:8], "thread": r["thread_id"],
        })
    return {"memories": out, "total": len(out)}


def graph_data(limit=80):
    rows = H.db.query(
        "SELECT id, layer, summary, content, recall_count, half_life_days, created, thread_id "
        "FROM memories WHERE status='active' ORDER BY created DESC LIMIT ?", (limit,))
    nodes = [{"id": r["id"], "layer": r["layer"],
              "label": (r["summary"] or r["content"] or "")[:24],
              "weight": r["recall_count"] or 0, "thread": r["thread_id"],
              "age_days": round((time.time() - (r["created"] or time.time())) / 86400, 1)}
             for r in rows]
    edges = H.db.query("SELECT source_id, target_id, edge_type FROM edges LIMIT 400")
    by_thread = {}
    for n in nodes:
        if n["thread"]:
            by_thread.setdefault(n["thread"], []).append(n["id"])
    auto = []
    for tid, ids in by_thread.items():
        for i in range(len(ids) - 1):
            auto.append({"source": ids[i], "target": ids[i + 1], "type": "thread"})
    ent_map = {}
    for r in H.db.query(
            "SELECT id, entities FROM memories WHERE status='active' "
            "ORDER BY created DESC LIMIT ?", (limit,)):
        try:
            ents = json.loads(r["entities"] or "[]")
        except Exception:
            ents = []
        for e in ents[:8]:
            if e and len(e) >= 2:
                ent_map.setdefault(e, []).append(r["id"])
    seen = set()
    for e, ids in ent_map.items():
        if len(ids) < 2:
            continue
        for i in range(min(len(ids) - 1, 3)):
            key = tuple(sorted([ids[i], ids[i + 1]]))
            if key in seen:
                continue
            seen.add(key)
            auto.append({"source": key[0], "target": key[1], "type": "entity"})
            if len(auto) > 300:
                break
    return {"nodes": nodes, "edges": edges + auto}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Tan API + 控制台已启动")
    print(f"  控制台: http://{args.host}:{args.port}/")
    print(f"  数据库: {config.DB_PATH}")
    print("Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        srv.shutdown()


if __name__ == "__main__":
    main()
