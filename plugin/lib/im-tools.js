// 往 IM 里主动发一条消息。
//
// 为什么要这个工具：proactive 那套走的是飞书群 webhook，只能单向推 ——
// 他回了消息这边收不到，等于一个喇叭。dsh-im 插件本身支持双向，
// 并且提供了 ctx.dshIm.send(botId, targetId, text)，就在同一个进程里能调。
//
// 所以：到点了不是去 POST 一个 webhook，而是直接让插件自己发。
// 这样发出去的消息就在他跟机器人原本的会话里，他回一句我就能收到。

import { readFileSync, existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const HOME = process.env.USERPROFILE || homedir();

/** 从 DSH 的集成配置里读出现有机器人，不写死任何 ID */
function loadBots() {
  const out = [];
  const dirs = [
    ["weixin", join(HOME, ".dsh", "integrations", "dsh-weixin")],
    ["feishu", join(HOME, ".dsh", "integrations", "dsh-feishu")],
  ];
  for (const [kind, dir] of dirs) {
    const f = join(dir, "config.json");
    if (!existsSync(f)) continue;
    try {
      const cfg = JSON.parse(readFileSync(f, "utf8"));
      for (const b of cfg.accounts || cfg.bots || []) {
        if (b.botId || b.id) {
          out.push({
            kind,
            botId: b.botId || b.id,
            name: b.botName || b.accountId || kind,
            owner: b.ownerUserId || (b.ownerOpenIds || [])[0] || "",
          });
        }
      }
    } catch {
      /* 配置文件坏了就当没有 */
    }
  }
  return out;
}

/**
 * 上层回报失败时，反过来用出站记录确认"到底发没发"。
 * 微信账号的 state.json 里 recentOutboundMessages 是插件自己写的：
 * 真发出去了才有这条，没发就没有 —— 比工具返回值靠谱。
 * 只认最近 2 分钟、正文逐字相等的那条，避免把旧消息当成本次的。
 */
async function confirmOutboundSent(text, timeoutMs) {
  const accountsRoot = join(HOME, ".dsh", "integrations", "dsh-weixin", "accounts");
  const want = String(text).trim();
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    let dirs = [];
    try {
      dirs = readdirSync(accountsRoot, { withFileTypes: true })
        .filter((d) => d.isDirectory())
        .map((d) => join(accountsRoot, d.name));
    } catch {
      dirs = [];
    }
    for (const dir of dirs) {
      const f = join(dir, "state.json");
      if (!existsSync(f)) continue;
      try {
        const st = JSON.parse(readFileSync(f, "utf8"));
        const list = Array.isArray(st?.recentOutboundMessages) ? st.recentOutboundMessages : [];
        for (let i = list.length - 1; i >= 0; i -= 1) {
          const m = list[i];
          if (!m || typeof m.sentAt !== "number") continue;
          if (Date.now() - m.sentAt > 2 * 60 * 1000) break;
          if (String(m.text || "").trim() === want) return m;
        }
      } catch {
        /* 文件正在写，下一轮再看 */
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return null;
}

export function registerImTools(ctx, hostCtx) {
  if (typeof ctx?.tools?.register !== "function") return () => {};

  // 工具要注册在 agent 级的 ctx 上；但 dshIm 是 host 级服务。
  // 两个 ctx 不一定是同一个实例，所以分开传：
  //   ctx      → 用来 ctx.tools.register
  //   hostCtx  → 用来读 ctx.dshIm.send
  const service = hostCtx || ctx;
  let imSend = null;
  try {
    imSend = service?.dshIm?.send || ctx?.dshIm?.send || null;
  } catch (e) {
    ctx?.logger?.warn?.(`dsh-tan: 读 dshIm 失败 ${e?.message || e}`);
  }

  const bots = loadBots();
  ctx?.logger?.info?.(
    `dsh-tan: 可投递的机器人 ${bots.map((b) => b.kind + "/" + b.name).join(", ") || "无"}`,
  );
  ctx?.logger?.info?.(
    `dsh-tan: dshIm.send ${imSend ? "可用" : "不可用"}`,
  );

  const desc = bots.length
    ? "已接入：" + bots.map((b) => `${b.kind}(${b.name})`).join("、")
    : "当前没有读到达标的机器人";

  const dispose = ctx.tools.register({
    name: "im_send",
    description:
      "主动往微信/飞书里发一条消息，不经由用户的提问触达。\n" +
      "用于：攒下的话到点提醒、任务完成通知、需要他本人知道的事。\n" +
      desc +
      "\n注意：targetId 是投递目标的别名，不是用户 ID。首次使用要先在\n" +
      "「设置 → IM机器人 → 齿轮 → 新建目标」里配一个，否则发不出去。",
    parameters: {
      type: "object",
      additionalProperties: false,
      properties: {
        text: { type: "string", description: "要发的正文，纯文本" },
        target: {
          type: "string",
          description: "投递目标别名 targetId。留空则用环境变量 TAN_IM_TARGET",
        },
        channel: {
          type: "string",
          description: "weixin 或 feishu，留空则优先微信",
        },
      },
      required: ["text"],
    },
    execute: async ({ text, target, channel }) => {
      const want = channel || "weixin";
      const bot = bots.find((b) => b.kind === want) || bots[0];
      if (!bot) return { ok: false, error: "没有可用的机器人" };

      // 目标有两种形式：
      //   1) 别名字符串 tgt_xxx —— 走 listTargets 查找
      //   2) 草稿对象 {kind, route} —— 跳过查找，直接发
      // 第二种是绕开 listTargets 的路：那条路上有脏会话记录会让插件内部
      // 抛 "content.some is not a function"（content 不是数组）。
      let targetId = target || process.env.TAN_IM_TARGET || bot.owner;
      if (typeof targetId === "string" && targetId.trim().startsWith("{")) {
        try {
          const obj = JSON.parse(targetId);
          if (obj && obj.kind && obj.route) targetId = obj;
        } catch {
          /* 不是 JSON 就按别名处理 */
        }
      }
      if (!targetId) {
        return {
          ok: false,
          error: "没有 targetId。请在设置里新建一个投递目标。",
          bots: bots.map((b) => ({ kind: b.kind, name: b.name })),
        };
      }
      if (!imSend) {
        return { ok: false, error: "ctx.dshIm.send 不可用" };
      }

      try {
        // dshIm.send(botId, targetId, text, opts) —— text 必须是字符串。
        // 传数组会被判 bad-request（"Message text is required"），所以别试数组。
        //
        // 已知坑（dsh-im 5.9.1）：消息其实已经发到微信了，但上层在把这条
        // 出站消息回灌进会话视图时抛 "content.some is not a function"
        // （harness-client 里对 content 按数组处理）。于是工具回报失败，
        // 实际消息已经躺在对方聊天里。谁要是在这个"失败"上重试，就是重复发送。
        //
        // 所以：收到这个错不重发，先去 state.json 的 recentOutboundMessages
        // 里核对这条正文在不在；在就按成功回报，不在才认输。
        const body = String(text);
        let r;
        try {
          r = await imSend.call(service.dshIm, bot.botId, targetId, body, { format: "plain" });
        } catch (e1) {
          const msg1 = String(e1?.message || e1);
          if (!/content\.some|not a function/.test(msg1)) throw e1;
          ctx?.logger?.info?.(`dsh-tan: 上层回报 ${msg1}，去 state.json 核对是否已发出`);
          const hit = await confirmOutboundSent(body, 4000);
          if (hit) {
            return {
              ok: true,
              channel: bot.kind,
              botId: bot.botId,
              targetId: typeof targetId === "string" ? targetId : JSON.stringify(targetId),
              verified: "state.json",
              deliveredAt: new Date(hit.sentAt).toISOString(),
              note: `上层回报「${msg1}」，但出站记录证明已发出（别重发，会重复）`,
            };
          }
          throw e1;
        }
        return {
          ok: r?.sent === true,
          channel: bot.kind,
          botId: bot.botId,
          targetId: typeof targetId === "string" ? targetId : JSON.stringify(targetId),
          raw: r ?? null,
        };
      } catch (e) {
        return {
          ok: false,
          error: String(e?.message || e),
          botId: bot.botId,
          targetId,
        };
      }
    },
    output: {
      schema: {
        type: "object",
        properties: {
          ok: { type: "boolean" },
          channel: { type: "string" },
          targetId: { type: "string" },
          error: { type: "string" },
        },
      },
      render: (r) =>
        r?.ok
          ? `已发到 ${r.channel || "IM"}（目标 ${r.targetId}）`
          : `没发出去：${r?.error || "未知原因"}`,
    },
  });

  return () => {
    if (typeof dispose === "function") dispose();
  };
}
