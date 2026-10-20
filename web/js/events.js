import { esc, exeClass, fmtDur, fmtTime } from "./format.js";

const ROW = 26;
const OVERSCAN = 10;

export class EventTable {
	constructor(scroll, spacer, countEl, { onPick }) {
		this.scroll = scroll;
		this.spacer = spacer;
		this.countEl = countEl;
		this.onPick = onPick;
		this.items = [];
		this.shown = [];
		this.unit = "ms";
		this.selectedExec = -1;
		this.filter = "";
		this.kinds = new Set(["EXEC", "POST", "TIMER"]);
		scroll.addEventListener("scroll", () => this.render());
		new ResizeObserver(() => this.render()).observe(scroll);
		spacer.addEventListener("click", (e) => {
			const row = e.target.closest(".events-row");
			if (row) this.onPick(this.shown[+row.dataset.n]);
		});
	}

	setTrace(trace) {
		const taskName = new Map(trace.tasks.map((t) => [t.id, t.name]));
		const items = [];
		for (const e of trace.execs) {
			items.push({
				kind: "EXEC", t: e.t, task: taskName.get(e.task) ?? String(e.task), sig: e.sig_name,
				from: e.src_name, type: e.type, exe: e.dur, wait: e.wait, exec: e.i,
			});
		}
		for (const p of trace.posts) {
			items.push({
				kind: "POST", t: p.t, task: taskName.get(p.des) ?? String(p.des), sig: p.sig_name,
				from: p.src_name, type: p.type, exe: null, wait: null, exec: p.exec,
			});
		}
		for (const tm of trace.timers) {
			items.push({
				kind: "TIMER", t: tm.t, task: taskName.get(tm.des) ?? String(tm.des), sig: tm.sig_name,
				from: tm.kind === "TIMER_SET" ? (tm.periodic ? "periodic " : "one shot ") + tm.duty_ms + " ms" : "removed",
				type: "", exe: null, wait: null, exec: -1,
			});
		}
		items.sort((a, b) => a.t - b.t);
		items.forEach((it, n) => (it.n = n + 1));
		for (const it of items) it.key = (it.kind + " " + it.task + " " + it.sig + " " + it.from).toLowerCase();
		this.items = items;
		this.apply();
	}

	setFilter(text) {
		this.filter = text.trim().toLowerCase();
		this.apply();
	}

	setKind(kind, on) {
		if (on) this.kinds.add(kind);
		else this.kinds.delete(kind);
		this.apply();
	}

	setUnit(unit) {
		this.unit = unit;
		this.render(true);
	}

	apply() {
		const words = this.filter.split(/\s+/).filter(Boolean);
		this.shown = this.items.filter((it) => this.kinds.has(it.kind) && words.every((w) => it.key.includes(w)));
		this.spacer.style.height = this.shown.length * ROW + "px";
		this.countEl.textContent = this.shown.length + " of " + this.items.length;
		this.render(true);
	}

	// the rows that pass the filter, as a file the browser saves
	export(format) {
		const cols = ["n", "kind", "t_us", "task", "sig", "from", "type", "exe_us", "wait_us"];
		const rows = this.shown.map((it) => [it.n, it.kind, it.t, it.task, it.sig, it.from, it.type, it.exe, it.wait]);
		let blob;
		if (format === "json") {
			blob = new Blob([JSON.stringify(rows.map((r) => Object.fromEntries(cols.map((c, i) => [c, r[i]]))), null, 1)],
				{ type: "application/json" });
		} else {
			const cell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v));
			blob = new Blob([[cols, ...rows].map((r) => r.map(cell).join(",")).join("\n") + "\n"], { type: "text/csv" });
		}
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = "akscope-events." + format;
		a.click();
		setTimeout(() => URL.revokeObjectURL(a.href), 1000);
	}

	select(execIndex) {
		this.selectedExec = execIndex;
		const n = this.shown.findIndex((it) => it.kind === "EXEC" && it.exec === execIndex);
		if (n >= 0) {
			const top = n * ROW;
			if (top < this.scroll.scrollTop || top + ROW > this.scroll.scrollTop + this.scroll.clientHeight) {
				this.scroll.scrollTop = top - this.scroll.clientHeight / 2;
			}
		}
		this.render(true);
	}

	render(force = false) {
		const first = Math.max(0, Math.floor(this.scroll.scrollTop / ROW) - OVERSCAN);
		const last = Math.min(this.shown.length, Math.ceil((this.scroll.scrollTop + this.scroll.clientHeight) / ROW) + OVERSCAN);
		if (!force && first === this.first && last === this.last) return;
		this.first = first;
		this.last = last;
		let html = "";
		for (let n = first; n < last; n++) {
			const it = this.shown[n];
			const sel = it.kind === "EXEC" && it.exec === this.selectedExec ? " selected" : "";
			html +=
				`<div class="events-row${sel}" data-n="${n}" style="top:${n * ROW}px">` +
				`<span class="num">${it.n}</span>` +
				`<span class="num">${fmtTime(it.t, this.unit)}</span>` +
				`<span><span class="tag tag-${it.kind.toLowerCase()}">${it.kind}</span></span>` +
				`<span title="${esc(it.task)}">${esc(it.task)}</span>` +
				`<span title="${esc(it.sig)}">${esc(it.sig)}</span>` +
				`<span title="${esc(it.from)}">${esc(it.from)}</span>` +
				`<span>${esc(it.type)}</span>` +
				`<span class="num ${it.exe != null ? exeClass(it.exe) : ""}">${it.exe != null ? fmtDur(it.exe) : ""}</span>` +
				`<span class="num">${it.wait != null ? fmtDur(it.wait) : ""}</span>` +
				`</div>`;
		}
		this.spacer.innerHTML = html;
	}
}
