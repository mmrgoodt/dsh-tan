# 更新日志

## v0.3.2 — 2026-11-05

这一版只修 bug，不动结构。两个故障都是「改完看起来没反应」的那种，根因不同，但串在一起很像。

### ① 记忆注入与写入全程 401（影响最大）

`api.py` 从 v0.2 起校验 token，只给插件留了一条免 token 通道：

```python
# api.py:215
if (self.headers.get("X-Tan-Source") or "") == "dsh-plugin":
    return True
```

而插件的 `callApi` 只发了 `Content-Type`。于是：

```
/context → 401 → callApi 返回 null → 提示词里没有「我记得的事」
/turn    → 401 → 写入被拒       → 记忆库停在最后一次旧版本写入
```

两处都被 `try/catch` 吞掉，界面上完全安静。表现是「它不记得我」，用户根本无从判断是召回为空、还是请求根本没进来。

实测（同一台机器，同一条 query）：

| 请求 | 结果 |
|---|---|
| `POST /context` 不带头 | `401 {"ok":false,"error":"需要 token"}` |
| `POST /context` 带 `X-Tan-Source: dsh-plugin` | `200`，返回人格 + 记忆 |
| `POST /turn` 不带头 | `401` |
| `POST /turn` 带该头 | `200` |

**修法**（`plugin/lib/index.js`）：`callApi` 统一加 `X-Tan-Source: dsh-plugin`；另外遇到 401 打一次 `console.warn`，不再静默。

> 注意：v0.3.1 里「短问候召回为空」的修法（fallback 到最近 3 条）本身没错，但当时是用带 token 的直连脚本测的，测出来的数据是真的，插件链路却是断的。这一版才真正把链路接通。

### ② 面板「保存」按钮点了像没反应

`plugin/optional/client.js` 里三处叠加：

```js
// 修改前
const startEdit = (m) => setEditing({ id: m.id, text: m.summary || m.content || "" });
...
const saveEdit = async () => {
  ...
  setEditing(null);
  await act("/memory/edit", { id: editing.id, content: t }, "已保存修改");
};
```

1. 编辑框里放的是 `summary`（列表为了紧凑显示摘要），不是正文；
2. 保存只写 `content`，`summary` 不动，而列表优先显示 `summary` → **保存成功了，界面照旧**；
3. `setEditing(null)` 之后再用 `editing.id`，依赖闭包旧值，写法易碎。

**修法**：编辑正文（`m.content || m.summary`）；保存时一并刷新 `summary`（取新内容前 60 字），列表立刻反映修改；先把 `id` 取出来再清状态。

### ③ 装完不生效的坑（文档层面）

DSH 加载的是 profile 下的**实体副本** `~/.dsh/profiles/desktop/node_modules/dsh-tan/lib/`，不是 `plugin/` 源码目录。改完源码必须重装：

```bash
python install.py --panel
# 然后重启 DSH（托盘图标右键 → 退出 → 重新打开）
```

只刷新页面不够——client bundle 只在启动时加载一次。

### 升级方式

```bash
git pull
python install.py --panel
# 重启 DSH
```

### 验证清单

- `~/.dsh/tan/context.log` 出现新的 `q='...' -> N 条/M tok` 行（说明插件在调 `/context` 且通过鉴权）
- 面板「记忆」页点编辑，框里是完整正文而不是摘要
- 改完点保存，列表那一行立刻变化
- 新开一个会话说「我是谁」，回复里能带出记得的事

---

## v0.3.1

- fix: 短问候召回为空时给最近的记忆（看起来像「不记得」的根因）
- docs: 语义检索改用零依赖的实体共现方案
- docs: 加 ROADMAP —— 从「记得住」到「懂你」的四步路径

## v0.3.0

- 迁移到 `D:\dsh-tan`，包名统一为 `dsh-tan`，数据目录 `~/.dsh/tan/`
- 控制台六页（概览 / 记忆 / 人格 / 参数 / 未闭合 / 可视化）
- 备份单文件格式 `.tanbak`，支持导出导入
