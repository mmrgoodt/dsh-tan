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
import { registerMemoryTools } from "./memory-tools.js";
import { registerNativeImTools } from "./im-native.js";
import { startWxLoop, replyToWechat } from "./wx-loop.js";
import { join } from "node:path";

export const name = "hippocampus";
// dshIm 是 dsh-im 插件提供的服务。cordis 要先声明依赖才允许读 ctx.dshIm，
// 否则抛 cannot get property "dshIm" without inject。
//
// 只能是数组，而且**必须把要用的服务全列上** —— inject 是独占的白名单：
// 只写 ["dshIm"] 之后，连 ctx.tools 都变成
// "cannot get property tools without inject"，工具一个都注册不上。
//
// 踩过的两个坑：
//   { optional: ["dshIm"] } → cordis 把 optional 当服务名去等，
//                              整个插件 pending，记忆注入一起停
//   ["dshIm"]              → tools 被锁，memory_save 也挂了
export const inject = ["tools", "dshIm", "typertGateway", "connection"];

const DEFAULT_API = "http://127.0.0.1:18765";
const DEFAULT_ROOT = "D:\\dsh-tan";
const MEMORY_ORDER = 9000;

let apiProcess = null;
let authWarned = false;
let lastContextKey = "";
let lastContextText = "";

/* ---------------- HTTP ---------------- */

async function callApi(base, path, body, timeoutMs = 1200) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const res = await fetch(base + path, {
      method: body === undefined ? "GET" : "POST",
      // api.py 从 v0.2 起校验 token，唯一给插件留的免 token 通道是
      // X-Tan-Source: dsh-plugin（见 api.py 的 _authorized 第 0 条）。
      // 少这个 header，/context 和 /turn 全部 401，记忆会静默失效。
      headers: {
        "Content-Type": "application/json",
        "X-Tan-Source": "dsh-plugin",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ac.signal,
    });
    if (res.status === 401) {
      if (!authWarned) {
        authWarned = true;
        console.warn("[hippocampus] 记忆服务返回 401：插件鉴权未通过，记忆注入/写入被跳过");
      }
      return null;
    }
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
  const d = event?.data;
  if (!d) return "";
  // 两种事件体不一样：
  //   user/message      → data.content
  //   assistant/message → data.message.content
  // 只读后者会让每一条用户消息都被读成空串（检索词恒为空、写入时 user 字段恒空）。
  const content = Array.isArray(d.content) ? d.content : d.message?.content;
  if (!Array.isArray(content)) return "";
  return content
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

/**
 * 宿主注入块的指纹。
 * 这些内容会以 user/message 事件出现，但根本不是用户说的话：
 * 时间快照、运行时上下文、系统提醒、技能清单、操作者锚点。
 * 拿它们去检索记忆，只会用一段英文模板把中文记忆全推下去。
 */
const HOST_BLOCK_MARKS = [
  "<system-reminder>",
  "<available_skills>",
  "<project_instructions>",
  "Current runtime context.",
  "[OPERATOR ANCHOR",
  "## Tools\n",
];

function looksLikeHostNoise(txt) {
  if (!txt) return true;
  const head = txt.slice(0, 400);
  return HOST_BLOCK_MARKS.some((m) => head.includes(m));
}

/**
 * 最近一条真正的用户消息（作为检索 query）
 *
 * 有个坑：system-prompt/assemble 是在**本轮用户消息写进事件流之前**调用的。
 * 实测 09:54:34 调 /context 时，events 末尾还是上一轮的 assistant 消息，
 * 于是 q 恒为空、每轮都在盲搜 —— 记忆系统对 IM 一直是瞎的。
 *
 * 所以要有兜底：拿不到本轮用户消息时，用最近一条 assistant 消息，
 * 至少让检索承接上一轮的上下文，比空搜强。
 */
function lastUserText(events) {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type !== "user/message") continue;
    const kind = ev.data?.source?.kind;
    // time-context / runtime-context 都是宿主塞的，不是用户说的话
    if (kind === "time-context" || kind === "runtime-context") continue;
    const txt = textOf(ev);
    if (!txt) continue;
    if (looksLikeHostNoise(txt)) continue;
    return txt.slice(0, 200);
  }
  // 兜底：沿用上一轮的助手发言，别让检索空转
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type !== "assistant/message") continue;
    const txt = textOf(ev);
    if (txt && txt.length > 8) return txt.slice(0, 200);
  }
  return "";
}

/** 当前 turn 序号，用来做「同一轮只取一次记忆」的缓存键 */
function currentTurn(events) {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "turn/start" && typeof ev.data?.turn === "number") {
      return ev.data.turn;
    }
  }
  return -1;
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

/* ---------------- /turn 提交节流 ---------------- */

/**
 * 把 1.5 秒窗口内的多个 turn 合并成一次 /turn 提交。
 *
 * 起因：连发三条消息时 turn/end 会来三次，以前就打三次 /turn，
 * 服务端跟着跑三遍切分/去重/写入。合并之后一次搞定，
 * user / assistant 各自按顺序用空行拼起来，turn 取最后一次。
 *
 * 只用 setTimeout + 数组，不引入依赖。
 */
const TURN_WINDOW_MS = 1500;
const TURN_MERGE_MAX = 5;

/** 所有还活着的合并器：插件卸载时要统一 flush 残留 */
const turnThrottles = new Set();

export function createTurnThrottle(submit, opts = {}) {
  const windowMs = Number.isFinite(opts.windowMs) ? opts.windowMs : TURN_WINDOW_MS;
  const mergeMax = Number.isFinite(opts.mergeMax) ? opts.mergeMax : TURN_MERGE_MAX;

  let queue = [];
  let timer = null;

  function stopTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  /** 立刻提交并清空缓冲；出任何错都只会少记一条，不影响对话 */
  function flush() {
    stopTimer();
    if (queue.length === 0) return 0;
    const batch = queue;
    queue = [];
    const last = batch[batch.length - 1];
    // 按顺序拼接，空段落直接跳过，避免连出两个空行
    const join = (key) => batch.map((x) => x[key]).filter(Boolean).join("\n\n");
    try {
      submit({
        user: join("user"),
        assistant: join("assistant"),
        session: last.session,
        turn: last.turn,
        count: batch.length,
      });
    } catch {
      // 提交失败只丢记忆，绝不能冒泡到对话
    }
    return batch.length;
  }

  /** 收一条；攒满 mergeMax 条立即 flush，否则等窗口结束 */
  function push(item) {
    if (!item) return;
    queue.push({
      user: item.user || "",
      assistant: item.assistant || "",
      session: item.session || "",
      turn: typeof item.turn === "number" ? item.turn : 0,
    });
    if (queue.length >= mergeMax) {
      flush();
      return;
    }
    // 窗口从第一条进来开始算，后面进来的不重置计时
    if (timer === null) {
      timer = setTimeout(() => {
        timer = null;
        flush();
      }, windowMs);
    }
  }

  const handle = {
    push,
    flush,
    get pending() {
      return queue.length;
    },
    dispose() {
      flush(); // 卸载前把攒着的先写完
      turnThrottles.delete(handle);
    },
  };
  turnThrottles.add(handle);
  return handle;
}

/** 插件卸载 / agent 释放时，把还没到窗口的 turn 一起写掉 */
export function flushAllTurnThrottles() {
  for (const t of Array.from(turnThrottles)) {
    try {
      t.flush();
    } catch {
      // 同上：吞掉
    }
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

  // 插件卸载时把所有 agent 攒着的 turn 一起写掉，别丢最后几条
  ctx.effect(() => () => flushAllTurnThrottles(), "hippocampus: turn throttle flush");

  // ①·5 让模型自己管记忆：register memory_save / memory_search
  // 注册失败只影响工具，不影响注入，所以整段包在 try 里
  try {
    const disposeTools = registerMemoryTools(ctx, base);
    if (typeof disposeTools === "function") ctx.effect?.(() => disposeTools, "hippocampus: memory tools");
  } catch (e) {
    logger?.warn?.(`dsh-tan: memory tools 未注册（${e}）`);
  }

  // ①·6 主动发消息：走 tan 自己的原生微信通道（im-native.js）
  //     不再依赖 dsh-im —— 那条路上消息能发出去，但 DSH 主程序会把 prompt
  //     内容当数组校验，回 "content.some is not a function"，返回值不可信。
  try {
    const disposeIm = registerNativeImTools(ctx);
    if (typeof disposeIm === "function") ctx.effect?.(() => disposeIm, "hippocampus: native im tools");
  } catch (e) {
    logger?.warn?.(`dsh-tan: 原生 im 工具未注册（${e}）`);
  }

  // ①·7 微信自动回：轮询新机器人 → 提交给会话 → 回合结束把回复发回去
  try {
    const stopLoop = startWxLoop(ctx, { logger });
    if (typeof stopLoop === "function") ctx.effect?.(() => stopLoop, "hippocampus: wx loop");
    logger?.info?.("dsh-tan: 微信自动回循环已就绪");
  } catch (e) {
    logger?.warn?.(`dsh-tan: 微信自动回未启动（${e}）`);
  }

  // ② 每个 agent 一建好就挂上本插件的事件（agent.ctx 随 agent 释放自动解绑）
  ctx.on("agent/created", ({ agent }) => {
    if (!agent?.ctx) return;

    // 工具在 agent.ctx 上再注册一次。根 ctx 未必暴露 tools，
    // 而 agent 级别是 dsh-browser-cdp 验证过的路径。
    try {
      if (!agent.__tanTools) {
        agent.__tanTools = true;
        registerMemoryTools(agent.ctx, base);
        // im_send 也必须在 agent 级注册 —— 根 ctx 上注册的工具不落到 agent，
        // 跟 memory_save 踩的是同一个坑。第一次只写在根 ctx，重启后完全没生效。
        // 原生通道只读配置文件，不需要从根 ctx 取服务，所以只传 agent.ctx。
        registerNativeImTools(agent.ctx);
      }
    } catch (e) {
      logger?.warn?.(`dsh-tan: agent 级工具注册失败 ${e}`);
    }

    // 对话前：人格 + 记忆 + 攒下的主动消息
    agent.ctx.on("system-prompt/assemble", async (assembly, context, next) => {
      const result = await next();
      try {
        if (context?.agent !== undefined && context.agent !== agent) return result;

        const events = agent.session?.snapshotEvents?.() ?? [];
        // assemble 一个 turn 里会被调好几次（每次都可能带工具定义刷新）。
        // 以前每次都重新 /context：日志里同一条 query 刷 3~12 行，
        // 服务端还跟着重复记强化。这里按 (session, turn) 缓存一次结果。
        const cacheKey = `${agent.session?.id ?? ""}#${currentTurn(events)}`;
        if (cacheKey !== lastContextKey) {
          const q = lastUserText(events);
          // 把 query 打出来：空 query 会让检索直接失效，这是最容易静默出问题的地方
          logger?.info?.(`[hippocampus] q=${JSON.stringify(q.slice(0, 40))} (${q.length})`);
          const data = await callApi(base, "/context", { query: q });
          lastContextKey = cacheKey;
          lastContextText = data?.text ?? "";
        }
        const text = lastContextText;
        const pending = drainPending(root);
        if (!text && !pending) return result;

        const drop = new Set(["hippocampus:memory", "hippocampus:pending"]);
        const sections = Array.isArray(result?.sections)
          ? result.sections.filter((s) => !drop.has(s?.name))
          : [];
        if (text) {
          sections.push({ name: "hippocampus:memory", order: MEMORY_ORDER, text });
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

    // 对话后：写入这一轮。
    // 每个 agent 一个合并器：不同会话的 turn 绝不能混进同一次提交里
    // （否则 session / turn 会被别的东西覆盖）。
    const turnThrottle = createTurnThrottle((batch) => {
      void callApi(
        base,
        "/turn",
        {
          user: batch.user,
          assistant: batch.assistant,
          // session 取提交那一刻的当前 session.id，turn 取最后一条
          session: agent.session?.id ?? batch.session,
          turn: batch.turn,
        },
        8000
      );
    });

    // agent 释放时把还没到窗口的 turn 写掉
    agent.ctx.effect?.(() => () => turnThrottle.dispose(), "hippocampus: turn throttle");

    agent.ctx.on("session/event", (session, event) => {
      try {
        if (event?.type !== "turn/end") return;
        if (session !== undefined && session !== agent.session) return;
        const turn = event.data?.turn;
        if (typeof turn !== "number") return;

        const events = agent.session?.snapshotEvents?.() ?? [];
        const { user, assistant } = extractTurn(events, turn);
        if (!user && !assistant) return;

        // 不在这里直接 POST，交给节流合并器（1.5 秒窗口 / 攒满 5 条即发）
        turnThrottle.push({
          user,
          assistant,
          session: agent.session?.id ?? "",
          turn,
        });

        // 这一轮如果是微信来的，把回复发回微信（每条都要回，不参与合并）
        const sid = agent.session?.id;
        if (sid && assistant) {
          void replyToWechat(sid, assistant).catch(() => {});
        }
      } catch {
        // 记忆写入失败不能影响对话
      }
    });
  });
}
