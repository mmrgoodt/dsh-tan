# -*- coding: utf-8 -*-
"""3D 神经元 · 波前传播版

上一版所有活跃节点一起呼吸，看着像一堆灯泡在闪，不像神经活动。
真正的神经活动是**信号沿突触扩散**：
  几个源点亮起来 → 涟漪沿连线推进 → 被扫过的节点亮黄 → 过去后暗掉。

实现：
  1. 从 working_memory 里取最活跃的几个概念当「发射源」
  2. 对每个节点算到最近源点的跳数（加权 BFS）
  3. 动画里波前从 0 往外推，节点亮度 = 高斯(距离 - 波前)
  4. 连线同样按两端节点的亮度染色

这样看起来才像「脑子里有东西在跑」，而不是一堆灯在闪。
"""
import math
import os
from collections import deque

import draw_brain_anim as _A

# 尺寸自己定，GIF 太大 IM 发不出去：1280x960 有 6.6MB，
# 缩到 1000x750 再加少几帧，体积能砍一半以上。
W, H = 1000, 750
OUT = _A.OUT
_ok, load, layout = _A._ok, _A.load, _A.layout
Image, ImageDraw, ImageFilter, ImageFont = (
    _A.Image, _A.ImageDraw, _A.ImageFilter, _A.ImageFont)


def project(pos, w, h):
    import math as _m
    out = []
    ay, ax = _m.radians(24), _m.radians(-16)
    cy, sy = _m.cos(ay), _m.sin(ay)
    cx, sx = _m.cos(ax), _m.sin(ax)
    for x, y, z in pos:
        x1 = x * cy - z * sy
        z1 = x * sy + z * cy
        y1 = y * cx - z1 * sx
        z2 = y * sx + z1 * cx
        d = max(60.0, 900.0 + z2)
        s = 900.0 / d
        out.append((w / 2 + x1 * s * 1.95, h / 2 + y1 * s * 1.95, z2))
    return out

FRAMES = 26
FRAME_MS = 95
SIGMA = 1.1          # 波前的宽度（跳数）。大了整张图一起亮，就没有「扩散」感了
SOURCES = 3          # 同时从几个点发波


def hop_dist(adj, srcs):
    """多源加权 BFS：返回到最近源点的「跳数」（按边权重折减）"""
    inf = 1e9
    dist = {k: inf for k in adj}
    dq = deque()
    for s in srcs:
        if s in dist:
            dist[s] = 0.0
            dq.append(s)
    while dq:
        cur = dq.popleft()
        base = dist[cur]
        for nb, w in adj.get(cur, ()):
            # 边越强，走得越快：强关系上信号传得顺
            step = 1.0 / max(0.3, min(3.0, w * 0.35))
            nd = base + step
            if nd < dist[nb] - 1e-6:
                dist[nb] = nd
                dq.append(nb)
    return dist


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

    # 发射源：working_memory 里强度最高的几个
    hot_names = sorted(active.items(), key=lambda kv: -kv[1])
    srcs = [n for n, _ in hot_names if n in idx][:SOURCES]
    if not srcs:
        srcs = list(idx)[:SOURCES]

    dist = hop_dist(adj, srcs)
    finite = [d for d in dist.values() if d < 1e9]
    dmax = max(finite) if finite else 1.0
    # 用原始跳数，不归一化 —— 归一化会把整个分布压进 0~1，
    # 高斯一算全都在波前里，结果整张图同时点亮。
    dn = {k: (v if v < 1e9 else dmax + 2) for k, v in dist.items()}

    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 11)
    fbig = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 21)
    fsmall = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 13)

    base = Image.new("RGB", (W, H), (4, 7, 16))
    es = [(idx[a], idx[b], w) for a, b, w in edges if a in idx and b in idx]
    es.sort(key=lambda e: min(pts[e[0]][2], pts[e[1]][2]))
    order = sorted(range(len(pts)), key=lambda i: pts[i][2])

    frames = []
    for f in range(FRAMES):
        # 波前从最内侧推到最外侧
        wave = -0.6 + (dmax + 1.2) * (f / (FRAMES - 1))

        line_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dot_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(line_layer)
        dd = ImageDraw.Draw(dot_layer)

        # 每个节点的当前亮度（0~1）
        glow = {}
        for name, i in idx.items():
            d = dn.get(name, 1.2)
            g = math.exp(-((d - wave) ** 2) / (2 * SIGMA ** 2))
            glow[i] = g

        for a, b, w in es:
            x1, y1, z1 = pts[a]; x2, y2, z2 = pts[b]
            if abs(x1 - x2) + abs(y1 - y2) > 380:
                continue
            t = ((z1 + z2) / 2 - zmin) / span
            g = max(glow[a], glow[b])
            # 线：底子淡蓝，被波扫到变金黄
            r = int(35 + 70 * t + 200 * g)
            gg = int(110 + 90 * t + 95 * g)
            bl = int(190 + 60 * t - 120 * g)
            al = int(30 + 110 * (t ** 1.4) + 130 * g)
            dl.line([(x1, y1), (x2, y2)],
                    fill=(min(255, r), min(255, gg), max(0, bl), min(250, al)),
                    width=max(1, int(min(3, 1 + w * 0.18 + 2 * g))))

        for i in order:
            x, y, z = pts[i]
            t = (z - zmin) / span
            g = glow[i]
            # 底子：静止的蓝点，很小
            rad0 = 1.4 + 2.0 * (t ** 1.6)
            dd.ellipse([x - rad0, y - rad0, x + rad0, y + rad0],
                       fill=(105, 170, 235, int(70 + 110 * t)))
            # 被波扫到：叠一层黄，大小跟着亮起来
            if g > 0.06:
                rad = rad0 + (2.0 + 6.0 * g) * (0.6 + 0.4 * t)
                a = int(255 * min(1.0, g * 1.25))
                dd.ellipse([x - rad, y - rad, x + rad, y + rad],
                           fill=(255, 208, 78, a))
                r2 = rad * 0.38
                dd.ellipse([x - r2, y - r2, x + r2, y + r2],
                           fill=(255, 252, 225, min(255, a + 30)))

        glow_d = dot_layer.filter(ImageFilter.GaussianBlur(9))
        glow_l = line_layer.filter(ImageFilter.GaussianBlur(3))
        img = Image.alpha_composite(base.convert("RGBA"), glow_l)
        img = Image.alpha_composite(img, glow_d)
        img = Image.alpha_composite(img, line_layer)
        img = Image.alpha_composite(img, dot_layer)

        # 全部名字：被波扫到的变亮黄，其余暗蓝
        txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(txt)
        for i in range(len(pts)):
            x, y, z = pts[i]
            t = (z - zmin) / span
            g = glow[i]
            if g > 0.25:
                fill = (255, 235, 150, int(180 + 75 * g))
            else:
                fill = (150, 195, 235, int(105 + 85 * t))
            td.text((x + 6, y - 7), names[i][0], font=font, fill=fill)
        img = Image.alpha_composite(img, txt)

        cap = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cd = ImageDraw.Draw(cap)
        cd.text((28, 22), "记忆皮层 · 信号传播", font=fbig, fill=(230, 242, 255, 240))
        cd.text((28, 52),
                "%d 概念 / %d 突触   ·   源点 %d 个   ·   黄色 = 信号经过"
                % (len(names), len(es), len(srcs)),
                font=fsmall, fill=(140, 185, 225, 225))
        # 源点名字，让人知道波从哪来
        cd.text((28, H - 28), "发射源：" + "、".join(srcs),
                font=fsmall, fill=(255, 214, 120, 230))
        img = Image.alpha_composite(img, cap)

        frames.append(img.convert("RGB"))

    q = [f.quantize(colors=128, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
         for f in frames]
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
