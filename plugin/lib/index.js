/**
 * Tan 记忆插件（Host 侧，纯后端，无 Web UI）
 *
 * 职责：
 *   1. 对话前：调 Python API 取「人格 + 记忆」，作为一个 system prompt 段落塞进去
 *   2. 对话后：把这一轮的用户/助手文本发给 Python 写入
 *   3. 启动时确保 Python 服务在跑
 *
 * 设计约束（v0.2）：
 *   - 不再带任何客户端（browser）代码。DSH 的 Web 启动会把"没激活的插件条目"
 *     当成致命错误，一个写错的 client 包会把整个应用送进安全模式。
 *   - 事件必须挂在 agent.ctx 上。DSH 的 session/event、system-prompt/assemble
 *     是在 agent 作用域里派发的，挂在根 ctx 上收不到。
 *   - 任何失败都只影响记忆本身，绝不影响对话。
 */
import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export const name = "hippocampus";
export const inject = [];

const DEFAULT_API = "http://127.0.0.1:18765";
const DEFAULT_ROOT = "D:\\dsh-tan";
const MEMORY_ORDER = 9000;

let apiProcess = null;

/* ---------------- HTTP ---------------- */

async function callApi(base, path, body, timeoutMs = 1200) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const res = await fetch(base + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ac.signal,
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function waitHealthy(base, tries) {
  for (let i = 0; i < tries; i++) {
    const r = await callApi(base, "/health", undefined, 500);
    if (r?.ok) return true;
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

/** 有 pythonw.exe 就用它（不弹黑框、不绑控制台） */
function pickWindowless(dir) {
  const w = join(dir, "pythonw.exe");
  return existsSync(w) ? w : null;
}


function spawnPython(root, logger) {
  const script = join(root, "api.py");
  if (!existsSync(script)) {
    logger?.warn?.(`[hippocampus] 找不到 ${script}，跳过自动启动`);
    return null;
  }
  // 优先用内置 Python（npm install 时下到 vendor/，用户不用装）
  const bundled =
    process.platform === "win32"
      ? join(root, "plugin", "vendor", "python", "python.exe")
      : join(root, "plugin", "vendor", "python", "bin", "python3");
  const py = existsSync(bundled)
    ? bundled
    : process.env.HIPPOCAMPUS_PYTHON || (process.platform === "win32" ? "python" : "python3");
  try {
    const child = spawn(py, [script], {
      cwd: root,
      // 跟 DSH 同生共死：DSH 退出时一起走
      detached: false,
      stdio: "ignore",
      windowsHide: true,
    });

    child.on("error", (e) => logger?.warn?.(`[hippocampus] 启动 ${py} 失败: ${e.message}`));
    return child;
  } catch (e) {
    logger?.warn?.(`[hippocampus] 启动失败: ${e.message}`);
    return null;
  }
}

/* ---------------- 会话文本 ---------------- */

function textOf(event) {
  const msg = event?.data?.message;
  if (!msg || !Array.isArray(msg.content)) return "";
  return msg.content
    .filter((c) => c?.type === "text" && typeof c.text === "string")
    .map((c) => c.text)
    .join("");
}

/** 取某一轮的 user / assistant 文本 */
function extractTurn(events, turn) {
  const startIdx = events.findLastIndex(
    (e) => e.type === "turn/start" && e.data?.turn === turn
  );
  if (startIdx < 0) return { user: "", assistant: "" };
  const endIdx = events.findIndex(
    (e, i) => i > startIdx && e.type === "turn/end" && e.data?.turn === turn
  );
  const window = events.slice(startIdx + 1, endIdx < 0 ? events.length : endIdx);

  let user = "";
  let assistant = "";
  for (const ev of window) {
    const txt = textOf(ev);
    if (!txt) continue;
    if (ev.type === "user/message") user = txt;
    else if (ev.type === "assistant/message") assistant = txt;
  }
  return { user, assistant };
}

/** 最近一条用户消息（作为检索 query） */
function lastUserText(events) {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type !== "user/message") continue;
    const txt = textOf(events[i]);
    if (txt) return txt;
  }
  return "";
}

/** 读取并清空「待播报」队列（主动消息在下次对话开头带出来） */
function drainPending(root) {
  const file = join(root, "pending.jsonl");
  try {
    if (!existsSync(file)) return "";
    const raw = readFileSync(file, "utf8").trim();
    if (!raw) return "";
    writeFileSync(file, "", "utf8");
    const texts = [];
    for (const line of raw.split("\n").filter(Boolean)) {
      try {
        const o = JSON.parse(line);
        if (o.text) texts.push(o.text);
      } catch {}
    }
    return texts.join("\n");
  } catch {
    return "";
  }
}

/* ---------------- 插件主体 ---------------- */

export function apply(ctx, config = {}) {
  if (config.enabled === false) return;

  const base = config.apiBase || DEFAULT_API;
  const root = config.root || DEFAULT_ROOT;
  const logger = ctx.logger;

  // ① 确保 Python 服务在跑：不阻塞启动，失败也只记一条日志
  void (async () => {
    if (await waitHealthy(base, 1)) return;
    apiProcess = spawnPython(root, logger);
    if (apiProcess) await waitHealthy(base, 25);
  })();

  // DSH 退出时把服务一起带走（不在后台留进程占资源）
  ctx.effect(() => () => {
    try {
      apiProcess?.kill();
    } catch {}
    apiProcess = null;
  }, "hippocampus: python service");

  // DSH 运行期间的服务守护：每 60 秒探一次，挂了自动拉起来
  // 插件自己就是看门狗，不需要额外的后台进程
  let lastRestart = 0;
  const guard = setInterval(async () => {
    if (await waitHealthy(base, 1)) return;
    const now = Date.now();
    if (now - lastRestart < 30000) return;   // 30 秒内只重启一次，防止打转
    lastRestart = now;
    logger?.warn?.("[hippocampus] 服务没响应，正在重新拉起…");
    try {
      apiProcess = spawnPython(root, logger);
      const ok = await waitHealthy(base, 25);
      if (ok) logger?.info?.("[hippocampus] 服务已恢复");
      else logger?.warn?.("[hippocampus] 拉起失败，下轮再试");
    } catch (e) {
      logger?.warn?.("[hippocampus] 重启出错: " + e.message);
    }
  }, 60000);

  ctx.effect(() => () => clearInterval(guard), "hippocampus: health guard");

  // ② 每个 agent 一建好就挂上本插件的事件（agent.ctx 随 agent 释放自动解绑）
  ctx.on("agent/created", ({ agent }) => {
    if (!agent?.ctx) return;

    // 对话前：人格 + 记忆 + 攒下的主动消息
    agent.ctx.on("system-prompt/assemble", async (assembly, context, next) => {
      const result = await next();
      try {
        if (context?.agent !== undefined && context.agent !== agent) return result;

        const events = agent.session?.snapshotEvents?.() ?? [];
        const data = await callApi(base, "/context", { query: lastUserText(events) });
        const pending = drainPending(root);
        if (!data?.text && !pending) return result;

        const drop = new Set(["hippocampus:memory", "hippocampus:pending"]);
        const sections = Array.isArray(result?.sections)
          ? result.sections.filter((s) => !drop.has(s?.name))
          : [];
        if (data?.text) {
          sections.push({ name: "hippocampus:memory", order: MEMORY_ORDER, text: data.text });
        }
        if (pending) {
          sections.push({
            name: "hippocampus:pending",
            order: MEMORY_ORDER + 1,
            text:
              "## 我本来想主动跟你说的话\n" +
              pending +
              "\n（这是在你不方便的时候攒下的，自然地带出来，别生硬）",
          });
        }
        return { ...result, sections };
      } catch {
        return result;
      }
    });

    // 对话后：写入这一轮
    agent.ctx.on("session/event", (session, event) => {
      try {
        if (event?.type !== "turn/end") return;
        if (session !== undefined && session !== agent.session) return;
        const turn = event.data?.turn;
        if (typeof turn !== "number") return;

        const events = agent.session?.snapshotEvents?.() ?? [];
        const { user, assistant } = extractTurn(events, turn);
        if (!user && !assistant) return;

        void callApi(
          base,
          "/turn",
          { user, assistant, session: agent.session?.id ?? "", turn },
          8000
        );
      } catch {
        // 记忆写入失败不能影响对话
      }
    });
  });
}
