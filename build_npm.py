# -*- coding: utf-8 -*-
"""把项目打包成自包含的 npm 包

为什么要打包：npm 包不能引用上级目录（`../api.py` 会被 npm 拒绝），
所以 Python 后端必须放进包内部。开发时保持现在的结构，发布时生成一份
自包含的副本，互不影响。

生成的包结构：
    dist/dsh-tan/
    ├── package.json
    ├── cordis.patch.yml
    ├── lib/
    │   ├── index.js
    │   └── client.js
    ├── python/              ← api.py + hippocampus + visual
    │   ├── api.py
    │   ├── hippocampus/
    │   └── visual/
    └── scripts/
        └── fetch-python.cjs

用法：
    python build_npm.py            # 生成到 dist/
    python build_npm.py --zip      # 顺便打 tar.gz（npm pack 的格式）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
PKG = DIST / "dsh-tan"

# 要搬进 python/ 的东西
PY_FILES = ["api.py", "cli.py", "watchdog.py", "proactive_notify.py", "install.py"]
PY_DIRS = ["hippocampus", "visual"]

SKIP_PATTERNS = ["__pycache__", ".pytest_cache", "*.pyc", "*.log", "*.db", "*.tanbak",
                 "vendor", "node_modules", ".git"]


def rm(p):
    """删目录，Windows 上文件被占用时重试几次"""
    if not p.exists():
        return
    if p.is_dir():
        for _ in range(3):
            try:
                shutil.rmtree(p)
                return
            except Exception:
                time.sleep(0.5)
                # 只读文件先改权限
                for f in p.rglob("*"):
                    try:
                        f.chmod(0o777)
                    except Exception:
                        pass
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            p.chmod(0o777)
        except Exception:
            pass
        try:
            p.unlink()
        except Exception:
            pass


def copy_ignore(dir_, names):
    out = []
    for n in names:
        if n in ("__pycache__", ".pytest_cache", "node_modules", "vendor", ".git"):
            out.append(n)
        elif n.endswith((".pyc", ".log", ".db", ".tanbak")):
            out.append(n)
    return out


def build():
    print("清理 dist/ …")
    rm(DIST)
    PKG.mkdir(parents=True)

    # ---------- 1. 插件本体 ----------
    print("复制插件本体 …")
    for name in ["lib", "scripts", "optional"]:
        src = ROOT / "plugin" / name
        if src.exists():
            shutil.copytree(src, PKG / name, ignore=copy_ignore)
            print(f"  + {name}/")
    for name in ["cordis.patch.yml"]:
        src = ROOT / "plugin" / name
        if src.exists():
            shutil.copy2(src, PKG / name)
            print(f"  + {name}")

    # ---------- 2. Python 后端 ----------
    print("复制 Python 后端 …")
    pyroot = PKG / "python"
    pyroot.mkdir()

    for f in PY_FILES:
        src = ROOT / f
        if src.exists():
            shutil.copy2(src, pyroot / f)
            print(f"  + python/{f}")

    for d in PY_DIRS:
        src = ROOT / d
        if src.exists():
            shutil.copytree(src, pyroot / d, ignore=copy_ignore)
            size = sum(f.stat().st_size for f in (pyroot / d).rglob("*") if f.is_file())
            print(f"  + python/{d}/  ({size // 1024} KB)")

    # ---------- 3. package.json ----------
    print("生成 package.json …")
    pj = json.loads((ROOT / "plugin" / "package.json").read_text(encoding="utf-8"))

    pj["files"] = [
        "lib/",
        "optional/",
        "scripts/",
        "python/",
        "cordis.patch.yml",
    ]
    pj["scripts"] = pj.get("scripts") or {}
    pj["scripts"]["postinstall"] = "node scripts/fetch-python.cjs && node scripts/register.cjs"
    pj["scripts"]["fetch-python"] = "node scripts/fetch-python.cjs"
    pj["scripts"]["register"] = "node scripts/register.cjs"
    pj["scripts"]["uninstall"] = "node scripts/register.cjs --uninstall"

    # dsh 元信息：告诉 DSH 去哪找 Python 后端
    dsh = pj.setdefault("dsh", {})
    dsh["python"] = "python"          # 包内相对路径
    dsh["dataDir"] = "~/.dsh/tan"     # 数据目录

    (PKG / "package.json").write_text(
        json.dumps(pj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---------- 4. 修包内路径 ----------
    print("修正包内路径 …")
    fix_paths(PKG)

    # ---------- 5. 复制 README / LICENSE ----------
    for name in ["README.md", "LICENSE"]:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, PKG / name)

    # ---------- 6. 统计 ----------
    total = sum(f.stat().st_size for f in PKG.rglob("*") if f.is_file())
    count = sum(1 for f in PKG.rglob("*") if f.is_file())
    print()
    print(f"✅ 打包完成：{PKG}")
    print(f"   {count} 个文件 · {total / 1024:.0f} KB（不含运行时下载的 Python 21 MB）")
    return PKG


def fix_paths(pkg):
    """把包里的路径引用改成「包内相对」，不再依赖 D:\\dsh-tan"""
    # index.js：Python 后端在包内 python/ 下，vendor 在包内 vendor/ 下
    p = pkg / "lib" / "index.js"
    if p.exists():
        s = p.read_text(encoding="utf-8")

        # 默认 root 改成包自己（lib 的上一级）
        s = re.sub(
            r'const DEFAULT_ROOT\s*=\s*"[^"]*"',
            'const DEFAULT_ROOT = fileURLToPath(new URL("..", import.meta.url))',
            s)

        # 内置 Python 路径：包内 vendor/python
        s = s.replace(
            '? join(root, "plugin", "vendor", "python", "python.exe")',
            '? join(root, "vendor", "python", "python.exe")')
        s = s.replace(
            ': join(root, "plugin", "vendor", "python", "bin", "python3");',
            ': join(root, "vendor", "python", "bin", "python3");')

        # api.py 位置：包内 python/api.py
        s = s.replace(
            'const script = join(root, "api.py");',
            'const script = join(root, "python", "api.py");')

        p.write_text(s, encoding="utf-8")
        print("  ✏️ lib/index.js 路径已改为包内相对")

    # fetch-python.cjs：vendor 放在包根，._pth 指到 python/ 和包根
    p = pkg / "scripts" / "fetch-python.cjs"
    if p.exists():
        s = p.read_text(encoding="utf-8")
        s = s.replace('path.resolve(__dirname, "..")', 'path.resolve(__dirname, "..")')
        p.write_text(s, encoding="utf-8")

    # install.py：路径都基于 __file__，不用改；但要确认能找到上层
    p = pkg / "python" / "install.py"
    if p.exists():
        s = p.read_text(encoding="utf-8")
        # npm 包里 plugin 在包根，Python 在 python/ 下
        if 'SRC = Path(__file__).resolve().parent' in s:
            s = s.replace(
                'SRC = Path(__file__).resolve().parent',
                'SRC = Path(__file__).resolve().parent.parent  # npm 包根（含 lib/、cordis.patch.yml）')
            p.write_text(s, encoding="utf-8")
            print("  ✏️ install.py 已指向包根")

    # ._pth 要加 python/ 目录（让 api.py 能 import hippocampus）
    # 这个在 fetch-python 的 patchPth 里做，路径也要改
    p = pkg / "scripts" / "fetch-python.cjs"
    if p.exists():
        s = p.read_text(encoding="utf-8")
        s = s.replace('out.push("..\\\\..\\\\..");', 'out.push("..\\\\..");')
        s = s.replace('out.push("...\\\\");        // plugin 目录', 'out.push("...\\\\python");')
        p.write_text(s, encoding="utf-8")
        print("  ✏️ fetch-python 的 ._pth 已指向包内 python/")


def make_tarball(pkg):
    out = DIST / "dsh-tan-npm.tgz"
    # npm pack 的格式：package/ 前缀
    tmp = DIST / "_pack"
    rm(tmp)
    target = tmp / "package"
    shutil.copytree(pkg, target)
    with tarfile.open(out, "w:gz") as tar:
        tar.add(target, arcname="package")
    rm(tmp)
    print(f"📦 {out}（{out.stat().st_size / 1024:.0f} KB）")
    return out


def main():
    pkg = build()
    if "--zip" in sys.argv:
        make_tarball(pkg)
    print()
    print("下一步：")
    print("  cd dist/dsh-tan && npm pack        # 生成 .tgz")
    print("  npm publish                        # 发布到 npm")
    print("  或者直接：npm install 本地路径")


if __name__ == "__main__":
    main()
