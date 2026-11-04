#!/usr/bin/env node
/**
 * 下载一个便携版 Python 到 vendor/python/，让用户不用自己装 Python。
 *
 * 用的是 Python 官方的 embeddable 包（约 11 MB）：
 *   - 解压即用，不写注册表、不进 PATH
 *   - 自带标准库（含 sqlite3，本项目只需要这个）
 *   - 删掉整个 vendor/ 目录就干净卸载
 *
 * 触发方式：
 *   - npm install 后自动跑（package.json 的 postinstall）
 *   - 手动：node scripts/fetch-python.js
 *   - 强制重下：node scripts/fetch-python.js --force
 *
 * 下载失败不报错退出（exit 0），只是打警告——这样 npm install 不会因为
 * 网络问题整体失败，用户装了系统 Python 照样能用。
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const VERSION = "3.11.9"; // embeddable 最后一个稳定小版本
const ROOT = path.resolve(__dirname, "..");
const VENDOR = path.join(ROOT, "vendor");
const TARGET = path.join(VENDOR, "python");

// 各平台的官方包地址
// 镜像优先：国内 python.org 慢到没法用（实测 5 分钟下不完 11MB），
// 国内源 20~30 MB/s，一秒搞定。官方源留最后兜底。
const SOURCES = {
  "win32-x64": [
    `https://mirrors.huaweicloud.com/python/${VERSION}/python-${VERSION}-embed-amd64.zip`,
    `https://registry.npmmirror.com/-/binary/python/${VERSION}/python-${VERSION}-embed-amd64.zip`,
    `https://mirrors.aliyun.com/python-release/windows/python-${VERSION}-embed-amd64.zip`,
    `https://www.python.org/ftp/python/${VERSION}/python-${VERSION}-embed-amd64.zip`,
  ],
  "darwin-x64": [
    `https://registry.npmmirror.com/-/binary/python/${VERSION}/python-${VERSION}-macos11.pkg`,
    `https://www.python.org/ftp/python/${VERSION}/python-${VERSION}-macos11.pkg`,
  ],
  "linux-x64": [], // Linux 一般自带 python3，不做内置
};

function log(msg) {
  console.log("[tan/python] " + msg);
}

function platformKey() {
  const p = process.platform;
  const a = process.arch;
  if (p === "win32" && (a === "x64" || a === "arm64")) return "win32-x64";
  if (p === "darwin") return "darwin-x64";
  if (p === "linux") return "linux-x64";
  return null;
}

function pythonExe(dir) {
  if (process.platform === "win32") return path.join(dir, "python.exe");
  return path.join(dir, "bin", "python3");
}

function alreadyGood() {
  const exe = pythonExe(TARGET);
  if (!fs.existsSync(exe)) return false;
  try {
    execFileSync(exe, ["-c", "import sqlite3, json, http.server; print('ok')"], {
      stdio: "pipe",
      timeout: 15000,
    });
    return true;
  } catch {
    return false;
  }
}

async function download(url, dest) {
  log("下载 " + url);
  const r = await fetch(url, { redirect: "follow" });
  if (!r.ok) throw new Error("HTTP " + r.status);
  const total = Number(r.headers.get("content-length") || 0);
  const buf = Buffer.from(await r.arrayBuffer());
  fs.writeFileSync(dest, buf);
  const mb = (buf.length / 1048576).toFixed(1);
  log(`  拿到 ${mb} MB${total ? " / " + (total / 1048576).toFixed(1) + " MB" : ""}`);
}

/**
 * embeddable 版用 ._pth 锁定 sys.path，PYTHONPATH 会被无视。
 * 解压后往里加两个相对路径，让它能找到项目根的 hippocampus 包。
 */
function patchPth(dir) {
  const pth = path.join(dir, "python311._pth");
  if (!fs.existsSync(pth)) return;
  let txt = fs.readFileSync(pth, "utf8");
  if (txt.indexOf("..\\..") !== -1) return;

  // 包根：vendor/python 往上两级
  // 两种结构都兼容：
  //   开发结构  D:\dsh-tan\plugin\vendor\python  -> 包根有 hippocampus/
  //   npm 包结构 dsh-tan\vendor\python            -> 包根有 python/hippocampus/
  const pkgRoot = path.resolve(dir, "..\\..");
  const hasPythonDir = fs.existsSync(path.join(pkgRoot, "python"));

  const lines = txt.split(/\r?\n/);
  const out = [];
  for (const ln of lines) {
    out.push(ln);
    if (ln.trim() === ".") {
      out.push("..\\..");  // 包根
      if (hasPythonDir) {
        out.push("..\\..\\python");  // npm 包：python/ 子目录
      } else {
        out.push("..\\..");  // 开发结构：包根即代码目录
      }
    }
  }
  fs.writeFileSync(pth, out.join("\r\n") + "\r\n", "utf8");
  log("  已改 python311._pth（" + (hasPythonDir ? "npm 包结构" : "开发结构") + "）");
}

function unzip(zip, outDir) {
  fs.mkdirSync(outDir, { recursive: true });
  if (process.platform === "win32") {
    // Windows 10+ 自带 tar（能解 zip），比 PowerShell 快且安静
    try {
      execFileSync("tar", ["-xf", zip, "-C", outDir], { stdio: "pipe", timeout: 120000 });
      return;
    } catch {
      execFileSync(
        "powershell",
        ["-NoProfile", "-Command", `Expand-Archive -LiteralPath '${zip}' -DestinationPath '${outDir}' -Force`],
        { stdio: "pipe", timeout: 180000 }
      );
      return;
    }
  }
  execFileSync("unzip", ["-o", "-q", zip, "-d", outDir], { stdio: "pipe", timeout: 120000 });
}

async function main() {
  const force = process.argv.includes("--force");

  if (!force && alreadyGood()) {
    log("已有可用的内置 Python，跳过");
    return;
  }

  const key = platformKey();
  if (!key) {
    log(`不支持的平台 ${process.platform}/${process.arch}，将使用系统 Python`);
    return;
  }

  const urls = SOURCES[key] || [];
  if (!urls.length) {
    log("这个平台不提供内置 Python（一般本来就自带），将使用系统 Python");
    return;
  }

  fs.mkdirSync(VENDOR, { recursive: true });
  const tmp = path.join(os.tmpdir(), `tan-python-${VERSION}.zip`);

  let ok = false;
  for (const url of urls) {
    try {
      await download(url, tmp);
      ok = true;
      break;
    } catch (e) {
      log("  失败：" + e.message + "，换下一个源");
    }
  }

  if (!ok) {
    log("⚠️  下载失败。可以：");
    log("   ① 手动下载 " + urls[0]);
    log("   ② 解压到 " + TARGET);
    log("   ③ 或者自己装 Python 3.10+，插件会自动用系统的");
    return;
  }

  log("解压到 " + TARGET);
  fs.rmSync(TARGET, { recursive: true, force: true });
  unzip(tmp, TARGET);
  patchPth(TARGET);
  try {
    fs.unlinkSync(tmp);
  } catch {}

  // embeddable 版默认把标准库放在 python311.zip 里，需要允许它
  // （官方包已经把 ._pth 配好了，这里只做可用性验证）
  if (alreadyGood()) {
    const exe = pythonExe(TARGET);
    const v = execFileSync(exe, ["-c", "import sys;print(sys.version.split()[0])"], {
      encoding: "utf8",
      timeout: 15000,
    }).trim();
    log("✅ 内置 Python " + v + " 就绪（" + exe + "）");
    log("   用户不需要装 Python 了");
  } else {
    log("⚠️  解压后跑不起来，将回退到系统 Python");
  }
}

main().catch((e) => {
  log("出错：" + e.message);
  // 不让 npm install 因此失败
  process.exitCode = 0;
});
