# -*- coding: utf-8 -*-
"""proactive_notify.py —— Tan 主动消息发送模块

职责：
    1. 每 5 分钟问一次引擎「现在该不该开口」（H.ask_next_loop()）
    2. 该开口就按优先级投递：飞书 Webhook → Windows Toast → 待播报队列
    3. 无论通道成不成，都往 pending.jsonl 追加一条，供 DSH 插件下次对话开头播报

用法：
    python proactive_notify.py                   # 常驻循环，默认 5 分钟一轮
    python proactive_notify.py --once            # 只检查一次（测试用）
    python proactive_notify.py --drain           # 取出待播报队列并清空（可读格式）
    python proactive_notify.py --drain --raw     # 取出并输出原始 JSONL（给插件消费）
    python proactive_notify.py --test "测试文本"  # 强行走一遍所有通道，不动引擎配额
    python proactive_notify.py --interval 60     # 自定义轮询间隔（秒）

环境变量：
    FEISHU_WEBHOOK      飞书自定义机器人地址；有值就优先走飞书
    TAN_HOME    记忆库根目录，默认 ~/.dsh/hippocampus

只用标准库：urllib / json / time / subprocess / logging / sqlite3(经由引擎)。
"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---------------------------------------------------------------- 常量 / 路径

# 记忆库根目录：和 niannian/config.py 保持一致（同样支持 TAN_HOME 覆盖）
USER_HOME = Path(os.environ.get("USERPROFILE") or Path.home())
ROOT = Path(os.environ.get("TAN_HOME") or (USER_HOME / ".dsh" / "tan"))

PENDING_PATH = ROOT / "pending.jsonl"   # 待播报队列（一行一个 JSON）
LOG_PATH = ROOT / "proactive.log"       # 运行日志
LOCK_PATH = ROOT / "proactive.lock"     # 单实例锁（只有常驻模式用）

DEFAULT_INTERVAL = 300                  # 5 分钟
MIN_INTERVAL = 30                       # 防止手抖把循环跑成 CPU 杀手
FEISHU_TIMEOUT = 10                     # 飞书 POST 超时（秒）
PS_TIMEOUT = 30                         # PowerShell 通知子进程超时（秒）
TOAST_MAX_CHARS = 180                   # 通知气泡放不下太长的话，截断
LOOP_LOG_EVERY = 1                      # 每轮都记一行检查结果（便于排查）

# Windows 下创建子进程时不要弹黑框
CREATE_NO_WINDOW = 0x08000000

# 时段中文名（只用于日志和 --drain 的可读输出）
SLOT_LABEL = {
    "morning": "早上",
    "noon": "中午",
    "evening": "晚上",
    "manual": "手动",
}

log = logging.getLogger("hippocampus.proactive")


# ---------------------------------------------------------------- 基础工具

def ensure_root() -> None:
    """确保记忆库目录存在"""
    ROOT.mkdir(parents=True, exist_ok=True)


def setup_logging(verbose: bool = True) -> None:
    """日志：滚动文件 + 控制台。

    文件按 1MB × 3 滚动，UTF-8 编码（中文不会乱码）。
    """
    ensure_root()
    if log.handlers:                     # 避免重复挂 handler
        return
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")

    fh = RotatingFileHandler(
        str(LOG_PATH), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    log.addHandler(fh)

    if verbose:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        log.addHandler(sh)


def fmt_ts(ts: float | int | None) -> str:
    """unix 时间戳 → 本地可读时间"""
    if not ts:
        return "-"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return "-"


def one_line(text: str) -> str:
    """把消息压成一行（通知气泡只认单行）"""
    return " ".join(str(text).split())


def truncate(text: str, n: int = TOAST_MAX_CHARS) -> str:
    """超长截断，保留原意"""
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- 引擎

_ENGINE = None


def get_engine():
    """惰性加载 Tan 引擎（--drain 不需要它，所以放到函数里 import）"""
    global _ENGINE
    if _ENGINE is None:
        proj_dir = Path(__file__).resolve().parent
        if str(proj_dir) not in sys.path:
            sys.path.insert(0, str(proj_dir))
        from hippocampus import Hippocampus  # noqa: WPS433  (延迟导入是刻意的)

        _ENGINE = Hippocampus()
        log.info("引擎就绪，数据库：%s", ROOT / "memory.db")
    return _ENGINE


def persona_title() -> str:
    """通知标题：优先用人格名，拿不到就用产品名"""
    try:
        p = get_engine().persona.get()
        name = ((p or {}).get("name") or "").strip()
        if name:
            return name
    except Exception:                    # 标题只是装饰，失败不影响发送
        pass
    return "Tan"


# ---------------------------------------------------------------- 通道 a：飞书

def send_feishu(text: str, webhook: str | None = None) -> bool:
    """POST 到飞书自定义机器人。

    请求体：{"msg_type":"text","content":{"text":"..."}}
    成功判定：HTTP 200 且返回体里 code / StatusCode 为 0。
    """
    webhook = (webhook if webhook is not None else os.environ.get("FEISHU_WEBHOOK") or "").strip()
    if not webhook:
        return False

    payload = {"msg_type": "text", "content": {"text": str(text)}}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )

    status, raw = 0, ""
    try:
        with urllib.request.urlopen(req, timeout=FEISHU_TIMEOUT) as resp:
            status = getattr(resp, "status", 200) or 200
            raw = resp.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as exc:
        # 飞书拒绝时（关键词不匹配 / 签名错）也会带响应体，抓出来才看得懂
        try:
            raw = exc.read().decode("utf-8", "replace").strip()
        except Exception:
            raw = ""
        log.warning("飞书 HTTP 错误 %s：%s", exc.code, raw or exc.reason)
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("飞书连接失败：%s", exc)
        return False

    code = None
    try:
        obj = json.loads(raw) if raw else {}
        if isinstance(obj, dict):
            code = obj.get("code", obj.get("StatusCode"))
            if code is None and "data" in obj:
                code = 0
    except json.JSONDecodeError:
        obj = {}

    ok = status == 200 and (code in (0, "0", None))
    if ok:
        log.info("飞书已送达（HTTP %s）", status)
    else:
        log.warning("飞书返回异常：HTTP %s code=%s body=%s", status, code, raw[:300])
    return ok


# ---------------------------------------------------------------- 通道 b：Windows 通知

# PowerShell 脚本：BurntToast → WinRT Toast API → msg.exe → 托盘气泡，逐级兜底。
# 消息正文和标题用 base64 传进去，彻底绕开命令行代码页带来的中文乱码。
# 全程不弹模态对话框（msg.exe 用 Start-Process 非阻塞拉起，气泡用 NotifyIcon）。
PS_TEMPLATE = r"""
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$b64Title = '@B64_TITLE@'
$b64Text  = '@B64_TEXT@'
$title = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64Title))
$msg   = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64Text))
$script:chan = ''

function Try-BurntToast {
    if (-not (Get-Module -ListAvailable -Name BurntToast)) { return $false }
    try {
        Import-Module BurntToast -ErrorAction Stop
        New-BurntToastNotification -Text $title, $msg -ErrorAction Stop | Out-Null
        $script:chan = 'burnttoast'
        return $true
    } catch { return $false }
}

function Try-WinRT {
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    } catch { return $false }
    $ete = [System.Security.SecurityElement]::Escape($title)
    $esc = [System.Security.SecurityElement]::Escape($msg)
    $xml = '<toast duration="short"><visual><binding template="ToastGeneric"><text>' + $ete + '</text><text>' + $esc + '</text></binding></visual></toast>'
    $appIds = @(
        '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe',
        'Microsoft.Windows.PowerShell',
        'Microsoft.Windows.Explorer'
    )
    foreach ($appId in $appIds) {
        try {
            $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
            $doc.LoadXml($xml)
            $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
            $script:chan = 'winrt'
            return $true
        } catch { }
    }
    return $false
}

function Try-Msg {
    try {
        $exe = Join-Path $env:SystemRoot 'System32\msg.exe'
        if (-not (Test-Path $exe)) { return $false }
        $argList = @('*', '/time:20', ('"' + $msg + '"'))
        Start-Process -FilePath $exe -ArgumentList $argList -WindowStyle Hidden -ErrorAction Stop | Out-Null
        $script:chan = 'msg'
        return $true
    } catch { return $false }
}

function Try-Balloon {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $ni = New-Object System.Windows.Forms.NotifyIcon
        $ni.Icon = [System.Drawing.SystemIcons]::Information
        $ni.Visible = $true
        $ni.ShowBalloonTip(8000, $title, $msg, [System.Windows.Forms.ToolTipIcon]::Info)
        Start-Sleep -Seconds 9
        $ni.Dispose()
        $script:chan = 'balloon'
        return $true
    } catch { return $false }
}

$ok = Try-BurntToast
if (-not $ok) { $ok = Try-WinRT }
if (-not $ok) { $ok = Try-Msg }
if (-not $ok) { $ok = Try-Balloon }
Write-Output ('CHANNEL=' + $script:chan)
if ($ok) { exit 0 } else { exit 1 }
"""


def _ps_quote_b64(text: str) -> str:
    """UTF-8 → base64（ASCII 安全，杜绝编码踩坑）"""
    import base64

    return base64.b64encode(str(text).encode("utf-8")).decode("ascii")


def send_windows_toast(text: str, title: str = "Hippocampus") -> str:
    """弹 Windows 通知，成功返回所用通道名（burnttoast/winrt/msg/balloon），失败返回空串。

    做法：把 PowerShell 脚本写成临时 .ps1 再 -File 执行，正文走 base64 参数，
    stdin/stdout 全走管道，CREATE_NO_WINDOW 保证不闪黑框。
    """
    body = truncate(one_line(text))
    head = truncate(one_line(title), 40)
    script = (
        PS_TEMPLATE.replace("@B64_TITLE@", _ps_quote_b64(head))
        .replace("@B64_TEXT@", _ps_quote_b64(body))
    )

    tmp_dir = Path(tempfile.gettempdir()) / "hippocampus_toast"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        tmp_dir = Path(tempfile.gettempdir())
    ps1 = tmp_dir / f"toast_{os.getpid()}_{int(time.time() * 1000)}.ps1"

    try:
        ps1.write_text(script, encoding="utf-8", newline="\r\n")
    except OSError as exc:
        log.warning("写入 PowerShell 脚本失败：%s", exc)
        return ""

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", str(ps1),
    ]
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": PS_TIMEOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW

    try:
        proc = subprocess.run(cmd, **kwargs)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        channel = ""
        for line in out.splitlines():
            if line.startswith("CHANNEL="):
                channel = line.split("=", 1)[1].strip()
        if proc.returncode == 0 and channel:
            log.info("Windows 通知已弹出（通道 %s）", channel)
            return channel
        log.warning(
            "Windows 通知失败 rc=%s out=%s err=%s",
            proc.returncode, out[:200], err[:300],
        )
        return ""
    except subprocess.TimeoutExpired:
        log.warning("Windows 通知超时（%ss），已放弃本次弹窗", PS_TIMEOUT)
        return ""
    except OSError as exc:
        log.warning("启动 PowerShell 失败：%s", exc)
        return ""
    finally:
        try:
            ps1.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- 通道 c：待播报队列

def enqueue(text: str, slot: str = "manual", ts: float | None = None) -> bool:
    """追加一条到 pending.jsonl：{"ts":unix,"slot":..,"text":..}，一行一个 JSON。

    先落盘（flush + fsync），保证 DSH 插件下次一定能读到。
    """
    ensure_root()
    record = {"ts": round(float(ts if ts is not None else time.time()), 3),
              "slot": slot or "",
              "text": str(text)}
    line = json.dumps(record, ensure_ascii=False)
    try:
        with open(PENDING_PATH, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        log.warning("写入待播报队列失败：%s", exc)
        return False
    log.info("已入队：%s", PENDING_PATH)
    return True


def drain(raw: bool = False) -> int:
    """取出待播报队列并清空（打印出来），返回取出的条数。

    用「原子改名」抢占文件：先 os.replace 成临时名再读，
    这样即使常驻循环正在 append，也不会丢消息（append 会重新创建 pending.jsonl）。
    """
    ensure_root()
    if not PENDING_PATH.exists():
        if not raw:
            print("待播报队列：空")
        return 0

    claimed = PENDING_PATH.with_name(f"pending.jsonl.draining.{int(time.time() * 1000)}")
    try:
        os.replace(PENDING_PATH, claimed)
    except OSError as exc:
        log.warning("队列抢占失败：%s", exc)
        print(f"待播报队列：读取失败（{exc}）")
        return 0

    count, bad = 0, 0
    try:
        text = claimed.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("读取队列文件失败：%s", exc)
        claimed.unlink(missing_ok=True)
        return 0

    try:
        if raw:
            # 原样输出，交给调用方（DSH 插件）自己解析
            sys.stdout.write(text if text.endswith("\n") or not text else text + "\n")
            count = sum(1 for ln in text.splitlines() if ln.strip())
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    log.warning("队列里有坏行，已跳过：%s", line[:200])
                    continue
                count += 1
                slot = rec.get("slot") or "-"
                label = SLOT_LABEL.get(slot, slot)
                print(f"[{fmt_ts(rec.get('ts'))}] ({label}) {rec.get('text') or ''}")
            if count == 0:
                print("待播报队列：空")
            elif bad:
                print(f"—— 共 {count} 条（另有 {bad} 行格式损坏已跳过）——")
            else:
                print(f"—— 共 {count} 条，队列已清空 ——")
    finally:
        try:
            claimed.unlink()
        except OSError:
            pass

    log.info("--drain 取走 %d 条（raw=%s）", count, raw)
    return count


# ---------------------------------------------------------------- 投递编排

def push(text: str, slot: str = "manual", title: str | None = None) -> dict:
    """按优先级投递一条消息，并且**总是**写进 pending 队列。

    原来的逻辑是「投递成功就不写队列」，本意是避免同一句话弹完通知又在对话里说一遍。
    但结果是：Windows 弹窗一直成功 → 队列永远是空的 → 聊天窗口里永远看不到，
    你只会看到一闪而过的通知，转头就忘了。

    现在改成两条腿都走：通知负责提醒，队列负责在下一次对话里真的说出来。
    """
    result = {"feishu": False, "windows": "", "queued": False, "channel": "queue"}

    # ① 飞书优先
    hook = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
    if hook:
        result["feishu"] = send_feishu(text, hook)
        if result["feishu"]:
            result["channel"] = "feishu"
            result["queued"] = enqueue(text, slot)
            return result

    # ② 飞书没配（或失败）→ Windows 通知
    channel = send_windows_toast(text, title or persona_title())
    result["windows"] = channel
    if channel:
        result["channel"] = f"windows:{channel}"
        # 通知弹过了，但还是要入队 —— 聊天窗口才是他真正会看的地方
        result["queued"] = enqueue(text, slot)
        return result

    # ③ 全挂 → 入队兜底（下次对话播报，不丢）
    result["queued"] = enqueue(text, slot)
    result["channel"] = "queue"
    return result


def check_once(force_text: str | None = None) -> dict:
    """跑一次判断 + 投递；返回 {"sent": bool, ...}。

    注意：ask_next_loop() 在不该发时返回 (None, 原因)，
    原因可能是「静默时段」「不在计划时段」「今天额度已满」「evening 已发过」。
    """
    t0 = time.time()

    if force_text:
        text, slot = force_text, "manual"
        log.info("手动测试消息：%s", text)
        if not str(text).strip():
            return {"sent": False, "reason": "空消息"}
    else:
        try:
            H = get_engine()
            text, slot = H.ask_next_loop()
        except Exception:
            log.exception("调用 ask_next_loop() 失败（数据库被占用？）")
            return {"sent": False, "reason": "引擎异常"}

        if not text or not str(text).strip():
            reason = slot or "未知原因"
            if LOOP_LOG_EVERY:
                log.info("本轮不发：%s", reason)
            return {"sent": False, "reason": reason}

    text = str(text).strip()
    log.info("本轮要发 [%s]：%s", slot, text)
    channels = push(text, slot)
    cost = int((time.time() - t0) * 1000)
    log.info("投递完成：%s（%dms）", channels, cost)
    return {"sent": True, "slot": slot, "text": text, "channels": channels, "cost_ms": cost}


# ---------------------------------------------------------------- 单实例锁

def _pid_alive(pid: int) -> bool:
    """判断进程是否还活着。

    Windows 上绝不能用 os.kill(pid, 0) —— 那会真的把进程 TerminateProcess。
    所以走 ctypes 的 OpenProcess + GetExitCodeProcess(STILL_ACTIVE)。
    """
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            # 打不开多半是权限不足（别的会话里的进程），按「还活着」处理，别抢锁
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return True                      # 判断不了就当它活着：宁可不发，不可重发


def _lock_holder_pid() -> int | None:
    """从锁文件里读出 pid"""
    try:
        text = LOCK_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"pid=(\d+)", text)
    return int(m.group(1)) if m else None


def _lock_is_fresh(interval: int) -> bool:
    """锁是否被别人活着占着。

    两重判断：①持有的 pid 还活着 ②mtime 在 max(60, 2×interval) 内。
    进程被强杀（TerminateProcess 不会跑 finally）留下的残锁，靠 ① 立刻识别并接管。
    """
    if not LOCK_PATH.exists():
        return False
    try:
        age = time.time() - LOCK_PATH.stat().st_mtime
    except OSError:
        return False
    pid = _lock_holder_pid()
    if pid is not None and not _pid_alive(pid):
        return False                     # 持有者已死，锁是残骸
    return age < max(60, interval * 2)


def _acquire_lock(interval: int) -> bool:
    ensure_root()
    if LOCK_PATH.exists() and _lock_is_fresh(interval):
        try:
            holder = LOCK_PATH.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            holder = "?"
        log.warning("已有常驻实例在跑（%s），本进程退出", holder)
        print(f"已有常驻实例在跑（{holder}），不重复启动。", file=sys.stderr)
        return False
    try:
        LOCK_PATH.write_text(f"pid={os.getpid()} since={fmt_ts(time.time())}", encoding="utf-8")
    except OSError:
        pass
    return True


def _touch_lock() -> None:
    try:
        os.utime(LOCK_PATH, None)
    except OSError:
        pass


def _release_lock() -> None:
    try:
        if LOCK_PATH.exists():
            LOCK_PATH.unlink()
    except OSError:
        pass


def _sleep(seconds: int) -> bool:
    """可中断的睡眠；被 Ctrl+C 打断返回 False"""
    end = time.time() + seconds
    try:
        while True:
            left = end - time.time()
            if left <= 0:
                return True
            time.sleep(min(1.0, left))
    except KeyboardInterrupt:
        return False


# ---------------------------------------------------------------- 主循环

def run_loop(interval: int = DEFAULT_INTERVAL) -> int:
    """常驻循环：每 interval 秒检查一次"""
    interval = max(MIN_INTERVAL, int(interval))
    if not _acquire_lock(interval):
        return 1
    atexit.register(_release_lock)       # 正常退出/异常退出都清锁，少留残骸

    hook = (os.environ.get("FEISHU_WEBHOOK") or "").strip()
    log.info(
        "启动常驻循环 pid=%d 间隔=%ds 飞书=%s 队列=%s",
        os.getpid(), interval, "已配置" if hook else "未配置→走 Windows 通知", PENDING_PATH,
    )
    print(f"Tan 主动消息已启动：每 {interval} 秒检查一次（Ctrl+C 停止）")
    print(f"  日志：{LOG_PATH}")
    print(f"  队列：{PENDING_PATH}")

    try:
        while True:
            try:
                check_once()
            except KeyboardInterrupt:
                raise
            except Exception:
                # 单轮炸掉不能让守护进程跟着死
                log.exception("本轮检查异常，5 秒后继续")
                _sleep(5)
            _touch_lock()
            if not _sleep(interval):
                break
    except KeyboardInterrupt:
        print("\n已停止")
        log.info("收到 Ctrl+C，退出循环")
    finally:
        _release_lock()
    return 0


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="proactive_notify.py",
        description="Tan 主动消息发送器（飞书 / Windows 通知 / 待播报队列）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python proactive_notify.py            常驻循环\n"
            "  python proactive_notify.py --once     只检查一次\n"
            "  python proactive_notify.py --drain    取出队列并清空\n"
            "  python proactive_notify.py --test \"测试\"  强制走一遍通道\n"
        ),
    )
    ap.add_argument("--once", action="store_true", help="只检查一次就退出")
    ap.add_argument("--drain", action="store_true", help="取出待播报队列并清空")
    ap.add_argument("--raw", action="store_true", help="配合 --drain：输出原始 JSONL")
    ap.add_argument("--test", metavar="TEXT", default=None, help="强制推送一条测试消息（不占引擎配额）")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="轮询间隔秒数，默认 300")
    ap.add_argument("--quiet", action="store_true", help="不往控制台打日志，只写文件")
    return ap


def main(argv: list[str] | None = None) -> int:
    # 让中文在管道里也不炸（重定向时默认是 cp936，遇到少见字符会抛异常）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    setup_logging(verbose=not args.quiet)
    ensure_root()

    # --drain 优先：它不需要引擎，也不该因为引擎坏了而取不出消息
    if args.drain:
        drain(raw=args.raw)
        return 0

    if args.test is not None:
        res = check_once(force_text=args.test)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("sent") else 1

    if args.once:
        res = check_once()
        print(json.dumps(res, ensure_ascii=False))
        return 0

    return run_loop(args.interval)


if __name__ == "__main__":
    sys.exit(main())
