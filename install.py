# -*- coding: utf-8 -*-
"""Tan ⇄ DSH 安装器（v0.3.1）

用法：
    python install.py              # 只装宿主（最安全，默认）
    python install.py --panel      # 连设置页一起装（装前先自检 client bundle）
    python install.py --uninstall  # 卸载，重启 DSH 即回到普通模式
    python install.py --status     # 只看状态，不改动

为什么默认不装设置页：
    DSH 的 Web 启动要求每个客户端条目都"激活成功"。任何一个条目卡在
    pending/failed，应用会连续重载 3 次后切进安全模式（2026-11-04 已发生两次）。

    v0.3 那次事故的原因：client.js 的 inject 里写的是包名
    （"@deepseek-ai/dsh-client-ui-settings" 等），而客户端插件要的是
    【服务短名】（"slots" / "connection" / "locale" / "layout" ...）。
    写包名 → 服务永远等不到 → entry 一直 pending → 安全模式。

包结构约定：
    plugin/            宿主包，永久 host-only：没有 dsh.client、没有 client.js
    plugin/optional/   设置页源码，只有 --panel 时才复制进 profile

    这样即使 pnpm 把 node_modules/dsh-tan 重建回指向源目录的链接，
    也不会把客户端条目带进去 —— 面板最多是"没装"，不会进安全模式。

安装机制（v0.3 起，与旧版不同）：
    * profile/package.json 里加 dependencies["dsh-tan"] = "link:<本目录>/plugin"
    * 加进 dsh.profile.bundles，由包内的 plugin/cordis.patch.yml 负责 insert
    * 不再往 profile/cordis.patch.yml 写 insert（重复注册也会让 entry 卡住）
"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "plugin"
SRC_CLIENT = SRC / "optional" / "client.js"

PROFILE = Path.home() / ".dsh" / "profiles" / "desktop"
PROFILE_PKG = PROFILE / "package.json"
PROFILE_PATCH = PROFILE / "cordis.patch.yml"
TARGET = PROFILE / "node_modules" / "dsh-tan"
IGNORED = PROFILE / "node_modules" / ".ignored" / "dsh-tan"

PKG_NAME = "dsh-tan"
MARK_START = "# ===== Hippocampus"

# dsh.client.inject 必须写【服务短名】，不是包名！
#
# 这条是第四次进安全模式之后确认的：DSH 客户端 loader 读的是
# package.json 里的 dsh.client.inject，写成 @deepseek-ai/dsh-client-*
# 会让条目永远停在 pending（waiting for services），3 次重载后 web boot 失败。
# dsh-mnemon 的真实写法：["slots","sessions","workspaces","uiSession","connection","locale","layout"]
# 我们只需要一个插槽服务。
PANEL_SERVICES = ["slots"]


def fail(msg):
    print("❌ " + msg)
    return False


def ok(msg):
    print("✅ " + msg)


# ---------------------------------------------------------------- 路径工具

def is_link(path):
    """Windows 的 junction 不算 symlink，用 resolve 结果判断。"""
    if not path.exists() and not path.is_symlink():
        return False
    try:
        return path.is_symlink() or path.resolve() != Path(str(path.absolute()))
    except OSError:
        return False


def remove_target():
    if not (TARGET.exists() or TARGET.is_symlink()):
        return
    if TARGET.is_symlink():
        TARGET.unlink()
    elif TARGET.resolve() != Path(str(TARGET.absolute())):
        TARGET.rmdir()  # junction：只删链接本身，不动目标目录
    else:
        shutil.rmtree(TARGET)


# ---------------------------------------------------------------- 客户端自检

def check_client_bundle(path):
    """校验 client.js 的 inject 只写服务短名（包名是上次进安全模式的根因）。"""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"const\s+inject\s*=\s*\[(.*?)\]", text, re.S)
    if not m:
        return False, "client.js 里找不到 `const inject = [...]`"
    specs = re.findall(r'"([^"]+)"', m.group(1))
    if not specs:
        return False, "client.js 的 inject 是空的"
    bad = [s for s in specs if s.startswith("@") or "/" in s]
    if bad:
        return False, (
            "inject 里写了包名：" + ", ".join(bad) +
            "\n   客户端插件要用服务短名（如 slots / connection / locale），"
            "\n   写包名会让条目一直 pending，进而进安全模式。"
        )
    if "slots" not in specs:
        return False, "inject 里缺少 slots（设置页必须依赖它）"
    return True, "inject = " + json.dumps(specs, ensure_ascii=False)


def check_panel_meta(meta):
    """校验将要写进 package.json 的 dsh.client.inject 只有服务短名。

    DSH 客户端 loader 读的是这里，不是 client.js 的 exports.inject——
    两边不一致的时候，client.js 改对了也照样 pending。
    """
    specs = list(((meta.get("dsh") or {}).get("client") or {}).get("inject") or [])
    if not specs:
        return False, "dsh.client.inject 是空的"
    bad = [x for x in specs if str(x).startswith("@") or "/" in str(x)]
    if bad:
        return False, (
            "package.json 的 dsh.client.inject 里写了包名：" + ", ".join(bad) +
            "\n   这里必须写服务短名（slots / connection / locale ...），"
            "\n   否则条目一直 pending，三次重载后进安全模式。"
        )
    if "slots" not in specs:
        return False, "dsh.client.inject 里缺少 slots"
    return True, "inject = " + json.dumps(specs, ensure_ascii=False)


def panel_meta(meta):
    """在宿主清单上补 dsh.client 与 ./client 导出。"""
    m = json.loads(json.dumps(meta))
    m.setdefault("dsh", {})["client"] = {"platform": "web", "inject": list(PANEL_SERVICES)}
    m.setdefault("exports", {})["./client"] = "./lib/client.js"
    return m


# ---------------------------------------------------------------- profile 读写

def load_profile_pkg():
    return json.loads(PROFILE_PKG.read_text(encoding="utf-8"))


def save_profile_pkg(data):
    bak = PROFILE_PKG.with_name(PROFILE_PKG.name + ".bak-hippocampus")
    shutil.copy2(PROFILE_PKG, bak)
    PROFILE_PKG.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"   备份: {bak.name}")


def strip_profile_patch():
    """清掉旧版 install.py 写进 profile/cordis.patch.yml 的 insert 段。"""
    if not PROFILE_PATCH.exists():
        return
    text = PROFILE_PATCH.read_text(encoding="utf-8")
    idx = text.find(MARK_START)
    if idx < 0:
        return
    bak = PROFILE_PATCH.with_suffix(".yml.bak-hippocampus")
    shutil.copy2(PROFILE_PATCH, bak)
    PROFILE_PATCH.write_text(text[:idx].rstrip() + "\n", encoding="utf-8")
    print(f"   已清掉 profile/cordis.patch.yml 里的旧 insert（备份: {bak.name}）")


# ---------------------------------------------------------------- 三个动作

def status():
    print(f"profile : {PROFILE}  {'✅' if PROFILE.exists() else '❌ 不存在'}")
    if PROFILE_PKG.exists():
        pkg = load_profile_pkg()
        dep = pkg.get("dependencies", {}).get(PKG_NAME)
        bundles = pkg.get("dsh", {}).get("profile", {}).get("bundles", [])
        print(f"依赖     : {dep or '（没有）'}")
        print(f"bundles  : {'✅ 已登记' if PKG_NAME in bundles else '❌ 未登记'}")
    print(f"插件目录 : {TARGET}  {'✅ 已安装' if TARGET.exists() else '❌ 未安装'}")
    if TARGET.exists():
        has_client = (TARGET / "lib" / "client.js").exists()
        print(f"设置页   : {'✅ 装了（有 client.js）' if has_client else '— 没装（纯宿主）'}")
        if is_link(TARGET):
            print("⚠️  插件目录是个链接，指向源目录（pnpm 重建过？）")
    if IGNORED.exists():
        print(f"⚠️  另有被丢弃的旧副本: {IGNORED}（可删）")
    text = PROFILE_PATCH.read_text(encoding="utf-8") if PROFILE_PATCH.exists() else ""
    if MARK_START in text:
        print("⚠️  profile/cordis.patch.yml 里还残留旧 insert（下次安装会清掉）")
    return True


def install(panel=False):
    if not PROFILE.exists():
        return fail(f"找不到 DSH profile: {PROFILE}")
    if not (SRC / "lib" / "index.js").exists():
        return fail(f"找不到 {SRC / 'lib' / 'index.js'}")

    # 源包必须永远是 host-only：有 dsh.client 就说明结构被改坏了
    try:
        meta = json.loads((SRC / "package.json").read_text(encoding="utf-8"))
    except Exception as e:
        return fail(f"plugin/package.json 解析失败: {e}")
    if "client" in (meta.get("dsh") or {}):
        return fail(
            "plugin/package.json 里出现了 dsh.client。\n"
            "   宿主包必须保持 host-only（客户端代码放 plugin/optional/），"
            "\n   否则 pnpm 重建 node_modules 时会把客户端条目带进去 → 安全模式。"
        )

    if panel:
        if not SRC_CLIENT.exists():
            return fail(f"--panel 需要 {SRC_CLIENT}，但它不存在")
        good, detail = check_client_bundle(SRC_CLIENT)
        if not good:
            return fail("客户端 bundle 自检没通过：\n   " + detail.replace("\n", "\n   "))
        ok("客户端 bundle 自检通过：" + detail)
        target_meta = panel_meta(meta)
        m_good, m_detail = check_panel_meta(target_meta)
        if not m_good:
            return fail("package.json 侧自检没通过：\n   " + m_detail.replace("\n", "\n   "))
        ok("package.json 侧自检通过：" + m_detail)
    else:
        if SRC_CLIENT.exists():
            print("ℹ️  有设置页源码 plugin/optional/client.js，但这次只装宿主（要设置页加 --panel）")
        target_meta = meta

    # 1) profile/package.json：link 依赖 + bundle 登记
    pkg = load_profile_pkg()
    deps = pkg.setdefault("dependencies", {})
    deps[PKG_NAME] = "link:" + SRC.as_posix()
    prof = pkg.setdefault("dsh", {}).setdefault("profile", {})
    bundles = prof.setdefault("bundles", [])
    if PKG_NAME not in bundles:
        bundles.append(PKG_NAME)
    save_profile_pkg(pkg)
    ok("profile/package.json 已登记（link + bundles）")

    # 2) 清旧版 insert，避免重复注册
    strip_profile_patch()

    # 3) 复制插件文件（TARGET 建成真实目录，避免写进链接目标）
    remove_target()
    (TARGET / "lib").mkdir(parents=True)
    shutil.copy2(SRC / "lib" / "index.js", TARGET / "lib" / "index.js")
    if panel:
        shutil.copy2(SRC_CLIENT, TARGET / "lib" / "client.js")
    # bundle.patch 指向的文件必须真的在包里，否则 DSH 读 overlay 时 ENOENT →
    # 插件列表里显示「异常」
    src_patch = SRC / "cordis.patch.yml"
    if not src_patch.exists():
        return fail(f"缺少 {src_patch}（package.json 里 dsh.bundle.patch 指向它）")
    shutil.copy2(src_patch, TARGET / "cordis.patch.yml")
    (TARGET / "package.json").write_text(
        json.dumps(target_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if not (TARGET / "cordis.patch.yml").exists():
        return fail("cordis.patch.yml 没复制成功")
    installed_client = (TARGET / "lib" / "client.js").exists()
    if installed_client != panel:
        return fail("复制结果和预期不一致（client.js 状态异常），安装中止")
    ok(f"插件已安装到 {TARGET}" + ("（含设置页）" if panel else "（纯宿主）"))

    print()
    print("⚠️  需要【重启 DSH】才生效（托盘图标右键 → 退出，再打开）")
    if panel:
        print("    如果重启后又进安全模式：python install.py --uninstall 然后重启")
    else:
        print("    重启后标题栏不该出现安全模式提示")
    return True


def uninstall():
    if PROFILE_PKG.exists():
        pkg = load_profile_pkg()
        changed = False
        deps = pkg.get("dependencies", {})
        if PKG_NAME in deps:
            deps.pop(PKG_NAME)
            changed = True
        prof = pkg.get("dsh", {}).get("profile", {})
        bundles = prof.get("bundles")
        if isinstance(bundles, list) and PKG_NAME in bundles:
            prof["bundles"] = [b for b in bundles if b != PKG_NAME]
            changed = True
        if changed:
            save_profile_pkg(pkg)
            ok("profile/package.json 已移除插件登记")
        else:
            print("ℹ️  profile/package.json 里本来就没有 hippocampus")
    for path, label in ((TARGET, "插件目录"), (IGNORED, "旧副本 .ignored")):
        if path.exists() or path.is_symlink():
            remove_target() if path == TARGET else shutil.rmtree(path)
            ok(f"已删除{label}")
    strip_profile_patch()
    print()
    print("⚠️  重启 DSH 生效（现在如果在安全模式，重启后应自动回到普通模式）")
    return True


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        good = uninstall()
    elif "--status" in sys.argv:
        good = status()
    else:
        good = install(panel="--panel" in sys.argv)
    sys.exit(0 if good else 1)
