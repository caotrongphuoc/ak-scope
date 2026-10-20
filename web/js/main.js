import { readPalette, taskColorIndex } from "./colors.js";
import { DetailView } from "./detail.js";
import { EventTable } from "./events.js";
import { esc, fmtDur, fmtTime } from "./format.js";
import { StatsView } from "./stats.js";
import { TimelineView } from "./timeline.js";

const $ = (id) => document.getElementById(id);

// per viewer preferences only, the page works without them
function pref(key, fallback) {
	try {
		return localStorage.getItem("akscope." + key) || fallback;
	} catch {
		return fallback;
	}
}

function savePref(key, value) {
	try {
		localStorage.setItem("akscope." + key, value);
	} catch {
		/* storage blocked */
	}
}

const state = {
	trace: null,
	tasks: [],
	visible: new Set(),
	unit: pref("unit", "ms"),
	theme: pref("theme", "dark"),
};

const timeline = new TimelineView($("timeline"), $("timeline-wrap"), $("tooltip"), {
	onSelect: (i) => selectExec(i, false),
	onView: (v0, v1) => {
		$("view-range").textContent = `${fmtTime(Math.max(v0, 0), state.unit)} to ${fmtTime(v1, state.unit)}  (${fmtDur(v1 - v0)})`;
	},
});

const events = new EventTable($("events-scroll"), $("events-spacer"), $("event-count"), {
	onPick: (it) => {
		if (it.exec >= 0) selectExec(it.exec, true);
		else timeline.reveal(it.t);
	},
});

const detail = new DetailView($("detail"), { onPick: (i) => selectExec(i, true) });
const stats = new StatsView($("stats"));

function selectExec(i, reveal) {
	timeline.select(i, reveal);
	detail.show(i);
	events.select(i);
}

function applyTheme(theme) {
	state.theme = theme;
	document.documentElement.dataset.theme = theme;
	for (const b of $("theme-switch").querySelectorAll("button")) {
		b.classList.toggle("active", b.dataset.themeValue === theme);
	}
	savePref("theme", theme);
	if (state.trace) buildColors();
	timeline.draw();
}

function applyUnit(unit) {
	state.unit = unit;
	for (const b of $("unit-switch").querySelectorAll("button")) {
		b.classList.toggle("active", b.dataset.unit === unit);
	}
	savePref("unit", unit);
	timeline.setUnit(unit);
	events.setUnit(unit);
	detail.setUnit(unit);
	timeline.onView(timeline.v0, timeline.v1);
}

function buildColors() {
	const palette = readPalette();
	const colors = new Map();
	state.tasks.forEach((t, n) => colors.set(t.id, palette[taskColorIndex(t, n)]));
	timeline.colors = colors;
	state.colors = colors;
	renderTaskList();
}

function renderTaskList() {
	const counts = new Map();
	for (const e of state.trace.execs) counts.set(e.task, (counts.get(e.task) || 0) + 1);
	$("task-list").innerHTML = state.tasks.map((t) => {
		const off = state.visible.has(t.id) ? "" : " off";
		const pri = t.pri != null ? "p" + t.pri : "";
		return `<li class="${off.trim()}" data-task="${t.id}" title="${esc(t.name)}  id ${t.id} ${pri}">` +
			`<input type="checkbox" ${off ? "" : "checked"} tabindex="-1">` +
			`<span class="swatch" style="background:${state.colors.get(t.id).fill}"></span>` +
			`<span class="name">${esc(t.name)}</span>` +
			`<span class="count">${counts.get(t.id) || 0}</span></li>`;
	}).join("");
}

function updateRows() {
	timeline.setRows(state.tasks.filter((t) => state.visible.has(t.id)));
}

function showTrace(trace) {
	state.trace = trace;
	// higher priority first, the kernel runs those first
	state.tasks = [...trace.tasks].sort((a, b) => (b.pri ?? -1) - (a.pri ?? -1) || a.id - b.id);
	const active = new Set(trace.execs.map((e) => e.task));
	for (const p of trace.posts) active.add(p.des);
	// the timer task never runs a traced handler, keep its row off unless something posts to it
	state.visible = new Set(state.tasks.filter((t) => active.has(t.id)).map((t) => t.id));

	$("empty").hidden = true;
	$("layout").hidden = false;
	buildColors();
	timeline.setTrace(trace, state.colors);
	updateRows();
	events.setTrace(trace);
	detail.setTrace(trace);
	stats.setTrace(trace);
	$("console").textContent = trace.text || "(no console text)";
	renderInfo(trace);
	applyUnit(state.unit);
}

function renderInfo(tr) {
	const info = tr.info || {};
	const parts = [`<b>${esc(tr.name || "trace")}</b>`];
	if (info.app_ver) parts.push(`app ${info.app_ver.join(".")}`);
	if (info.cpu_hz) parts.push(`${info.cpu_hz / 1e6} MHz`);
	parts.push(fmtDur(tr.duration_us));
	parts.push(`${tr.execs.length} runs`);
	if (tr.dropped) parts.push(`<span class="warn">${tr.dropped} dropped</span>`);
	if (tr.stream && tr.stream.corrupt) parts.push(`<span class="warn">${tr.stream.corrupt} corrupt</span>`);
	if (!tr.has_symbols) parts.push(`<span class="warn">no names, open with --sym</span>`);
	if (!tr.info || !tr.info.cpu_hz) parts.push(`<span class="warn">no INFO, 32 MHz assumed</span>`);
	$("trace-info").innerHTML = parts.map((p) => `<span>${p}</span>`).join("");
}

function showError(msg) {
	$("load-error").textContent = msg;
	$("load-error").hidden = false;
}

async function openFile(file) {
	const res = await fetch("/api/decode?name=" + encodeURIComponent(file.name), {
		method: "POST",
		body: await file.arrayBuffer(),
	});
	const body = await res.json().catch(() => ({ error: "bad reply from server" }));
	if (!res.ok || body.error) {
		showError(body.error || "cannot open " + file.name);
		return;
	}
	if (!body.execs.length && !body.posts.length) {
		showError(file.name + " has no trace records. Was the firmware built with akscope build?");
		return;
	}
	$("load-error").hidden = true;
	showTrace(body);
}

async function loadInitial() {
	const res = await fetch("/api/trace");
	if (res.status === 200) showTrace(await res.json());
}

// controls

for (const id of ["btn-open", "btn-open-empty"]) $(id).addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", (e) => {
	if (e.target.files[0]) openFile(e.target.files[0]);
	e.target.value = "";
});

document.addEventListener("dragover", (e) => {
	e.preventDefault();
	$("empty").classList.add("drop");
});
document.addEventListener("dragleave", () => $("empty").classList.remove("drop"));
document.addEventListener("drop", (e) => {
	e.preventDefault();
	$("empty").classList.remove("drop");
	if (e.dataTransfer.files[0]) openFile(e.dataTransfer.files[0]);
});

$("theme-switch").addEventListener("click", (e) => {
	const b = e.target.closest("button");
	if (b) applyTheme(b.dataset.themeValue);
});
$("unit-switch").addEventListener("click", (e) => {
	const b = e.target.closest("button");
	if (b) applyUnit(b.dataset.unit);
});
$("btn-fit").addEventListener("click", () => timeline.fit());

$("task-list").addEventListener("click", (e) => {
	const li = e.target.closest("li[data-task]");
	if (!li) return;
	const id = +li.dataset.task;
	if (state.visible.has(id)) state.visible.delete(id);
	else state.visible.add(id);
	renderTaskList();
	updateRows();
});
$("btn-all-tasks").addEventListener("click", () => {
	state.visible = new Set(state.tasks.map((t) => t.id));
	renderTaskList();
	updateRows();
});

$("event-filter").addEventListener("input", (e) => events.setFilter(e.target.value));
for (const box of document.querySelectorAll(".events-tools input[data-kind]")) {
	box.addEventListener("change", () => events.setKind(box.dataset.kind, box.checked));
}

for (const tab of document.querySelectorAll(".tab")) {
	tab.addEventListener("click", () => {
		for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t === tab);
		for (const body of document.querySelectorAll(".tab-body")) body.hidden = body.dataset.body !== tab.dataset.tab;
		if (tab.dataset.tab === "events") events.render(true);
	});
}

document.addEventListener("keydown", (e) => {
	if (!state.trace || e.target.closest("input")) return;
	const mid = (timeline.v0 + timeline.v1) / 2;
	const half = (timeline.v1 - timeline.v0) / 2;
	if (e.key === "f") timeline.fit();
	else if (e.key === "Escape") {
		timeline.clearMeasure();
		selectExec(-1, false);
	} else if (e.key === "+" || e.key === "=") timeline.setView(mid - half / 1.5, mid + half / 1.5);
	else if (e.key === "-") timeline.setView(mid - half * 1.5, mid + half * 1.5);
});

applyTheme(state.theme);
loadInitial();
