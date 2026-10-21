// What the gallery shows: each entry is one component variant of the Figma file, named by its
// page and symbol name, with the HTML that must look the same. tests/figma/compare.py reads
// this list from the page and compares every entry with the variant drawn from the .fig file.

const icon = (name, style = "") => `<span class="icon" style="--icon:url(../icons/${name}.svg)${style}"></span>`;
// the text box of the Figma instance has a fixed width (45 for "Button"); a real label is as wide as its text
const label = (text, width) => `<span class="btn-label" style="width:${width}px">${text}</span>`;

function button(type, content, danger, state) {
	const cls = ["btn"];
	const mod = { Primary: "btn--primary", Secondary: "btn--secondary", Tertiary: "btn--tertiary", Link: "btn--link",
		Icon_btn: "btn--icon btn--square", Big_icon_btn: "btn--icon btn--icon-lg" }[type];
	cls.push(mod);
	if (danger === "Yes") cls.push("btn--danger");
	if (content === "No" && (type === "Primary" || type === "Secondary")) cls.push("btn--square");
	if (state === "Hover") cls.push("is-hover");
	if (state === "Disabled") cls.push("is-disabled");
	let inner;
	if (type === "Link") inner = label("Button", 45);
	else if (content === "Yes") inner = `${icon("trash")}${label("Button", 45)}${icon("edit2")}`;
	else inner = icon("trash");
	return `<button class="${cls.join(" ")}" type="button">${inner}</button>`;
}

const BUTTONS = [
	["Primary", "Yes", "No"], ["Primary", "Yes", "Yes"], ["Primary", "No", "No"], ["Primary", "No", "Yes"],
	["Secondary", "Yes", "No"], ["Secondary", "No", "No"],
	["Tertiary", "Yes", "No"], ["Tertiary", "Yes", "Yes"],
	["Link", "Yes", "No"],
	["Icon_btn", "No", "No"], ["Icon_btn", "No", "Yes"], ["Big_icon_btn", "No", "No"],
];

export const ENTRIES = [];
for (const mode of ["dark", "light"]) {
	for (const [type, content, danger] of BUTTONS) {
		for (const state of ["Default", "Hover", "Disabled"]) {
			ENTRIES.push({
				group: "Button",
				page: "Button",
				ref: `Type=${type}, Content=${content}, Danger=${danger}, State=${state}`,
				mode,
				html: button(type, content, danger, state),
			});
		}
	}
}

for (const mode of ["dark", "light"]) {
	for (const [color, disable] of [["blue", "No"], ["primary", "No"], ["green", "No"], ["yellow", "No"],
		["gray", "No"], ["gray", "Yes"], ["red", "No"]]) {
		const cls = ["badge"];
		if (color !== "gray") cls.push("badge--" + color);
		if (disable === "Yes") cls.push("is-disabled");
		ENTRIES.push({ group: "Badge", page: "Badge", ref: `Color=${color}, Disable=${disable}, Type=Count`, mode,
			html: `<span class="${cls.join(" ")}">99</span>` });
	}
	for (const color of ["red", "gray", "green", "yellow", "blue"]) {
		ENTRIES.push({ group: "Badge", page: "Badge", ref: `Color=${color}, Disable=No, Type=Dot`, mode, pad: 2,
			html: `<span class="dot${color === "gray" ? "" : " dot--" + color}"></span>` });
	}
	for (const [type, color] of [["Error", "red"], ["Success", "green"], ["processing", "blue"], ["warning", "yellow"]]) {
		ENTRIES.push({ group: "Badge", page: "Badge", ref: `Type=${type}`, mode,
			html: `<span class="status"><span class="dot dot--${color}"></span><span style="width:43px">Status</span></span>` });
	}
}

const TAG_COLORS = ["neutral", "Orange", "Red", "Indigo", "Pink", "Blue", "Purple", "turquoise", "green", "lime",
	"Yellow", "Sky", "Maroon"];
for (const mode of ["dark", "light"]) {
	for (const color of TAG_COLORS) {
		for (const [disable, hover] of [["False", "False"], ["False", "True"], ["True", "False"]]) {
			const cls = ["tag", "tag--" + color.toLowerCase()];
			if (hover === "True") cls.push("is-hover");
			if (disable === "True") cls.push("is-disabled");
			ENTRIES.push({ group: "Tag", page: "Tag", ref: `Color=${color}, Disable=${disable}, Hover=${hover}`, mode,
				html: `<span class="${cls.join(" ")}">${icon("danger")}<span style="width:25px">Text</span>` +
					`${icon("x", "")}</span>`.replace('class="icon" style="--icon:url(../icons/x.svg)"',
						'class="icon tag-close" style="--icon:url(../icons/x.svg)"') });
		}
	}
	for (const [variant, cls] of [["Default", ""], ["Variant2", " is-hover"]]) {
		ENTRIES.push({ group: "Tag", page: "Tag", ref: `Property 1=${variant}`, mode,
			html: `<span class="tag-add${cls}">${icon("add")}<span style="width:45px">Newtag</span></span>` });
	}
}

function checkbox(state, selected, indeterminate, text) {
	const cls = ["checkbox"];
	if (state === "Hover") cls.push("is-hover");
	if (state === "Disable") cls.push("is-disabled");
	if (selected === "true") cls.push("is-checked");
	if (indeterminate === "true") cls.push("is-indeterminate");
	const label = text ? `<span style="width:30px">${text}</span>` : "";
	return `<label class="${cls.join(" ")}"><span class="checkbox-box"></span>${label}</label>`;
}

for (const mode of ["dark", "light"]) {
	for (const state of ["Default", "Hover", "Disable"]) {
		for (const [selected, ind] of [["false", "false"], ["true", "false"], ["false", "true"]]) {
			ENTRIES.push({ group: "Checkbox", page: "Check box", mode,
				ref: `state=${state}, selected=${selected}, Indeterminate=${ind}`, html: checkbox(state, selected, ind) });
		}
	}
	for (const [variant, state] of [["Default", "Default"], ["hover", "Hover"], ["Disable", "Disable"]]) {
		ENTRIES.push({ group: "Checkbox", page: "Check box", mode, ref: `Property 1=${variant}`,
			html: checkbox(state, "false", "false", "Item") });
	}
}

function toggle(checked, text, disabled, size) {
	const cls = ["switch"];
	if (size === "Small") cls.push("switch--small");
	if (text) cls.push("switch--text");
	if (checked) cls.push("is-checked");
	if (disabled) cls.push("is-disabled");
	// Figma sizes the label box to whole pixels
	const widths = { ON: size === "Small" ? 14 : 19, OFF: size === "Small" ? 18 : 24 };
	const word = checked ? "ON" : "OFF";
	const label = text ? `<span style="width:${widths[word]}px">${word}</span>` : "";
	return `<button class="${cls.join(" ")}" type="button" role="switch"><span class="switch-knob"></span>${label}</button>`;
}

for (const mode of ["dark", "light"]) {
	for (const size of ["Medium", "Small"]) {
		for (const text of [false, true]) {
			for (const checked of [false, true]) {
				for (const disabled of [false, true]) {
					ENTRIES.push({ group: "Switch", page: "Switch", mode,
						ref: `Checked=${checked}, Text=${text}, Icon=false, Loading=false, Disabled=${disabled}, Animation=false, Size=${size}`,
						html: toggle(checked, text, disabled, size) });
				}
			}
		}
	}
}

for (const mode of ["dark", "light"]) {
	ENTRIES.push({ group: "Segmented", page: "Segmented", ref: "Segmented", mode,
		html: `<div class="segmented">${button("Primary", "Yes", "No", "Default")}` +
			`${button("Tertiary", "Yes", "No", "Default")}${button("Tertiary", "Yes", "No", "Default")}</div>` });
}

// the leading icon of the Figma tabs is a VMS product icon; the gallery keeps its place empty
// and the compare leaves that square out
function tab(disable, active, hover, divide) {
	const cls = ["tab"];
	if (divide) cls.push("tab--divide");
	if (active) cls.push("is-active");
	if (hover) cls.push("is-hover");
	if (disable) cls.push("is-disabled");
	const inner = `<span class="icon" style="background:none"></span>` +
		`<span class="tab-label" style="width:29px">Tab</span><span class="badge">99</span>` +
		`${icon("chevron-down1").replace('class="icon"', 'class="icon tab-arrow"')}`;
	return `<button class="${cls.join(" ")}" type="button">${divide ? `<span class="tab-pill">${inner}</span>` : inner}</button>`;
}

for (const mode of ["dark", "light"]) {
	for (const [disable, active, hover] of [[true, false, false], [false, false, false], [false, false, true],
		[false, true, true], [false, true, false]]) {
		for (const divide of [true, false]) {
			ENTRIES.push({ group: "Tabs", page: "Tabs", mode,
				ref: `Disable=${disable}, Active=${active}, Hover=${hover}, Divide=${divide ? "True" : "False"}`,
				ignore: [divide ? [10, 19, 18, 18] : [6, 7, 18, 18]],
				html: tab(disable, active, hover, divide) });
		}
	}
}

function searchBox(state, value) {
	const cls = ["input"];
	if (state === "focus") cls.push("is-focus");
	if (state === "error") cls.push("is-error");
	if (state === "disable") cls.push("is-disabled");
	const clear = value ? icon("close-circle-bold").replace('class="icon"', 'class="icon input-clear"') : "";
	return `<label class="${cls.join(" ")}" style="width:296px"><input placeholder="Input search" value="${value}"` +
		`${state === "disable" ? " disabled" : ""}>${clear}<button class="input-addon" type="button">${icon("setting2")}</button></label>`;
}

function select(state, filled) {
	const cls = ["select"];
	if (!filled) cls.push("is-empty");
	if (state === "hover") cls.push("is-hover");
	if (state === "active") cls.push("is-active");
	if (state === "disable") cls.push("is-disabled");
	return `<button class="${cls.join(" ")}" type="button" style="width:222px"><span class="select-value">Please select</span>` +
		`${icon("chevron-down1")}</button>`;
}

for (const mode of ["dark", "light"]) {
	for (const [state, value] of [["empty", ""], ["filled", "Example"], ["focus", "Example"], ["error", "Example"], ["disable", ""]]) {
		ENTRIES.push({ group: "Input", page: "Input ", ref: `state=${state}`, mode, html: searchBox(state, value),
			// Figma draws the text caret of the focused box
			ignore: state === "focus" ? [[68, 8, 5, 18]] : [] });
	}
	for (const state of ["default", "hover", "active", "disable"]) {
		ENTRIES.push({ group: "Select", page: "Select", mode, html: select(state, true),
			ref: `state=${state}, Filled=true, Multi select=false, single select=true` });
		ENTRIES.push({ group: "Select", page: "Select", mode, html: select(state, false),
			ref: `state=${state}, Filled=false, Multi select=false, single select=false` });
	}
}

function menuItem(state, single, selected, danger, width) {
	const cls = ["menu-item"];
	if (danger) cls.push("menu-item--danger");
	if (single && selected) cls.push("is-selected");
	if (state === "hover") cls.push("is-hover");
	if (state === "disable") cls.push("is-disabled");
	let html = "";
	if (!single) {
		const cb = ["checkbox"];
		if (selected) cb.push("is-checked", "is-hover");
		if (state === "hover") cb.push("is-hover");
		if (state === "disable") cb.push("is-disabled");
		html += `<span class="${cb.join(" ")}"><span class="checkbox-box"></span></span>`;
	}
	html += `<span class="menu-label">${icon("home")}<span>Menu</span></span>`;
	if (!single) html += icon("chevron-right1").replace('class="icon"', 'class="icon menu-more"');
	else if (selected) html += `<span class="icon menu-tick"></span>`;
	return `<button class="${cls.join(" ")}" type="button" style="width:${width}px">${html}</button>`;
}

const MENU_ITEMS = [
	["default", false, false, false, 144], ["default", false, false, true, 144], ["disable", false, false, true, 144],
	["hover", false, false, true, 144], ["default", true, true, false, 122], ["hover", true, true, true, 122],
	["default", true, true, true, 122], ["default", true, false, false, 116], ["default", true, false, true, 120],
	["hover", true, true, false, 122], ["hover", false, false, false, 144], ["default", false, true, false, 144],
	["disable", false, false, false, 144],
];
for (const mode of ["dark", "light"]) {
	for (const [state, single, selected, danger, width] of MENU_ITEMS) {
		ENTRIES.push({ group: "Dropdown", page: "Dropdown", mode, html: menuItem(state, single, selected, danger, width),
			ref: `state=${state}, single select=${single}, selected=${selected}, danger=${danger}` });
	}
	const item = menuItem("default", false, false, false, 144);
	ENTRIES.push({ group: "Dropdown", page: "Dropdown", mode, ref: "Dropdown-Menu",
		html: `<div class="menu" style="width:156px"><div class="menu-group">Group title</div>${item}${item}` +
			`<div class="menu-group">Group title</div>${item}${item}</div>` });
}

const STATUS_ICON = { info: "warning-circle-bold", success: "tick-circle-bold", warning: "warning-circle-bold",
	error: "close-circle-bold" };
const smallButton = (text, width) => `<button class="btn btn--secondary btn--small" type="button">${label(text, width)}</button>`;
const close = (cls) => icon("x").replace('class="icon"', `class="icon ${cls}"`);

function alert(size, state) {
	const ico = icon(STATUS_ICON[state]).replace('class="icon"', 'class="icon alert-icon"');
	if (size === "small") {
		return `<div class="alert alert--${state}" style="width:395px">${ico}<span class="alert-text">Text</span>` +
			`<span class="alert-actions">${smallButton("Xem chi tiết", 57)}${close("alert-close")}</span></div>`;
	}
	return `<div class="alert alert--large alert--${state}" style="width:395px"><div class="alert-body">${ico}` +
		`<div class="alert-content"><span class="alert-title">Title</span><span>Sub text</span></div>${close("alert-close")}</div>` +
		`<div class="alert-actions">${smallButton("Chỉnh sửa", 49)}${smallButton("Xem chi tiết", 57)}</div></div>`;
}

const MESSAGES = { info: ["info-bold", "(199) Tệp đã được chọn"], success: ["tick-circle-bold", "Tải thành công"],
	warning: ["warning-circle-bold", null], error: ["warning-circle-bold", "Tải thất bại"] };
// Figma rounds the text boxes of the messages up to whole pixels
const MESSAGE_WIDTH = { info: 158, success: 98, error: 74 };

for (const mode of ["dark", "light"]) {
	for (const size of ["small", "large"]) {
		for (const state of ["info", "success", "warning", "error"]) {
			ENTRIES.push({ group: "Alert", page: "Alert", ref: `size=${size}, state=${state}`, mode, html: alert(size, state) });
		}
	}
	for (const [type, [ico, text]] of Object.entries(MESSAGES)) {
		if (!text) continue;
		ENTRIES.push({ group: "Message", page: "Message", ref: `Type=${type}`, mode,
			html: `<div class="message message--${type}">${icon(ico)}<span style="width:${MESSAGE_WIDTH[type]}px">${text}</span>${close("message-close")}</div>` });
	}
	for (const placement of ["top", "bottom", "left", "right"]) {
		const width = placement === "left" || placement === "right" ? 236 : 244;
		ENTRIES.push({ group: "Tooltip", page: "Tooltip", ref: `Placement=${placement}, Blocked=No`, mode,
			html: `<div class="tooltip tooltip--${placement}" style="width:${width}px">Content<br>Content</div>` });
	}
}

const box = () => `<span class="checkbox"><span class="checkbox-box"></span></span>`;
const sort = (cls = "") => `<span class="sort${cls}"><span class="sort-up"></span><span class="sort-down"></span></span>`;
const cell = (tag, width, inner, hover) =>
	`<table class="table" style="width:${width}px;table-layout:fixed"><tr><${tag}${hover ? ' class="is-hover"' : ""}>${inner}</${tag}></tr></table>`;

for (const mode of ["dark", "light"]) {
	for (const hover of [false, true]) {
		ENTRIES.push({ group: "Table", page: "Table", mode, ref: `Type=Title_cell, hover=${hover}`,
			html: cell("th", hover ? 92 : 118, `<span class="cell-line">${box()}<span class="cell-title">` +
				`<span style="width:30px">Title</span>${icon("help-circle")}</span>${sort()}</span>`, hover) });
		ENTRIES.push({ group: "Table", page: "Table", mode, ref: `Type=Body_cell, hover=${hover}`,
			html: cell("td", 101, `<div class="cell-line">${icon("message-text")}<span style="width:54px">Content</span></div>` +
				`<div class="cell-sub is-indented">&lt;Sub_text&gt;</div>`, hover) });
		ENTRIES.push({ group: "Table", page: "Table", mode, ref: `Type=Checkbox_cell, hover=${hover}`,
			html: cell("td", 40, `<div class="cell-line" style="height:16px">${box()}</div>`, hover) });
		ENTRIES.push({ group: "Table", page: "Table", mode, ref: `Type=Status_cell, hover=${hover}`,
			html: cell("td", 79, `<div class="status"><span class="dot dot--red"></span><span style="width:43px">Status</span></div>` +
				`<div class="cell-sub">&lt;Sub_text&gt;</div>`, hover) });
	}
	for (const [type, hover, cls] of [["no_active", "no", ""], ["no_active", "yes", " is-hover"], ["ascend", "no", " is-ascend"],
		["descend", "no", " is-descend"], ["ascend", "yes", " is-ascend is-hover"], ["descend", "yes", " is-descend is-hover"]]) {
		ENTRIES.push({ group: "Table", page: "Table", mode, ref: `Type=${type}, Hover=${hover}`, html: sort(cls) });
	}
}
