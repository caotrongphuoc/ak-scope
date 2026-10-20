import { esc, exeClass, fmtDur, fmtTime } from "./format.js";

const MAX_MESSAGES = 300;
const LANE_W = 150;
const TIME_W = 110;
const EXE_W = 90;
const HEAD_H = 32;
const ROW_H = 24;

// sequence diagram of the messages inside the timeline view: one lane per task, one row per
// message, from the posting task (or timer, idle, isr) to the task that ran it
export class FlowView {
	constructor(root, note, { onPick }) {
		this.root = root;
		this.note = note;
		this.onPick = onPick;
		this.unit = "ms";
		this.selected = -1;
		this.v0 = 0;
		this.v1 = 0;
		root.addEventListener("click", (e) => {
			const g = e.target.closest(".msg[data-exec]");
			if (g) this.onPick(+g.dataset.exec);
		});
	}

	// order: the tasks as the timeline shows them, so lanes and rows line up in the reader's head
	setTrace(trace, order) {
		this.trace = trace;
		this.order = order || trace.tasks;
		this.taskName = new Map(trace.tasks.map((t) => [t.id, t.name]));
	}

	setUnit(unit) {
		this.unit = unit;
	}

	setView(v0, v1) {
		this.v0 = v0;
		this.v1 = v1;
	}

	select(i) {
		this.selected = i;
	}

	// handler runs in the view with where their message came from
	messages() {
		const out = [];
		for (const e of this.trace.execs) {
			if (e.t < this.v0 || e.t > this.v1) continue;
			const p = e.post >= 0 ? this.trace.posts[e.post] : null;
			out.push({
				t: p ? p.t : e.t,
				from: p ? this.lane(p.src) : e.src === "timer" ? "timer" : "unknown",
				to: this.lane(e.task),
				label: e.sig_name,
				exec: e,
			});
		}
		out.sort((a, b) => a.t - b.t);
		return out;
	}

	lane(task) {
		if (typeof task === "string") return task;
		return this.taskName.get(task) ?? String(task);
	}

	render() {
		if (!this.trace) return;
		const msgs = this.messages();
		if (msgs.length > MAX_MESSAGES) {
			this.note.textContent = `${msgs.length} messages in the timeline view, zoom in to under ${MAX_MESSAGES} to see the flow`;
			this.root.innerHTML = "";
			return;
		}
		this.note.textContent = msgs.length
			? `${msgs.length} messages in the timeline view, ${fmtTime(Math.max(this.v0, 0), this.unit)} to ${fmtTime(this.v1, this.unit)}`
			: "No messages in the timeline view";

		// lanes in task order, sources that are not tasks first
		const used = new Set();
		for (const m of msgs) {
			used.add(m.from);
			used.add(m.to);
		}
		const special = ["timer", "idle", "isr", "unknown"].filter((n) => used.has(n));
		const tasks = this.order.map((t) => t.name).filter((n) => used.has(n) && !special.includes(n));
		const lanes = [...special, ...tasks];
		const x = new Map(lanes.map((n, i) => [n, TIME_W + i * LANE_W + LANE_W / 2]));
		const width = TIME_W + lanes.length * LANE_W + EXE_W;
		const height = HEAD_H + msgs.length * ROW_H + 8;

		let svg = `<svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">`;
		for (const n of lanes) {
			const cx = x.get(n);
			svg += `<line class="lane-line" x1="${cx}" y1="${HEAD_H - 6}" x2="${cx}" y2="${height}"/>`;
			svg += `<text class="lane-head" x="${cx}" y="18" text-anchor="middle">${esc(n.length > 20 ? n.slice(0, 19) + ".." : n)}<title>${esc(n)}</title></text>`;
		}
		msgs.forEach((m, i) => {
			const y = HEAD_H + i * ROW_H;
			const mid = y + ROW_H - 7;
			const x0 = x.get(m.from);
			const x1 = x.get(m.to);
			const sel = m.exec.i === this.selected ? " selected" : "";
			svg += `<g class="msg${sel}" data-exec="${m.exec.i}">`;
			svg += `<rect class="msg-bg" x="0" y="${y}" width="${width}" height="${ROW_H}"/>`;
			svg += `<text class="msg-time" x="8" y="${mid + 3}">${fmtTime(m.t, this.unit)}</text>`;
			if (x0 === x1) {
				// a task posting to itself: a small loop on its lane
				svg += `<path class="msg-line" fill="none" d="M${x0} ${mid - 6} h24 v8 h-20"/>`;
				svg += `<path class="msg-head" d="M${x0 + 4} ${mid + 2} l6 -3 v6 z"/>`;
				svg += `<text class="msg-label" x="${x0 + 30}" y="${mid}">${esc(m.label)}</text>`;
			} else {
				const dir = x1 > x0 ? 1 : -1;
				svg += `<line class="msg-line" x1="${x0}" y1="${mid}" x2="${x1 - dir * 6}" y2="${mid}"/>`;
				svg += `<path class="msg-head" d="M${x1} ${mid} l${-dir * 7} -4 v8 z"/>`;
				svg += `<text class="msg-label" x="${(x0 + x1) / 2}" y="${mid - 4}" text-anchor="middle">${esc(m.label)}</text>`;
			}
			svg += `<text class="msg-exe ${exeClass(m.exec.dur)}" x="${width - 8}" y="${mid + 3}" text-anchor="end">${fmtDur(m.exec.dur)}</text>`;
			svg += `</g>`;
		});
		svg += `</svg>`;
		this.root.innerHTML = svg;
	}
}
