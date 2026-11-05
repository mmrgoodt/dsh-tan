/**
 * dsh-tan 记忆工具：让模型自己管记忆
 *
 * 为什么要有这个：
 *   插件原来靠 /turn 把每轮对话丢给 Python，让正则去猜「这句算不算偏好」。
 *   正则猜不准 —— 「不用做这个我需要你能更改设备的燃烧时间」就被当成了性格。
 *
 *   模型自己最清楚这一轮产生了什么。所以开两个工具：
 *     memory_save   —— 存：由模型判断存什么、算什么类型
 *     memory_search —— 查：模型主动检索，不用等注入
 *
 * 服务端 /mem/save 做原子化决策（add / update / noop），不重复存同一件事。
 */

const DEFAULT_API = "http://127.0.0.1:18765";

async function post(base, path, body, timeoutMs = 4000) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const res = await fetch(base + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // 少了这个 header 服务端一律 401，记忆会静默失效
        "X-Tan-Source": "dsh-plugin",
      },
      body: JSON.stringify(body ?? {}),
      signal: ac.signal,
    });
    if (!res.ok) return { error: `HTTP ${res.status}` };
    return await res.json();
  } catch (e) {
    return { error: String(e?.message ?? e) };
  } finally {
    clearTimeout(timer);
  }
}

export function registerMemoryTools(ctx, base = DEFAULT_API) {
  // 静默失败吃过三次亏。这次把 ctx 的形态直接打出来。
  const shape = {
    tools: typeof ctx?.tools,
    register: typeof ctx?.tools?.register,
    on: typeof ctx?.on,
    systemPrompt: typeof ctx?.systemPrompt,
  };
  ctx?.logger?.info?.(`dsh-tan: ctx 形态 ${JSON.stringify(shape)}`);
  if (typeof ctx?.tools?.register !== "function") {
    ctx?.logger?.warn?.("dsh-tan: 没有 tools.register，工具注册不了");
    return () => {};
  }
  ctx.logger?.info?.("dsh-tan: 开始注册 memory_save / memory_search");

  const disposers = [];

  // ── 存 ──────────────────────────────────────────────
  try {
    disposers.push(
      ctx.tools.register({
        name: "memory_save",
        description:
          "把一件事写进长期记忆。由你自己判断该不该记、算什么类型：\n" +
          "  fact       事实（环境、配置、结论、路径）\n" +
          "  preference 他的偏好（怎么说话、喜欢什么、不要什么）\n" +
          "  lesson     教训（踩过的坑、别再犯的错）\n" +
          "  method     可复用的步骤或做法\n" +
          "  identity   关于「他是谁」\n" +
          "  episode    某天发生了什么事\n" +
          "规则：一件事一条，写完整句子，不要写「用户说了什么」这种转述。\n" +
          "服务端会自己判断重复（已记过的只强化）和冲突（新事实覆盖旧事实）。",
        parameters: {
          type: "object",
          additionalProperties: false,
          properties: {
            text: { type: "string", description: "要记住的内容，一句话说清一件事" },
            kind: {
              type: "string",
              enum: ["fact", "preference", "lesson", "method", "identity", "episode"],
              description: "记忆类型",
            },
            about: { type: "string", description: "可选：这件事的主题词，便于以后检索" },
          },
          required: ["text"],
        },
        output: {
          schema: {
            type: "object",
            additionalProperties: true,
            properties: {
              ok: { type: "boolean" },
              action: { type: "string" },
              id: { type: "string" },
              similar: { type: "number" },
              reason: { type: "string" },
            },
          },
          // DSH 要求 output 必须带 render，只写 schema 会抛
          // "must declare output { schema, render, presentationMeta? }"
          render: (_args, r) => [
            {
              type: "text",
              text: r?.ok
                ? `记忆已处理：${r.action}${r.id ? " " + r.id : ""}${r.reason ? "（" + r.reason + "）" : ""}`
                : `记忆没写进去：${r?.error ?? "未知原因"}`,
            },
          ],
        },
        execute: async ({ text, kind, about }) => {
          const r = await post(base, "/mem/save", { text, kind: kind || "fact", about: about || "" });
          if (r.error) return { ok: false, error: r.error };
          return {
            ok: true,
            action: r.action,
            id: r.id,
            similar: r.similar,
            reason:
              r.action === "noop"
                ? "已经记过了，只做了强化"
                : r.action === "update"
                  ? `覆盖了旧条目 ${r.superseded}`
                  : "记住了",
          };
        },
      })
    );
  } catch (e) {
    ctx.logger?.warn?.(`dsh-tan: memory_save 注册失败 ${e}`);
  }

  // ── 查 ──────────────────────────────────────────────
  try {
    disposers.push(
      ctx.tools.register({
        name: "memory_search",
        description:
          "在自己过去的记忆里检索。对话开始时通常已经自动注入了相关记忆，\n" +
          "这里用于：想确认某件事记过没有、按具体关键词精确找、或者查更久以前的记录。",
        parameters: {
          type: "object",
          additionalProperties: false,
          properties: {
            query: { type: "string", description: "要查的内容，用关键词而不是整句话" },
            limit: { type: "integer", description: "返回几条，默认 6，最多 15" },
          },
          required: ["query"],
        },
        output: {
          schema: {
            type: "object",
            additionalProperties: true,
            properties: {
              ok: { type: "boolean" },
              memories: {
                type: "array",
                items: {
                  type: "object",
                  additionalProperties: true,
                  properties: {
                    id: { type: "string" },
                    layer: { type: "string" },
                    text: { type: "string" },
                  },
                },
              },
            },
          },
          render: (_args, r) => {
            const list = r?.memories ?? [];
            if (!list.length) {
              return [{ type: "text", text: r?.note || "没找到相关的记忆。" }];
            }
            return [{
              type: "text",
              text: list.map((m, i) => `${i + 1}. [${m.layer}] ${m.text}`).join("\n"),
            }];
          },
        },
        execute: async ({ query, limit }) => {
          const r = await post(base, "/mem/search", {
            query,
            limit: Math.min(15, Math.max(1, limit || 6)),
          });
          if (r.error) return { ok: false, error: r.error };
          return { ok: true, memories: r.memories || [] };
        },
      })
    );
  } catch (e) {
    ctx.logger?.warn?.(`dsh-tan: memory_search 注册失败 ${e}`);
  }

  return () => {
    for (const d of disposers) {
      try {
        d?.();
      } catch {}
    }
  };
}
