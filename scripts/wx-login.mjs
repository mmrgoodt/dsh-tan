/**
 * 给 api.py 用的微信绑定桥：出码 / 查状态。
 * 用法：node scripts/wx-login.mjs start|poll
 * 输出：一行 JSON（api.py 直接转发给面板）
 *
 * 为什么放这儿：二维码的编码和 PNG 都在 plugin/lib 里（自研、零依赖），
 * Python 那边不重复实现一遍，只负责调用和转发。
 */
import { beginLogin, pollLogin } from '../plugin/lib/im-native.js';
import { encode, toPng } from '../plugin/lib/qr.js';
import { readFileSync } from 'node:fs';

const action = (process.argv[2] || 'start').trim();
const ACCOUNT = 'D:/dsh-tan/state/weixin-account.json';

function accountInfo() {
  try {
    const a = JSON.parse(readFileSync(ACCOUNT, 'utf8'));
    return { bound: true, botId: a.botId, ownerUserId: a.ownerUserId, createdAt: a.createdAt };
  } catch {
    return { bound: false };
  }
}

try {
  if (action === 'start') {
    const r = await beginLogin();
    if (!r.ok) {
      console.log(JSON.stringify({ ok: false, error: r.error || '申请二维码失败' }));
    } else {
      const { modules, version } = encode(r.qrText);
      const png = toPng(modules, { scale: 12, margin: 3 });
      console.log(JSON.stringify({
        ok: true,
        status: 'wait',
        qrcode: r.qrcode,
        version,
        qrText: r.qrText,
        png: 'data:image/png;base64,' + png.toString('base64'),
      }));
    }
  } else if (action === 'poll') {
    const p = await pollLogin({});
    console.log(JSON.stringify({ ...p, ...accountInfo() }));
  } else if (action === 'status') {
    console.log(JSON.stringify({ ok: true, ...accountInfo() }));
  } else {
    console.log(JSON.stringify({ ok: false, error: `未知动作 ${action}` }));
  }
} catch (e) {
  console.log(JSON.stringify({ ok: false, error: String(e?.message || e) }));
}
