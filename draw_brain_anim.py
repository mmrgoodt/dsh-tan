# -*- coding: utf-8 -*-
"""3D 神经元动图：全标签 + 活跃节点脉动

用户要的三件事：
  1. 每个节点都显示名字
  2. 没在跑的神经元 → 蓝色
  3. 在跑的神经元 → 黄色，而且是动的

PNG 没法动，所以出 GIF。
「在跑」的判据不是随机挑，是 working_memory 里 30 分钟内更新过的概念 ——
那是系统此刻真正亮着的焦点。
"""
import math
import os
import sqlite3
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 960
FOCUS_COLOR = (255, 214, 90)      # 活跃：黄
IDLE_COLOR = (105, 170, 235)      # 静止：蓝
FRAMES = 18
FRAME_MS = 90

DB = os.path.expanduser(r"~/.dsh/tan/memory.db")
OUT = r"D:\dsh-tan\visual\neuron3d.gif"
ACTIVE_WINDOW = 8 * 60         # 8 分钟内活跃过才算「在跑」（30 分钟太宽，黄蓝各一半看不出对比）

STOP_EDGE = set("还用的了是在有和与就都也这那你我他它们把被从对向以")
NOISE = set("""还要 用什 么办 什么 那个 这个 的话 了的 是的 就是 不是 没有 可以
需要 应该 可能 已经 现在 时候 东西 事情 问题 情况 方面 地方 我们 你们
他们 自己 这样 那样 这些 那些 一些 一个 一样 因为 所以 但是 如果 虽然
而且 或者 不过 然后 开始 结束 之后 之前 上面 下面 里面 外面 出来 起来
只有 一句 任何 条次 会自 两个 一步 这次 那次 每次 一次 次数 一直
关键 直接 自动 系统 文件 项目 内容 状态 说明 方式 方法 部分 全部 本身
""".split())


def _ok(name):
    if not name or not (2 <= len(name) <= 10):
        return False
    if name.lower() in NOISE or name.isdigit():
        return False
    if len(name) == 2 and (name[0] in STOP_EDGE or name[1] in STOP_EDGE):
        return False
    return True


def load():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    nodes = [n for n in con.execute(
        "SELECT name, freq FROM concepts ORDER BY freq DESC LIMIT 900"
    ).fetchall() if _ok(n["name"])][:130]
    keep = {n["name"] for n in nodes}
    edges = [(r["a"], r["b"], r["weight"])
             for r in con.execute("SELECT a, b, weight FROM synapses ORDER BY weight DESC")
             if r["a"] in keep and r["b"] in keep]

    # 谁在「跑」：working_memory 里 30 分钟内更新过的
    cutoff = time.time() - ACTIVE_WINDOW
    active = {}
    try:
        for r in con.execute(
            "SELECT concept, strength FROM working_memory WHERE updated >= ?", (cutoff,)
        ):
            active[r["concept"].lower()] = r["strength"]
    except Exception:
        pass
    con.close()

    # 只留最大连通分量
    adj = {}
    for a, b, w in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, best = set(), set()
    for start in adj:
        if start in seen:
            continue
        comp, stack = set(), [start]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack.extend(adj.get(x, ()))
        seen |= comp
        if len(comp) > len(best):
            best = comp
    nodes = [n for n in nodes if n["name"] in best]
    edges = [e for e in edges if e[0] in best and e[1] in best]
    return [(n["name"], n["freq"]) for n in nodes], edges, active


def layout(names, edges, iters=340):
    """3D 力导向（和静图是同一套参数，保证长一个样）"""
    import random
    rnd = random.Random(42)
    n = len(names)
    idx = {names[i][0]: i for i in range(n)}
    pos = []
    for i in range(n):
        phi = math.acos(1 - 2 * (i + 0.5) / n)
        theta = math.pi * (1 + 5 ** 0.5) * i
        r = 300.0
        pos.append([r * math.sin(phi) * math.cos(theta),
                    r * math.sin(phi) * math.sin(theta),
                    r * math.cos(phi)])
    E = [(idx[a], idx[b], w) for a, b, w in edges if a in idx and b in idx]
    deg = [0.0] * n
    for a, b, w in E:
        deg[a] += w
        deg[b] += w

    for it in range(iters):
        t = 1.0 - it / iters
        disp = [[0.0, 0.0, 0.0] for _ in range(n)]
        for i in range(n):
            xi, yi, zi = pos[i]
            for j in range(i + 1, n):
                dx, dy, dz = xi - pos[j][0], yi - pos[j][1], zi - pos[j][2]
                d2 = dx * dx + dy * dy + dz * dz + 0.01
                if d2 > 250000:
                    continue
                f = 2600.0 / d2
                d = math.sqrt(d2)
                ux, uy, uz = dx / d, dy / d, dz / d
                disp[i][0] += ux * f; disp[i][1] += uy * f; disp[i][2] += uz * f
                disp[j][0] -= ux * f; disp[j][1] -= uy * f; disp[j][2] -= uz * f
        for a, b, w in E:
            dx, dy, dz = pos[b][0] - pos[a][0], pos[b][1] - pos[a][1], pos[b][2] - pos[a][2]
            d = math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01
            f = min(4.0, d * 0.030) * min(2.5, w * 0.20)
            ux, uy, uz = dx / d, dy / d, dz / d
            disp[a][0] += ux * f; disp[a][1] += uy * f; disp[a][2] += uz * f
            disp[b][0] -= ux * f; disp[b][1] -= uy * f; disp[b][2] -= uz * f
        for i in range(n):
            for k in range(3):
                disp[i][k] -= pos[i][k] * 0.0060 * (1.0 + deg[i] * 0.03)
        step = 6.0 * t + 0.4
        for i in range(n):
            for k in range(3):
                pos[i][k] += max(-step, min(step, disp[i][k]))
    return pos


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
        out.append((w / 2 + x1 * s * 1.85, h / 2 + y1 * s * 1.85, z2))
    return out


def build_frames(names, edges, pos, active):
    w, h = W, H
    pts = project(pos, w, h)
    zs = [p[2] for p in pts]
    zmin, zmax = min(zs), max(zs)
    span = max(1e-6, zmax - zmin)
    idx = {names[i][0]: i for i in range(len(names))}

    degree = {}
    for a, b, ww in edges:
        if a in idx and b in idx:
            degree[a] = degree.get(a, 0.0) + ww
            degree[b] = degree.get(b, 0.0) + ww

    activ = {idx[k] for k in active if k in idx}

    font = None
    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            font = ImageFont.truetype(fp, 13)
            break
        except Exception:
            continue
    fbig = fsmall = None
    try:
        fbig = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 26)
        fsmall = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 15)
    except Exception:
        fbig = fsmall = font

    base = Image.new("RGB", (w, h), (4, 7, 16))
    es = [(idx[a], idx[b], ww) for a, b, ww in edges if a in idx and b in idx]
    es.sort(key=lambda e: min(pts[e[0]][2], pts[e[1]][2]))
    order = sorted(range(len(pts)), key=lambda i: pts[i][2])

    frames = []
    for f in range(FRAMES):
        ph = 2 * math.pi * f / FRAMES
        line_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dot_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dl = ImageDraw.Draw(line_layer)
        dd = ImageDraw.Draw(dot_layer)

        for a, b, ww in es:
            x1, y1, z1 = pts[a]; x2, y2, z2 = pts[b]
            if abs(x1 - x2) + abs(y1 - y2) > 380:
                continue
            t = ((z1 + z2) / 2 - zmin) / span
            # 活跃节点连的线也跟着亮
            hot = 1.0 if (a in activ or b in activ) else 0.0
            al = int(26 + 120 * (t ** 1.5) + 55 * hot * (0.5 + 0.5 * math.sin(ph)))
            if a in activ or b in activ:
                col = (150, 130, 60)
            else:
                col = (int(35 + 70 * t), int(110 + 90 * t), int(190 + 60 * t))
            dl.line([(x1, y1), (x2, y2)], fill=col + (min(230, al),),
                    width=max(1, int(min(3, 1 + ww * 0.18))))

        for i in order:
            x, y, z = pts[i]
            t = (z - zmin) / span
            is_hot = i in activ
            if is_hot:
                # 脉动：半径和亮度一起呼吸
                pulse = 0.5 + 0.5 * math.sin(ph)
                rad = 4.6 + 5.0 * pulse + 2.0 * t
                col = FOCUS_COLOR
                a = int(180 + 70 * pulse)
            else:
                rad = 1.5 + 2.4 * (t ** 1.6)
                col = IDLE_COLOR
                a = int(80 + 120 * (t ** 1.2))
            dd.ellipse([x - rad, y - rad, x + rad, y + rad], fill=col + (a,))
            if is_hot:
                r2 = rad * 0.42
                dd.ellipse([x - r2, y - r2, x + r2, y + r2], fill=(255, 255, 235, 240))

        glow_d = dot_layer.filter(ImageFilter.GaussianBlur(8))
        glow_l = line_layer.filter(ImageFilter.GaussianBlur(3))
        img = Image.alpha_composite(base.convert("RGBA"), glow_l)
        img = Image.alpha_composite(img, glow_d)
        img = Image.alpha_composite(img, line_layer)
        img = Image.alpha_composite(img, dot_layer)

        # 全部节点的名字：先画底衬，再写字
        if font:
            txt = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            td = ImageDraw.Draw(txt)
            for i in range(len(pts)):
                x, y, z = pts[i]
                name = names[i][0]
                t = (z - zmin) / span
                is_hot = i in activ
                # 名字放右侧，按深度调透明度
                al = int(120 + 120 * t)
                if is_hot:
                    fill = (255, 226, 130, min(255, al + 40))
                else:
                    fill = (165, 205, 240, al)
                td.text((x + 6, y - 7), name, font=font, fill=fill)
            img = Image.alpha_composite(img, txt)

        if fbig:
            cap = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            cd = ImageDraw.Draw(cap)
            cd.text((36, 30), "记忆皮层 · 概念与突触", font=fbig, fill=(230, 242, 255, 240))
            n_hot = len(activ)
            cd.text((36, 66),
                    "%d 概念 / %d 突触   ·   黄色 %d 个正在运行，蓝色 %d 个静止"
                    % (len(names), len(es), n_hot, len(names) - n_hot),
                    font=fsmall, fill=(140, 185, 225, 225))
            img = Image.alpha_composite(img, cap)

        frames.append(img.convert("RGB"))

    return frames


if __name__ == "__main__":
    names, edges, active = load()
    print("节点 %d / 边 %d / 活跃 %d" % (len(names), len(edges), len(active)))
    pos = layout(names, edges)
    print("布局完成")
    fs = build_frames(names, edges, pos, active)
    print("渲染 %d 帧" % len(fs))
    fs[0].save(OUT, save_all=True, append_images=fs[1:], duration=FRAME_MS,
               loop=0, optimize=True)
    print("已生成:", OUT, os.path.getsize(OUT) // 1024, "KB")
