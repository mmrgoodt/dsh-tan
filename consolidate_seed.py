# -*- coding: utf-8 -*-
"""巩固：把零散经历压成结论

做法（跟大脑一样）：不是把同类的话拼起来，是**提取共性写成一条可用的知识**。

这些结论是我根据已经掌握的对话内容手写的，不是正则生成的。
写完之后，它们会成为检索时的锚点——以后问相关问题，
先命中结论，再顺着图谱找到当初的细节。

每条都是「现在该知道什么」，不是「当时发生了什么」。
"""
import json
import urllib.request

API = "http://127.0.0.1:18765/mem/save"

# 注意：这个文件是**模板**，里的名字、路径、公司都换成了占位符。
# 真数据只在他本机的 memory.db 里，不进仓库 —— 别人 clone 下来跑这个脚本，
# 写进去的是占位符，不是他的真实记忆。
#
# kind: fact 事实 / preference 偏好 / lesson 教训 / method 方法
CONSOLIDATED = [
    ("这台机器的开发环境：Windows 11，用户 admin。Node v24.22.1 装在 "
     "C:\\Program Files\\nodejs（不在默认 PATH 里，命令行直接敲 node 会找不到，要用完整路径）。"
     "Python 3.15，Git 全局身份 <github-user> / <email>。全局 npm 包在 AppData\\Roaming\\npm。",
     "fact", "环境"),

    ("本机网络走 Clash Verge 的 TUN 模式：虚拟网卡叫 Meta，DNS 走 198.18.0.2（fake-ip 段，"
     "会让 host 进程的域名解析失败，得走 127.0.0.1）。退出代理前必须先关 TUN，"
     "否则 Meta 网卡会继续占着默认路由，表现为「没网」。桌面有急救脚本可一键恢复。",
     "fact", "Clash 网络"),

    ("他对我的要求：回答短、先给结论、别堆大段、语气轻松有温度，数字和结论必须准。"
     "不接受「作为AI」那套，也不要公事公办的腔调。他更在意「你到底做成了没有」，"
     "而不是「你努力了」。",
     "preference", "说话方式"),

    ("他做两套经营主体：<公司A>和<个体户B>，<业务名>小程序是主要业务。"
     "账号密码总清单在 D:\\<项目目录>\\<账号目录>\\<密码文件>，"
     "这个文件必须保持不入 git、仓库必须私有。",
     "fact", "业务"),

    ("dsh-tan 这个记忆项目的核心教训（花了整整一晚才找到）："
     "① DSH 加载的是 node_modules 里的副本，改 D:\\dsh-tan\\plugin 后必须同步，否则改动全部空转；"
     "② system-prompt/assemble 在本轮用户消息入库**之前**触发，取检索词要靠上一轮兜底，"
     "否则每轮都是空查询；"
     "③ recall_count 不回写的话，强化和遗忘两个机制全是空转。",
     "lesson", "dsh-tan"),

    ("检索该怎么做才像大脑：用 focus（命中词占这条记忆全部权重的比例）排序，"
     "而不是「命中没命中」。一条 400 字的汇报顺手提了一次关键词，focus 只有 0.004；"
     "一条专门讲它的，focus 0.05。差 10 倍，噪音自然沉底。",
     "method", "检索"),

    ("注入要有总量上限。预算 1500 token 时实际只用 512，却塞了 10 条，其中 6 条是噪音。"
     "砍到 6 条后是 200-330 token，正确答案一条没少。宁可少说，别拿废话占位置。",
     "method", "注入"),

    ("改 DSH 的插件或配置后要重启才生效，重启时有个坑：Electron 把子进程都放在同一个 "
     "Job Object 里，从 shell 里启动的重启脚本会跟着一起被杀。要用 Windows 计划任务 "
     "（schtasks）来跑，它由系统服务创建，不在那棵进程树里。",
     "method", "DSH 运维"),
]


def save(text, kind, about):
    body = json.dumps({"text": text, "kind": kind, "about": about}).encode("utf-8")
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json", "X-Tan-Source": "dsh-plugin"},
    )
    return json.load(urllib.request.urlopen(req, timeout=10))


if __name__ == "__main__":
    for text, kind, about in CONSOLIDATED:
        r = save(text, kind, about)
        print("%-10s %-8s %s" % (r.get("action"), kind, text[:44]))
