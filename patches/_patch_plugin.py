# -*- coding: utf-8 -*-
"""插件补丁：检索词只取真用户的话 + 同一 turn 不重复调 API"""
import io

p = "plugin/lib/index.js"
s = io.open(p, encoding="utf-8").read()


def sub1(text, old, new, tag):
    if old not in text:
        raise SystemExit("MISS: " + tag)
    if text.count(old) != 1:
        raise SystemExit("AMBIGUOUS(%d): %s" % (text.count(old), tag))
    return text.replace(old, new)


# 1) lastUserText：把宿主注入块挡掉
old_last = '''/** 最近一条用户消息（作为检索 query） */
function lastUserText(events) {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type !== "user/message") continue;
    // time-context 是宿主塞的时间快照，不是用户说的话，不能拿来当检索词
    if (ev.data?.source?.kind === "time-context") continue;
    const txt = textOf(ev);
    if (txt) return txt.slice(0, 200);
  }
  return "";
}'''

new_last = '''/**
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
  "## Tools\\n",
];

function looksLikeHostNoise(txt) {
  if (!txt) return true;
  const head = txt.slice(0, 400);
  return HOST_BLOCK_MARKS.some((m) => head.includes(m));
}

/** 最近一条真正的用户消息（作为检索 query） */
function lastUserText(events) {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type !== "user/message") continue;
    // time-context 是宿主塞的时间快照，不是用户说的话，不能拿来当检索词
    if (ev.data?.source?.kind === "time-context") continue;
    const txt = textOf(ev);
    if (!txt) continue;
    if (looksLikeHostNoise(txt)) continue;
    return txt.slice(0, 200);
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
}'''

s = sub1(s, old_last, new_last, "lastUserText")

# 2) assemble：同 turn 复用结果
old_assemble = '''        const events = agent.session?.snapshotEvents?.() ?? [];
        const data = await callApi(base, "/context", { query: lastUserText(events) });
        const pending = drainPending(root);
        if (!data?.text && !pending) return result;'''

new_assemble = '''        const events = agent.session?.snapshotEvents?.() ?? [];
        // assemble 一个 turn 里会被调好几次（每次都可能带工具定义刷新）。
        // 以前每次都重新 /context：日志里同一条 query 刷 3~12 行，
        // 服务端还跟着重复记强化。这里按 (session, turn) 缓存一次结果。
        const cacheKey = `${agent.session?.id ?? ""}#${currentTurn(events)}`;
        if (cacheKey !== lastContextKey) {
          const data = await callApi(base, "/context", { query: lastUserText(events) });
          lastContextKey = cacheKey;
          lastContextText = data?.text ?? "";
        }
        const text = lastContextText;
        const pending = drainPending(root);
        if (!text && !pending) return result;'''

s = sub1(s, old_assemble, new_assemble, "assemble_cache")

s = sub1(
    s,
    '''        if (data?.text) {
          sections.push({ name: "hippocampus:memory", order: MEMORY_ORDER, text: data.text });
        }''',
    '''        if (text) {
          sections.push({ name: "hippocampus:memory", order: MEMORY_ORDER, text });
        }''',
    "assemble_section",
)

# 3) 缓存变量声明
s = sub1(
    s,
    "let apiProcess = null;\nlet authWarned = false;",
    "let apiProcess = null;\nlet authWarned = false;\nlet lastContextKey = \"\";\nlet lastContextText = \"\";",
    "cache_vars",
)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("OK: plugin patched")
