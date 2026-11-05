# -*- coding: utf-8 -*-
"""Tan ⇄ DSH 安装器（v0.3.2）

用法：
    python install.py              # 只装宿主（默认：先试 junction，失败自动回退复制）
    python install.py --panel      # 连设置页一起装（装前先自检 client bundle，只能复制）
    python install.py --link       # 强制用目录联结点（profile 直连 D:\\dsh-tan\\plugin）
    python install.py --copy       # 强制用复制模式（老的 sync-plugin.cmd 行为）
    python install.py --uninstall  # 卸载，重启 DSH 即回到普通模式
    python install.py --status     # 只看状态，不改动
    python install.py --harden     # 收紧明文 token 的 ACL（可重复执行）

联结点（junction）的意义：
    复制模式下改 D:\\dsh-tan\\plugin 的源码不会自动进 profile，得手跑
    sync-plugin.cmd 或重装一次 —— 忘跑就是"改了没生效"。
    junction 让 profile\\node_modules\\dsh-tan 直接指向源码目录，
    改完重启 DSH 就生效。建 junction 需要管理员或开发者模式，失败自动回退复制。

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
import os
import re
import shutil
import stat
import subprocess
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

# 明文 token 的家：
#   飞书机器人 state.json（里面是 app secret / token 明文）
#   tan 自己的访问令牌
FEISHU_BOTS = Path.home() / ".dsh" / "integrations" / "dsh-feishu" / "bots"
TAN_TOKEN = Path.home() / ".dsh" / "tan" / "token.txt"

# 收紧后只保留当前用户和 SYSTEM，这几个主体要显式踢掉
DROP_PRINCIPALS = (
    "Users",
    "Everyone",
    "Authenticated Users",
    "BUILTIN\\Users",
    "NT AUTHORITY\\Authenticated Users",
)

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
    """链接判断（含 junction）。

    Windows 的 junction 不是 symlink，path.is_symlink() 对它是 False，
    所以还要看 REPARSE_POINT 属性位。
    """
    if is_reparse_point(path):
        return True
    try:
        return path.is_symlink()
    except OSError:
        return False


def is_reparse_point(path):
    """是不是 junction / 符号链接之类的重解析点（删之前必须问一遍）。"""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def remove_target(path=None):
    """删掉 profile 里的插件目录；是链接就只摘链接，绝不动源目录。

    坑：shutil.rmtree 对 junction 会顺着链接一路删目标里的东西，
    也就是把 D:\\dsh-tan\\plugin 干掉。所以先看 reparse point，是就用 rmdir。
    """
    path = Path(path) if path is not None else TARGET
    if not (path.exists() or is_reparse_point(path)):
        return True

    if is_reparse_point(path):
        try:
            os.rmdir(path)          # junction / 目录符号链接：只删链接本身
        except OSError:
            try:
                os.unlink(path)     # 文件型符号链接走这条
            except OSError as e:
                print(f"ℹ️  删链接失败（{e}），跳过：{path}")
                return False
        return not is_reparse_point(path)

    shutil.rmtree(path, ignore_errors=False)
    return True


def make_junction(link, target):
    """用 cmd /c mklink /J 建目录联结点。成功返回 True。"""
    link = Path(link)
    target = Path(target)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() or is_reparse_point(link):
        remove_target(link)
    try:
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, errors="replace",
        )
    except OSError as e:
        print(f"ℹ️  mklink 调不起来：{e}")
        return False
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    # mklink 成功时会打印「为 <link> <<===>> <target> 创建的联接」
    if r.returncode != 0 or not is_reparse_point(link):
        if out:
            print("   mklink 输出：" + out.replace("\n", " ")[:200])
        return False
    try:
        return Path(os.path.realpath(link)).resolve() == Path(os.path.realpath(target)).resolve()
    except OSError:
        return True


def source_intact():
    """源目录还在吗？卸载后必须成立，否则说明动到了 D:\\dsh-tan\\plugin。"""
    return (SRC / "lib" / "index.js").exists()


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
            how = "junction 直连源码" if is_reparse_point(TARGET) else "符号链接"
            print(f"安装方式 : {how} → {os.path.realpath(TARGET)}")
        else:
            print("安装方式 : 复制副本（改源码要跑 sync-plugin.cmd）")
    if IGNORED.exists():
        print(f"⚠️  另有被丢弃的旧副本: {IGNORED}（可删）")
    text = PROFILE_PATCH.read_text(encoding="utf-8") if PROFILE_PATCH.exists() else ""
    if MARK_START in text:
        print("⚠️  profile/cordis.patch.yml 里还残留旧 insert（下次安装会清掉）")
    return True


def install(panel=False, want="auto"):
    """want: auto = 先试 junction 失败回退复制；link = 强制 junction；copy = 强制复制。"""
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

    # 3) 落地插件目录：优先 junction 直连源码，失败回退复制
    #
    # --panel 只能复制：junction 指向源码目录，client.js 就会落到
    # D:\dsh-tan\plugin\lib 里，源码被污染，package.json 也没法按 profile 版改写。
    if panel:
        if want == "link":
            print("ℹ️  --panel 需要复制模式（junction 会往源码目录写 client.js），本次按复制装")
        want = "copy"

    src_patch = SRC / "cordis.patch.yml"
    if not src_patch.exists():
        return fail(f"缺少 {src_patch}（package.json 里 dsh.bundle.patch 指向它）")

    remove_target()   # 先清掉旧目录/旧链接，避免写进链接目标
    linked = False
    if want in ("auto", "link"):
        linked = make_junction(TARGET, SRC)
        if linked:
            ok(f"已建立目录联结点：{TARGET} → {SRC}")
        elif want == "link":
            return fail(
                "--link 要求联结点模式，但 mklink /J 没成功。\n"
                "   建 junction 需要管理员权限，或打开「设置 → 系统 → 开发者选项 → 开发者模式」。"
            )
        else:
            print("ℹ️  联结点建不上，自动回退到复制模式（能用，只是改源码后要同步）")

    if not linked:
        (TARGET / "lib").mkdir(parents=True)
        # 整个 lib/ 一起复制：index.js 还 import 了 memory-tools / im-native /
        # wx-loop / qr / im-tools，只复制 index.js 的话插件一加载就 ENOENT
        # （sync-plugin.cmd 老老实实列了这些文件名，就是这个原因）。
        for f in sorted((SRC / "lib").glob("*.js")):
            shutil.copy2(f, TARGET / "lib" / f.name)
        if panel:
            shutil.copy2(SRC_CLIENT, TARGET / "lib" / "client.js")
        # bundle.patch 指向的文件必须真的在包里，否则 DSH 读 overlay 时 ENOENT →
        # 插件列表里显示「异常」
        shutil.copy2(src_patch, TARGET / "cordis.patch.yml")
        (TARGET / "package.json").write_text(
            json.dumps(target_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    if not (TARGET / "cordis.patch.yml").exists():
        return fail("cordis.patch.yml 不在安装目录里")
    installed_client = (TARGET / "lib" / "client.js").exists()
    if installed_client != panel:
        return fail("安装结果和预期不一致（client.js 状态异常），安装中止")

    how = "junction 直连源码" if linked else "复制副本"
    ok(f"插件已安装到 {TARGET}（{how}" + ("，含设置页" if panel else "，纯宿主") + "）")

    print()
    if linked:
        print("💡 以后改 D:\\dsh-tan\\plugin 下的源码不用再跑 sync-plugin.cmd ——")
        print("   这个目录是联结点，DSH 直接读源码。")
    else:
        print("💡 这次是复制模式：改 D:\\dsh-tan\\plugin 的源码后还得跑 sync-plugin.cmd，")
        print("   或者 python install.py --link 改成联结点直连。")
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
    # 卸载插件目录：是 junction 就只摘链接，源目录一根毫毛都不动
    if TARGET.exists() or is_reparse_point(TARGET):
        if is_reparse_point(TARGET):
            print(f"ℹ️  {TARGET} 是联结点，只删链接，源目录 {SRC} 保留")
        remove_target(TARGET)
        if TARGET.exists() or is_reparse_point(TARGET):
            return fail(f"{TARGET} 没能删掉（可能被占用）")
        ok("已删除插件目录")
        if not source_intact():
            return fail(f"源目录不见了：{SRC}（这说明动了不该动的地方，先去看一眼）")
        ok(f"源目录完好：{SRC}")

    if IGNORED.exists() or is_reparse_point(IGNORED):
        if is_reparse_point(IGNORED):
            remove_target(IGNORED)   # 链接也只摘链接
        else:
            shutil.rmtree(IGNORED)
        ok("已删除旧副本 .ignored")

    strip_profile_patch()
    print()
    print("⚠️  重启 DSH 生效（现在如果在安全模式，重启后应自动回到普通模式）")
    return True


# ---------------------------------------------------------------- 权限收紧

# 收紧后只留这两个主体的完全控制
HARDEN_USER = os.environ.get("USERNAME") or Path.home().name
KEEP_PRINCIPALS = (HARDEN_USER, "SYSTEM")


def _indent(text, prefix="    "):
    return "\n".join(prefix + ln for ln in (text or "").splitlines())


def _decode(raw):
    """icacls 的输出是控制台码页（中文机器上是 GBK），先试 UTF-8 再退回 mbcs。"""
    for enc in ("utf-8", "mbcs", "cp936", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def run_icacls(*args):
    """跑一条 icacls，返回 (输出, 返回码)。"""
    r = subprocess.run(["icacls", *[str(a) for a in args]], capture_output=True)
    return _decode((r.stdout or b"") + (r.stderr or b"")).strip(), r.returncode


def acl_lines(text, path):
    """只留 ACE 行，把 icacls 那句本地化的统计行、空行都丢掉。"""
    out = []
    prefix = str(path)
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith(prefix):
            line = line[len(prefix):].strip()
        if re.match(r"^.+?:(?:\([^)]+\))+$", line):
            out.append(line)
    return out


def principals_of(text, path):
    """从 icacls 输出里抠出 ACE 的主体名（WHO:(flags) 的 WHO 部分）。"""
    return [ln.split(":")[0].strip() for ln in acl_lines(text, path)]


def harden_one(path, is_dir=False):
    """断继承 → 只留 admin + SYSTEM 完全控制 → 打印前后对比。可重复执行。"""
    path = Path(path)
    if not path.exists():
        print(f"\nℹ️  跳过（不存在）：{path}")
        return True

    print(f"\n目标：{path}{'（目录）' if is_dir else '（文件）'}")
    before, _ = run_icacls(path)
    print("--- icacls 调整前 ---")
    print(_indent("\n".join(acl_lines(before, path))) if acl_lines(before, path) else "    （读不到）")

    # 目录要带 (OI)(CI)，让以后新建的文件也自动只归这两个主体
    if is_dir:
        grant = [f"{HARDEN_USER}:(OI)(CI)F", "SYSTEM:(OI)(CI)F"]
    else:
        grant = [f"{HARDEN_USER}:F", "SYSTEM:F"]
    # /inheritance:r 砍掉所有继承来的 ACE，/grant:r 只重设这两个主体
    run_icacls(path, "/inheritance:r", "/grant:r", *grant)
    # 再显式踢一遍常见的大范围主体（有的机器上它们是以显式 ACE 存在的）
    for who in DROP_PRINCIPALS:
        run_icacls(path, "/remove:g", who)   # 不存在时 icacls 会报错，忽略即可

    after, rc = run_icacls(path)
    print("--- icacls 调整后 ---")
    print(_indent("\n".join(acl_lines(after, path))) if acl_lines(after, path) else "    （读不到）")
    if rc != 0:
        return fail(f"读 ACL 失败（icacls 返回 {rc}）")

    left = principals_of(after, path)
    bad = [w for w in left if any(k in w.lower() for k in ("everyone", "authenticated", "users"))]
    has_user = any(HARDEN_USER.lower() in w.lower() for w in left)
    has_system = any("system" in w.lower() for w in left)
    print(f"    主体：{', '.join(left) if left else '（空）'}")
    if bad:
        return fail(f"还留着这些主体：{', '.join(bad)}")
    if not (has_user and has_system):
        return fail(f"缺少 {HARDEN_USER} 或 SYSTEM 的 ACE")
    ok(f"只剩 {HARDEN_USER} + SYSTEM 完全控制")
    return True


def harden():
    """把明文 token 的 ACL 收紧到「只有当前用户 + SYSTEM」。可重复跑。"""
    print("=== 收紧明文 token 的权限（只动 ACL，token 内容与位置都不变）===")
    if os.name != "nt":
        return fail("icacls 只有 Windows 上有")
    print(f"当前用户：{HARDEN_USER}")

    targets = []
    if FEISHU_BOTS.exists():
        for bot in sorted(FEISHU_BOTS.iterdir()):
            if bot.is_dir() and (bot / "state.json").exists():
                targets.append((bot, True))              # 目录
                targets.append((bot / "state.json", False))  # 文件
    else:
        print(f"ℹ️  没有飞书机器人目录：{FEISHU_BOTS}")

    if TAN_TOKEN.exists():
        targets.append((TAN_TOKEN, False))
    else:
        print(f"ℹ️  没有 tan 令牌文件：{TAN_TOKEN}")

    if not targets:
        return fail("没有找到需要收紧的文件")

    good = True
    for path, is_dir in targets:
        good = harden_one(path, is_dir=is_dir) and good

    print()
    print("说明：这是按主体收紧 ACL —— 继承已断开，其他账户（Users / Everyone /")
    print("      Authenticated Users）都被移除了。token 仍是明文，只是别人读不到了。")
    if good:
        ok(f"共处理 {len(targets)} 个对象")
    return good


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        good = uninstall()
    elif "--status" in sys.argv:
        good = status()
    elif "--harden" in sys.argv:
        good = harden()
    else:
        want = "link" if "--link" in sys.argv else ("copy" if "--copy" in sys.argv else "auto")
        good = install(panel="--panel" in sys.argv, want=want)
    sys.exit(0 if good else 1)
