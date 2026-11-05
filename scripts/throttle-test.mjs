/**
 * /turn 节流合并器的离线自测（不联网、不碰数据库）
 *
 * 跑法：node scripts/throttle-test.mjs
 *
 * 只验证 plugin/lib/index.js 里导出的 createTurnThrottle：
 *   1. 连续 3 次 push（模拟连发三条消息的三个 turn/end）→ 只 flush 一次，
 *      user / assistant 各按顺序用空行拼起来，turn 取最后一次
 *   2. 攒满 5 条立即 flush，不等窗口
 *   3. dispose() 会把残留写掉（agent 释放 / 插件卸载的场景）
 */
import assert from "node:assert/strict";
import { createTurnThrottle } from "../plugin/lib/index.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ok = (m) => console.log("✅ " + m);

/* ---------- 用例 1：窗口内 3 条合成 1 次 ---------- */
const calls = [];
const t1 = createTurnThrottle((batch) => calls.push(batch), { windowMs: 300 });

// 模拟 3 条消息在窗口内连续结束（同一个事件循环 tick，跟真实连发一样快）
t1.push({ user: "第一句", assistant: "回一", session: "sess-1", turn: 1 });
t1.push({ user: "第二句", assistant: "回二", session: "sess-1", turn: 2 });
t1.push({ user: "第三句", assistant: "回三", session: "sess-1", turn: 3 });

assert.equal(calls.length, 0, "窗口没结束前不该提交");
assert.equal(t1.pending, 3, "缓冲里应该攒着 3 条");

await sleep(500); // 等窗口过去

assert.equal(calls.length, 1, "3 次触发只该发生 1 次 flush");
assert.equal(calls[0].user, "第一句\n\n第二句\n\n第三句", "user 应按顺序空行拼接");
assert.equal(calls[0].assistant, "回一\n\n回二\n\n回三", "assistant 同理");
assert.equal(calls[0].session, "sess-1", "session 取当前会话");
assert.equal(calls[0].turn, 3, "turn 取最后一次");
assert.equal(calls[0].count, 3, "合并条数 3");
assert.equal(t1.pending, 0, "flush 后缓冲要清空");
console.log("   flush 次数:", calls.length, "| payload:", JSON.stringify(calls[0]));
ok("窗口内 3 条 turn 合并成 1 次提交，user 拼接正确");

/* ---------- 用例 2：攒满 5 条立即 flush ---------- */
const calls2 = [];
const t2 = createTurnThrottle((b) => calls2.push(b), { windowMs: 5000 });
for (let i = 1; i <= 5; i++) {
  t2.push({ user: "u" + i, assistant: "a" + i, session: "sess-2", turn: i });
}
assert.equal(calls2.length, 1, "第 5 条进来就该立即 flush");
assert.equal(calls2[0].count, 5, "一批 5 条");
assert.equal(calls2[0].turn, 5, "turn 取最后一条");
assert.equal(t2.pending, 0, "立即 flush 后缓冲清空");
t2.dispose();
console.log("   立即 flush payload turn =", calls2[0].turn, "| count =", calls2[0].count);
ok("攒满 5 条立即 flush，不等窗口");

/* ---------- 用例 3：dispose 时 flush 残留 ---------- */
const calls3 = [];
const t3 = createTurnThrottle((b) => calls3.push(b), { windowMs: 5000 });
t3.push({ user: "残留甲", assistant: "答甲", session: "sess-3", turn: 7 });
assert.equal(calls3.length, 0, "还没到窗口");
t3.dispose(); // 相当于 agent 释放 / 插件卸载
assert.equal(calls3.length, 1, "dispose 必须把残留写掉");
assert.equal(calls3[0].user, "残留甲");
assert.equal(calls3[0].turn, 7);
ok("dispose / 卸载时 flush 残留");

/* ---------- 用例 4：submit 抛异常不外溢 ---------- */
const t4 = createTurnThrottle(() => {
  throw new Error("模拟提交失败");
}, { windowMs: 100 });
t4.push({ user: "x", assistant: "y", session: "s", turn: 1 });
await sleep(200);
assert.equal(t4.pending, 0, "异常也不该让缓冲卡住");
ok("提交抛异常被吞掉，缓冲照常清空");

console.log("\n全部通过：节流合并器行为符合预期。");
