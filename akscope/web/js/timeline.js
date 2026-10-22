import { readTheme } from "./colors.js";
import { esc, exeClass, fmtDur, fmtTick, fmtTime, niceStep } from "./format.js";

const GUTTER = 180;
const AXIS = 28;
// rows as high as a Figma Tree item
const ROW = 34;
const BLOCK_PAD = 7;
const MIN_SPAN_US = 5;
const MAX_ARROWS = 400;
const RUN_PX = 3;
// widest view, also the window kept while following a live capture that just started
const MIN_MAX_SPAN_US = 10e6;

// first index in a list sorted by .t whose t is >= value
function lowerBound(list, value) {
	let lo = 0;
	let hi = list.length;
	while (lo < hi) {
		const mid = (lo + hi) >> 1;
		if (list[mid].t < value) lo = mid + 1;
		else hi = mid;
	}
	return lo;
}

export class TimelineView {
	constructor(canvas, wrap, tooltip, { onSelect, onView }) {
		this.canvas = canvas;
		this.wrap = wrap;
		this.tooltip = tooltip;
		this.ctx = canvas.getContext("2d");
		this.onSelect = onSelect;
		this.onView = onView;
		this.trace = null;
		this.rows = [];
		this.unit = "ms";
		this.v0 = 0;
		this.v1 = 1;
		this.selected = -1;
		this.measure = null;
		this.drag = null;
		this.pending = false;

		new ResizeObserver(() => this.draw()).observe(wrap);
		// the axis is painted at the scroll offset, so repaint on vertical scroll
		wrap.addEventListener("scroll", () => this.draw());
		canvas.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
		canvas.addEventListener("mousedown", (e) => this.onDown(e));
		window.addEventListener("mousemove", (e) => this.onMove(e));
		window.addEventListener("mouseup", (e) => this.onUp(e));
		canvas.addEventListener("mouseleave", () => this.hideTip());
	}

	setTrace(trace, colors) {
		this.trace = trace;
		this.colors = colors;
		this.byTask = new Map();
		this.maxDur = new Map();
		for (const e of trace.execs) {
			if (!this.byTask.has(e.task)) this.byTask.set(e.task, []);
			this.byTask.get(e.task).push(e);
			this.maxDur.set(e.task, Math.max(this.maxDur.get(e.task) || 0, e.dur));
		}
		for (const list of this.byTask.values()) list.sort((a, b) => a.t - b.t);
		this.selected = -1;
		this.fit();
	}

	// live capture: handler runs arrive in start order, so each task list stays sorted
	append(execs) {
		for (const e of execs) {
			if (!this.byTask.has(e.task)) this.byTask.set(e.task, []);
			this.byTask.get(e.task).push(e);
			this.maxDur.set(e.task, Math.max(this.maxDur.get(e.task) || 0, e.dur));
		}
		this.draw();
	}

	// keep the newest records at the right edge, same zoom
	followEnd() {
		const span = this.v1 - this.v0;
		const end = this.trace.duration_us;
		// until the capture is longer than the window, keep it pinned at the start
		if (end < span * 0.95) return;
		this.setView(end - span * 0.95, end + span * 0.05);
	}

	setRows(tasks) {
		this.rows = tasks;
		this.rowOf = new Map(tasks.map((t, i) => [t.id, i]));
		this.draw();
	}

	setUnit(unit) {
		this.unit = unit;
		this.draw();
	}

	fit() {
		const d = this.trace ? this.trace.duration_us : 1;
		// a live capture starts empty: show its first seconds instead of a few microseconds
		if (this.trace && this.trace.live && d < MIN_MAX_SPAN_US) {
			this.setView(-MIN_MAX_SPAN_US * 0.02, MIN_MAX_SPAN_US * 0.98);
			return;
		}
		this.setView(-d * 0.01, d * 1.01);
	}

	setView(v0, v1) {
		const max = Math.max((this.trace ? this.trace.duration_us : 0) * 1.2, MIN_MAX_SPAN_US);
		let span = Math.min(Math.max(v1 - v0, MIN_SPAN_US), max);
		const mid = (v0 + v1) / 2;
		this.v0 = mid - span / 2;
		this.v1 = mid + span / 2;
		this.draw();
		if (this.onView) this.onView(this.v0, this.v1);
	}

	// show an exec or a time near the middle, zooming in if the view is much wider
	reveal(t, dur = 0) {
		let span = this.v1 - this.v0;
		const want = Math.max(dur * 8, 200);
		if (span > want * 50) span = want * 20;
		this.setView(t + dur / 2 - span / 2, t + dur / 2 + span / 2);
	}

	select(index, reveal = false) {
		this.selected = index;
		if (reveal && index >= 0) {
			const e = this.trace.execs[index];
			const x0 = this.x(e.t);
			const x1 = this.x(e.t + e.dur);
			if (x0 < GUTTER || x1 > this.width) this.reveal(e.t, e.dur);
			const row = this.rowOf.get(e.task);
			if (row != null) {
				const y = AXIS + row * ROW;
				if (y < this.wrap.scrollTop + AXIS || y + ROW > this.wrap.scrollTop + this.wrap.clientHeight) {
					this.wrap.scrollTop = y - this.wrap.clientHeight / 2;
				}
			}
		}
		this.draw();
	}

	x(t) {
		return GUTTER + ((t - this.v0) / (this.v1 - this.v0)) * (this.width - GUTTER);
	}

	t(x) {
		return this.v0 + ((x - GUTTER) / (this.width - GUTTER)) * (this.v1 - this.v0);
	}

	draw() {
		if (this.pending) return;
		this.pending = true;
		requestAnimationFrame(() => {
			this.pending = false;
			this.paint();
		});
	}

	paint() {
		const dpr = window.devicePixelRatio || 1;
		const width = this.wrap.clientWidth;
		const height = Math.max(this.wrap.clientHeight, AXIS + this.rows.length * ROW + 8);
		if (this.canvas.width !== width * dpr || this.canvas.height !== height * dpr) {
			this.canvas.width = width * dpr;
			this.canvas.height = height * dpr;
			this.canvas.style.width = width + "px";
			this.canvas.style.height = height + "px";
		}
		this.width = width;
		this.height = height;
		const c = this.ctx;
		const th = readTheme();
		this.theme = th;
		c.setTransform(dpr, 0, 0, dpr, 0, 0);
		c.fillStyle = th.surface;
		c.fillRect(0, 0, width, height);
		if (!this.trace) return;

		this.paintRows(c, th);
		this.paintGrid(c, th);
		this.paintBlocks(c, th);
		this.paintArrows(c, th);
		this.paintRestarts(c, th);
		this.paintMeasure(c, th);
		this.paintLabels(c, th);
		this.paintAxis(c, th);
	}

	paintRows(c, th) {
		for (let i = 0; i < this.rows.length; i++) {
			if (i % 2 === 1) {
				c.fillStyle = th.rowAlt;
				c.fillRect(0, AXIS + i * ROW, this.width, ROW);
			}
		}
	}

	ticks() {
		const span = this.v1 - this.v0;
		const step = niceStep((span / (this.width - GUTTER)) * 110);
		const out = [];
		for (let t = Math.ceil(this.v0 / step) * step; t <= this.v1; t += step) out.push(t);
		return { step, out };
	}

	paintGrid(c, th) {
		c.strokeStyle = th.borderSubtle;
		c.lineWidth = 1;
		c.beginPath();
		for (const t of this.ticks().out) {
			const x = Math.round(this.x(t)) + 0.5;
			c.moveTo(x, AXIS);
			c.lineTo(x, this.height);
		}
		c.stroke();
	}

	paintAxis(c, th) {
		// the axis stays on top of the visible area when the rows scroll
		const top = this.wrap.scrollTop;
		c.fillStyle = th.surface;
		c.fillRect(0, top, this.width, AXIS);
		c.strokeStyle = th.border;
		c.beginPath();
		c.moveTo(0, top + AXIS - 0.5);
		c.lineTo(this.width, top + AXIS - 0.5);
		c.stroke();
		const { step, out } = this.ticks();
		c.fillStyle = th.fg2;
		c.font = "12px " + th.sans;
		c.textBaseline = "middle";
		for (const t of out) {
			const x = Math.round(this.x(t)) + 0.5;
			c.fillRect(x, top + AXIS - 6, 1, 6);
			c.fillText(fmtTick(t, step), x + 4, top + AXIS / 2);
		}
		c.fillStyle = th.surface;
		c.fillRect(0, top, GUTTER, AXIS);
	}

	paintLabels(c, th) {
		c.fillStyle = th.surface;
		c.fillRect(0, AXIS, GUTTER, this.height - AXIS);
		c.strokeStyle = th.border;
		c.beginPath();
		c.moveTo(GUTTER - 0.5, 0);
		c.lineTo(GUTTER - 0.5, this.height);
		c.stroke();
		c.font = "14px " + th.sans;
		c.textBaseline = "middle";
		this.rows.forEach((task, i) => {
			const y = AXIS + i * ROW;
			if (i % 2 === 1) {
				c.fillStyle = th.rowAlt;
				c.fillRect(0, y, GUTTER, ROW);
			}
			c.fillStyle = this.colors.get(task.id).fill;
			c.fillRect(12, y + ROW / 2 - 6, 12, 12);
			c.fillStyle = th.fg;
			this.clipText(c, task.name, 32, y + ROW / 2, GUTTER - 40);
		});
	}

	clipText(c, text, x, y, max) {
		if (max <= 6) return;
		if (c.measureText(text).width <= max) {
			c.fillText(text, x, y);
			return;
		}
		let lo = 0;
		let hi = text.length;
		while (lo < hi) {
			const mid = (lo + hi + 1) >> 1;
			if (c.measureText(text.slice(0, mid) + "..").width <= max) lo = mid;
			else hi = mid - 1;
		}
		if (lo > 0) c.fillText(text.slice(0, lo) + "..", x, y);
	}

	// execs of one task that overlap the view
	visible(task) {
		const list = this.byTask.get(task) || [];
		const from = lowerBound(list, this.v0 - (this.maxDur.get(task) || 0));
		const to = lowerBound(list, this.v1);
		return list.slice(from, to);
	}

	paintBlocks(c, th) {
		c.font = "12px " + th.sans;
		c.textBaseline = "middle";
		const sel = this.selected >= 0 ? this.trace.execs[this.selected] : null;
		this.rows.forEach((task, i) => {
			const y = AXIS + i * ROW + BLOCK_PAD;
			const h = ROW - 2 * BLOCK_PAD;
			const col = this.colors.get(task.id);
			// blocks under RUN_PX wide that touch are drawn as one run, so a zoomed out view
			// costs a few fills per row instead of one per handler run
			let runX0 = -1;
			let runX1 = -1;
			const flush = () => {
				if (runX1 > runX0) {
					// lighter than a real block: a run hides the idle gaps between handlers
					c.globalAlpha = 0.55;
					c.fillStyle = col.fill;
					c.fillRect(runX0, y, runX1 - runX0, h);
					c.globalAlpha = 1;
				}
				runX0 = runX1 = -1;
			};
			for (const e of this.visible(task.id)) {
				const x0 = this.x(e.t);
				const x1 = Math.max(this.x(e.t + e.dur), x0 + 1);
				if (x1 < GUTTER || x0 > this.width) continue;
				if (x1 - x0 < RUN_PX) {
					if (runX1 >= 0 && x0 <= runX1 + 1) {
						runX1 = Math.max(runX1, x1);
					} else {
						flush();
						runX0 = x0;
						runX1 = x1;
					}
					continue;
				}
				flush();
				c.fillStyle = col.fill;
				c.fillRect(x0, y, x1 - x0, h);
				if (e.src === "timer") {
					c.fillStyle = th.fg2;
					c.fillRect(x0, y - 4, 1, 4);
				}
				if (x1 - x0 > 30) {
					c.fillStyle = col.on;
					this.clipText(c, e.sig_name, Math.max(x0, GUTTER) + 4, y + h / 2, Math.min(x1, this.width) - Math.max(x0, GUTTER) - 8);
				}
			}
			flush();
			if (sel && sel.task === task.id) {
				const x0 = this.x(sel.t);
				const x1 = Math.max(this.x(sel.t + sel.dur), x0 + 1);
				c.strokeStyle = th.primary;
				c.lineWidth = 2;
				c.strokeRect(x0 - 1, y - 2, x1 - x0 + 2, h + 4);
				c.lineWidth = 1;
			}
		});
	}

	rowCenter(task) {
		const r = this.rowOf.get(task);
		return r == null ? null : AXIS + r * ROW + ROW / 2;
	}

	arrow(c, x0, y0, x1, y1) {
		c.beginPath();
		c.moveTo(x0, y0);
		c.lineTo(x1, y1);
		c.stroke();
		const a = Math.atan2(y1 - y0, x1 - x0);
		c.beginPath();
		c.moveTo(x1, y1);
		c.lineTo(x1 - 6 * Math.cos(a - 0.4), y1 - 6 * Math.sin(a - 0.4));
		c.lineTo(x1 - 6 * Math.cos(a + 0.4), y1 - 6 * Math.sin(a + 0.4));
		c.closePath();
		c.fill();
	}

	paintArrows(c, th) {
		const posts = this.trace.posts;
		const sel = this.selected >= 0 ? this.trace.execs[this.selected] : null;
		const hot = new Set();
		if (sel) {
			if (sel.post >= 0) hot.add(sel.post);
			for (const p of posts) {
				if (p.src === sel.task && p.t >= sel.t && p.t <= sel.t + sel.dur) hot.add(p.i);
			}
		}
		const draw = (p, color) => {
			if (p.exec < 0) return;
			const e = this.trace.execs[p.exec];
			const y0 = this.rowCenter(p.src);
			const y1 = this.rowCenter(e.task);
			if (y0 == null || y1 == null) return;
			if (Math.max(p.t, e.t) < this.v0 || Math.min(p.t, e.t) > this.v1) return;
			c.strokeStyle = color;
			c.fillStyle = color;
			this.arrow(c, this.x(p.t), y0, this.x(e.t), y1);
		};
		c.lineWidth = 1;
		const lo = lowerBound(posts, this.v0 - (this.v1 - this.v0));
		const hi = lowerBound(posts, this.v1);
		// past a few hundred arrows the view is unreadable and slow; zoom in to see them
		if (hi - lo <= MAX_ARROWS) {
			for (let i = lo; i < hi; i++) {
				if (!hot.has(i)) draw(posts[i], th.arrow);
			}
		}
		c.lineWidth = 2;
		for (const i of hot) draw(posts[i], th.primary);
		c.lineWidth = 1;
	}

	// the board restarted here (verify R09); the time it took to boot is not in the trace
	paintRestarts(c, th) {
		const top = this.wrap.scrollTop + AXIS + 6;
		c.font = "12px " + th.sans;
		c.textBaseline = "middle";
		for (const r of this.trace.restarts || []) {
			const x = Math.round(this.x(r.t)) + 0.5;
			if (x < GUTTER || x > this.width) continue;
			c.strokeStyle = th.red;
			c.setLineDash([4, 3]);
			c.beginPath();
			c.moveTo(x, AXIS);
			c.lineTo(x, this.height);
			c.stroke();
			c.setLineDash([]);
			const label = "restart";
			const w = c.measureText(label).width + 12;
			c.fillStyle = th.red;
			c.fillRect(x + 2, top, w, 20);
			c.fillStyle = "#ffffff";
			c.fillText(label, x + 8, top + 10);
		}
	}

	paintMeasure(c, th) {
		if (!this.measure) return;
		const [a, b] = [Math.min(this.measure.t0, this.measure.t1), Math.max(this.measure.t0, this.measure.t1)];
		const x0 = this.x(a);
		const x1 = this.x(b);
		c.fillStyle = th.selected;
		c.fillRect(x0, AXIS, x1 - x0, this.height - AXIS);
		c.strokeStyle = th.primary;
		c.beginPath();
		c.moveTo(x0 + 0.5, AXIS);
		c.lineTo(x0 + 0.5, this.height);
		c.moveTo(x1 + 0.5, AXIS);
		c.lineTo(x1 + 0.5, this.height);
		c.stroke();
		const label = fmtDur(b - a);
		c.font = "12px " + th.mono;
		const w = c.measureText(label).width + 12;
		const top = this.wrap.scrollTop + AXIS + 6;
		c.fillStyle = th.primary;
		c.fillRect((x0 + x1) / 2 - w / 2, top, w, 20);
		c.fillStyle = "#ffffff";
		c.textBaseline = "middle";
		c.fillText(label, (x0 + x1) / 2 - w / 2 + 6, top + 10);
	}

	hit(mx, my) {
		const row = Math.floor((my - AXIS) / ROW);
		if (mx < GUTTER || row < 0 || row >= this.rows.length) return null;
		const task = this.rows[row].id;
		let best = null;
		let bestDist = 4;
		for (const e of this.visible(task)) {
			const x0 = this.x(e.t);
			const x1 = Math.max(this.x(e.t + e.dur), x0 + 1);
			const d = mx < x0 ? x0 - mx : mx > x1 ? mx - x1 : 0;
			if (d < bestDist) {
				best = e;
				bestDist = d;
				if (d === 0) break;
			}
		}
		return best;
	}

	pos(e) {
		const r = this.canvas.getBoundingClientRect();
		return [e.clientX - r.left, e.clientY - r.top];
	}

	onWheel(e) {
		if (!this.trace) return;
		const [mx] = this.pos(e);
		if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
			e.preventDefault();
			const dt = (e.deltaX / (this.width - GUTTER)) * (this.v1 - this.v0);
			this.setView(this.v0 + dt, this.v1 + dt);
			return;
		}
		if (e.ctrlKey || !e.shiftKey) {
			e.preventDefault();
			const f = Math.exp(e.deltaY * 0.0015);
			const at = mx > GUTTER ? this.t(mx) : (this.v0 + this.v1) / 2;
			this.setView(at - (at - this.v0) * f, at + (this.v1 - at) * f);
		}
	}

	onDown(e) {
		if (!this.trace || e.button !== 0) return;
		const [mx, my] = this.pos(e);
		if (mx < GUTTER) return;
		if (e.shiftKey) {
			this.measure = { t0: this.t(mx), t1: this.t(mx) };
			this.drag = { mode: "measure" };
		} else {
			this.drag = { mode: "pan", x: mx, y: my, v0: this.v0, v1: this.v1, moved: false };
			this.canvas.classList.add("dragging");
		}
		this.hideTip();
	}

	onMove(e) {
		if (!this.trace) return;
		const [mx, my] = this.pos(e);
		if (this.drag && this.drag.mode === "pan") {
			const dx = mx - this.drag.x;
			if (Math.abs(dx) > 2) this.drag.moved = true;
			const dt = (dx / (this.width - GUTTER)) * (this.drag.v1 - this.drag.v0);
			this.setView(this.drag.v0 - dt, this.drag.v1 - dt);
			return;
		}
		if (this.drag && this.drag.mode === "measure") {
			this.measure.t1 = this.t(mx);
			this.draw();
			return;
		}
		if (e.target !== this.canvas) return;
		const hit = this.hit(mx, my);
		if (hit) this.showTip(hit, mx, my);
		else this.hideTip();
	}

	onUp(e) {
		if (!this.drag) return;
		const drag = this.drag;
		this.drag = null;
		this.canvas.classList.remove("dragging");
		if (drag.mode === "pan" && !drag.moved && e.target === this.canvas) {
			const [mx, my] = this.pos(e);
			const hit = this.hit(mx, my);
			this.measure = null;
			this.select(hit ? hit.i : -1);
			if (this.onSelect) this.onSelect(hit ? hit.i : -1);
		}
	}

	showTip(e, mx, my) {
		const tip = this.tooltip;
		const cls = exeClass(e.dur);
		tip.innerHTML =
			`<div class="t-title">${esc(e.sig_name)}</div>` +
			`<dl class="kv">` +
			`<dt>task</dt><dd>${esc(this.trace.tasks.find((t) => t.id === e.task)?.name ?? e.task)}</dd>` +
			`<dt>from</dt><dd>${esc(e.src_name)}</dd>` +
			`<dt>start</dt><dd>${fmtTime(e.t, this.unit)}</dd>` +
			`<dt>exe</dt><dd class="${cls}">${fmtDur(e.dur)}</dd>` +
			`<dt>wait</dt><dd>${e.wait != null ? fmtDur(e.wait) : e.wait_ms_kernel != null ? e.wait_ms_kernel + " ms (kernel)" : "-"}</dd>` +
			`<dt>type</dt><dd>${esc(e.type)}</dd>` +
			(e.log ? `<dt>console</dt><dd>${e.log.length} line${e.log.length === 1 ? "" : "s"}</dd>` : "") +
			`</dl>`;
		tip.hidden = false;
		const pad = 14;
		let left = mx + pad;
		let top = my + pad;
		if (left + tip.offsetWidth > this.width) left = mx - tip.offsetWidth - pad;
		if (top + tip.offsetHeight > this.wrap.scrollTop + this.wrap.clientHeight) top = my - tip.offsetHeight - pad;
		tip.style.left = left + "px";
		tip.style.top = top + "px";
	}

	hideTip() {
		this.tooltip.hidden = true;
	}

	clearMeasure() {
		this.measure = null;
		this.draw();
	}
}
