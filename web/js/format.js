// time values in the trace JSON are microseconds from the first record

export const UNITS = {
	us: { div: 1, digits: 1, label: "us" },
	ms: { div: 1e3, digits: 3, label: "ms" },
	s: { div: 1e6, digits: 6, label: "s" },
};

export function fmtTime(us, unit) {
	const u = UNITS[unit];
	return (us / u.div).toFixed(u.digits) + " " + u.label;
}

// durations pick their own unit so short handlers stay readable
export function fmtDur(us) {
	if (us == null) return "-";
	if (us < 1000) return us.toFixed(us < 10 ? 2 : 1) + " us";
	if (us < 1e6) return (us / 1e3).toFixed(3) + " ms";
	return (us / 1e6).toFixed(3) + " s";
}

// axis tick step: 1, 2 or 5 times a power of ten, about `target` us
export function niceStep(target) {
	const p = Math.pow(10, Math.floor(Math.log10(target)));
	for (const m of [1, 2, 5, 10]) {
		if (m * p >= target) return m * p;
	}
	return 10 * p;
}

// unit from the tick step, and just enough decimals to tell two ticks apart
export function fmtTick(us, step) {
	const decimals = (exp) => Math.max(0, Math.ceil(exp - Math.log10(step) - 1e-9));
	if (step >= 1e5) return (us / 1e6).toFixed(decimals(6)) + " s";
	if (step >= 100) return (us / 1e3).toFixed(decimals(3)) + " ms";
	return us.toFixed(0) + " us";
}

// exe time thresholds from the AK run to completion guideline (ak://guide/kernel-task-log)
export const EXE_WARN_US = 10e3;
export const EXE_ERROR_US = 50e3;

export function exeClass(us) {
	if (us >= EXE_ERROR_US) return "too-slow";
	if (us >= EXE_WARN_US) return "slow";
	return "";
}

export function esc(s) {
	return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}
