import { esc, exeClass, fmtDur } from "./format.js";

const TASK_COLS = [
	{ key: "name", label: "task" },
	{ key: "count", label: "count", num: true },
	{ key: "exe_min_us", label: "exe min", num: true, dur: true },
	{ key: "exe_avg_us", label: "exe avg", num: true, dur: true },
	{ key: "exe_max_us", label: "exe max", num: true, dur: true, exe: true },
	{ key: "exe_total_us", label: "exe total", num: true, dur: true },
	{ key: "wait_max_us", label: "wait max", num: true, dur: true },
	{ key: "load_pct", label: "cpu load", num: true, pct: true },
];

const SIG_COLS = [
	{ key: "task_name", label: "task" },
	{ key: "sig_name", label: "signal" },
	{ key: "count", label: "count", num: true },
	{ key: "exe_min_us", label: "exe min", num: true, dur: true },
	{ key: "exe_avg_us", label: "exe avg", num: true, dur: true },
	{ key: "exe_max_us", label: "exe max", num: true, dur: true, exe: true },
	{ key: "wait_max_us", label: "wait max", num: true, dur: true },
];

function cell(col, row) {
	const v = row[col.key];
	if (col.dur) return `<td class="num ${col.exe ? exeClass(v) : ""}">${fmtDur(v)}</td>`;
	if (col.pct) return `<td class="num">${v.toFixed(3)} %</td>`;
	if (col.num) return `<td class="num">${v}</td>`;
	return `<td>${esc(v)}</td>`;
}

class SortTable {
	constructor(caption, cols, rows, sortKey) {
		this.caption = caption;
		this.cols = cols;
		this.rows = rows;
		this.key = sortKey;
		this.asc = false;
	}

	html(id) {
		const rows = [...this.rows].sort((a, b) => {
			const x = a[this.key] ?? -Infinity;
			const y = b[this.key] ?? -Infinity;
			const r = typeof x === "string" ? x.localeCompare(y) : x - y;
			return this.asc ? r : -r;
		});
		const head = this.cols.map((c) => {
			const cls = [c.num ? "num" : "", c.key === this.key ? "sorted" : "", c.key === this.key && this.asc ? "asc" : ""];
			return `<th class="${cls.join(" ")}" data-table="${id}" data-key="${c.key}">${c.label}</th>`;
		}).join("");
		const body = rows.map((r) => "<tr>" + this.cols.map((c) => cell(c, r)).join("") + "</tr>").join("");
		return `<table class="data-table"><caption>${this.caption}</caption><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
	}
}

export class StatsView {
	constructor(root) {
		this.root = root;
		root.addEventListener("click", (e) => {
			const th = e.target.closest("th[data-key]");
			if (!th) return;
			const t = this.tables[th.dataset.table];
			if (t.key === th.dataset.key) t.asc = !t.asc;
			else {
				t.key = th.dataset.key;
				t.asc = false;
			}
			this.render();
		});
	}

	setTrace(trace) {
		this.trace = trace;
		this.tables = {
			tasks: new SortTable("Per task", TASK_COLS, trace.task_stats, "exe_total_us"),
			signals: new SortTable("Per signal", SIG_COLS, trace.signal_stats, "exe_max_us"),
		};
		this.render();
	}

	render() {
		const tr = this.trace;
		const maxExe = tr.execs.reduce((m, e) => (e.dur > m.dur ? e : m), { dur: 0 });
		const busy = tr.task_stats.reduce((s, t) => s + t.exe_total_us, 0);
		const st = tr.stream || {};
		const cards = [
			["Handler runs", tr.execs.length, ""],
			["Duration", fmtDur(tr.duration_us), ""],
			["CPU in handlers", (100 * busy / (tr.duration_us || 1)).toFixed(2), "%"],
			["Longest handler", fmtDur(maxExe.dur), maxExe.sig_name ? esc(maxExe.sig_name) : ""],
			["Dropped records", tr.dropped, ""],
			["Corrupt frames", st.corrupt ?? 0, ""],
		];
		this.root.innerHTML =
			`<div class="cards">` +
			cards.map(([label, value, unit]) =>
				`<div class="card"><div class="label">${label}</div><div class="value">${value} <small>${unit}</small></div></div>`).join("") +
			`</div>` +
			this.tables.tasks.html("tasks") +
			this.tables.signals.html("signals");
	}
}
