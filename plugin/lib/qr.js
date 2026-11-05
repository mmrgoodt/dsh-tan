/**
 * 自己写的二维码编码器 —— tan 的登录码不借别人的库。
 *
 * 支持：字节模式（UTF-8）、纠错等级 M、版本 1–10 自动选最小、8 种掩码按罚分挑最优。
 * 输出：布尔矩阵（true = 黑），以及一个不依赖任何第三方包的 PNG 编码器。
 *
 * 为什么不用现成的 qrcode 包：登录码是 tan 自己的门面，别人包一升级、一改 API，
 * 这个流程就得跟着改。算法写在自己手里，只有标准，没有依赖。
 *
 * 正确性验证：本地拿 qrcode 包当对照物（只在测试里用，运行时不需要），
 * 同一段文本同一版本，矩阵必须逐位相同。见 test-qr.mjs。
 */

import { deflateSync } from 'node:zlib';

/* ---------------- 标准常量 ---------------- */

const ECL_BITS = { L: 0b01, M: 0b00, Q: 0b11, H: 0b10 };

// 版本 1–10 的总码字数 / 数据码字数 / 每块纠错码字数 / 块数（纠错等级 M）
const TOTAL_CODEWORDS = [26, 44, 70, 100, 134, 172, 196, 242, 292, 346];
const DATA_CODEWORDS_M = [16, 28, 44, 64, 86, 108, 124, 154, 182, 216];
const EC_PER_BLOCK_M = [10, 16, 26, 18, 24, 16, 18, 22, 22, 26];
const BLOCKS_M = [1, 1, 1, 2, 2, 4, 4, 4, 5, 5];

// 对齐图案的中心坐标
const ALIGN_POS = [
  [], [6, 18], [6, 22], [6, 26], [6, 30],
  [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50],
];

const PAD_BYTES = [0xec, 0x11];

/* ---------------- GF(256) 与 Reed-Solomon ---------------- */

const GF_EXP = new Uint8Array(512);
const GF_LOG = new Uint8Array(256);
(() => {
  let x = 1;
  for (let i = 0; i < 255; i += 1) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i += 1) GF_EXP[i] = GF_EXP[i - 255];
})();

function gfMul(a, b) {
  if (a === 0 || b === 0) return 0;
  return GF_EXP[GF_LOG[a] + GF_LOG[b]];
}

function rsGeneratorPoly(degree) {
  let poly = [1];
  for (let i = 0; i < degree; i += 1) {
    const next = new Array(poly.length + 1).fill(0);
    for (let j = 0; j < poly.length; j += 1) {
      next[j] ^= poly[j];
      next[j + 1] ^= gfMul(poly[j], GF_EXP[i]);
    }
    poly = next;
  }
  return poly;
}

function rsEncode(data, ecCount) {
  const gen = rsGeneratorPoly(ecCount);
  const res = new Array(ecCount).fill(0);
  for (const byte of data) {
    const factor = byte ^ res[0];
    res.shift();
    res.push(0);
    if (factor !== 0) {
      for (let i = 0; i < ecCount; i += 1) res[i] ^= gfMul(gen[i + 1], factor);
    }
  }
  return res;
}

/* ---------------- 位流 ---------------- */

class BitBuffer {
  constructor() {
    this.bits = [];
  }

  put(value, length) {
    for (let i = length - 1; i >= 0; i -= 1) this.bits.push((value >>> i) & 1);
  }

  get length() {
    return this.bits.length;
  }

  toBytes() {
    const out = [];
    for (let i = 0; i < this.bits.length; i += 8) {
      let byte = 0;
      for (let j = 0; j < 8; j += 1) byte = (byte << 1) | (this.bits[i + j] || 0);
      out.push(byte);
    }
    return out;
  }
}

/* ---------------- 数据编码 ---------------- */

function pickVersion(byteLength) {
  for (let v = 1; v <= 10; v += 1) {
    const countBits = v <= 9 ? 8 : 16;
    const capacityBits = DATA_CODEWORDS_M[v - 1] * 8;
    if (4 + countBits + byteLength * 8 <= capacityBits) return v;
  }
  return 0;
}

function buildCodewords(bytes, version) {
  const dataCw = DATA_CODEWORDS_M[version - 1];
  const countBits = version <= 9 ? 8 : 16;

  const bb = new BitBuffer();
  bb.put(0b0100, 4); // 字节模式
  bb.put(bytes.length, countBits);
  for (const b of bytes) bb.put(b, 8);

  const capacityBits = dataCw * 8;
  if (bb.length > capacityBits) throw new Error('数据超出该版本容量');
  // 结束符最多 4 位
  const terminator = Math.min(4, capacityBits - bb.length);
  bb.put(0, terminator);
  // 补到整字节
  while (bb.length % 8 !== 0) bb.bits.push(0);

  const data = bb.toBytes();
  let idx = 0;
  while (data.length < dataCw) {
    data.push(PAD_BYTES[idx % 2]);
    idx += 1;
  }

  // 分块 + 纠错
  const numBlocks = BLOCKS_M[version - 1];
  const ecLen = EC_PER_BLOCK_M[version - 1];
  const shortSize = Math.floor(dataCw / numBlocks);
  const longCount = dataCw % numBlocks;
  const blocks = [];
  let cursor = 0;
  for (let i = 0; i < numBlocks; i += 1) {
    const size = shortSize + (i >= numBlocks - longCount ? 1 : 0);
    const chunk = data.slice(cursor, cursor + size);
    cursor += size;
    blocks.push({ data: chunk, ec: rsEncode(chunk, ecLen) });
  }

  // 交织
  const out = [];
  const maxData = Math.max(...blocks.map((b) => b.data.length));
  for (let i = 0; i < maxData; i += 1) {
    for (const b of blocks) if (i < b.data.length) out.push(b.data[i]);
  }
  for (let i = 0; i < ecLen; i += 1) {
    for (const b of blocks) out.push(b.ec[i]);
  }

  const expected = TOTAL_CODEWORDS[version - 1];
  if (out.length !== expected) throw new Error(`码字数不符：${out.length} != ${expected}`);
  return out;
}

/* ---------------- 矩阵 ---------------- */

function makeMatrix(size) {
  return Array.from({ length: size }, () => new Array(size).fill(null));
}

function placeFinder(m, row, col) {
  for (let r = -1; r <= 7; r += 1) {
    for (let c = -1; c <= 7; c += 1) {
      const rr = row + r;
      const cc = col + c;
      if (rr < 0 || rr >= m.length || cc < 0 || cc >= m.length) continue;
      const inside = r >= 0 && r <= 6 && c >= 0 && c <= 6;
      const dark = inside && (
        r === 0 || r === 6 || c === 0 || c === 6
        || (r >= 2 && r <= 4 && c >= 2 && c <= 4)
      );
      m[rr][cc] = dark;
    }
  }
}

function placeAlignment(m, version) {
  const pos = ALIGN_POS[version - 1];
  for (const r of pos) {
    for (const c of pos) {
      // 跟三个定位图形重叠的位置跳过
      const nearFinder = (r <= 8 && c <= 8) || (r <= 8 && c >= m.length - 9) || (r >= m.length - 9 && c <= 8);
      if (nearFinder) continue;
      for (let dr = -2; dr <= 2; dr += 1) {
        for (let dc = -2; dc <= 2; dc += 1) {
          const dark = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
          m[r + dr][c + dc] = dark;
        }
      }
    }
  }
}

function placeTiming(m) {
  for (let i = 8; i < m.length - 8; i += 1) {
    const dark = i % 2 === 0;
    if (m[6][i] === null) m[6][i] = dark;
    if (m[i][6] === null) m[i][6] = dark;
  }
}

function reserveFormatAreas(m) {
  const size = m.length;
  for (let i = 0; i < 9; i += 1) {
    if (m[8][i] === null) m[8][i] = false;
    if (m[i][8] === null) m[i][8] = false;
  }
  for (let i = 0; i < 8; i += 1) {
    if (m[8][size - 1 - i] === null) m[8][size - 1 - i] = false;
    if (m[size - 1 - i][8] === null) m[size - 1 - i][8] = false;
  }
  m[size - 8][8] = true; // 固定的暗模块
}

function bchRemainder(value, poly, polyBits) {
  let v = value << (polyBits - 1);
  const topBit = 1 << (polyBits - 1 + Math.floor(Math.log2(value || 1)));
  for (let bit = Math.floor(Math.log2(v || 1)); bit >= polyBits - 1; bit -= 1) {
    if ((v >> bit) & 1) v ^= poly << (bit - (polyBits - 1));
  }
  void topBit;
  return v;
}

function formatBits(ecl, mask) {
  const data = (ECL_BITS[ecl] << 3) | mask;
  let rem = data << 10;
  const gen = 0b10100110111;
  for (let bit = 14; bit >= 10; bit -= 1) {
    if ((rem >> bit) & 1) rem ^= gen << (bit - 10);
  }
  return ((data << 10) | rem) ^ 0b101010000010010;
}

function versionBits(version) {
  if (version < 7) return null;
  let rem = version << 12;
  const gen = 0b1111100100101;
  for (let bit = 17; bit >= 12; bit -= 1) {
    if ((rem >> bit) & 1) rem ^= gen << (bit - 12);
  }
  return (version << 12) | rem;
}

function writeFormat(m, ecl, mask) {
  const size = m.length;
  const bits = formatBits(ecl, mask);
  const bit = (i) => ((bits >> i) & 1) === 1;

  // 第一份：左上角，沿第 8 行与第 8 列摆
  for (let i = 0; i <= 5; i += 1) m[i][8] = bit(i);
  m[7][8] = bit(6);
  m[8][8] = bit(7);
  m[8][7] = bit(8);
  for (let i = 9; i < 15; i += 1) m[8][14 - i] = bit(i);

  // 第二份：右上角第 8 行 + 左下角第 8 列
  for (let i = 0; i < 8; i += 1) m[8][size - 1 - i] = bit(i);
  for (let i = 8; i < 15; i += 1) m[size - 15 + i][8] = bit(i);

  m[size - 8][8] = true; // 固定的暗模块
}

function writeVersion(m, version) {
  const bits = versionBits(version);
  if (bits === null) return;
  const size = m.length;
  for (let i = 0; i < 18; i += 1) {
    const dark = ((bits >> i) & 1) === 1;
    const r = Math.floor(i / 3);
    const c = i % 3;
    m[size - 11 + c][r] = dark;
    m[r][size - 11 + c] = dark;
  }
}

function zigzagPlace(m, data, log) {
  const size = m.length;
  let bitIndex = 0;
  let upward = true;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5; // 跳过竖直时序线那一列
    for (let vert = 0; vert < size; vert += 1) {
      const row = upward ? size - 1 - vert : vert;
      for (let j = 0; j < 2; j += 1) {
        const col = right - j;
        if (m[row][col] !== null) continue;
        const bit = bitIndex < data.length * 8
          ? ((data[bitIndex >> 3] >> (7 - (bitIndex & 7))) & 1) === 1
          : false;
        m[row][col] = bit;
        if (log) log.push([row, col]);
        bitIndex += 1;
      }
    }
    upward = !upward;
  }
  return bitIndex;
}

const MASKS = [
  (r, c) => (r + c) % 2 === 0,
  (r) => r % 2 === 0,
  (r, c) => c % 3 === 0,
  (r, c) => (r + c) % 3 === 0,
  (r, c) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
  (r, c) => ((r * c) % 2) + ((r * c) % 3) === 0,
  (r, c) => (((r * c) % 2) + ((r * c) % 3)) % 2 === 0,
  (r, c) => (((r + c) % 2) + ((r * c) % 3)) % 2 === 0,
];

function applyMask(m, maskIndex, reserved) {
  const size = m.length;
  const out = m.map((row) => row.slice());
  for (let r = 0; r < size; r += 1) {
    for (let c = 0; c < size; c += 1) {
      if (reserved[r][c]) continue;
      if (MASKS[maskIndex](r, c)) out[r][c] = !out[r][c];
    }
  }
  return out;
}

function penalty(m) {
  const size = m.length;
  let score = 0;

  // 规则 1：同色连续 5 个以上
  for (let r = 0; r < size; r += 1) {
    let run = 1;
    for (let c = 1; c < size; c += 1) {
      if (m[r][c] === m[r][c - 1]) run += 1;
      else {
        if (run >= 5) score += 3 + (run - 5);
        run = 1;
      }
    }
    if (run >= 5) score += 3 + (run - 5);
  }
  for (let c = 0; c < size; c += 1) {
    let run = 1;
    for (let r = 1; r < size; r += 1) {
      if (m[r][c] === m[r - 1][c]) run += 1;
      else {
        if (run >= 5) score += 3 + (run - 5);
        run = 1;
      }
    }
    if (run >= 5) score += 3 + (run - 5);
  }

  // 规则 2：2×2 同色块
  for (let r = 0; r < size - 1; r += 1) {
    for (let c = 0; c < size - 1; c += 1) {
      const v = m[r][c];
      if (v === m[r][c + 1] && v === m[r + 1][c] && v === m[r + 1][c + 1]) score += 3;
    }
  }

  // 规则 3：像定位图形的 1:1:3:1:1 序列
  const pattern = [true, false, true, true, true, false, true];
  const checkLine = (line) => {
    for (let i = 0; i + 6 < line.length; i += 1) {
      let hit = true;
      for (let k = 0; k < 7; k += 1) {
        if (line[i + k] !== pattern[k]) {
          hit = false;
          break;
        }
      }
      if (!hit) continue;
      const before = i >= 4 && line.slice(i - 4, i).every((v) => v === false);
      const after = i + 11 <= line.length && line.slice(i + 7, i + 11).every((v) => v === false);
      if (before || after) score += 40;
    }
  };
  for (let r = 0; r < size; r += 1) checkLine(m[r]);
  for (let c = 0; c < size; c += 1) checkLine(m.map((row) => row[c]));

  // 规则 4：黑块占比
  let dark = 0;
  for (const row of m) for (const v of row) if (v) dark += 1;
  const ratio = (dark * 100) / (size * size);
  score += Math.floor(Math.abs(ratio - 50) / 5) * 10;

  return score;
}

/* ---------------- 对外接口 ---------------- */

export function encode(text, { ecl = 'M', mask: forceMask = null } = {}) {
  if (ecl !== 'M') throw new Error('目前只实现了纠错等级 M');
  const bytes = [...Buffer.from(String(text), 'utf8')];
  const version = pickVersion(bytes.length);
  if (!version) throw new Error(`内容太长（${bytes.length} 字节），超出本编码器支持的版本上限`);

  const codewords = buildCodewords(bytes, version);
  const size = version * 4 + 17;
  const base = makeMatrix(size);

  placeFinder(base, 0, 0);
  placeFinder(base, 0, size - 7);
  placeFinder(base, size - 7, 0);
  placeAlignment(base, version);
  placeTiming(base);
  reserveFormatAreas(base);
  writeVersion(base, version);

  const reserved = base.map((row) => row.map((v) => v !== null));
  const __log = [];
  zigzagPlace(base, codewords, __log);

  let best = null;
  let bestScore = Infinity;
  let bestMask = 0;
  for (let mask = 0; mask < 8; mask += 1) {
    if (forceMask !== null && mask !== forceMask) continue;
    const candidate = applyMask(base, mask, reserved);
    writeFormat(candidate, ecl, mask);
    const score = penalty(candidate);
    if (score < bestScore) {
      bestScore = score;
      best = candidate;
      bestMask = mask;
    }
  }
  return { version, size, modules: best, mask: bestMask, penalty: bestScore };
}

/** 终端里直接看的 ASCII 版 */
export function toAscii(modules, { quiet = 2, inverse = false } = {}) {
  const size = modules.length;
  const pad = ' '.repeat(quiet);
  const lines = [];
  for (let i = 0; i < quiet; i += 1) lines.push('');
  for (let r = 0; r < size; r += 1) {
    let line = pad;
    for (let c = 0; c < size; c += 1) {
      const dark = inverse ? !modules[r][c] : modules[r][c];
      line += dark ? '██' : '  ';
    }
    lines.push(line + pad);
  }
  for (let i = 0; i < quiet; i += 1) lines.push('');
  return lines.join('\n');
}

/* ---------------- PNG（zlib 是 Node 内置的，不算第三方） ---------------- */

function crc32(buf) {
  let c;
  const table = crc32.table || (crc32.table = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n += 1) {
      c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })());
  let crc = 0xffffffff;
  for (const byte of buf) crc = table[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const typeBuf = Buffer.from(type, 'ascii');
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

/** 把矩阵渲染成 PNG 字节（黑底白码 / 白底黑码都可） */
export function toPng(modules, { scale = 8, margin = 4, dark = [0, 0, 0], light = [255, 255, 255] } = {}) {
  const size = modules.length;
  const dim = (size + margin * 2) * scale;
  const raw = Buffer.alloc(dim * (dim * 3 + 1));
  let p = 0;
  for (let y = 0; y < dim; y += 1) {
    raw[p] = 0; // filter: none
    p += 1;
    const my = Math.floor(y / scale) - margin;
    for (let x = 0; x < dim; x += 1) {
      const mx = Math.floor(x / scale) - margin;
      const inside = my >= 0 && my < size && mx >= 0 && mx < size;
      const isDark = inside && modules[my][mx];
      const rgb = isDark ? dark : light;
      raw[p] = rgb[0];
      raw[p + 1] = rgb[1];
      raw[p + 2] = rgb[2];
      p += 3;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(dim, 0);
  ihdr.writeUInt32BE(dim, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // color type: truecolor
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  return Buffer.concat([
    sig,
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/* 仅供测试：把中间产物抖出来（含放置顺序） */
export function __debugOrder(text) {
  const bytes = [...Buffer.from(String(text), 'utf8')];
  const version = pickVersion(bytes.length);
  const size = version * 4 + 17;
  const base = makeMatrix(size);
  placeFinder(base, 0, 0);
  placeFinder(base, 0, size - 7);
  placeFinder(base, size - 7, 0);
  placeAlignment(base, version);
  placeTiming(base);
  reserveFormatAreas(base);
  writeVersion(base, version);
  const reserved = base.map((row) => row.map((v) => v !== null));
  const order = [];
  let upward = true;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert += 1) {
      const row = upward ? size - 1 - vert : vert;
      for (let j = 0; j < 2; j += 1) {
        const col = right - j;
        if (reserved[row][col]) continue;
        order.push([row, col]);
      }
    }
    upward = !upward;
  }
  return { version, size, order, reserved };
}

/* 仅供测试：把中间产物抖出来 */
export function __debug(text) {
  const bytes = [...Buffer.from(String(text), 'utf8')];
  const version = pickVersion(bytes.length);
  return { version, codewords: buildCodewords(bytes, version) };
}
