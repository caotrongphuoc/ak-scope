import { readPalette, taskColorIndex } from "./colors.js";
import { DetailView } from "./detail.js";
import { EventTable } from "./events.js";
import { FlowView } from "./flow.js";
import { esc, fmtDur, fmtTime } from "./format.js";
import { StatsView } from "./stats.js";
import { TimelineView } from "./timeline.js";
import { VerifyView } from "./verify.js";

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

const EVENTS_REFRESH_MS = 1000;

const state = {
	trace: null,
	tasks: [],
	visible: new Set(),
	unit: pref("unit", "ms"),
	theme: pref("theme", "dark"),
	page: pref("page", "trace"),
	config: {},
	live: { state: "idle" },
	follow: true,
	eventsDirty: false,
	lastEvents: 0,
};

const timeline = new TimelineView($("timeline"), $("timeline-wrap"), $("tooltip"), {
	onSelect: (i) => selectExec(i, false),
	onView: (v0, v1) => {
		$("view-range").textContent = `${fmtTime(Math.max(v0, 0), state.unit)} to ${fmtTime(v1, state.unit)}  (${fmtDur(v1 - v0)})`;
		flow.setView(v0, v1);
		if (state.page === "flow") scheduleFlow();
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
const flow = new FlowView($("flow"), $("flow-note"), { onPick: (i) => selectExec(i, true) });
const verifyView = new VerifyView($("verify"), $("verify-note"), $("verify-badge"), $("btn-verify-copy"), {
	onPick: (f) => {
		if (f.exec >= 0) selectExec(f.exec, true);
		else timeline.reveal(f.t_us);
	},
});

let verifyTimer = null;
async function runVerify() {
	clearTimeout(verifyTimer);
	const source = state.trace && state.trace.live ? "live" : "current";
	const r = await postJson("/api/verify", { source });
	if (r.error) {
		$("verify-note").textContent = r.error;
		return;
	}
	verifyView.show(r);
	// the static rules run once in the background; ask again until they are in
	if (r.static === "running") verifyTimer = setTimeout(runVerify, 2000);
}

let flowPending = false;
function scheduleFlow() {
	if (flowPending) return;
	flowPending = true;
	requestAnimationFrame(() => {
		flowPending = false;
		flow.render();
	});
}

function selectExec(i, reveal) {
	if (reveal && i >= 0) setFollow(false);
	timeline.select(i, reveal);
	detail.show(i);
	events.select(i);
	flow.select(i);
	if (state.page === "flow") scheduleFlow();
}

function applyTheme(theme) {
	state.theme = theme;
	document.documentElement.dataset.theme = theme;
	for (const b of $("theme-switch").querySelectorAll("button")) {
		b.classList.toggle("is-active", b.dataset.themeValue === theme);
	}
	savePref("theme", theme);
	if (state.trace) buildColors();
	timeline.draw();
}

function applyUnit(unit) {
	state.unit = unit;
	for (const b of $("unit-switch").querySelectorAll("button")) {
		b.classList.toggle("is-active", b.dataset.unit === unit);
	}
	savePref("unit", unit);
	timeline.setUnit(unit);
	events.setUnit(unit);
	detail.setUnit(unit);
	flow.setUnit(unit);
	verifyView.setUnit(unit);
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
	// a Figma Tree item per task: the checkbox shows the timeline row, the count is a Badge
	$("task-list").innerHTML = state.tasks.map((t) => {
		const on = state.visible.has(t.id);
		const pri = t.pri != null ? "p" + t.pri : "";
		return `<div class="tree-item${on ? "" : " is-off"}" data-task="${t.id}" title="${esc(t.name)}  id ${t.id} ${pri}">` +
			`<span class="checkbox${on ? " is-checked" : ""}"><span class="checkbox-box"></span></span>` +
			`<span class="task-swatch" style="background:${state.colors.get(t.id).fill}"></span>` +
			`<span class="tree-title">${esc(t.name)}</span>` +
			`<span class="badge">${counts.get(t.id) || 0}</span></div>`;
	}).join("");
}

function updateRows() {
	timeline.setRows(state.tasks.filter((t) => state.visible.has(t.id)));
}

function sortTasks(tasks) {
	// higher priority first, the kernel runs those first
	return [...tasks].sort((a, b) => (b.pri ?? -1) - (a.pri ?? -1) || a.id - b.id);
}

function activeTasks(trace) {
	const active = new Set(trace.execs.map((e) => e.task));
	for (const p of trace.posts) active.add(p.des);
	return active;
}

function showTrace(trace) {
	state.trace = trace;
	state.tasks = sortTasks(trace.tasks);
	// the timer task never runs a traced handler, keep its row off unless something posts to it
	const active = activeTasks(trace);
	state.active = active;
	state.visible = new Set(state.tasks.filter((t) => active.has(t.id)).map((t) => t.id));

	$("empty").hidden = true;
	$("layout").hidden = false;
	showPage(state.page);
	buildColors();
	timeline.setTrace(trace, state.colors);
	updateRows();
	events.setTrace(trace);
	detail.setTrace(trace);
	stats.setTrace(trace);
	flow.setTrace(trace, state.tasks);
	$("console").textContent = trace.text || "(no console text)";
	renderInfo(trace);
	applyUnit(state.unit);
	$("btn-follow").hidden = !trace.live;
	verifyView.clear();
	if (state.page === "verify") runVerify();
}

// live capture: add what the server sent since the last delta
function applyDelta(d) {
	const tr = state.trace;
	if (!tr) return;
	tr.execs.push(...d.execs);
	tr.posts.push(...d.posts);
	for (const e of d.execs) {
		if (e.post >= 0 && tr.posts[e.post]) tr.posts[e.post].exec = e.i;
	}
	tr.timers.push(...d.timers);
	tr.pools.push(...d.pools);
	tr.marks.push(...d.marks);
	tr.text += d.text;
	tr.duration_us = d.duration_us;
	tr.dropped = d.dropped;
	tr.info = d.info;
	tr.cpu_hz = d.cpu_hz;
	tr.stream = d.stream;

	let rowsChanged = false;
	if (d.tasks) {
		const known = new Set(tr.tasks.map((t) => t.id));
		if (d.tasks.some((t) => !known.has(t.id))) {
			tr.tasks = d.tasks;
			state.tasks = sortTasks(tr.tasks);
			buildColors();
			detail.setTrace(tr);
			flow.setTrace(tr, state.tasks);
			rowsChanged = true;
		}
	}
	// a task gets its row the first time it runs or is posted to, as when a file is opened
	for (const id of [...d.execs.map((e) => e.task), ...d.posts.map((p) => p.des)]) {
		if (!state.active.has(id)) {
			state.active.add(id);
			state.visible.add(id);
			rowsChanged = true;
		}
	}
	if (rowsChanged) {
		renderTaskList();
		updateRows();
	}
	if (d.task_stats) {
		tr.task_stats = d.task_stats;
		tr.signal_stats = d.signal_stats;
		stats.setTrace(tr);
		renderTaskList();
	}
	if (d.text) $("console").textContent = tr.text;

	timeline.append(d.execs);
	if (state.follow) timeline.followEnd();
	state.eventsDirty = true;
	renderInfo(tr);
}

function refreshEvents(now) {
	if (state.eventsDirty && now - state.lastEvents > EVENTS_REFRESH_MS) {
		state.eventsDirty = false;
		state.lastEvents = now;
		events.setTrace(state.trace);
	}
	requestAnimationFrame(refreshEvents);
}
requestAnimationFrame(refreshEvents);

// the open trace in the top bar: its name, then the facts as Figma Tags; problems are red or yellow
function renderInfo(tr) {
	const info = tr.info || {};
	const tag = (text, color = "neutral") => `<span class="tag tag--${color}"><span>${text}</span></span>`;
	const parts = [`<span class="trace-name">${esc((tr.name || "trace").split("/").pop())}</span>`];
	if (info.app_ver) parts.push(tag(`app ${info.app_ver.join(".")}`));
	if (info.cpu_hz) parts.push(tag(`${info.cpu_hz / 1e6} MHz`));
	parts.push(tag(fmtDur(tr.duration_us)));
	parts.push(tag(`${tr.execs.length} runs`));
	if (tr.dropped) parts.push(tag(`${tr.dropped} dropped`, "red"));
	if (tr.stream && tr.stream.corrupt) parts.push(tag(`${tr.stream.corrupt} corrupt`, "red"));
	if (!tr.has_symbols) parts.push(tag("no names", "yellow"));
	if (!info.cpu_hz) parts.push(tag("no INFO, 32 MHz assumed", "yellow"));
	$("trace-info").innerHTML = parts.join("");
}

function showError(msg) {
	$("load-error-text").textContent = msg;
	$("load-error").hidden = false;
}

async function openFile(file) {
	const res = await fetch("/api/decode?name=" + encodeURIComponent(file.name), {
		method: "POST",
		headers: { "Content-Type": "application/octet-stream" },
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

// live controls

async function postJson(path, body) {
	const res = await fetch(path, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(body || {}),
	});
	return res.json().catch(() => ({ error: "bad reply from server" }));
}

async function loadPorts() {
	const ports = await (await fetch("/api/ports")).json();
	const sel = $("port-select");
	const want = sel.value || pref("port", "") || state.config.port || "";
	sel.innerHTML = ports.length
		? ports.map((p) => `<option value="${esc(p.device)}">${esc(p.device)}  ${esc(p.description || "")}</option>`).join("")
		: `<option value="">no serial port</option>`;
	if (ports.some((p) => p.device === want)) sel.value = want;
}

// the capture state as a Figma status badge: green recording, blue flashing, red error
function renderLive(st) {
	state.live = st;
	const el = $("live-status");
	const busy = st.state !== "idle";
	const error = st.state === "idle" && st.message;
	const dot = { recording: "dot--green", flashing: "dot--blue" }[st.state] || (error ? "dot--red" : "");
	el.className = "status" + (st.state === "recording" ? " is-recording" : "") + (error ? " is-error" : "");
	el.querySelector(".dot").className = "dot " + dot;
	const file = st.file ? st.file.split("/").pop() : "";
	el.querySelector(".live-text").textContent =
		st.message || (st.state === "recording" ? "recording " + file : st.state === "flashing" ? "flashing" : "idle");
	el.title = st.message || st.file || "";
	$("btn-record").hidden = st.state === "recording";
	$("btn-record").disabled = st.state === "flashing";
	$("btn-stop").hidden = st.state !== "recording";
	$("btn-flash").disabled = st.state === "flashing";
	$("port-select").disabled = busy;
	$("baud-select").disabled = busy;
}

function connectLive() {
	const es = new EventSource("/api/live");
	es.addEventListener("status", (e) => renderLive(JSON.parse(e.data)));
	es.addEventListener("snapshot", (e) => {
		setFollow(true);
		showTrace(JSON.parse(e.data));
	});
	es.addEventListener("delta", (e) => applyDelta(JSON.parse(e.data)));
	// EventSource reconnects by itself after a server restart
}

function setFollow(on) {
	state.follow = on;
	$("btn-follow").classList.toggle("btn--primary", on);
	$("btn-follow").classList.toggle("btn--secondary", !on);
	if (on && state.trace && state.trace.live) timeline.followEnd();
}

function liveTarget() {
	const port = $("port-select").value;
	const baud = +$("baud-select").value;
	savePref("port", port);
	savePref("baud", String(baud));
	return { port, baud };
}

$("btn-record").addEventListener("click", async () => {
	const r = await postJson("/api/record/start", liveTarget());
	if (r.error) renderLive({ state: "idle", message: r.error });
});
$("btn-stop").addEventListener("click", () => postJson("/api/record/stop"));
// flashing replaces the firmware on the kit, so it asks first
$("btn-flash").addEventListener("click", () => {
	const { port, baud } = liveTarget();
	$("flash-text").textContent = `${state.config.flash_target} is written to the kit on ${port || "the serial port"} ` +
		`with ak-flash, then the trace is recorded from boot at ${baud} baud.`;
	$("flash-dialog").hidden = false;
	$("btn-flash-go").focus();
});
$("btn-flash-cancel").addEventListener("click", () => ($("flash-dialog").hidden = true));
$("flash-dialog").addEventListener("keydown", (e) => {
	if (e.key === "Escape") $("flash-dialog").hidden = true;
});
$("btn-flash-go").addEventListener("click", async () => {
	$("flash-dialog").hidden = true;
	const r = await postJson("/api/flash", liveTarget());
	if (r.error) renderLive({ state: "idle", message: r.error });
});
$("port-select").addEventListener("focus", loadPorts);
$("btn-follow").addEventListener("click", () => setFollow(!state.follow));

async function init() {
	applyTheme(state.theme);
	showPage(state.page);
	state.config = await (await fetch("/api/config")).json();
	$("brand-version").textContent = state.config.version ? "v" + state.config.version : "";
	if (state.config.live) {
		$("live-controls").hidden = false;
		$("btn-flash").hidden = !state.config.flash_target;
		$("btn-flash").title = state.config.flash_target ? "flash " + state.config.flash_target + ", then record from boot" : "";
		$("baud-select").value = pref("baud", String(state.config.baud || 115200));
		await loadPorts();
		connectLive();
	}
	const res = await fetch("/api/trace");
	if (res.status === 200 && !(state.trace && state.trace.live)) showTrace(await res.json());
}

// controls

for (const id of ["btn-open", "btn-open-empty"]) $(id).addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", (e) => {
	if (e.target.files[0]) openFile(e.target.files[0]);
	e.target.value = "";
});

document.addEventListener("dragover", (e) => {
	e.preventDefault();
	if (!$("empty").hidden) $("empty").classList.add("drop");
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
$("btn-fit").addEventListener("click", () => {
	setFollow(false);
	timeline.fit();
});
// zoom or pan by hand stops following the newest records
$("timeline").addEventListener("mousedown", () => setFollow(false));
$("timeline").addEventListener("wheel", (e) => {
	if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) setFollow(false);
}, { passive: true });

$("task-list").addEventListener("click", (e) => {
	const li = e.target.closest("[data-task]");
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
for (const box of document.querySelectorAll("input[data-kind]")) {
	box.addEventListener("change", () => events.setKind(box.dataset.kind, box.checked));
}
$("btn-verify").addEventListener("click", runVerify);
$("btn-export-csv").addEventListener("click", () => events.export("csv"));
$("btn-export-json").addEventListener("click", () => events.export("json"));

function showPage(page) {
	if (!document.querySelector(`.page[data-page="${page}"]`)) page = "trace";
	state.page = page;
	savePref("page", page);
	for (const b of document.querySelectorAll(".nav-item[data-page]")) b.classList.toggle("is-active", b.dataset.page === page);
	for (const p of document.querySelectorAll(".page[data-page]")) p.hidden = p.dataset.page !== page;
	if (!state.trace) return;
	if (page === "trace") timeline.draw();
	if (page === "events") events.render(true);
	if (page === "flow") flow.render();
	if (page === "verify" && !verifyView.result) runVerify();
}

$("navside").addEventListener("click", (e) => {
	const b = e.target.closest(".nav-item[data-page]");
	if (b) showPage(b.dataset.page);
});

document.addEventListener("keydown", (e) => {
	if (!state.trace || e.target.closest("input, select")) return;
	const mid = (timeline.v0 + timeline.v1) / 2;
	const half = (timeline.v1 - timeline.v0) / 2;
	if (e.key === "f") {
		setFollow(false);
		timeline.fit();
	} else if (e.key === "Escape") {
		timeline.clearMeasure();
		selectExec(-1, false);
	} else if (e.key === "+" || e.key === "=") timeline.setView(mid - half / 1.5, mid + half / 1.5);
	else if (e.key === "-") timeline.setView(mid - half * 1.5, mid + half * 1.5);
});

init();
