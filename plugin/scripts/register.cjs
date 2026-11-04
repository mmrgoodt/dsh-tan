#!/usr/bin/env node
/**
 * 把 dsh-tan 注册进 DSH 的插件列表，并把服务做成「开机自启 + 挂了自动重启」
 *
 * npm install 只会把包放进 node_modules，DSH 不会自动加载它。
 * 必须在 profile 的 package.json 里登记两处：
 *   1. dependencies        "dsh-tan": "file:..."      pnpm 认得
 *   2. dsh.profile.bundles 数组里加 "dsh-tan"          DSH 真正加载
 *
 * 另外装一个开机自启（启动文件夹 + VBS），让 Python 服务跟着开机起来，
 * 由 watchdog.py 守着——挂了 30 秒内自动拉回来。
 *
 * 用法：
 *   node scripts/register.cjs              # 安装
 *   node scripts/register.cjs --uninstall  # 移除登记和自启
 */
const fs = require("fs");
const os = require("os");
const path = require("path");

const PKG_NAME = "dsh-tan";
const HERE = path.resolve(__dirname, ".."); // 包根
const isUninstall = process.argv.includes("--uninstall");

function log(m) {
  console.log("[tan/register] " + m);
}

function dshHome() {
  if (process.env.DSH_HOME) return process.env.DSH_HOME;
  return path.join(os.homedir(), ".dsh");
}

function findProfiles() {
  const dir = path.join(dshHome(), "profiles");
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .map((n) => path.join(dir, n))
    .filter((p) => {
      try {
        return fs.statSync(p).isDirectory() && fs.existsSync(path.join(p, "package.json"));
      } catch {
        return false;
      }
    });
}

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function backup(p) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const bak = p + ".bak-tan-" + stamp;
  fs.copyFileSync(p, bak);
  return bak;
}

/* ---------------- profile 登记 ---------------- */

function register(profileDir) {
  const pkgPath = path.join(profileDir, "package.json");
  const pkg = readJson(pkgPath);
  const profileName = path.basename(profileDir);

  if (!pkg.dsh) {
    log(`跳过 ${profileName}（不是 DSH profile）`);
    return false;
  }

  const deps = (pkg.dependencies = pkg.dependencies || {});
  const bundles = ((pkg.dsh.profile = pkg.dsh.profile || {}).bundles =
    pkg.dsh.profile.bundles || []);

  let changed = false;

  if (isUninstall) {
    if (deps[PKG_NAME]) {
      delete deps[PKG_NAME];
      changed = true;
    }
    const i = bundles.indexOf(PKG_NAME);
    if (i !== -1) {
      bundles.splice(i, 1);
      changed = true;
    }
  } else {
    const target = "file:" + HERE.replace(/\\/g, "/");
    if (!deps[PKG_NAME]) {
      deps[PKG_NAME] = target;
      changed = true;
    }
    if (!bundles.includes(PKG_NAME)) {
      bundles.push(PKG_NAME);
      changed = true;
    }
  }

  if (!changed) {
    log(`${profileName} 已登记过，跳过`);
    return false;
  }

  const bak = backup(pkgPath);
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n", "utf8");
  log(`${profileName} ${isUninstall ? "已移除" : "已登记"}（备份：${path.basename(bak)}）`);
  return true;
}

/* ---------------- main ---------------- */

function main() {
  const profiles = findProfiles();
  if (!profiles.length) {
    log("没找到 DSH profile。先装 DSH，再运行：node scripts/register.cjs");
    return;
  }

  let ok = 0;
  for (const p of profiles) {
    try {
      if (register(p)) ok++;
    } catch (e) {
      log(`处理 ${path.basename(p)} 失败：${e.message}`);
    }
  }

  if (isUninstall) {
    log("✅ 已移除，重启 DSH 生效");
    return;
  }

  log("");
  log(ok ? "✅ 登记完成，重启 DSH 生效" : "✅ 无需改动");
  log("   托盘图标右键 → 退出 → 重新打开");
  log("");
  log("服务生命周期：跟着 DSH 走——DSH 在它在，DSH 关它关");
  log("DSH 运行期间服务崩了，插件 60 秒内自己拉起来");
}

main();
