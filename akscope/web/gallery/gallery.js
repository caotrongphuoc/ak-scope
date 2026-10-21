import { ENTRIES } from "./entries.js";

const root = document.getElementById("gallery");
const groups = new Map();
ENTRIES.forEach((e, n) => {
	const key = e.group + " " + e.mode;
	if (!groups.has(key)) {
		const row = document.createElement("section");
		row.className = "g-row";
		row.dataset.theme = e.mode;
		root.append(row);
		groups.set(key, row);
	}
	const cell = document.createElement("div");
	cell.className = "g-cell";
	cell.dataset.n = n;
	const pad = e.pad ? ` style="padding:${e.pad}px"` : "";
	cell.innerHTML = `<div class="g-target"${pad}>${e.html}</div><div class="g-name">${e.ref}</div>`;
	groups.get(key).append(cell);
});
window.GALLERY = ENTRIES.map((e, n) => ({ n, page: e.page, ref: e.ref, mode: e.mode, group: e.group, pad: e.pad || 0, ignore: e.ignore || [] }));
// text makes cells a fraction of a pixel wide, which would move every following cell off the
// pixel grid; whole pixel cells keep each target where the Figma reference is drawn
document.fonts.ready.then(() => {
	for (const cell of root.querySelectorAll(".g-cell")) {
		cell.style.width = Math.ceil(cell.getBoundingClientRect().width) + "px";
	}
	window.GALLERY_READY = true;
});
