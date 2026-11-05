/**
 * 微信自动回的闭环（v1）
 *
 * 链路：轮询新机器人的消息 → 用 typertGateway 把话提交给会话 → 这一轮结束时
 *      拿到我的回复文本 → 用原生通道发回微信。
 *
 * 为什么用 typertGateway：DSH 的 HTTP /api 对普通浏览器是关着的
 * （403 "Browser access is disabled"，要渲染器令牌）。但进程内的
 * typertGateway 不受这个限制 —— dsh-im 自己就是用它在提交 prompt 的。
 *
 * 默认关闭：state/weixin-native.json 里 enabled=true 才跑。
 */
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { pollOnce, sendText } from './im-native.js';

const HOME = process.env.USERPROFILE || '';
const ACCOUNTS = join(HOME, '.dsh', 'integrations', 'dsh-weixin', 'accounts');

/** 待回队列：sessionId -> { rpcId, at, from } */
const pending = new Map();

export function pendingFor(sessionId) {
  return pending.get(sessionId) || null;
}

export function settlePending(sessionId) {
  pending.delete(sessionId);
}

/** dsh-im 记着「微信会话 → DSH 会话」的映射，直接读它，不猜 */
function sessionIdForAnyChat() {
  if (!existsSync(ACCOUNTS)) return null;
  try {
    for (const name of readdirSync(ACCOUNTS)) {
      const f = join(ACCOUNTS, name, 'state.json');
      if (!existsSync(f)) continue;
      const st = JSON.parse(readFileSync(f, 'utf8'));
      const sessions = st?.sessions || {};
      const first = Object.values(sessions).find((v) => typeof v === 'string' && v);
      if (first) return first;
    }
  } catch {
    /* 读不到就算了 */
  }
  return null;
}

export function startWxLoop(ctx, { logger } = {}) {
  let gateway = null;
  try {
    gateway = ctx?.typertGateway || ctx?.get?.('typertGateway') || null;
  } catch {
    gateway = null;
  }
  if (!gateway || typeof gateway.invoke !== 'function') {
    logger?.warn?.('wx-loop: 拿不到 typertGateway（' + (gateway ? '没有 invoke' : '服务为空') + '），自动回暂不可用');
    return () => {};
  }
  logger?.info?.('wx-loop: 网关就绪，开始轮询新机器人消息');

  // typert 的调用需要一个 peer（dsh-im 也是这么拿的），没有它 prepareInvocation 会炸
  let peer = null;
  try {
    const conn = ctx?.connection || ctx?.get?.('connection');
    if (conn && typeof conn.createPeer === 'function') {
      peer = conn.createPeer({ principal: { kind: 'owner' }, interactions: 'none' });
    }
  } catch (e) {
    logger?.warn?.(`wx-loop: 建 peer 失败 ${e?.message || e}`);
  }

  let stopped = false;
  let running = false;

  const tick = async () => {
    if (stopped || running) return;
    running = true;
    try {
      const p = await pollOnce({ timeoutMs: 1500 });
      if (p.ok && p.added > 0) {
        const sessionId = sessionIdForAnyChat();
        if (!sessionId) return;
        for (const msg of p.messages) {
          const rpcId = `wx-${msg.id}`;
          pending.set(sessionId, { rpcId, at: Date.now(), from: msg.from });
          const args = {
            request: {
              requestId: rpcId,
              sessionId,
              mode: 'queue',
              content: [{ type: 'text', text: msg.text }],
            },
          };
          const signal = new AbortController().signal;
          const attempts = [
            { name: 'invoke+signal+peer', run: () => gateway.invoke({ namespace: 'session', method: 'prompt', args, signal, peer }) },
            { name: 'invoke+signal', run: () => gateway.invoke({ namespace: 'session', method: 'prompt', args, signal }) },
            { name: 'invoke-plain', run: () => gateway.invoke({ namespace: 'session', method: 'prompt', args }) },
          ];
          let done = false;
          let lastErr = null;
          for (const a of attempts) {
            try {
              await a.run();
              logger?.info?.(`wx-loop: 已提交（${a.name}）${msg.id}`);
              done = true;
              break;
            } catch (e) {
              lastErr = e;
              logger?.warn?.(`wx-loop: ${a.name} 失败 ${e?.message || e}`);
            }
          }
          if (!done) {
            pending.delete(sessionId);
            logger?.warn?.(`wx-loop: 三种提交姿势都没成，最后错误 ${lastErr?.message || lastErr}`);
          }
        }
      }
    } catch (e) {
      logger?.warn?.(`wx-loop: 轮询异常 ${e?.message || e}`);
    } finally {
      running = false;
    }
  };

  const timer = setInterval(() => void tick(), 4000);

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

/** 这一轮说完了：把回复发回微信 */
export async function replyToWechat(sessionId, text) {
  const p = pendingFor(sessionId);
  if (!p) return false;
  const body = String(text || '').trim();
  if (!body) return false;
  settlePending(sessionId);
  const r = await sendText(body, { toUserId: p.from });
  return r?.ok === true;
}
