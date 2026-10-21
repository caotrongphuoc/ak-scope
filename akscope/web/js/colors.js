// task colors come from app-tokens.css (on top of the Figma tokens), so a theme change applies to the canvas too

const N_COLORS = 8;
const TIMER_TASK_ID = 0;

export function cssVar(name) {
	return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function taskColorIndex(task, order) {
	if (task.id === TIMER_TASK_ID) return "timer";
	return order % N_COLORS;
}

export function readPalette() {
	const p = {};
	for (let i = 0; i < N_COLORS; i++) {
		p[i] = { fill: cssVar("--task-" + i), on: cssVar("--task-" + i + "-on") };
	}
	p.timer = { fill: cssVar("--task-timer"), on: cssVar("--task-timer-on") };
	return p;
}

export function readTheme() {
	return {
		surface: cssVar("--bg-surface"),
		rowAlt: cssVar("--bg-row-alt"),
		border: cssVar("--border-base"),
		borderSubtle: cssVar("--border-subtle"),
		fg: cssVar("--fg-base"),
		fg2: cssVar("--fg-secondary"),
		primary: cssVar("--primary"),
		selected: cssVar("--bg-selected"),
		arrow: cssVar("--arrow"),
		red: cssVar("--red"),
		yellow: cssVar("--yellow"),
		mono: cssVar("--font-mono"),
		sans: cssVar("--font-sans"),
	};
}
