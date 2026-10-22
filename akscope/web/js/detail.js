import { esc, exeClass, fmtDur, fmtTime } from "./format.js";

// right panel: the selected handler run, where its message came from and what it posted
export class DetailView {
	constructor(root, { onPick }) {
		this.root = root;
		this.onPick = onPick;
		this.unit = "ms";
		root.addEventListener("click", (e) => {
			const b = e.target.closest("[data-exec]");
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
			this.root.innerHTML = `<p class="detail-empty">Click a block on the timeline, or a row of Events or Flow.</p>`;
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
			if (parent) {
				from = `<button class="btn btn--link" type="button" data-exec="${parent.i}"><span class="btn-label">${esc(e.src_name)}</span></button>`;
			}
		}

		let html =
			`<h2 class="detail-title">${esc(e.sig_name)}</h2>` +
			`<p class="detail-sub">${esc(name(e.task))}</p>` +
			`<dl class="kv">` +
			`<dt>start</dt><dd>${fmtTime(e.t, this.unit)}</dd>` +
			`<dt>exe</dt><dd class="${exeClass(e.dur)}">${fmtDur(e.dur)}</dd>` +
			`<dt>wait</dt><dd>${e.wait != null ? fmtDur(e.wait) : "-"}</dd>` +
			`<dt>kernel wait</dt><dd>${e.wait_ms_kernel != null ? e.wait_ms_kernel + " ms" : "-"}</dd>` +
			`<dt>type</dt><dd>${esc(e.type)}</dd>` +
			`<dt>signal</dt><dd>${e.sig}</dd>` +
			`<dt>from</dt><dd>${from}</dd>` +
			`</dl>`;

		// the posts and timers are the items of a Figma Dropdown menu: a handled post opens its run
		const item = (label, sub, exec) =>
			`<button class="menu-item${exec >= 0 ? "" : " is-disabled"}" type="button"${exec >= 0 ? ` data-exec="${exec}"` : ""}>` +
			`<span class="menu-label"><span>${label}${sub ? ` <span class="detail-empty">${sub}</span>` : ""}</span></span>` +
			(exec >= 0 ? `<span class="icon menu-more" style="--icon:url(/icons/chevron-right1.svg)"></span>` : "") + `</button>`;
		html += `<div class="detail-section">Posted in this handler (${posted.length})</div>`;
		html += posted.length
			? posted.map((p) => item(`${esc(name(p.des))} / ${esc(p.sig_name)}`, p.exec >= 0 ? "" : "not handled in the trace", p.exec)).join("")
			: `<p class="detail-empty">None</p>`;

		if (timers.length) {
			html += `<div class="detail-section" style="margin-top:16px">Timers in this handler (${timers.length})</div>` +
				timers.map((t) => item(`${t.kind === "TIMER_SET" ? "set" : "remove"} ${esc(name(t.des))} / ${esc(t.sig_name)}`,
					t.kind === "TIMER_SET" ? `${t.duty_ms} ms ${t.periodic ? "periodic" : "one shot"}` : "", -1)).join("");
		}
		// the base kit's debug prints of this run (core/runlog.py); a blocking console holds the
		// handler while it sends them, 10 bits a character
		if (e.log && e.log.length) {
			const baud = (tr.header || {}).baud;
			const chars = e.log.reduce((n, l) => n + l.length + 1, 0);
			const cost = baud ? ` <span class="detail-empty">${chars} characters, ${fmtDur(chars * 10 * 1e6 / baud)} at ${baud} baud</span>` : "";
			html += `<div class="detail-section" style="margin-top:16px">Console in this run${cost}</div>` +
				`<pre class="run-log">${e.log.map(esc).join("\n")}</pre>`;
		}
		this.root.innerHTML = html;
	}
}
