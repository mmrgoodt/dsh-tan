# -*- coding: utf-8 -*-
"""发布前检查：确认仓库里没有任何个人信息、真实记忆、密钥

每次 git push / npm publish / 打 Release 之前跑一次：
    python check_release.py

退出码 0 = 可以发布，1 = 有问题需要先处理
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 不该出现的个人标识
PERSONAL = [
    "谭荣国", "老谭", "鲜果通", "先开", "咸阳", "mmrgoodt", "rongguo",
    "61918816", "wx0adc8", "XLGame", "迅雷下载",
]

# 不该出现的本机路径 / 用户名
LOCAL_PATHS = ["C:/Users/admin", "C:\\Users\\admin", "D:\\DSH记忆插件"]

# 不该被提交的文件类型
FORBIDDEN_FILES = [
    r"\.db$", r"\.db-wal$", r"\.db-shm$", r"\.tanbak$",
    r"token\.txt$", r"\.log$", r"pending\.jsonl$", r"config\.json$",
    r"^dist/", r"^plugin/vendor/", r"^backup/", r"^raw/", r"^snapshots/",
]

problems = []


def run(*args):
    r = subprocess.run(args, cwd=str(ROOT), capture_output=True)
    return r.returncode, (r.stdout or b"").decode("utf-8", "ignore")


def check_git():
    code, _ = run("git", "rev-parse", "--is-inside-work-tree")
    if code != 0:
        problems.append(("❌", "不是 git 仓库"))
        return []

    _, out = run("git", "ls-tree", "-r", "--name-only", "HEAD")
    files = [f for f in out.splitlines() if f.strip()]

    if not files:
        problems.append(("❌", "还没有提交"))
        return []

    # 1. 数据文件不能进仓库
    for f in files:
        for pat in FORBIDDEN_FILES:
            if re.search(pat, f):
                problems.append(("❌", f"不该提交的文件：{f}"))
                break

    # 2. 内容扫描
    hits = {"personal": [], "path": [], "secret": []}
    for f in files:
        p = ROOT / f
        if not p.exists() or p.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for word in PERSONAL:
            if word in txt:
                hits["personal"].append(f"{f}: {word}")

        for lp in LOCAL_PATHS:
            if lp in txt:
                hits["path"].append(f"{f}: {lp}")

        # 常见的密钥形态
        for m in re.finditer(r"(ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|"
                             r"xox[baprs]-[A-Za-z0-9-]{10,})", txt):
            hits["secret"].append(f"{f}: {m.group(0)[:20]}…")

        # tan 的访问令牌（43 字符 url-safe，紧跟在 token 赋值后）
        for m in re.finditer(r'token\s*=\s*["\']([A-Za-z0-9_-]{40,})["\']', txt, re.I):
            hits["secret"].append(f"{f}: 疑似 token {m.group(1)[:12]}…")

    for kind, label in (("personal", "个人信息"), ("path", "本机路径"), ("secret", "密钥")):
        for h in hits[kind]:
            problems.append(("❌", f"{label}：{h}"))

    return files


def check_working_tree():
    """未提交的改动里有没有脏东西"""
    code, out = run("git", "status", "--porcelain")
    if code != 0:
        return 0
    dirty = [l for l in out.splitlines() if l.strip()]
    if dirty:
        problems.append(("⚠️", f"{len(dirty)} 个文件未提交（发布前应该提交或忽略）"))
    return len(dirty)


def check_dist():
    """npm 包目录（如果存在）也要干净"""
    dist = ROOT / "dist" / "dsh-tan"
    if not dist.exists():
        return

    for f in dist.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(dist).as_posix()
        if rel.startswith("vendor/"):
            continue
        if f.suffix in (".db", ".tanbak", ".log"):
            problems.append(("❌", f"npm 包里有数据文件：{rel}"))
        if f.stat().st_size > 1024 * 1024:
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for word in PERSONAL:
            if word in txt:
                problems.append(("❌", f"npm 包 {rel}: 含 {word}"))


def check_tests():
    """测试必须全绿"""
    code, out = run(sys.executable, "-m", "pytest", "tests/", "-q")
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    if code != 0:
        problems.append(("❌", f"测试失败：{tail}"))
    return tail


def main():
    print("=" * 60)
    print("Tan 发布前检查")
    print("=" * 60)
    print()

    files = check_git()
    print(f"仓库文件：{len(files)} 个")

    dirty = check_working_tree()
    print(f"未提交改动：{dirty} 个")

    check_dist()
    print("npm 包：已检查" if (ROOT / "dist").exists() else "npm 包：未生成，跳过")

    tail = check_tests()
    print(f"测试：{tail}")
    print()

    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for lvl, msg in problems:
            print(f"  {lvl} {msg}")
        print()
        print("先处理完再发布。")
        return 1

    print("✅ 干净，可以发布：")
    print("   git push")
    print("   cd dist/dsh-tan && npm publish")
    return 0


if __name__ == "__main__":
    sys.exit(main())
