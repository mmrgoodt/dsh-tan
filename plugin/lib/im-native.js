/**
 * tan 原生微信通道（v1）
 *
 * 为什么自己写：dsh-im 那条路上，消息能被发出去，但 DSH 主程序在处理 prompt
 * 内容时把字符串当数组用，回一句 "content.some is not a function"，于是工具
 * 永远报失败。发没发出去只能靠猜。这个文件把「发」和「收」都收回到 tan 自己
 * 手里，成功与否看微信接口自己的返回，不再看别人的脸色。
 *
 * 做三件事：
 *   1. 发 —— 直接打 ilink/bot/sendmessage，返回 {ok, httpStatus, ret}
 *   2. 收 —— 自己维护游标打 ilink/bot/getupdates，落到收件箱 jsonl
 *   3. 查 —— 本地状态、游标、未读，都在一个 state 文件里
 *
 * 重要前提：微信的 getupdates 游标是「一条队」，谁拿谁消费。dsh-im 的微信
 * 通道还开着的时候，两边同时轮询会互相抢消息。所以自动收消息默认关闭，
 * 要用 im_watch {"enable":true} 显式打开，并且先把 dsh-im 那边的微信停掉。
 *
 * 配置来源（都不写死）：
 *   ~/.dsh/integrations/dsh-weixin/config.json      机器人账号与 baseUrl
 *   ~/.dsh/integrations/dsh-weixin/workspaces.json   已配的投递目标
 *   ~/.dsh/.credentials.yaml                         bot token
 *   <TAN_ROOT>/state/weixin-native.json              游标 / 开关 / 已读
 *   <TAN_ROOT>/state/weixin-inbox.jsonl              收到的消息
 */

import { existsSync, mkdirSync, readFileSync, appendFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, dirname } from 'node:path';
import { randomUUID, randomBytes } from 'node:crypto';
import { encode as qrEncode, toPng as qrToPng, toAscii as qrToAscii } from './qr.js';

const HOME = process.env.USERPROFILE || homedir();
const DSH = join(HOME, '.dsh');
const WX_DIR = join(DSH, 'integrations', 'dsh-weixin');
const TAN_ROOT = process.env.TAN_ROOT || 'D:\\dsh-tan';
const STATE_PATH = join(TAN_ROOT, 'state', 'weixin-native.json');
const INBOX_PATH = join(TAN_ROOT, 'state', 'weixin-inbox.jsonl');
const ACCOUNT_PATH = join(TAN_ROOT, 'state', 'weixin-account.json');   // 自己扫码登录拿到的账号
const LOGIN_PATH = join(TAN_ROOT, 'state', 'weixin-login.json');       // 正在进行的扫码会话
const QR_PNG_PATH = join(TAN_ROOT, 'state', 'login-qrcode.png');

const ILINK_APP_ID = 'bot';
const ILINK_CLIENT_VERSION = (2 << 16) | (4 << 8) | 6;
const PROTOCOL_VERSION = '2.4.6';
const DEFAULT_LONG_POLL_MS = 35_000;
const SEND_TIMEOUT_MS = 20_000;

/* ---------------- 配置 ---------------- */

function readJson(path, fallback = null) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return fallback;
  }
}

function loadAccount() {
  // 先用 tan 自己扫码登录拿到的账号；没有才回落到 dsh-im 的配置
  const own = readJson(ACCOUNT_PATH);
  if (own?.token && own?.botId) {
    return {
      botId: own.botId,
      baseUrl: own.baseUrl || 'https://ilinkai.weixin.qq.com/',
      ownerUserId: own.ownerUserId || '',
      token: own.token,
      source: 'tan',
    };
  }

  const cfg = readJson(join(WX_DIR, 'config.json'));
  const account = cfg?.accounts?.[0] || cfg?.bots?.[0];
  if (!account) throw new Error('读不到微信机器人配置（config.json）');
  const tokenRef = account.tokenRef;
  if (!tokenRef) throw new Error('机器人配置里没有 tokenRef');

  const credPath = join(DSH, '.credentials.yaml');
  if (!existsSync(credPath)) throw new Error(`找不到凭证文件 ${credPath}`);
  const credText = readFileSync(credPath, 'utf8');
  const hit = credText.match(new RegExp(`^\\s*${tokenRef}\\s*:\\s*(.+)$`, 'm'));
  if (!hit) throw new Error(`凭证里找不到 ${tokenRef}`);

  return {
    botId: account.botId || account.id,
    baseUrl: account.baseUrl || 'https://ilinkai.weixin.qq.com/',
    ownerUserId: account.ownerUserId || (account.ownerOpenIds || [])[0] || '',
    token: hit[1].trim().replace(/^["']|["']$/g, ''),
  };
}

function defaultTarget() {
  const own = readJson(ACCOUNT_PATH);
  if (own?.ownerUserId) return own.ownerUserId;
  const ws = readJson(join(WX_DIR, 'workspaces.json'), {});
  const cfg = readJson(join(WX_DIR, 'config.json'), {});
  const botId = cfg?.accounts?.[0]?.botId || cfg?.bots?.[0]?.id;
  const targets = ws?.deliveryTargets?.[botId] || {};
  for (const t of Object.values(targets)) {
    const to = t?.route?.toUserId;
    if (typeof to === 'string' && to) return to;
  }
  return cfg?.accounts?.[0]?.ownerUserId || '';
}

function loadState() {
  const s = readJson(STATE_PATH, null);
  if (s && typeof s === 'object') {
    return { cursor: '', seen: [], enabled: false, lastReadAt: 0, ...s };
  }
  return { cursor: '', seen: [], enabled: false, lastReadAt: 0 };
}

function saveState(state) {
  mkdirSync(dirname(STATE_PATH), { recursive: true });
  writeFileSync(STATE_PATH, JSON.stringify(state, null, 1));
}

/* ---------------- 协议 ---------------- */

function headers(token) {
  return {
    'iLink-App-Id': ILINK_APP_ID,
    'iLink-App-ClientVersion': String(ILINK_CLIENT_VERSION),
    'content-type': 'application/json',
    AuthorizationType: 'ilink_bot_token',
    'X-WECHAT-UIN': Buffer.from(String(randomBytes(4).readUInt32BE(0)), 'utf8').toString('base64'),
    Authorization: `Bearer ${token}`,
  };
}

function baseInfo() {
  return { channel_version: PROTOCOL_VERSION, bot_agent: 'TanNativeChannel/1.0' };
}

async function callApi(account, endpoint, body, timeoutMs) {
  const url = new URL(endpoint, account.baseUrl);
  const res = await fetch(url, {
    method: 'POST',
    headers: headers(account.token),
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const raw = await res.text();
  let parsed = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    /* 非 JSON 一律当失败，原文带回去 */
  }
  return { httpStatus: res.status, httpOk: res.ok, parsed, raw };
}

export async function sendText(text, { toUserId } = {}) {
  const body = String(text ?? '').trim();
  if (!body) return { ok: false, error: '正文是空的' };
  let account;
  try {
    account = loadAccount();
  } catch (e) {
    return { ok: false, error: String(e?.message || e) };
  }
  const to = toUserId || defaultTarget() || account.ownerUserId;
  if (!to) return { ok: false, error: '没有收件人：配置里也没有可用的投递目标' };

  const clientId = `tan-${randomUUID()}`;
  let r;
  try {
    r = await callApi(account, 'ilink/bot/sendmessage', {
      msg: {
        from_user_id: '',
        to_user_id: to,
        client_id: clientId,
        message_type: 2,
        message_state: 2,
        item_list: [{ type: 1, text_item: { text: body } }],
      },
      base_info: baseInfo(),
    }, SEND_TIMEOUT_MS);
  } catch (e) {
    return { ok: false, error: `网络失败: ${e?.message || e}`, to, clientId };
  }

  const ret = r.parsed?.ret ?? r.parsed?.errcode ?? null;
  const rejected = ret !== null && ret !== undefined && ret !== 0;
  const ok = r.httpOk && !rejected;
  return {
    ok,
    httpStatus: r.httpStatus,
    ret,
    to,
    clientId,
    ...(ok ? {} : { raw: String(r.raw || '').slice(0, 500) }),
  };
}

/** 打一次 getupdates，返回新消息（不落盘） */
async function fetchUpdates(account, state, timeoutMs) {
  const r = await callApi(account, 'ilink/bot/getupdates', {
    get_updates_buf: state.cursor || '',
    base_info: baseInfo(),
  }, timeoutMs);

  const msgs = Array.isArray(r.parsed?.msgs) ? r.parsed.msgs : [];
  const nextCursor = r.parsed?.get_updates_buf;
  return { msgs, nextCursor: typeof nextCursor === 'string' ? nextCursor : state.cursor, httpStatus: r.httpStatus };
}

/** 从一条原始消息里抠出文本；不是文本消息就返回空 */
function messageText(msg) {
  const items = Array.isArray(msg?.item_list) ? msg.item_list : [];
  const parts = [];
  for (const item of items) {
    if (item?.type === 1 && typeof item.text_item?.text === 'string') parts.push(item.text_item.text);
  }
  return parts.join('\n').trim();
}

/** 轮询一次，把新消息追加进收件箱。返回本次新增条数 */
export async function pollOnce({ timeoutMs = DEFAULT_LONG_POLL_MS } = {}) {
  let account;
  try {
    account = loadAccount();
  } catch (e) {
    return { ok: false, added: 0, error: String(e?.message || e) };
  }
  const state = loadState();
  let r;
  try {
    r = await fetchUpdates(account, state, timeoutMs);
  } catch (e) {
    // 长轮询本来就会因为「这段时间没人说话」而超时，那不是错误。
    // 微信这套接口空转就是超时，跟 dsh-im 把 timeout 当「没有新消息」一致。
    const timedOut = e?.name === 'TimeoutError' || e?.name === 'AbortError' || /abort|timeout/i.test(String(e?.message || ''));
    if (timedOut) return { ok: true, added: 0, timedOut: true, messages: [], cursor: state.cursor };
    return { ok: false, added: 0, error: `拉取失败: ${e?.message || e}` };
  }

  const seen = new Set(state.seen);
  const fresh = [];
  for (const msg of r.msgs) {
    const id = String(msg?.msg_id ?? msg?.message_id ?? '');
    const text = messageText(msg);
    if (!id || !text || seen.has(id)) continue;
    seen.add(id);
    fresh.push({
      id,
      from: msg?.from_user_id || '',
      at: msg?.create_time_ms || Date.now(),
      text,
    });
  }

  if (fresh.length) {
    mkdirSync(dirname(INBOX_PATH), { recursive: true });
    appendFileSync(INBOX_PATH, fresh.map((m) => JSON.stringify(m)).join('\n') + '\n');
  }

  if (r.nextCursor && r.nextCursor !== state.cursor) state.cursor = r.nextCursor;
  state.seen = [...seen].slice(-500);
  saveState(state);
  return { ok: true, added: fresh.length, messages: fresh, cursor: state.cursor };
}

/** 读收件箱里没读过的消息 */
export function readInbox({ limit = 20, markRead = true } = {}) {
  if (!existsSync(INBOX_PATH)) return { ok: true, messages: [], unread: 0 };
  const lines = readFileSync(INBOX_PATH, 'utf8').split('\n').filter(Boolean);
  const state = loadState();
  const since = state.lastReadAt || 0;
  const all = [];
  for (const line of lines) {
    try {
      const m = JSON.parse(line);
      if (m && typeof m.text === 'string') all.push(m);
    } catch {
      /* 坏行跳过 */
    }
  }
  const unread = all.filter((m) => (m.at || 0) > since);
  const messages = unread.slice(-limit);
  if (markRead && unread.length) {
    state.lastReadAt = Math.max(...unread.map((m) => m.at || 0));
    saveState(state);
  }
  return { ok: true, messages, unread: unread.length, total: all.length };
}

/* ---------------- 工具注册 ---------------- */

export function registerNativeImTools(ctx) {
  if (typeof ctx?.tools?.register !== 'function') return () => {};

  const disposers = [];

  disposers.push(ctx.tools.register({
    name: 'im_send',
    description:
      '主动给他发一条微信。走 tan 自己的原生通道，直接打微信 bot 接口，' +
      '不再经过 dsh-im / DSH 的消息管线，所以返回值可信：ok=true 就是真发出去了。\n' +
      '用于：到点提醒、任务完成通知、需要他本人马上知道的事。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        text: { type: 'string', description: '要发的正文，纯文本' },
        to: { type: 'string', description: '收件人 userId，留空发默认目标' },
      },
      required: ['text'],
    },
    execute: async ({ text, to }) => sendText(text, { toUserId: to }),
    output: {
      schema: {
        type: 'object',
        properties: {
          ok: { type: 'boolean' },
          httpStatus: { type: 'number' },
          ret: { type: ['number', 'null'] },
          to: { type: 'string' },
          error: { type: 'string' },
          raw: { type: 'string' },
        },
      },
      render: (r) => (r?.ok
        ? `已发出（HTTP ${r.httpStatus}）→ ${r.to}`
        : `没发出去：${r?.error || `HTTP ${r?.httpStatus} ret=${r?.ret}`}`),
    },
  }));

  disposers.push(ctx.tools.register({
    name: 'im_inbox',
    description:
      '读微信收件箱里还没读过的消息（由 im_poll / im_watch 拉取）。' +
      '默认读完就标记已读。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        limit: { type: 'number', description: '最多返回几条，默认 20' },
        markRead: { type: 'boolean', description: '是否标记已读，默认 true' },
      },
    },
    execute: async ({ limit, markRead }) => readInbox({ limit: limit || 20, markRead: markRead !== false }),
    output: {
      schema: {
        type: 'object',
        properties: {
          ok: { type: 'boolean' },
          unread: { type: 'number' },
          total: { type: 'number' },
          messages: { type: 'array' },
        },
      },
      render: (r) => {
        if (!r?.ok) return '收件箱读不出来';
        if (!r.messages?.length) return `没有未读（收件箱共 ${r.total ?? 0} 条）`;
        return `未读 ${r.unread} 条：\n` + r.messages
          .map((m) => `· ${m.text.slice(0, 60)}${m.text.length > 60 ? '…' : ''}`)
          .join('\n');
      },
    },
  }));

  disposers.push(ctx.tools.register({
    name: 'im_poll',
    description:
      '手动拉一次微信新消息（一次长轮询），结果落进收件箱。' +
      '注意：dsh-im 的微信通道还开着时会跟它抢游标，先用 im_channel {"enable":false} 确认。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        timeoutMs: { type: 'number', description: '长轮询超时，默认 35000' },
      },
    },
    execute: async ({ timeoutMs }) => pollOnce({ timeoutMs: timeoutMs || DEFAULT_LONG_POLL_MS }),
    output: {
      schema: {
        type: 'object',
        properties: {
          ok: { type: 'boolean' },
          added: { type: 'number' },
          error: { type: 'string' },
          messages: { type: 'array' },
          cursor: { type: 'string' },
        },
      },
      render: (r) => (r?.ok ? `这次拉到 ${r.added} 条新消息` : `拉取失败：${r?.error || '未知'}`),
    },
  }));

  disposers.push(ctx.tools.register({
    name: 'im_channel',
    description:
      '查看或开关 tan 原生微信通道：enable=true 开始自动收消息，false 停。' +
      '开关只影响轮询收消息，「发」随时可用。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        enable: { type: 'boolean', description: '留空只看状态' },
      },
    },
    execute: async ({ enable }) => {
      const state = loadState();
      if (typeof enable === 'boolean') {
        state.enabled = enable;
        saveState(state);
      }
      let account = null;
      try {
        account = loadAccount();
      } catch {
        /* 配置不全也照常报状态 */
      }
      return {
        ok: true,
        enabled: state.enabled,
        botId: account?.botId || null,
        defaultTarget: defaultTarget() || account?.ownerUserId || null,
        cursor: state.cursor ? `${state.cursor.slice(0, 12)}…` : '（空，还没拉过）',
        statePath: STATE_PATH,
        inboxPath: INBOX_PATH,
      };
    },
    output: {
      schema: {
        type: 'object',
        properties: {
          ok: { type: 'boolean' },
          enabled: { type: 'boolean' },
          botId: { type: 'string' },
          defaultTarget: { type: 'string' },
          cursor: { type: 'string' },
          statePath: { type: 'string' },
          inboxPath: { type: 'string' },
        },
      },
      render: (r) => [
        `自动收消息：${r?.enabled ? '开' : '关'}`,
        `机器人：${r?.botId || '—'}`,
        `默认收件人：${r?.defaultTarget || '—'}`,
        `游标：${r?.cursor || '—'}`,
      ].join('\n'),
    },
  }));

  disposers.push(ctx.tools.register({
    name: 'im_login',
    description:
      '给 tan 自己的微信通道做扫码绑定：action=start 生成二维码（图案是自研编码器画的，' +
      '存在 state/login-qrcode.png），action=poll 查扫码进度，确认后凭据写进 ' +
      'D:\dsh-tan\state\weixin-account.json，之后发消息就用这个账号，不再借 dsh-im。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        action: { type: 'string', enum: ['start', 'poll', 'status'], description: 'start 出码 / poll 查进度 / status 看当前账号' },
        verifyCode: { type: 'string', description: '微信要求输验证码时填这里' },
      },
      required: ['action'],
    },
    execute: async ({ action, verifyCode }) => {
      if (action === 'start') return beginLogin();
      if (action === 'poll') return pollLogin({ verifyCode });
      const own = readJson(ACCOUNT_PATH, null);
      const state = loadState();
      return {
        ok: true,
        account: own
          ? { botId: own.botId, ownerUserId: own.ownerUserId, baseUrl: own.baseUrl, createdAt: own.createdAt, source: own.source }
          : null,
        accountPath: ACCOUNT_PATH,
        qrPng: existsSync(QR_PNG_PATH) ? QR_PNG_PATH : null,
        polling: state.enabled,
      };
    },
    output: {
      schema: {
        type: 'object',
        properties: {
          ok: { type: 'boolean' },
          status: { type: 'string' },
          pngPath: { type: 'string' },
          ascii: { type: 'string' },
          qrText: { type: 'string' },
          botId: { type: 'string' },
          account: { type: 'object' },
          error: { type: 'string' },
          hint: { type: 'string' },
          note: { type: 'string' },
        },
      },
      render: (r) => {
        if (!r?.ok) return `没成：${r?.error || '未知原因'}`;
        if (r.ascii) {
          return `二维码已生成：${r.pngPath}
内容：${r.qrText}

${r.ascii}

${r.hint || ''}`;
        }
        if (r.status) return `扫码状态：${r.status}${r.note ? `（${r.note}）` : ''}`;
        return `账号：${r.account ? r.account.botId : '还没绑定'}`;
      },
    },
  }));

  return () => {
    for (const d of disposers) {
      try {
        if (typeof d === 'function') d();
      } catch {
        /* 卸载失败不影响别的 */
      }
    }
  };
}

/* ---------------- 扫码登录（自己的二维码） ---------------- */

/**
 * 申请一张绑定二维码。
 * 微信只给一段文本（qrcode_img_content），图案由我们自己画 —— qr.js 是自研编码器。
 */
export async function beginLogin({ botType = '3' } = {}) {
  let account;
  try {
    account = loadAccount();
  } catch {
    account = { baseUrl: 'https://ilinkai.weixin.qq.com/', token: '' };
  }
  const url = new URL(`ilink/bot/get_bot_qrcode?bot_type=${encodeURIComponent(botType)}`, account.baseUrl);
  let res;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: {
        'iLink-App-Id': ILINK_APP_ID,
        'iLink-App-ClientVersion': String(ILINK_CLIENT_VERSION),
        'content-type': 'application/json',
      },
      body: JSON.stringify({ local_token_list: [] }),
      signal: AbortSignal.timeout(15000),
    });
  } catch (e) {
    return { ok: false, error: `申请二维码失败: ${e?.message || e}` };
  }
  const raw = await res.text();
  let parsed = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    /* 非 JSON */
  }
  const qrText = typeof parsed?.qrcode_img_content === 'string' ? parsed.qrcode_img_content.trim() : '';
  const qrcode = typeof parsed?.qrcode === 'string' ? parsed.qrcode.trim() : '';
  if (!res.ok || !qrText || !qrcode) {
    return { ok: false, httpStatus: res.status, error: '微信没有给回二维码内容', raw: String(raw).slice(0, 300) };
  }

  // 自己编码、自己出图
  let pngPath = null;
  let ascii = '';
  let version = null;
  try {
    const { modules, version: v } = qrEncode(qrText);
    version = v;
    mkdirSync(dirname(QR_PNG_PATH), { recursive: true });
    writeFileSync(QR_PNG_PATH, qrToPng(modules, { scale: 10, margin: 4 }));
    pngPath = QR_PNG_PATH;
    ascii = qrToAscii(modules, { quiet: 1 });
  } catch (e) {
    return { ok: false, error: `二维码生成失败: ${e?.message || e}`, qrcode, qrText };
  }

  mkdirSync(dirname(LOGIN_PATH), { recursive: true });
  writeFileSync(LOGIN_PATH, JSON.stringify({ qrcode, qrText, startedAt: Date.now(), pngPath }, null, 1));

  return {
    ok: true,
    qrcode,
    qrText,
    version,
    pngPath,
    ascii,
    hint: '用手机微信扫这张码；扫完让我 poll 一下就能拿到凭据',
  };
}

/** 查一次扫码状态；confirmed 时把凭据写成 tan 自己的账号文件 */
export async function pollLogin({ qrcode, verifyCode } = {}) {
  const login = readJson(LOGIN_PATH, null);
  const token = String(qrcode || login?.qrcode || '').trim();
  if (!token) return { ok: false, error: '还没有发起过扫码，先 start 一次' };

  const base = 'https://ilinkai.weixin.qq.com/';
  let endpoint = `ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(token)}`;
  if (verifyCode) endpoint += `&verify_code=${encodeURIComponent(String(verifyCode).trim())}`;

  let res;
  try {
    res = await fetch(new URL(endpoint, base), {
      method: 'GET',
      headers: {
        'iLink-App-Id': ILINK_APP_ID,
        'iLink-App-ClientVersion': String(ILINK_CLIENT_VERSION),
      },
      signal: AbortSignal.timeout(40000),
    });
  } catch (e) {
    const timedOut = e?.name === 'TimeoutError' || /abort/i.test(String(e?.message || ''));
    if (timedOut) return { ok: true, status: 'wait', note: '还没扫（本轮等待超时）' };
    return { ok: false, error: `查询状态失败: ${e?.message || e}` };
  }
  const raw = await res.text();
  let parsed = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    /* 非 JSON */
  }
  const status = typeof parsed?.status === 'string' ? parsed.status : '';
  if (!status) return { ok: false, error: '微信没回状态', raw: String(raw).slice(0, 300) };

  if (status === 'confirmed') {
    const botToken = parsed.bot_token;
    const accountId = parsed.ilink_bot_id;
    const ownerUserId = parsed.ilink_user_id;
    if (!botToken || !accountId || !ownerUserId) {
      return { ok: false, status, error: '授权成功但凭据不完整' };
    }
    const account = {
      botId: `wx_${String(accountId).replace(/[^0-9a-zA-Z]/g, '').slice(0, 24)}`,
      accountId,
      ownerUserId,
      baseUrl: typeof parsed.baseurl === 'string' && parsed.baseurl ? parsed.baseurl : 'https://ilinkai.weixin.qq.com/',
      token: botToken,
      createdAt: new Date().toISOString(),
      source: 'tan-qr',
    };
    mkdirSync(dirname(ACCOUNT_PATH), { recursive: true });
    writeFileSync(ACCOUNT_PATH, JSON.stringify(account, null, 1));
    return { ok: true, status, accountPath: ACCOUNT_PATH, botId: account.botId, ownerUserId, note: 'tan 现在有自己的账号了' };
  }

  return { ok: true, status, needVerifyCode: status === 'need_verifycode', verifyCodeHint: parsed?.verify_code_hint ?? null };
}
