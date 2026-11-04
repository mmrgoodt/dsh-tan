# -*- coding: utf-8 -*-
"""进化：黄金测试、快照回滚、参数自适应"""
import json
import os
import shutil
import time
from pathlib import Path

from . import config
from .db import new_id

DEFAULT_GOLDEN = [
    # 每题 = (问题, 期望命中的关键词)
    # 通用场景：别人 clone 下来直接能跑，不掺任何私人信息
    ("上次那个 bug 是怎么修的", ["报错", "日志", "定位"]),
    ("文件一般放在哪个目录", ["路径", "目录", "项目"]),
    ("代理出问题怎么恢复", ["代理", "网络", "脚本"]),
    ("项目的配置文件在哪", ["配置", "config", "路径"]),
    ("密码清单放在哪", ["密码", "账号", "gitignore"]),
    ("SSH 密钥怎么配的", ["SSH", "id_ed25519", "公钥"]),
    ("我喜欢什么样的说话方式", ["轻松", "简短", "有温度"]),
    ("备份放在哪个目录", ["备份", "backup", "目录"]),
    ("为什么命令行找不到 node", ["PATH", "完整路径", "环境变量"]),
    ("上次推荐的方案我为什么不满意", ["场景", "需求", "贴合"]),
    ("什么是 DNS 缓存污染", ["DNS", "缓存", "解析"]),
    ("手上有几个在进行中的项目", ["项目", "进行中", "清单"]),
    ("定期要做的事是什么", ["定期", "每周", "提醒"]),
    ("我的代码提交用什么身份", ["git", "user.name", "email"]),
    ("代码仓库地址是什么", ["仓库", "remote", "地址"]),
    ("工作目录在哪", ["工作区", "路径", "目录"]),
    ("删不掉的残留文件是什么", ["dll", "残留", "重启"]),
    ("什么事我让你别再做", ["推荐", "先问", "确认"]),
    ("这个项目做到哪一步了", ["进度", "阶段", "下一步"]),
    ("我最看重什么", ["重要", "在意", "优先"]),
]


class Evolution:
    def __init__(self, db, retriever=None):
        self.db = db
        self.retriever = retriever
        self._seed_golden()

    # ---------- 黄金测试集 ----------
    def _seed_golden(self):
        row = self.db.one("SELECT COUNT(*) c FROM golden_cases")
        if row and row["c"] == 0:
            for q, kws in DEFAULT_GOLDEN:
                self.db.execute(
                    "INSERT INTO golden_cases (question, keywords) VALUES (?,?)",
                    (q, json.dumps(kws, ensure_ascii=False)),
                )

    def run_golden(self, verbose=False):
        """跑 20 题，看记忆库里能不能捞到关键词"""
        cases = self.db.query("SELECT * FROM golden_cases")
        if not cases or not self.retriever:
            return {"total": 0, "passed": 0, "score": 0, "details": []}

        passed = 0
        details = []
        for c in cases:
            kws = json.loads(c["keywords"])
            mems = self.retriever.recall(c["question"], limit=8)
            blob = " ".join((m.get("content") or "") + (m.get("summary") or "") for m in mems)
            hit = sum(1 for k in kws if k.lower() in blob.lower())
            ok = hit >= 1
            if ok:
                passed += 1
            details.append({"q": c["question"], "hit": hit, "ok": ok})
            self.db.execute("UPDATE golden_cases SET last_score=? WHERE id=?", (hit, c["id"]))

        total = len(cases)
        return {
            "total": total,
            "passed": passed,
            "score": round(passed / total * 100) if total else 0,
            "pass_line": config.GOLDEN_PASS_LINE,
            "healthy": passed >= config.GOLDEN_PASS_LINE,
            "details": details if verbose else [],
        }

    # ---------- 快照 ----------
    def snapshot(self, note=""):
        ver = f"v{time.strftime('%Y%m%d-%H%M%S')}"
        src = Path(config.DB_PATH)
        dst = Path(config.SNAPSHOT_DIR) / f"{ver}.db"
        try:
            shutil.copy2(src, dst)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        self.db.execute(
            "INSERT INTO snapshots (version,created,params,note) VALUES (?,?,?,?)",
            (ver, time.time(), json.dumps(config.BUDGET, ensure_ascii=False), note),
        )
        return {"ok": True, "version": ver, "file": str(dst)}

    def list_snapshots(self):
        return self.db.query("SELECT * FROM snapshots ORDER BY created DESC")

    def rollback(self, version):
        src = Path(config.SNAPSHOT_DIR) / f"{version}.db"
        if not src.exists():
            return {"ok": False, "error": "快照不存在"}
        # 先把当前的存一份
        self.snapshot("回滚前自动备份")
        try:
            shutil.copy2(src, config.DB_PATH)
            return {"ok": True, "version": version}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---------- 参数自适应 ----------
    def auto_tune(self):
        """基于真实反馈微调参数（幅度 ≤20%，每天最多 1 个）"""
        changes = []

        # ① 如果记忆平均被用到时已经半死 → 延长 L1 半衰期
        rows = self.db.query(
            "SELECT * FROM memories WHERE layer='L1' AND status='active' AND recall_count>0"
        )
        if len(rows) >= 5:
            decayed = 0
            for m in rows:
                days = (time.time() - (m["created"] or time.time())) / 86400
                hl = max(0.5, float(m["half_life_days"] or 3))
                if pow(2.718281828, -days / hl) < 0.3:
                    decayed += 1
            ratio = decayed / len(rows)
            if ratio > 0.6:
                changes.append(("L1 半衰期偏短", f"{ratio:.0%} 的记忆被用到时已跌破 30%"))

        # ② 被否定的记忆多 → 提高相关度门槛（这里只记录建议）
        rej = self.db.one("SELECT COUNT(*) c FROM memories WHERE reject_count>0")
        if rej and rej["c"] >= 3:
            changes.append(("否定偏多", f"{rej['c']} 条记忆被否认过，建议收紧检索"))

        # ③ Open Loop 提了没人理 → 降频
        lp = self.db.one("SELECT COUNT(*) c FROM open_loops WHERE ask_count>=2 AND status='open'")
        if lp and lp["c"] >= 3:
            changes.append(("Open Loop 无人响应", f"{lp['c']} 条提过 2 次以上未闭环，建议降频"))

        return changes

    # ---------- 周报 ----------
    def weekly_report(self):
        st = self.db.stats()
        gold = self.run_golden()
        loops = self.db.one("SELECT COUNT(*) c FROM open_loops WHERE status='open'")["c"]
        p = self.db.persona()
        weeks = self.db.query(
            "SELECT COUNT(*) c FROM memories WHERE created > ?", (time.time() - 7 * 86400,)
        )[0]["c"]
        lines = [
            "📈 记忆系统周报",
            "",
            f"【本周新增】{weeks} 条记忆",
            f"【总量】{st['memories']} 条 / {st['threads']} 个 Thread",
            f"【未闭合】{loops} 件",
            f"【人格】{p['stage'] if p else '初识'} · 聊了 {p['turns'] if p else 0} 轮",
            f"【黄金测试】{gold['passed']}/{gold['total']}（{gold['score']}%）"
            + ("✅" if gold["healthy"] else " ⚠ 需关注"),
        ]
        tune = self.auto_tune()
        if tune:
            lines.append("")
            lines.append("【建议调整】")
            for a, b in tune:
                lines.append(f"· {a}：{b}")
        return "\n".join(lines)
