# -*- coding: utf-8 -*-
"""3D 神经元 · 多波并行 + 余晖版

上一版的三个毛病：
  1. 一条波扫全图，看着像雷达扫描 —— 真实脑区是多路同时放电
  2. 波过去立刻熄灭 —— 真实神经元有「余晖」，刚放完电还在恢复
  3. 静息时整张图太暗 —— 看不出「这里躺着一堆东西」

改法：
  · 每个源单独一条波，相位错开 1/N 周期，永远有波在里面跑
  · 波头之后跟一条拖尾，被激活过的节点缓慢变暗
  · 背景点加大加亮，静息时也能看出结构
  · 颜色走「白黄 → 亮黄 → 橙 → 暗蓝」的衰减链，而不是两档跳变
"""
import math
import os
from collections import deque

import draw_brain_anim as _A

W, H = 1000, 750
OUT = _A.OUT
load, layout = _A.load, _A.layout
Image, ImageDraw, ImageFilter, ImageFont = (
    _A.Image, _A.ImageDraw, _A.ImageFilter, _A.ImageFont)

FRAMES = 24
FRAME_MS = 95
SIGMA = 0.10         # 波头很窄，才看得出「一条边在走」
TRAIL = 0.13         # 拖尾短，否则全图都留着余温
SOURCES = 1          # 单源：一次只有一滴水落下去，扩散看得最清楚


def project(pos, w, h):
    out = []
    ay, ax = math.radians(24), math.radians(-16)
    cy, sy = math.cos(ay), math.sin(ay)
    cx, sx = math.cos(ax), math.sin(ax)
    for x, y, z in pos:
        x1 = x * cy - z * sy
        z1 = x * sy + z * cy
        y1 = y * cx - z1 * sx
        z2 = y * sx + z1 * cx
        d = max(60.0, 900.0 + z2)
        s = 900.0 / d
        out.append((w / 2 + x1 * s * 1.95, h / 2 + y1 * s * 1.95, z2))
    return out


def bfs_from(adj, src):
    """单源加权 BFS，返回该源到每个点的跳数"""
    inf = 1e9
    dist = {k: inf for k in adj}
    if src not in dist:
        return dist
    dist[src] = 0.0
    dq = deque([src])
    while dq:
        cur = dq.popleft()
        base = dist[cur]
        for nb, w in adj.get(cur, ()):
            step = 1.0 / max(0.3, min(3.0, w * 0.35))
            nd = base + step
            if nd < dist[nb] - 1e-6:
                dist[nb] = nd
                dq.append(nb)
    return dist


def heat_color(g, t):
    """亮度和深度 → 颜色。走的是白黄 → 橙 → 蓝的衰减链"""
    if g <= 0.04:
        # 静息时压到很暗 —— 之前给到 55~150，整张图都是亮的，波完全看不出来
        return (int(70 + 30 * t), int(120 + 45 * t), int(190 + 45 * t)), int(18 + 52 * t)
    if g > 0.75:
        return (255, 252, 235), int(140 + 115 * min(1.0, g))
    if g > 0.42:
        return (255, 214, 92), int(120 + 135 * g)
    if g > 0.18:
        return (255, 165, 70), int(80 + 160 * g)
    # 余晖：橙往蓝褪
    k = g / 0.18
    r = int(160 + 70 * k)
    gg = int(150 + 40 * k)
    b = int(190 + 30 * k)
    return (r, gg, b), int(50 + 90 * k)


def build(names, edges, pos, active, path):
    pts = project(pos, W, H)
    zs = [p[2] for p in pts]
    zmin, zmax = min(zs), max(zs)
    span = max(1e-6, zmax - zmin)
    idx = {names[i][0]: i for i in range(len(names))}

    adj = {}
    for a, b, w in edges:
        if a in idx and b in idx:
            adj.setdefault(a, []).append((b, w))
            adj.setdefault(b, []).append((a, w))

    hot = sorted(active.items(), key=lambda kv: -kv[1])
    srcs = [n for n, _ in hot if n in idx][:SOURCES]
    if len(srcs) < SOURCES:
        for n in idx:
            if n not in srcs:
                srcs.append(n)
            if len(srcs) >= SOURCES:
                break

    # 距离不用跳数 —— 概念图直径只有 3~5 跳，波头一推就扫完全屏，
    # 出来是一片白。改用 3D 空间距离，波就像水波一样从一个点扩开。
    pos_by_name = {names[i][0]: pos[i] for i in range(len(names))}

    def spatial(src):
        sx, sy, sz = pos_by_name.get(src, (0, 0, 0))
        out = {}
        for nm, (x, y, z) in pos_by_name.items():
            out[nm] = math.sqrt((x - sx) ** 2 + (y - sy) ** 2 + (z - sz) ** 2)
        return out

    dists = [spatial(s) for s in srcs]
    dmax = 1.0
    for d in dists:
        fin = [v for v in d.values()]
        if fin:
            dmax = max(dmax, max(fin))

    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 11)
    fbig = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 21)
    fsmall = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 13)

    base = Image.new("RGB", (W, H), (4, 7, 16))
    es = [(idx[a], idx[b], w) for a, b, w in edges if a in idx and b in idx]
    es.sort(key=lambda e: min(pts[e[0]][2], pts[e[1]][2]))
    order = sorted(range(len(pts)), key=lambda i: pts[i][2])

    frames = []
    for f in range(FRAMES):
        prog = f / FRAMES
        line_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dot_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(line_layer)
        dd = ImageDraw.Draw(dot_layer)

        # 每个节点取「所有源里最强的那个激活度」
        glow = {}
        for name, i in idx.items():
            best = 0.0
            for si in range(len(srcs)):
                # 相位错开：永远有波在里面跑，不会出现全图同时熄灭
                ph = (prog + si / len(srcs)) % 1.0
                wave = -0.12 + 1.24 * ph
                d = dists[si].get(name, dmax) / max(1e-6, dmax)
                # 波头
                head = math.exp(-((d - wave) ** 2) / (2 * SIGMA ** 2))
                # 拖尾：刚过去的地方还没凉
                trail = math.exp(-((d - (wave - TRAIL * 0.5)) ** 2) / (2 * TRAIL ** 2)) * 0.55
                best = max(best, head, trail)
            glow[i] = min(1.0, best)

        for a, b, w in es:
            x1, y1, z1 = pts[a]; x2, y2, z2 = pts[b]
            if abs(x1 - x2) + abs(y1 - y2) > 380:
                continue
            t = ((z1 + z2) / 2 - zmin) / span
            g = max(glow[a], glow[b])
            col, _ = heat_color(g, t)
            al = int(9 + 46 * (t ** 1.4) + 185 * g)
            dl.line([(x1, y1), (x2, y2)],
                    fill=col + (min(240, al),),
                    width=max(1, int(min(4, 1 + w * 0.16 + 2.4 * g))))

        for i in order:
            x, y, z = pts[i]
            t = (z - zmin) / span
            g = glow[i]
            col, a = heat_color(g, t)
            # 静息时也有底子：不然看着像什么都没在跑
            rad = 1.2 + 2.0 * (t ** 1.5) + 7.0 * g
            dd.ellipse([x - rad, y - rad, x + rad, y + rad], fill=col + (min(250, a),))
            if g > 0.55:
                r2 = rad * 0.40
                dd.ellipse([x - r2, y - r2, x + r2, y + r2],
                           fill=(255, 255, 250, 235))

        glow_d = dot_layer.filter(ImageFilter.GaussianBlur(8))
        glow_l = line_layer.filter(ImageFilter.GaussianBlur(3))
        img = Image.alpha_composite(base.convert("RGBA"), glow_l)
        img = Image.alpha_composite(img, glow_d)
        img = Image.alpha_composite(img, line_layer)
        img = Image.alpha_composite(img, dot_layer)

        txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(txt)
        for i in range(len(pts)):
            x, y, z = pts[i]
            t = (z - zmin) / span
            g = glow[i]
            if g > 0.45:
                fill = (255, 238, 175, 245)
            elif g > 0.15:
                fill = (255, 200, 120, 220)
            else:
                fill = (120, 165, 210, int(50 + 70 * t))
            td.text((x + 6, y - 7), names[i][0], font=font, fill=fill)
        img = Image.alpha_composite(img, txt)

        cap = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cd = ImageDraw.Draw(cap)
        cd.text((24, 18), "记忆皮层 · 信号传播", font=fbig, fill=(230, 242, 255, 240))
        cd.text((24, 46), "%d 概念 / %d 突触 / %d 路并行"
                % (len(names), len(es), len(srcs)),
                font=fsmall, fill=(140, 185, 225, 220))
        cd.text((24, H - 24), "发射源：" + "、".join(srcs),
                font=fsmall, fill=(255, 214, 120, 225))
        img = Image.alpha_composite(img, cap)
        frames.append(img.convert("RGB"))

    q = [fr.quantize(colors=128, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
         for fr in frames]
    q[0].save(path, save_all=True, append_images=q[1:],
              duration=FRAME_MS, loop=0, optimize=True)
    return path


if __name__ == "__main__":
    names, edges, active = load()
    print("节点 %d / 边 %d / 焦点 %d" % (len(names), len(edges), len(active)))
    pos = layout(names, edges)
    print("布局完成")
    build(names, edges, pos, active, OUT)
    print("已生成:", OUT, os.path.getsize(OUT) // 1024, "KB")
