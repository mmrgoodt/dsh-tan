window.__ModuleLoader__.load({ id: "dsh-tan", factory: (require) => {
var module = { exports: {} }; var exports = module.exports;

let react = require("react");
const h = react.createElement;

/* ============================================================
   Tan 设置页
   - 注册到 plugins.bundle.config（插件详情页）
   - 注册到 main + sidebar.panellist（左侧边栏「记忆」）
   inject 只能是服务短名，见文件底部 const inject = ["slots"]
   ============================================================ */

const API = "http://127.0.0.1:18765";
const KEY = "dsh-tan";

const LAYER = {
	L4: { n: "身份", c: "#f5c850" },
	L5: { n: "自我", c: "#f5c850" },
	L3: { n: "方法", c: "#48c774" },
	L2: { n: "事实", c: "#58a6ff" },
	"L2-": { n: "教训", c: "#f85149" },
	L1: { n: "情景", c: "#d29922" },
};
const KIND = { promise: "答应", paused: "搁置", failed: "没成", todo: "待办", known_issue: "已知" };

/* ============================================================
   样式（注入一次）
   ============================================================ */
const CSS = `
.hip-root{
  height:100%;min-height:0;overflow-y:auto;overflow-x:hidden;
  padding:20px 26px 64px;box-sizing:border-box;
  font:13px/1.6 "Segoe UI","Microsoft YaHei UI",system-ui,sans-serif;
  color:#e6edf3;scrollbar-width:thin;scrollbar-color:rgba(255,255,255,.16) transparent;
  overscroll-behavior:contain;
}
.hip-root::-webkit-scrollbar{width:10px}
.hip-root::-webkit-scrollbar-track{background:transparent}
.hip-root::-webkit-scrollbar-thumb{background:rgba(255,255,255,.13);border-radius:6px;border:3px solid transparent;background-clip:content-box}
.hip-root::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.26);background-clip:content-box}

/* ---- 顶部 ---- */
.hip-head{display:flex;align-items:center;gap:12px;margin-bottom:22px;padding-bottom:18px;border-bottom:1px solid rgba(255,255,255,.055)}
.hip-logo{
  width:38px;height:38px;border-radius:11px;flex:0 0 auto;
  background:linear-gradient(140deg,#58a6ff,#bc8cff);
  display:flex;align-items:center;justify-content:center;
  font-size:18px;font-weight:800;color:#fff;
  box-shadow:0 6px 20px rgba(88,166,255,.25);
}
.hip-title{font-size:17px;font-weight:700;letter-spacing:.3px}
.hip-sub{font-size:11px;opacity:.45;margin-top:2px}

/* ---- 统计卡 ---- */
.hip-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:11px;margin-bottom:22px}
.hip-stat{
  position:relative;overflow:hidden;
  padding:14px 16px;border-radius:13px;
  background:rgba(255,255,255,.028);
  border:1px solid rgba(255,255,255,.075);
  transition:border-color .18s,transform .18s,background .18s;
}
.hip-stat:hover{border-color:rgba(255,255,255,.16);background:rgba(255,255,255,.045);transform:translateY(-1px)}
.hip-stat::after{
  content:"";position:absolute;left:0;top:0;bottom:0;width:3px;
  background:var(--accent,#58a6ff);opacity:.75;
}
.hip-stat-k{font-size:10.5px;letter-spacing:.4px;opacity:.5;margin-bottom:7px;text-transform:uppercase}
.hip-stat-v{font-size:28px;font-weight:800;line-height:1;letter-spacing:-1px}
.hip-stat-s{font-size:10.5px;opacity:.38;margin-top:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ---- 标签页 ---- */
.hip-tabs{display:inline-flex;padding:4px;gap:2px;border-radius:11px;
  background:rgba(0,0,0,.28);border:1px solid rgba(255,255,255,.06);margin-bottom:20px}
.hip-tab{
  padding:7px 16px;border-radius:8px;font-size:12px;font-weight:600;
  color:rgba(230,237,243,.55);cursor:pointer;border:0;background:transparent;
  transition:.16s;font-family:inherit;white-space:nowrap;
}
.hip-tab:hover{color:#e6edf3;background:rgba(255,255,255,.05)}
.hip-tab.on{color:#0d1117;background:#e6edf3}
.hip-tab .n{opacity:.55;font-weight:500;margin-left:5px;font-size:11px}
.hip-tab.on .n{opacity:.6}

/* ---- 分组卡 ---- */
.hip-group{
  border-radius:14px;padding:18px 20px 8px;margin-bottom:14px;
  background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.012));
  border:1px solid rgba(255,255,255,.085);
  box-shadow:0 2px 14px rgba(0,0,0,.16);
}
.hip-group-h{display:flex;align-items:center;gap:9px;margin-bottom:16px;padding-bottom:11px;border-bottom:1px solid rgba(255,255,255,.055)}
.hip-group-h .ic{font-size:14px;opacity:.85}
.hip-group-h .t{font-size:12.5px;font-weight:700;letter-spacing:.3px}
.hip-group-h .line{flex:1;height:1px;background:linear-gradient(90deg,rgba(255,255,255,.09),transparent)}

/* ---- 单个参数 ---- */
.hip-field{padding:0 0 16px}
.hip-lab{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-bottom:9px}
.hip-lab .name{font-size:12.5px;opacity:.82}
.hip-lab .val{
  font-size:13.5px;font-weight:700;color:#79c0ff;font-variant-numeric:tabular-nums;
  letter-spacing:-.1px;padding:2.5px 10px;border-radius:7px;
  background:rgba(88,166,255,.1);border:1px solid rgba(88,166,255,.22);
  white-space:nowrap;
}
.hip-lab .val .u{font-size:10px;font-weight:600;opacity:.55;margin-left:3px}
.hip-hint{font-size:10.5px;opacity:.36;margin-top:7px;line-height:1.5}

/* ---- 滑块 ---- */
.hip-range{
  -webkit-appearance:none;appearance:none;width:100%;height:22px;
  background:transparent;cursor:pointer;display:block;outline:none;
}
.hip-range::-webkit-slider-runnable-track{
  height:5px;border-radius:3px;
  background:linear-gradient(90deg,#1f6feb 0%,#58a6ff var(--p,0%),rgba(255,255,255,.07) var(--p,0%),rgba(255,255,255,.07) 100%);
  box-shadow:inset 0 1px 2px rgba(0,0,0,.35);
}
.hip-range::-webkit-slider-thumb{
  -webkit-appearance:none;appearance:none;
  width:13px;height:13px;border-radius:50%;margin-top:-4.5px;
  background:#fff;border:2.5px solid #58a6ff;
  box-shadow:0 1px 4px rgba(0,0,0,.4);
  transition:box-shadow .16s,transform .16s;
}
.hip-range:hover::-webkit-slider-thumb{box-shadow:0 0 0 7px rgba(88,166,255,.14)}
.hip-range:active::-webkit-slider-thumb{transform:scale(1.12);box-shadow:0 0 0 9px rgba(88,166,255,.18)}
.hip-range::-moz-range-track{height:4px;border-radius:2px;background:rgba(255,255,255,.085)}
.hip-range::-moz-range-progress{height:4px;border-radius:2px;background:linear-gradient(90deg,#58a6ff,#7d9dff)}
.hip-range::-moz-range-thumb{width:13px;height:13px;border-radius:50%;background:#fff;border:2.5px solid #58a6ff}

/* ---- 按钮 ---- */
.hip-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:18px;padding-top:16px;border-top:1px solid rgba(255,255,255,.07)}
.hip-btn{
  padding:8px 17px;border-radius:9px;font-size:12.5px;font-weight:600;
  cursor:pointer;font-family:inherit;transition:.16s;
  background:rgba(255,255,255,.055);color:#e6edf3;
  border:1px solid rgba(255,255,255,.1);
}
.hip-btn:hover{background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.2);transform:translateY(-1px)}
.hip-btn:active{transform:none}
.hip-btn.pri{
  background:linear-gradient(135deg,#1f6feb,#388bfd);border-color:#2f81f7;color:#fff;
  box-shadow:0 4px 14px rgba(31,111,235,.3);
}
.hip-btn.pri:hover{background:linear-gradient(135deg,#2f81f7,#4d9dff);box-shadow:0 6px 18px rgba(31,111,235,.4)}

/* ---- 列表项 ---- */
.hip-row{
  display:flex;align-items:flex-start;gap:11px;
  padding:11px 0;border-bottom:1px solid rgba(255,255,255,.05);
}
.hip-row:last-child{border-bottom:0}
.hip-row .bar{width:3px;border-radius:2px;align-self:stretch;min-height:32px;flex:0 0 3px;opacity:.85}
.hip-row .body{flex:1;min-width:0}
.hip-row .txt{font-size:12.5px;line-height:1.55;word-break:break-word}
.hip-row .meta{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:5px;font-size:10.5px;opacity:.42}
.hip-tag{
  font-size:9.5px;padding:1.5px 7px;border-radius:5px;letter-spacing:.2px;
  border:1px solid rgba(255,255,255,.13);opacity:.9;
}
.hip-row .acts{display:flex;gap:6px;flex:0 0 auto;opacity:0;transition:opacity .16s}
.hip-row:hover .acts{opacity:1}
.hip-mini{
  padding:4px 11px;border-radius:7px;font-size:10.5px;font-weight:600;cursor:pointer;
  background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.11);
  color:#e6edf3;font-family:inherit;transition:.14s;
}
.hip-mini:hover{background:rgba(255,255,255,.12)}
.hip-edit-box{
  width:100%;min-height:90px;resize:vertical;box-sizing:border-box;
  padding:10px 12px;border-radius:8px;font:12.5px/1.6 inherit;
  background:rgba(0,0,0,.3);color:#e6edf3;
  border:1px solid rgba(88,166,255,.45);outline:none;
}
.hip-edit-box:focus{border-color:#58a6ff;box-shadow:0 0 0 3px rgba(88,166,255,.12)}

/* ---- 空状态 / 提示 ---- */
.hip-empty{text-align:center;padding:38px 20px;font-size:12.5px;opacity:.35}
.hip-toast{
  position:sticky;bottom:0;margin-top:14px;padding:9px 16px;border-radius:9px;
  font-size:12px;font-weight:600;background:rgba(63,185,80,.14);
  border:1px solid rgba(63,185,80,.35);color:#56d364;
}
.hip-toast.err{background:rgba(248,81,73,.13);border-color:rgba(248,81,73,.35);color:#ff7b72}
.hip-offline{text-align:center;padding:70px 20px}
.hip-offline h3{font-size:16px;color:#f85149;margin-bottom:14px}
.hip-offline code{
  display:inline-block;margin:6px 0;padding:5px 12px;border-radius:7px;
  background:rgba(0,0,0,.35);color:#58a6ff;font-size:12.5px;
}
.hip-foot{margin-top:18px;font-size:10.5px;opacity:.28;line-height:1.7}
`;

function useStyle() {
	react.useEffect(function () {
		if (document.getElementById("hip-style")) return;
		const el = document.createElement("style");
		el.id = "hip-style";
		el.textContent = CSS;
		document.head.appendChild(el);
	}, []);
}

/* ============================================================
   数据
   ============================================================ */
async function api(path, opts) {
	const ac = new AbortController();
	const t = setTimeout(() => ac.abort(), 5000);
	try {
		const baseHeaders = { "X-Tan-Source": "dsh-plugin" };
		const merged = Object.assign({ signal: ac.signal }, opts || {});
		merged.headers = Object.assign(baseHeaders, merged.headers || {});
		const r = await fetch(API + path, merged);
		if (!r.ok) throw new Error("HTTP " + r.status);
		return await r.json();
	} finally { clearTimeout(t); }
}
const post = (p, body) => api(p, {
	method: "POST", headers: { "Content-Type": "application/json" },
	body: JSON.stringify(body || {}),
});

function ago(d) {
	if (d == null) return "?";
	return d < 1 ? "今天" : d < 2 ? "昨天" : d < 8 ? Math.round(d) + " 天前" : d < 31 ? "上个月" : "很久以前";
}

/* ============================================================
   小组件
   ============================================================ */
function Stat(props) {
	return h("div", { className: "hip-stat", style: { "--accent": props.color || "#58a6ff" } },
		h("div", { className: "hip-stat-k" }, props.label),
		h("div", { className: "hip-stat-v", style: props.color ? { color: props.color } : null }, props.value),
		props.sub ? h("div", { className: "hip-stat-s", title: props.sub }, props.sub) : null
	);
}

function Field(props) {
	const pct = function (v) {
		const d = props.max - props.min;
		return d <= 0 ? 0 : Math.round(((v - props.min) / d) * 100);
	};
	return h("div", { className: "hip-field" },
		h("div", { className: "hip-lab" },
			h("span", { className: "name" }, props.label),
			h("span", { className: "val" }, String(props.value),
				props.unit ? h("span", { className: "u" }, props.unit) : null)
		),
		h("input", {
			type: "range", className: "hip-range",
			min: props.min, max: props.max, step: props.step || 1,
			value: props.value,
			style: { "--p": pct(props.value) + "%" },
			onChange: (e) => props.onChange(props.k, Number(e.target.value)),
		}),
		props.hint ? h("div", { className: "hip-hint" }, props.hint) : null
	);
}

function Group(props) {
	return h("div", { className: "hip-group" },
		h("div", { className: "hip-group-h" },
			h("span", { className: "ic" }, props.icon || "•"),
			h("span", { className: "t" }, props.title),
			h("span", { className: "line" })
		),
		props.children
	);
}

/* ============================================================
   主组件
   ============================================================ */
function ConfigCard() {
	useStyle();
	const [st, setSt] = react.useState(null);
	const [cfg, setCfg] = react.useState(null);
	const [loops, setLoops] = react.useState([]);
	const [mem, setMem] = react.useState([]);
	const [err, setErr] = react.useState("");
	const [msg, setMsg] = react.useState("");
	const [bad, setBad] = react.useState(false);
	const [tab, setTab] = react.useState("params");
	const [editing, setEditing] = react.useState(null);   // {id, text}
	const dirty = react.useRef({});

	const load = react.useCallback(async () => {
		try {
			const [a, b, c, d] = await Promise.all([
				api("/status"), api("/config"), api("/loops"), api("/memories?limit=30"),
			]);
			setSt(a); setCfg(b.config); setLoops(c.loops || []); setMem(d.memories || []);
			setErr("");
		} catch (e) {
			setErr(String((e && e.message) || e));
		}
	}, []);

	react.useEffect(() => {
		load();
		const t = setInterval(load, 8000);
		return () => clearInterval(t);
	}, [load]);

	const flash = (t, isErr) => {
		setMsg(t); setBad(!!isErr);
		setTimeout(() => { setMsg(""); setBad(false); }, 2600);
	};

	const onSlide = (k, v) => {
		dirty.current[k] = v;
		setCfg(Object.assign({}, cfg, { [k]: v }));
	};

	const save = async () => {
		const patch = Object.assign({}, dirty.current);
		if (!Object.keys(patch).length) return flash("没有改动");
		try {
			const r = await post("/config", patch);
			dirty.current = {};
			setCfg(r.config);
			flash("已保存 " + Object.keys(r.applied || {}).length + " 项");
		} catch (e) { flash("保存失败：" + e.message, true); }
	};

	const reset = async () => {
		try {
			await post("/config", {
				half_life_l1: 3, half_life_l2: 180, reinforce_factor: 1.8, reject_factor: 0.5,
				budget_identity: 200, budget_semantic: 500, budget_episodic: 300,
				proactive_max_per_day: 4, proactive_quiet_start: 23, proactive_quiet_end: 7,
				loops_per_turn: 1, loop_cooldown_hours: 24, noise_archive_days: 3,
			});
			dirty.current = {};
			await load();
			flash("已恢复默认");
		} catch (e) { flash("失败：" + e.message, true); }
	};

	/* ---- 备份：导出 / 导入 ---- */
	const fileRef = react.useRef(null);

	const doExport = async () => {
		flash("正在打包…");
		try {
			const r = await fetch(API + "/export", { headers: { "X-Tan-Source": "dsh-plugin" } });
			if (!r.ok) { flash("导出失败：HTTP " + r.status, true); return; }
			const blob = await r.blob();
			const name = r.headers.get("X-Backup-Name") || "tan-backup.tanbak";
			const a = document.createElement("a");
			a.href = URL.createObjectURL(blob);
			a.download = name;
			a.click();
			setTimeout(() => URL.revokeObjectURL(a.href), 3000);
			flash("已下载 " + name);
		} catch (e) {
			flash("导出出错：" + e.message, true);
		}
	};

	const pickImport = () => fileRef.current && fileRef.current.click();

	const onFile = async (e) => {
		const f = e.target.files && e.target.files[0];
		if (!f) return;
		try {
			const buf = await f.arrayBuffer();
			const rp = await fetch(API + "/import/preview", { method: "POST", body: buf, headers: { "X-Tan-Source": "dsh-plugin" } });
			const info = await rp.json();
			if (!info.ok) { flash("文件无效：" + (info.error || "未知"), true); return; }
			const okGo = window.confirm(
				"确认导入这份备份？\n\n" +
				info.summary + "\n\n" +
				"导入后当前记忆会被替换。\n旧库会自动备份到 backup/ 目录，可随时还原。"
			);
			if (!okGo) { e.target.value = ""; return; }
			flash("正在导入…");
			const r2 = await fetch(API + "/import", { method: "POST", body: buf, headers: { "X-Tan-Source": "dsh-plugin" } });
			const res = await r2.json();
			if (res.ok) {
				flash("已导入 · " + res.summary);
				await load();
			} else {
				flash("导入失败：" + (res.error || "未知"), true);
			}
		} catch (err) {
			flash("导入出错：" + err.message, true);
		} finally {
			e.target.value = "";
		}
	};

	// 编辑的必须是正文：列表显示优先用 summary，这里若也取 summary，
	// 保存后 summary 没变，列表看上去就像「根本没保存」。
	const startEdit = (m) => setEditing({ id: m.id, text: m.content || m.summary || "" });

	const cancelEdit = () => setEditing(null);

	const saveEdit = async () => {
		if (!editing) return;
		const id = editing.id;                       // 先抓住 id，后面会清空 editing
		const t = (editing.text || "").trim();
		if (!t) { flash("内容不能为空", true); return; }
		setEditing(null);
		// summary 一并刷新，否则列表仍显示旧摘要，用户以为没存上
		await act("/memory/edit", { id, content: t, summary: t.slice(0, 60) }, "已保存修改");
	};

	const act = async (p, body, label) => {
		try { await post(p, body); await load(); flash(label); }
		catch (e) { flash("失败：" + e.message, true); }
	};

	/* ---- 服务未启动 ---- */
	if (err) {
		return h("div", { className: "hip-root" },
			h("div", { className: "hip-offline" },
				h("h3", null, "记忆服务未启动"),
				h("div", { style: { opacity: .5, fontSize: 12, marginBottom: 4 } }, "命令行运行："),
				h("code", null, "python api.py"),
				h("div", { style: { opacity: .35, fontSize: 11.5, marginTop: 10 } },
					"或双击 D:\\dsh-tan\\启动记忆服务.bat"),
				h("div", { style: { marginTop: 22 } },
					h("button", { className: "hip-btn pri", onClick: load }, "重新连接"))
			)
		);
	}

	if (!st || !cfg) {
		return h("div", { className: "hip-root" },
			h("div", { style: { opacity: .35, padding: 20 } }, "读取中…"));
	}

	const p = st.persona || {};
	const layers = Object.entries(st.by_layer || {});
	const layerSub = layers.length
		? layers.map(([l, n]) => (LAYER[l] || { n: l }).n + " " + n).join(" · ")
		: "还没有记忆";

	const tabs = [
		{ id: "params", label: "参数", n: null },
		{ id: "memory", label: "记忆", n: st.memories },
		{ id: "loops", label: "未闭合", n: loops.length },
	];

	return h("div", { className: "hip-root" },

		/* 头部 */
		h("div", { className: "hip-head" },
			h("div", { className: "hip-logo" }, "记"),
			h("div", null,
				h("div", { className: "hip-title" }, "记忆"),
				h("div", { className: "hip-sub" }, "本地 SQLite · 会遗忘 · 有性格")
			)
		),

		/* 统计 */
		h("div", { className: "hip-stats" },
			h(Stat, { label: "记忆", value: st.memories, color: "#58a6ff", sub: layerSub }),
			h(Stat, { label: "Thread", value: st.threads, color: "#48c774", sub: "按事组织" }),
			h(Stat, { label: "未闭合", value: st.open_loops, color: st.open_loops ? "#f5c850" : null, sub: st.open_loops ? "有话要跟你说" : "都做完了" }),
			h(Stat, { label: "人格", value: p.stage || "初识", color: "#bc8cff", sub: "聊过 " + (p.turns || 0) + " 轮" })
		),

		/* 标签页 */
		h("div", { className: "hip-tabs" },
			tabs.map((t) =>
				h("button", {
					key: t.id,
					className: "hip-tab" + (tab === t.id ? " on" : ""),
					onClick: () => setTab(t.id),
				}, t.label, t.n != null ? h("span", { className: "n" }, t.n) : null)
			)
		),

		/* ============ 参数页 ============ */
		tab === "params" && h("div", null,
			h(Group, { icon: "⏳", title: "遗忘速度" },
				h(Field, { k: "half_life_l1", label: "情景记忆半衰期", value: cfg.half_life_l1, min: 1, max: 30, step: 0.5, unit: "天", hint: "多少天后只剩一半强度", onChange: onSlide }),
				h(Field, { k: "half_life_l2", label: "事实记忆半衰期", value: cfg.half_life_l2, min: 30, max: 365, step: 5, unit: "天", hint: "事实、偏好多久开始淡化", onChange: onSlide }),
				h(Field, { k: "reinforce_factor", label: "回忆强化倍率", value: cfg.reinforce_factor, min: 1.2, max: 3, step: 0.1, unit: "×", hint: "每被用一次，半衰期乘多少", onChange: onSlide }),
				h(Field, { k: "reject_factor", label: "否定降权倍率", value: cfg.reject_factor, min: 0.2, max: 0.9, step: 0.05, unit: "×", hint: "被你说「记错了」时乘多少", onChange: onSlide })
			),
			h(Group, { icon: "🎯", title: "注入预算" },
				h(Field, { k: "budget_identity", label: "身份 / 自我", value: cfg.budget_identity, min: 100, max: 500, step: 50, unit: "token", hint: "你是谁、我是谁，固定给", onChange: onSlide }),
				h(Field, { k: "budget_semantic", label: "事实 / 偏好", value: cfg.budget_semantic, min: 200, max: 1200, step: 50, unit: "token", hint: "长期事实、喜好", onChange: onSlide }),
				h(Field, { k: "budget_episodic", label: "情景 / Thread", value: cfg.budget_episodic, min: 100, max: 800, step: 50, unit: "token", hint: "今天说过的话", onChange: onSlide })
			),
			h(Group, { icon: "🔔", title: "主动消息" },
				h(Field, { k: "proactive_max_per_day", label: "每天最多几条", value: cfg.proactive_max_per_day, min: 1, max: 10, unit: "条", hint: "超过就不再开口", onChange: onSlide }),
				h(Field, { k: "proactive_quiet_start", label: "静默开始", value: cfg.proactive_quiet_start, min: 20, max: 23, unit: "点", hint: "这个点之后不打扰", onChange: onSlide }),
				h(Field, { k: "proactive_quiet_end", label: "静默结束", value: cfg.proactive_quiet_end, min: 5, max: 10, unit: "点", hint: "这个点之后可以开口", onChange: onSlide })
			),
			h(Group, { icon: "📌", title: "未闭合提醒" },
				h(Field, { k: "loops_per_turn", label: "每轮最多提几条", value: cfg.loops_per_turn, min: 0, max: 3, unit: "条", hint: "0 = 不主动提", onChange: onSlide }),
				h(Field, { k: "loop_cooldown_hours", label: "同一事冷却", value: cfg.loop_cooldown_hours, min: 6, max: 72, step: 6, unit: "小时", hint: "问过一次，多久内不再提", onChange: onSlide }),
				h(Field, { k: "noise_archive_days", label: "闲聊归档天数", value: cfg.noise_archive_days, min: 1, max: 14, unit: "天", hint: "「嗯」「好的」这类多久后归档", onChange: onSlide })
			),
			h("div", { className: "hip-actions" },
				h("button", { className: "hip-btn pri", onClick: save }, "保存参数"),
				h("button", { className: "hip-btn", onClick: reset }, "恢复默认"),
				h("button", { className: "hip-btn", onClick: doExport }, "导出备份"),
				h("button", { className: "hip-btn", onClick: pickImport }, "导入备份"),
				h("button", { className: "hip-btn", onClick: () => window.open(API + "/", "_blank") }, "完整控制台"),
				h("input", {
					ref: fileRef,
					type: "file",
					accept: ".tanbak",
					style: { display: "none" },
					onChange: onFile,
				})
			)
		),

		/* ============ 记忆页 ============ */
		tab === "memory" && h(Group, { icon: "📚", title: "最近的记忆" },
			mem.length === 0
				? h("div", { className: "hip-empty" }, "还没有记忆，随便聊几句就有了")
				: mem.map((m) => {
					const L = LAYER[m.layer] || { n: m.layer, c: "#8b949e" };
					return h("div", { key: m.id, className: "hip-row" },
						h("div", { className: "bar", style: { background: L.c } }),
						h("div", { className: "body" },
							editing && editing.id === m.id
								? h("div", null,
									h("textarea", {
										className: "hip-edit-box",
										value: editing.text,
										autoFocus: true,
										onChange: (e) => setEditing({ id: editing.id, text: e.target.value }),
										onKeyDown: (e) => {
											if (e.key === "Escape") cancelEdit();
											if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) saveEdit();
										},
									}),
									h("div", { style: { display: "flex", gap: 6, marginTop: 8, alignItems: "center" } },
										h("button", { className: "hip-mini", onClick: saveEdit, style: { background: "#238636", borderColor: "#2ea043" } }, "保存"),
										h("button", { className: "hip-mini", onClick: cancelEdit }, "取消"),
										h("span", { style: { fontSize: 10.5, opacity: 0.35 } }, "Ctrl+Enter 保存 · Esc 取消")
									)
								)
								: h("div", { className: "txt" }, m.summary || m.content),
							(!editing || editing.id !== m.id) ? h("div", { className: "meta" },
								h("span", { className: "hip-tag", style: { color: L.c, borderColor: L.c + "66" } }, L.n),
								h("span", null, ago(m.age_days)),
								h("span", null, "用过 " + m.recall_count + " 次"),
								m.pinned ? h("span", { className: "hip-tag", style: { color: "#f5c850", borderColor: "#f5c85066" } }, "已钉住") : null
							) : null
						),
						h("div", { className: "acts" },
							h("button", {
								className: "hip-mini",
								onClick: () => startEdit(m),
							}, "编辑"),
							h("button", {
								className: "hip-mini",
								onClick: () => act("/memory/reinforce", { id: m.id }, "已强化"),
							}, "强化"),
							h("button", {
								className: "hip-mini",
								onClick: () => act("/memory/pin", { id: m.id, value: !m.pinned },
									m.pinned ? "已取消钉住" : "已钉住"),
							}, m.pinned ? "取消钉" : "钉住")
						)
					);
				})
		),

		/* ============ 未闭合页 ============ */
		tab === "loops" && h(Group, { icon: "◎", title: "未闭合的事" },
			loops.length === 0
				? h("div", { className: "hip-empty" }, "空的，都做完了")
				: loops.map((l) =>
					h("div", { key: l.id, className: "hip-row" },
						h("div", { className: "bar", style: { background: "#f5c850" } }),
						h("div", { className: "body" },
							h("div", { className: "txt" }, l.title),
							h("div", { className: "meta" },
								h("span", { className: "hip-tag", style: { color: "#f5c850", borderColor: "#f5c85066" } }, KIND[l.kind] || l.kind),
								h("span", null, ago((Date.now() / 1000 - l.created) / 86400)),
								l.ask_count ? h("span", null, "提过 " + l.ask_count + " 次") : null
							)
						),
						h("div", { className: "acts", style: { opacity: 1 } },
							h("button", { className: "hip-mini", onClick: () => act("/loop/close", { id: l.id }, "已完成") }, "完成"),
							h("button", { className: "hip-mini", onClick: () => act("/loop/mute", { id: l.id }, "不再提醒") }, "静音")
						)
					)
				)
		),

		/* 提示 */
		msg ? h("div", { className: "hip-toast" + (bad ? " err" : ""), style: { position: "static" } }, msg) : null,

		h("div", { className: "hip-foot" },
			"服务 " + API + " · 每 8 秒自动刷新 · 数据在 ~/.dsh/niannian/memory.db"
		)
	);
}

/* ============================================================
   侧边栏图标
   ============================================================ */
function HipSidebarIcon(props) {
	const size = (props && props.size) || 20;
	return h("div", {
		style: {
			width: size, height: size, borderRadius: Math.round(size * 0.28),
			background: "linear-gradient(135deg,#58a6ff,#bc8cff)",
			display: "flex", alignItems: "center", justifyContent: "center",
			fontSize: Math.round(size * 0.52), fontWeight: 800, color: "#fff",
			lineHeight: 1, userSelect: "none", flex: "0 0 auto",
		},
	}, "记");
}

/* ============================================================
   挂载
   ============================================================ */
function apply(ctx) {
	try {
		if (!ctx || !ctx.slots || typeof ctx.slots.inject !== "function") return;

		/* ① 插件详情页配置卡 */
		ctx.slots.inject("plugins.bundle.config", function () {
			try {
				return ctx.slots.register({
					name: "plugins.bundle.config",
					key: KEY,
					inject: function () { return {}; },
				}, ConfigCard);
			} catch (e) {
				console.warn("[hippocampus] 注册设置页失败", e);
				return function () {};
			}
		});

		/* ② 侧边栏 + 主面板 */
		ctx.slots.inject("main", function () {
			return ctx.slots.inject("sidebar.panellist", function () {
				const stops = [];
				try {
					stops.push(ctx.slots.register({
						name: "main", key: KEY,
						inject: function () { return {}; },
					}, ConfigCard));
				} catch (e) { console.warn("[hippocampus] 主面板注册失败", e); }
				try {
					stops.push(ctx.slots.register({
						name: "sidebar.panellist", id: KEY, order: 43,
						label: function () { return "记忆"; },
					}, HipSidebarIcon));
				} catch (e) { console.warn("[hippocampus] 侧边栏注册失败", e); }
				return function () {
					for (const s of stops) { try { s && s(); } catch (_) {} }
				};
			});
		});
	} catch (e) {
		console.warn("[hippocampus] apply 失败", e);
	}
}

const name = "dsh-tan";
const inject = ["slots"];

exports.apply = apply;
exports.inject = inject;
exports.name = name;
return module.exports; } });
