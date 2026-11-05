# -*- coding: utf-8 -*-
"""3D 神经元网络图：概念 + 突触连线，透视投影渲染成图

数据是真的：2137 个概念、11297 条突触，全从 memory.db 里来。
取连接最密的 260 个概念做主图 —— 全画出来会糊成一片毛球。

渲染不靠浏览器，纯 Pillow：
  1. 3D 力导向布局（斥力 + 沿边收缩）
  2. 透视投影（近大远小）
  3. 按深度着色：远的暗蓝、近的青白
  4. 三层叠加做辉光
"""
import math
import os
import random
import sqlite3
import sys

from PIL import Image, ImageDraw, ImageFilter

W, H = 1600, 1200
FOCUS = 900.0            # 透视焦距
NODE_LIMIT = 150
ITER = 420

DB = os.path.expanduser(r"~/.dsh/tan/memory.db")
OUT = r"D:\dsh-tan\visual\neuron3d.png"


# 2-gram 切出来的虚词组合。图里如果显示「只有」「一句」「任何」当枢纽，
# 那图就没意义了 —— 得先把碎片挡掉，让「迅雷」「Clash」浮上来。
STOP_EDGE = set("还用的了是在有和与就都也这那你我他它们把被从对向以")
NOISE = set("""还要 用什 么办 什么 那个 这个 的话 了的 是的 就是 不是 没有 可以
需要 应该 可能 已经 现在 时候 东西 事情 问题 情况 方面 地方 我们 你们
他们 自己 这样 那样 这些 那些 一些 一个 一样 因为 所以 但是 如果 虽然
而且 或者 不过 然后 开始 结束 之后 之前 上面 下面 里面 外面 出来 起来
只有 一句 任何 条次 会自 两个 一步 这次 那次 每次 一次 次数 一句 一直
关键 直接 自动 系统 文件 项目 内容 状态 说明 方式 方法 部分 全部 本身
""".split())


def _ok(name):
    if not name or not (2 <= len(name) <= 12):
        return False
    if name.lower() in NOISE:
        return False
    if name.isdigit():
        return False
    if len(name) == 2 and (name[0] in STOP_EDGE or name[1] in STOP_EDGE):
        return False
    return True


def load():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # 节点：出现次数最多、且像真词的概念
    nodes = [n for n in con.execute(
        "SELECT name, freq FROM concepts ORDER BY freq DESC LIMIT ?", (NODE_LIMIT * 6,)
    ).fetchall() if _ok(n["name"])][:NODE_LIMIT]
    keep = {n["name"] for n in nodes}
    # 边：两端都在节点集里的突触
    edges = []
    for r in con.execute("SELECT a, b, weight FROM synapses ORDER BY weight DESC"):
        if r["a"] in keep and r["b"] in keep:
            edges.append((r["a"], r["b"], r["weight"]))
    con.close()

    # 只留最大连通分量。孤立点把画面撑散了，看起来像星空不像网络。
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
    names = [n for n in nodes if n["name"] in best]
    edges = [e for e in edges if e[0] in best and e[1] in best]
    return [(n["name"], n["freq"]) for n in names], edges


def layout(names, edges, iters=ITER):
    """3D 力导向"""
    rnd = random.Random(42)
    n = len(names)
    idx = {names[i][0]: i for i in range(n)}

    # 球面初始化，避免一开始就叠在一起
    pos = []
    for i in range(n):
        # 斐波那契球
        phi = math.acos(1 - 2 * (i + 0.5) / n)
        theta = math.pi * (1 + 5 ** 0.5) * i
        r = 320.0
        pos.append([
            r * math.sin(phi) * math.cos(theta),
            r * math.sin(phi) * math.sin(theta),
            r * math.cos(phi),
        ])

    E = [(idx[a], idx[b], w) for a, b, w in edges if a in idx and b in idx]
    deg = [0.0] * n
    for a, b, w in E:
        deg[a] += w
        deg[b] += w

    for it in range(iters):
        t = 1.0 - it / iters
        disp = [[0.0, 0.0, 0.0] for _ in range(n)]

        # 斥力：只算近邻，远的不重要
        for i in range(n):
            xi, yi, zi = pos[i]
            for j in range(i + 1, n):
                dx = xi - pos[j][0]
                dy = yi - pos[j][1]
                dz = zi - pos[j][2]
                d2 = dx * dx + dy * dy + dz * dz + 0.01
                if d2 > 250000:      # 500 之外不管
                    continue
                f = 2600.0 / d2
                d = math.sqrt(d2)
                ux, uy, uz = dx / d, dy / d, dz / d
                disp[i][0] += ux * f
                disp[i][1] += uy * f
                disp[i][2] += uz * f
                disp[j][0] -= ux * f
                disp[j][1] -= uy * f
                disp[j][2] -= uz * f

        # 引力：沿边收缩
        for a, b, w in E:
            dx = pos[b][0] - pos[a][0]
            dy = pos[b][1] - pos[a][1]
            dz = pos[b][2] - pos[a][2]
            d = math.sqrt(dx * dx + dy * dy + dz * dz) + 0.01
            f = min(4.0, d * 0.030) * min(2.5, w * 0.20)
            ux, uy, uz = dx / d, dy / d, dz / d
            disp[a][0] += ux * f
            disp[a][1] += uy * f
            disp[a][2] += uz * f
            disp[b][0] -= ux * f
            disp[b][1] -= uy * f
            disp[b][2] -= uz * f

        # 向心：别飘走
        for i in range(n):
            for k in range(3):
                disp[i][k] -= pos[i][k] * 0.0060 * (1.0 + deg[i] * 0.03)

        step = 6.0 * t + 0.4
        for i in range(n):
            for k in range(3):
                pos[i][k] += max(-step, min(step, disp[i][k]))

    return pos


def project(pos):
    """透视投影 + 深度"""
    out = []
    # 整体定个好看的视角
    ay = math.radians(24)
    ax = math.radians(-16)
    cy, sy = math.cos(ay), math.sin(ay)
    cx, sx = math.cos(ax), math.sin(ax)
    for x, y, z in pos:
        # 绕 Y 转
        x1 = x * cy - z * sy
        z1 = x * sy + z * cy
        # 绕 X 转
        y1 = y * cx - z1 * sx
        z2 = y * sx + z1 * cx
        d = FOCUS + z2
        if d < 60:
            d = 60
        s = FOCUS / d
        out.append((W / 2 + x1 * s * 1.95, H / 2 + y1 * s * 1.95, z2))
    return out


def render(names, edges, pos, path):
    pts = project(pos)
    zs = [p[2] for p in pts]
    zmin, zmax = min(zs), max(zs)
    span = max(1e-6, zmax - zmin)

    idx = {names[i][0]: i for i in range(len(names))}

    base = Image.new("RGB", (W, H), (4, 7, 16))
    line_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dot_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dl = ImageDraw.Draw(line_layer)
    dd = ImageDraw.Draw(dot_layer)

    def depth_alpha(z):
        t = (z - zmin) / span        # 0 远 1 近
        return 0.10 + 0.90 * (t ** 1.5)

    # 画线：远的先画
    es = []
    for a, b, w in edges:
        if a in idx and b in idx:
            es.append((idx[a], idx[b], w))
    es.sort(key=lambda e: min(pts[e[0]][2], pts[e[1]][2]))

    for a, b, w in es:
        x1, y1, z1 = pts[a]
        x2, y2, z2 = pts[b]
        # 投到屏幕上太长的不画：那是弱关系被硬拉出来的丝
        if abs(x1 - x2) + abs(y1 - y2) > 420:
            continue
        t = ((z1 + z2) / 2 - zmin) / span
        al = int(28 + 150 * (t ** 1.5))
        wd = max(1, int(min(4, 1 + w * 0.22)))
        # 远蓝近青
        r = int(40 + 90 * t)
        g = int(120 + 120 * t)
        bl = int(200 + 55 * t)
        dl.line([(x1, y1), (x2, y2)], fill=(r, g, bl, al), width=wd)

    # 每个节点的连接度，用来决定颜色和大小
    degree = {}
    for a, b, w in edges:
        if a in idx and b in idx:
            degree[a] = degree.get(a, 0.0) + w
            degree[b] = degree.get(b, 0.0) + w
    dmax = max(degree.values()) if degree else 1.0

    # 画点：远的先画
    order = sorted(range(len(pts)), key=lambda i: pts[i][2])
    for i in order:
        x, y, z = pts[i]
        t = (z - zmin) / span
        name = names[i][0]
        d = degree.get(name, 0.0) / dmax        # 0~1，越大越是枢纽

        rad = 1.6 + 3.2 * (t ** 1.6) + 5.0 * (d ** 1.4)
        a = int(90 + 165 * (t ** 1.2))

        if d > 0.55:          # 枢纽：暖金
            col = (255, int(205 - 40 * (1 - d)), int(120 + 60 * d))
        elif d > 0.25:        # 中间：青
            col = (150, int(220 + 30 * t), 255)
        else:                 # 叶节点：暗蓝
            col = (110, 170, int(220 + 30 * t))
        dd.ellipse([x - rad, y - rad, x + rad, y + rad], fill=col + (a,))
        if d > 0.55:
            dd.ellipse([x - rad * 0.40, y - rad * 0.40, x + rad * 0.40, y + rad * 0.40],
                       fill=(255, 255, 245, 235))

    # 辉光：模糊后叠加
    glow = dot_layer.filter(ImageFilter.GaussianBlur(9))
    glow2 = line_layer.filter(ImageFilter.GaussianBlur(4))

    img = base.copy()
    img = Image.alpha_composite(img.convert("RGBA"), glow2)
    img = Image.alpha_composite(img, glow)
    img = Image.alpha_composite(img, line_layer)
    img = Image.alpha_composite(img, dot_layer)

    # 给连接度最高的几个概念标名字
    from PIL import ImageFont
    font = None
    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
               r"C:\Windows\Fonts\simhei.ttf"):
        try:
            font = ImageFont.truetype(fp, 20)
            break
        except Exception:
            continue

    if font:
        txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(txt)
        top = sorted(degree.items(), key=lambda kv: -kv[1])[:16]
        used = []          # 已经占用的标签位置，防重叠
        for name, _dv in top:
            if name not in idx:
                continue
            x, y, z = pts[idx[name]]
            t = (z - zmin) / span
            lx, ly = x + 12, y - 10
            # 太挤就不画：标签叠在一起比不画还难看
            if any(abs(lx - ux) < 78 and abs(ly - uy) < 24 for ux, uy in used):
                continue
            used.append((lx, ly))
            td.text((lx, ly), name, font=font,
                    fill=(255, 232, 180, int(150 + 90 * t)))
            if len(used) >= 11:
                break
        img = Image.alpha_composite(img, txt)

    # 标题
    from PIL import ImageFont as _IF
    try:
        f2 = _IF.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 30)
        f3 = _IF.truetype(r"C:\Windows\Fonts\msyh.ttc", 18)
    except Exception:
        f2 = f3 = font
    if f2:
        cap = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cd = ImageDraw.Draw(cap)
        cd.text((52, 44), "记忆皮层 · 概念与突触", font=f2, fill=(230, 242, 255, 240))
        cd.text((52, 86), "%d 个概念  /  %d 条突触  /  金=枢纽  青=中间  蓝=叶"
                % (len(names), len(es)), font=f3, fill=(130, 175, 215, 220))
        img = Image.alpha_composite(img, cap)

    img.convert("RGB").save(path, quality=95)
    return path


if __name__ == "__main__":
    names, edges = load()
    print("节点 %d / 边 %d" % (len(names), len(edges)))
    pos = layout(names, edges)
    print("布局完成")
    render(names, edges, pos, OUT)
    print("已生成:", OUT, os.path.getsize(OUT), "bytes")
