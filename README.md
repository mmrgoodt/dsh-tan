# Tan · 记忆插件

> 给 AI 装一个会遗忘、会沉淀、有性格的记忆
>
> 全部本地运行 · 对话时零模型调用 · 每日成本约 0.05 元

---

## 它是什么

一个挂在 DSH（DeepSeek Harness）上的记忆插件。你正常聊天，它在旁边记：

- **记得住**：分层存储，重要的事不会被忘，琐碎的会自己沉降
- **忘得掉**：艾宾浩斯式衰减，用一次反而记得更牢，没用的自然淡出
- **有性格**：从对话里慢慢学你喜欢的说话方式，分四个阶段成长
- **不打扰**：主动关心有时段限制（8-9 / 12-13 / 19-21 点），深夜静默

技术上：SQLite + 纯规则提取，**不调任何模型**，不联网，数据全在本地。

---

## 快速开始

### 方式一：npm（推荐，有 Node 环境）

```bash
npm install dsh-tan
```

`postinstall` 会自动做两件事：

1. 下载便携版 Python 3.11（10.7 MB，华为云镜像，约 5 秒）
2. 把插件登记到 DSH 的 profile 里

然后重启 DSH（托盘图标右键 → 退出 → 重新打开），侧边栏出现「记」Tan。

### 方式二：压缩包（不需要 Node）

1. 下载 Release 里的 zip，解压到任意目录（路径最好不带中文）
2. 双击 `一键安装.bat`
3. 按提示重启 DSH

脚本会自动找内置 Python、启动服务、装进 DSH。

### 方式三：源码

```bash
git clone https://github.com/<你的名字>/dsh-tan.git
cd dsh-tan
python install.py --panel      # 装插件（含设置页）
python api.py                  # 手动起服务
```

需要自己装 Python 3.10+。

---

## 装好之后长什么样

**侧边栏**：多一个「记」Tan，点开是记忆面板（参数 / 记忆 / 未闭合 三个 tab）

**网页控制台**：双击 `打开控制台.bat`，或者访问 `http://127.0.0.1:18765/`

七个页面：概览 · 记忆 · 人格 · 参数 · 未闭合 · 备份 · 可视化

**3D 神经元图**：控制台里点「可视化」，或者直接开 `neuron3d.html`

**数据在哪**：`~/.dsh/tan/`

```
memory.db          所有记忆
token.txt          访问令牌（首次启动自动生成）
config.json        你自己改的参数
backup/            导入前的自动备份
```

换电脑只要搬 `~/.dsh/tan/` 这一个目录。

---

## 服务怎么管

**跟着 DSH 走**：DSH 开它就在，DSH 关它就关，不留后台进程。

DSH 运行期间服务崩了，插件 60 秒内自己拉回来——插件本身就是看门狗，不需要额外的守护进程。

要手动起服务：

```bash
python api.py                    # 前台
pythonw api.py                   # 无窗口
python api.py --port 18766       # 换端口
```

---

## 能做什么

| 能力 | 状态 |
|---|---|
| 分层记忆（L1 情景 / L2 语义 / L2- 反例 / L3 程序 / L4 身份 / L5 自我） | ✅ |
| 自动提取要点、打可信度（S1–S5）、归层、归 Thread | ✅ |
| 遗忘曲线（指数衰减 + 半衰期） | ✅ |
| 回忆强化（用一次半衰期 ×1.8）、否定降权（×0.5） | ✅ |
| 三出口（噪音归档 / 自然沉降 / 容量清理） | ✅ |
| 检索打分 + 实体扩散 + Token 预算注入（1500） | ✅ |
| 未闭合清单（承诺 / 半途而废 / 失败 / TODO / 已知问题） | ✅ |
| 开口时接住（每轮最多提 1 条，24h 冷却） | ✅ |
| 人格系统（底色 + 塑形 + 四阶段 + 成长日志） | ✅ |
| 主动消息（时段 + 每日 4 条上限 + 深夜静默） | ✅ |
| 进化护栏（黄金测试 20 题 + 快照回滚 + 参数建议） | ✅ |
| 单文件备份（`.tanbak`，魔数 + SHA256 校验） | ✅ |
| 数据库自动迁移（`PRAGMA user_version`） | ✅ |
| 107 个单元测试 | ✅ |
| 内置便携 Python（用户不用自己装） | ✅ |
| 访问令牌（挡住浏览器里其他网页偷读） | ✅ |

## 还没做

| 事 | 为什么 |
|---|---|
| 真正的语义检索 | 需要向量化（embedding）。试过同义词扩展，在中文上会稀释相关度，已默认关闭 |
| 记忆提取换成语义模型 | 现在是纯正则，对复杂表达会漏 |
| 多设备同步 | 现在只有单文件备份，手动搬 |

---

## 目录结构

```
dsh-tan/
├── api.py                    HTTP 服务（127.0.0.1:18765）
├── install.py                装进 DSH
├── watchdog.py               服务守护（可选，DSH 自己也会守）
├── proactive_notify.py       主动消息
├── cli.py                    命令行
├── build_npm.py              打 npm 包
│
├── hippocampus/              记忆引擎（13 个模块）
│   ├── config.py             参数
│   ├── db.py                 SQLite 封装
│   ├── extractor.py          从对话提取
│   ├── retriever.py          检索打分
│   ├── writer.py             写入流程
│   ├── forget.py             遗忘曲线
│   ├── persona.py            人格
│   ├── loops.py              未闭合事项
│   ├── engine.py             统一入口
│   ├── backup.py             单文件备份
│   ├── evolution.py          黄金测试
│   ├── proactive.py          主动消息决策
│   └── migrate.py            数据库迁移
│
├── plugin/                   DSH 插件
│   ├── package.json
│   ├── cordis.patch.yml
│   ├── lib/index.js          宿主（挂事件、拉服务）
│   ├── lib/client.js         面板
│   ├── scripts/
│   │   ├── fetch-python.cjs  下载内置 Python
│   │   └── register.cjs      注册进 DSH
│   └── vendor/python/        内置 Python（自动下载，不进仓库）
│
├── visual/
│   ├── console.html          网页控制台
│   ├── neuron.html           2D 神经元
│   └── neuron3d.html         3D 神经元
│
└── tests/                    107 个用例
```

---

## 备份

**导出**：面板或控制台里点「导出」，得到一个 `.tanbak` 文件

格式：`TANBAK01` 魔数 + 元信息长度 + SHA256 + JSON 元信息 + 原始 SQLite 字节

**导入**：选文件 → 预览 → 确认。导入前会自动把当前库备份到 `backup/before-import-<时间>.db`

---

## 访问令牌

除 `/health` 外所有接口都要令牌，三种带法：

```
Authorization: Bearer <token>
?token=<token>
Cookie: tan_token=<token>
```

令牌在 `~/.dsh/tan/token.txt`，首次启动自动生成。

**它挡住的是**：浏览器里其他网页偷偷调 `localhost:18765` 读你的记忆
**它挡不住**：本机上有文件读权限的程序（能直接读 token.txt）

---

## 开发

```bash
python -m pytest tests/ -q          # 跑测试
python build_npm.py                 # 打 npm 包到 dist/
cd dist/dsh-tan && npm pack         # 生成 .tgz
```

---

## 许可

MIT
