# -*- coding: utf-8 -*-
"""Tan 记忆服务看门狗

每 30 秒探一次 /health：
  - 通 → 什么都不做
  - 不通 → 拉起 api.py，等它起来，记一笔

用法：
    python watchdog.py              # 前台跑（Ctrl+C 停）
    python watchdog.py --once       # 只检查一次（测试用）
    python watchdog.py --install    # 加入开机启动（启动文件夹，不需要管理员）
    python watchdog.py --uninstall  # 移除开机启动
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "http://127.0.0.1:18765/health"
LOG = HERE / "watchdog.log"
INTERVAL = 30
TASK_NAME = "TanMemoryService"
VBS_NAME = "TanMemoryService.vbs"

CRLF = chr(13) + chr(10)


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    try:
        # pythonw.exe 没有控制台，stdout 为 None，直接 print 会抛异常
        if sys.stdout is not None:
            print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + chr(10))
    except Exception:
        pass
    try:
        if LOG.exists() and LOG.stat().st_size > 512 * 1024:
            tail = LOG.read_text(encoding="utf-8", errors="ignore")[-200 * 1024:]
            LOG.write_text(tail, encoding="utf-8")
    except Exception:
        pass


def healthy(timeout=3):
    try:
        with urllib.request.urlopen(API, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")).get("ok") is True
    except Exception:
        return False


def _python_exe(windowless=True):
    """优先用插件自带的内置 Python，找不到再回退系统 Python

    兼容两种目录结构：
      开发结构   D:\dsh-tan\  -> python 在 plugin/vendor/python
      npm 包结构  dsh-tan\    -> python 在 vendor/python（本文件在 python/ 子目录里）
    """
    candidates = [
        HERE / "plugin" / "vendor" / "python",   # 开发结构
        HERE.parent / "vendor" / "python",       # npm 包结构
        HERE / "vendor" / "python",              # 扁平结构
    ]
    bundled = None
    for c in candidates:
        if c.exists():
            bundled = c
            break
    if bundled is None:
        bundled = candidates[0]
    if windowless:
        for name in ("pythonw.exe", "python.exe"):
            p = bundled / name
            if p.exists():
                return p
    else:
        for name in ("python.exe", "pythonw.exe"):
            p = bundled / name
            if p.exists():
                return p

    py = Path(sys.executable)
    if windowless:
        pyw = py.with_name("pythonw.exe")
        if pyw.exists():
            return pyw
    return py


def _spawn():
    exe = _python_exe(windowless=True)
    flags = (0x08000000 | 0x00000008) if os.name == "nt" else 0
    return subprocess.Popen(
        [str(exe), str(HERE / "api.py")],
        cwd=str(HERE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def wait_up(seconds=15):
    for _ in range(seconds * 2):
        if healthy():
            return True
        time.sleep(0.5)
    return False


def check_once():
    if healthy():
        return "ok"
    log("服务没响应，正在拉起…")
    try:
        p = _spawn()
    except Exception as e:
        log("启动失败：" + str(e))
        return "fail"
    if wait_up():
        log("已恢复（pid %s）" % p.pid)
        return "restarted"
    log("拉起后仍不通，下轮再试")
    return "fail"


def loop():
    log("看门狗启动 · 每 %d 秒探一次 · %s" % (INTERVAL, API))
    fails = 0
    while True:
        try:
            r = check_once()
            if r == "fail":
                fails += 1
                wait = min(300, INTERVAL * (2 ** min(fails, 3)))
            else:
                fails = 0
                wait = INTERVAL
        except KeyboardInterrupt:
            log("看门狗退出")
            return
        except Exception as e:
            log("异常：" + str(e))
            wait = INTERVAL
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            log("看门狗退出")
            return


# ---------------------------------------------------------------- 开机自启
# 用「启动文件夹 + VBS」：不需要管理员权限，VBS 的 0 参数表示不显示窗口

def _startup_dir():
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _vbs():
    return _startup_dir() / VBS_NAME


def install():
    if os.name != "nt":
        print("这个功能是给 Windows 写的")
        return

    exe = _python_exe(windowless=True)
    script = HERE / "watchdog.py"

    d = _startup_dir()
    d.mkdir(parents=True, exist_ok=True)
    vbs = _vbs()

    lines = [
        chr(39) + chr(39) + " 用 VBS 静默启动",
        "Set ws = CreateObject(" + chr(34) + "Wscript.Shell" + chr(34) + ")",
        "ws.CurrentDirectory = " + chr(34) + str(HERE) + chr(34),
        "ws.Run " + chr(34) + chr(34) + chr(34) + str(exe) + chr(34) + chr(34) + " "
        + chr(34) + chr(34) + str(script) + chr(34) + chr(34) + ", 0, False",
    ]
    vbs.write_text(CRLF.join(lines) + CRLF, encoding="utf-8")

    print("已加入开机启动")
    print("   文件：" + str(vbs))
    print("   命令：%s watchdog.py（无窗口）" % exe.name)

    try:
        _spawn()
        print("看门狗已在后台运行")
    except Exception as e:
        print("后台启动失败（不影响开机自启）：" + str(e))


def uninstall():
    vbs = _vbs()
    if vbs.exists():
        try:
            vbs.unlink()
            print("已移除开机启动")
        except Exception as e:
            print("移除失败：" + str(e))
    else:
        print("没找到开机启动项")
    try:
        subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
                       capture_output=True)
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    if "--install" in args:
        return install()
    if "--uninstall" in args:
        return uninstall()
    if "--once" in args:
        r = check_once()
    try:
        if sys.stdout is not None:
            print(json.dumps({"ok": True, "result": r}, ensure_ascii=False))
    except Exception:
        pass
        return
    loop()


if __name__ == "__main__":
    main()
