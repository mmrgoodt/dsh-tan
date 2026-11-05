# -*- coding: utf-8 -*-
"""3D 神经元 · 流动式

用户说「波前扩散」那套不好看，要流动式。

区别：
  波前 = 一圈一圈往外扫，像声呐
  流动 = 亮点沿着连线滑过去，像信号在神经纤维里传

做法：
  1. 每条强突触上放 1~2 个光粒子，速度跟权重挂钩（强连接传得快）
  2. 粒子沿边从一端滑到另一端，带一条短拖尾
  3. 粒子到哪儿，那个节点就微微亮起来 —— 局部而非全图
  4. 骨架（点和线）保持很暗，只做背景结构

于是画面是：一堆安静的蓝色神经元之间，有青色光点在连线里持续流动。
"""
import math
import os
import random

import draw_brain_anim as _A

W, H = 1000, 750
OUT = _A.OUT
load, layout = _A.load, _A.layout
Image, ImageDraw, ImageFilter, ImageFont = (
    _A.Image, _A.ImageDraw, _A.ImageFilter, _A.ImageFont)

FRAMES = 30
FRAME_MS = 80
MAX_EDGES_WITH_FLOW = 130     # 稀疏才好看：420 条边同时流会糊成一片白
PARTICLES_PER_EDGE = 1     # 每条边一个光点，多了就腻
TRAIL_LEN = 3                 # 拖尾点数
SPEED_BASE = 0.9              # 一个周期内跑几趟


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


def build(names, edges, pos, active, path):
    pts = project(pos, W, H)
    zs = [p[2] for p in pts]
    zmin, zmax = min(zs), max(zs)
    span = max(1e-6, zmax - zmin)
    idx = {names[i][0]: i for i in range(len(names))}

    rnd = random.Random(7)

    # 分配粒子：只给强突触，弱的目光跟不上
    es = [(idx[a], idx[b], w) for a, b, w in edges if a in idx and b in idx]
    es.sort(key=lambda e: -e[2])
    flow_edges = es[:MAX_EDGES_WITH_FLOW]

    parts = []
    for a, b, w in flow_edges:
        for _ in range(PARTICLES_PER_EDGE):
            parts.append((
                a, b,
                rnd.random(),                                  # 初相位
                SPEED_BASE * (0.45 + min(1.6, w * 0.16)),      # 强连接跑得快
            ))

    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 11)
    fbig = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 21)
    fsmall = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 13)

    base = Image.new("RGB", (W, H), (3, 6, 14))
    es_draw = sorted(es, key=lambda e: min(pts[e[0]][2], pts[e[1]][2]))
    order = sorted(range(len(pts)), key=lambda i: pts[i][2])

    frames = []
    for f in range(FRAMES):
        t = f / FRAMES

        skel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(skel)
        # 骨架：很暗，只交代结构
        for a, b, w in es_draw:
            x1, y1, z1 = pts[a]; x2, y2, z2 = pts[b]
            if abs(x1 - x2) + abs(y1 - y2) > 380:
                continue
            dt = ((z1 + z2) / 2 - zmin) / span
            sd.line([(x1, y1), (x2, y2)],
                    fill=(40, 70, 115, int(16 + 42 * dt)),
                    width=1)

        dot_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dd = ImageDraw.Draw(dot_layer)

        # 每个节点当前被粒子「照顾」到的亮度
        heat = [0.0] * len(pts)

        part_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pd = ImageDraw.Draw(part_layer)

        for a, b, ph, spd in parts:
            x1, y1, z1 = pts[a]; x2, y2, z2 = pts[b]
            if abs(x1 - x2) + abs(y1 - y2) > 380:
                continue
            # 位置沿着边走，走到头再从头开始
            u = (ph + t * spd) % 1.0
            dt = ((z1 + z2) / 2 - zmin) / span

            # 拖尾：往回画几个越来越淡的点
            for k in range(TRAIL_LEN):
                uu = (u - k * 0.035) % 1.0
                if uu > u:      # 绕回头的那一截不画，否则会横穿一条线
                    continue
                px = x1 + (x2 - x1) * uu
                py = y1 + (y2 - y1) * uu
                fade = [1.0, 0.55, 0.28][k]
                r = (4.2 if k == 0 else 2.6) * (0.7 + 0.5 * dt)
                a_ = int((230 if k == 0 else 125) * fade * (0.55 + 0.45 * dt))
                # 纯青：和深蓝的节点拉开，一眼能分出「哪个在动」
                pd.ellipse([px - r, py - r, px + r, py + r],
                           fill=(70, 255, 215, a_))

            # 粒子到哪儿，节点就热起来
            for node, uu in ((a, 0.0), (b, 1.0)):
                gap = min(abs(u - uu), 1 - abs(u - uu))
                heat[node] = max(heat[node], max(0.0, 1.0 - gap * 5.0))

        # 节点：静息蓝，被照顾到就偏青白
        for i in order:
            x, y, z = pts[i]
            dt = (z - zmin) / span
            g = heat[i]
            rad = 1.8 + 2.4 * (dt ** 1.5) + 5.0 * g
            if g > 0.05:
                r_ = int(90 + 90 * g)
                g_ = int(180 + 75 * g)
                b_ = int(210 + 40 * g)
                al = int(80 + 175 * g)
            else:
                r_, g_, b_ = 55, 105, 175
                al = int(26 + 58 * dt)
            dd.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(r_, g_, b_, al))
            if g > 0.6:
                r2 = rad * 0.38
                dd.ellipse([x - r2, y - r2, x + r2, y + r2], fill=(240, 255, 250, 240))

        glow_p = part_layer.filter(ImageFilter.GaussianBlur(5))
        glow_d = dot_layer.filter(ImageFilter.GaussianBlur(6))
        img = Image.alpha_composite(base.convert("RGBA"), skel)
        img = Image.alpha_composite(img, glow_d)
        img = Image.alpha_composite(img, dot_layer)
        img = Image.alpha_composite(img, glow_p)
        img = Image.alpha_composite(img, part_layer)

        txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(txt)
        for i in range(len(pts)):
            x, y, z = pts[i]
            dt = (z - zmin) / span
            g = heat[i]
            if g > 0.3:
                fill = (200, 255, 245, 240)
            else:
                fill = (95, 140, 185, int(45 + 68 * dt))
            td.text((x + 6, y - 7), names[i][0], font=font, fill=fill)
        img = Image.alpha_composite(img, txt)

        cap = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cd = ImageDraw.Draw(cap)
        cd.text((24, 18), "记忆皮层 · 信号流动", font=fbig, fill=(226, 245, 255, 240))
        cd.text((24, 46), "%d 概念 / %d 突触 / %d 条连线在流动"
                % (len(names), len(es), len(flow_edges)),
                font=fsmall, fill=(130, 180, 220, 220))
        cd.text((24, H - 24), "光点 = 正在传递的信号", font=fsmall,
                fill=(150, 230, 215, 220))
        img = Image.alpha_composite(img, cap)
        frames.append(img.convert("RGB"))

    q = [fr.quantize(colors=128, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
         for fr in frames]
    q[0].save(path, save_all=True, append_images=q[1:],
              duration=FRAME_MS, loop=0, optimize=True)
    return path


if __name__ == "__main__":
    names, edges, active = load()
    print("节点 %d / 边 %d" % (len(names), len(edges)))
    pos = layout(names, edges)
    print("布局完成")
    build(names, edges, pos, active, OUT)
    print("已生成:", OUT, os.path.getsize(OUT) // 1024, "KB")
