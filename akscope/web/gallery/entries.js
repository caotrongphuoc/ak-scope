// What the gallery shows: each entry is one component variant of the Figma file, named by its
// page and symbol name, with the HTML that must look the same. tests/figma/compare.py reads
// this list from the page and compares every entry with the variant drawn from the .fig file.

const icon = (name, style = "") => `<span class="icon" style="--icon:url(../icons/${name}.svg)${style}"></span>`;
// in the Figma file the right icon of a button keeps the base color in every state and type
// (white on a disabled button, dark on a blue one in light mode); the gallery shows it that way
const RIGHT_ICON = ";color:var(--color-foreground-base)";
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
	else if (content === "Yes") inner = `${icon("trash")}${label("Button", 45)}${icon("edit2", RIGHT_ICON)}`;
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
