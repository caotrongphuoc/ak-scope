import { esc, exeClass, fmtDur, fmtTime } from "./format.js";

// right panel: the selected handler run, where its message came from and what it posted
export class DetailView {
	constructor(root, { onPick }) {
		this.root = root;
		this.onPick = onPick;
		this.unit = "ms";
		root.addEventListener("click", (e) => {
			const b = e.target.closest("button[data-exec]");
			if (b) this.onPick(+b.dataset.exec);
		});
	}

	setTrace(trace) {
		this.trace = trace;
		this.taskName = new Map(trace.tasks.map((t) => [t.id, t.name]));
		this.show(-1);
	}

	setUnit(unit) {
		this.unit = unit;
		this.show(this.current ?? -1);
	}

	show(index) {
		this.current = index;
		if (index < 0) {
			this.root.innerHTML = `<p class="muted">Click a block on the timeline or a row below.</p>`;
			return;
		}
		const tr = this.trace;
		const e = tr.execs[index];
		const name = (id) => this.taskName.get(id) ?? String(id);
		const post = e.post >= 0 ? tr.posts[e.post] : null;
		const posted = tr.posts.filter((p) => p.src === e.task && p.t >= e.t && p.t <= e.t + e.dur);
		const timers = tr.timers.filter((t) => t.t >= e.t && t.t <= e.t + e.dur);

		let from = esc(e.src_name);
		if (post) {
			// the handler that made the post, if it was traced
			const parent = tr.execs.find((x) => x.task === post.src && x.t <= post.t && post.t <= x.t + x.dur);
			if (parent) from = `<button class="btn-text" data-exec="${parent.i}">${esc(e.src_name)}</button>`;
		}

		let html =
			`<h2>${esc(e.sig_name)}</h2>` +
			`<p class="sub">${esc(name(e.task))}</p>` +
			`<dl class="kv">` +
			`<dt>start</dt><dd>${fmtTime(e.t, this.unit)}</dd>` +
			`<dt>exe</dt><dd class="${exeClass(e.dur)}">${fmtDur(e.dur)}</dd>` +
			`<dt>wait</dt><dd>${e.wait != null ? fmtDur(e.wait) : "-"}</dd>` +
			`<dt>kernel wait</dt><dd>${e.wait_ms_kernel != null ? e.wait_ms_kernel + " ms" : "-"}</dd>` +
			`<dt>type</dt><dd>${esc(e.type)}</dd>` +
			`<dt>signal</dt><dd>${e.sig}</dd>` +
			`<dt>from</dt><dd>${from}</dd>` +
			`</dl>`;

		html += `<h3>Posted in this handler (${posted.length})</h3>`;
		if (posted.length) {
			html += `<ul class="link-list">` + posted.map((p) => {
				const label = `${esc(name(p.des))} / ${esc(p.sig_name)}`;
				return p.exec >= 0
					? `<li><button data-exec="${p.exec}">${label}</button></li>`
					: `<li>${label} <span class="muted">(not handled in the trace)</span></li>`;
			}).join("") + `</ul>`;
		} else {
			html += `<p class="muted">None</p>`;
		}

		if (timers.length) {
			html += `<h3>Timers in this handler (${timers.length})</h3><ul class="link-list">` +
				timers.map((t) => `<li>${t.kind === "TIMER_SET" ? "set" : "remove"} ${esc(name(t.des))} / ${esc(t.sig_name)}` +
					(t.kind === "TIMER_SET" ? ` <span class="muted">${t.duty_ms} ms ${t.periodic ? "periodic" : "one shot"}</span>` : "") +
					`</li>`).join("") + `</ul>`;
		}
		this.root.innerHTML = html;
	}
}
