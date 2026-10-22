import { esc, fmtTime } from "./format.js";

const RULES = {
	S01: "framework unchanged", S02: "handler blocks", S03: "IO outside io_cfg.c", S04: "task_ prefix",
	S05: "user signal range", S06: "task priority", S07: "posted, not handled", S08: "dead signal",
	S09: "common payload", S10: "global shared by tasks", S11: "ISR bracket", S12: "dynamic msg in ISR",
	S13: "debug prints on a blocking console",
	R01: "handler run time", R02: "queue wait", R03: "pool use", R04: "message storm",
	R05: "timer drift", R06: "no case for message", R07: "trace incomplete", R08: "expected sequence",
};

export class VerifyView {
	constructor(root, note, badge, copyBtn, { onPick }) {
		this.root = root;
		this.note = note;
		this.badge = badge;
		this.copyBtn = copyBtn;
		this.onPick = onPick;
		this.unit = "ms";
		this.result = null;
		root.addEventListener("click", (e) => {
			const row = e.target.closest(".vrow[data-n]");
			if (!row) return;
			const f = this.result.findings[+row.dataset.n];
			if (f.exec >= 0 || f.t_us != null) this.onPick(f);
		});
		copyBtn.addEventListener("click", () => navigator.clipboard.writeText(this.reportText()));
	}

	setUnit(unit) {
		this.unit = unit;
		if (this.result) this.render();
	}

	clear() {
		this.result = null;
		this.root.innerHTML = "";
		this.badge.hidden = true;
		this.copyBtn.hidden = true;
	}

	where(f) {
		if (f.file) return f.line ? `${f.file}:${f.line}` : f.file;
		if (f.t_us != null) return "at " + fmtTime(f.t_us, this.unit);
		return "";
	}

	reportText() {
		const r = this.result;
		const lines = r.findings.map((f) => `${this.where(f) ? this.where(f) + ": " : ""}${f.severity} ${f.rule} ${f.message}`);
		lines.push(`${r.errors} errors, ${r.warnings} warnings`);
		return lines.join("\n") + "\n";
	}

	show(result) {
		this.result = result;
		this.render();
	}

	render() {
		const r = this.result;
		const parts = [`${r.errors} error${r.errors === 1 ? "" : "s"}, ${r.warnings} warning${r.warnings === 1 ? "" : "s"}`];
		if (r.static === "running") parts.push("static rules still running, results below are from the trace only");
		else if (r.static === "failed") parts.push("static rules failed: " + r.static_error);
		else if (r.static === "none") parts.push("start the server with --project for the static rules");
		this.note.textContent = parts.join(". ");
		this.badge.hidden = !(r.errors || r.warnings);
		this.badge.textContent = r.errors || r.warnings;
		this.badge.classList.toggle("badge--red", !!r.errors);
		this.badge.classList.toggle("badge--yellow", !r.errors);
		this.copyBtn.hidden = !r.findings.length;
		if (!r.findings.length) {
			this.root.innerHTML = `<div class="empty"><span class="icon" style="--icon:url(/icons/shield-tick.svg)"></span>` +
				`<div class="empty-text"><span class="empty-title">No findings</span>` +
				`<span class="empty-desc">The trace keeps every rule that was checked.</span></div></div>`;
			return;
		}
		const head = `<div class="vrow vrow--head verify-cols"><span>Severity</span><span>Rule</span><span>Message</span><span>Where</span></div>`;
		this.root.innerHTML = head + `<div class="verify-list">` + r.findings.map((f, n) => {
			const link = f.exec >= 0 || f.t_us != null ? " is-link" : "";
			const color = { error: "red", warning: "yellow" }[f.severity] || "blue";
			return `<div class="vrow verify-cols${link}" data-n="${n}">` +
				`<span><span class="tag tag--${color}"><span>${esc(f.severity)}</span></span></span>` +
				`<span title="${esc(RULES[f.rule] || "")}">${esc(f.rule)}</span>` +
				`<span title="${esc(f.message)}">${esc(f.message)}</span>` +
				`<span class="where" title="${esc(this.where(f))}">${esc(this.where(f))}</span>` +
				`</div>`;
		}).join("") + `</div>`;
	}
}
