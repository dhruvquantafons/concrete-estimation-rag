/* BuildMore AI UI - plain JS, no build step. Talks to the same-origin REST API with an API key. */
"use strict";

// ------------------------------------------------------------------ state & helpers
const S = {
  key: lsGet("crag_key"),
  me: null,
  oas: null,
  overview: null,
  checker: lsGet("crag_checker") || "",
  location: lsGet("crag_location") || "delhi",
};

// Stored unit codes (normalize/units.py) -> the spelling printed in the DSR, used everywhere units are shown.
const UNIT_LABELS = {
  m3: "cum", m2: "sqm", m: "metre", kg: "kg", t: "tonne", quintal: "quintal", l: "litre", ml: "ml", "750_ml": "750 ml", kl: "kilolitre",
  each: "each", each_set: "each set", one_set: "one set", pair: "pair", "10_nos": "10 Nos", "100_nos": "100 Nos", "1000_nos": "1000 Nos", set: "set", "10_sets": "10 sets", "1000_l": "1000 litre", bag: "bag (50 kg)",
  cm: "cm", cm_per_m: "cm per metre", letter_cm_height: "per letter per cm height", wheel: "per wheel", bundle: "bundle", roll: "roll", cartridge: "cartridge", dm3: "cudm", "10_dm3": "10 cudm", each_hole: "each hole", test: "per test", joint: "joint", each_cut: "each cut", m_per_m_height: "metre per metre height", m2_per_m_height: "sqm per metre height", kg_per_m_span: "kg per metre span", kg_per_m_height: "kg per metre height", m3_per_m_span: "cum per metre span", m_per_m_span: "metre per metre span", m_cm_girth: "per metre per cm girth (60 cm deep)", each_job: "each job", day: "day", hour: "hour", month: "month", km: "km", job: "job (L.S.)", point: "point", score: "score",
  "10m2": "10 sqm", "100m2": "100 sqm", sqm_per_coat: "sqm per coat", "100m": "100 m", m3_km: "km/cum", t_km: "tonne km",
  m_depth: "metre depth", m3_m_depth: "cum per metre depth", cm_depth_100m: "per cm depth per 100 m", cm_depth_cm_width_m: "per cm depth per cm width per metre length",
};
const unitLabel = (u) => (u ? UNIT_LABELS[u] || u : "");

function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } }

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const $ = (sel, root = document) => root.querySelector(sel);
// replaceChildren() would print a skipped (null/false) section as text; fill() drops them and flattens arrays.
const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (v) => v == null ? "—" : "₹ " + Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = (v, d = 2) => v == null || v === "" ? "—" : (typeof v === "number" ? v.toLocaleString("en-IN", { maximumFractionDigits: d }) : String(v));
const can = (scope) => !!S.me && (S.me.scopes.includes(scope) || S.me.scopes.includes("admin"));
const ACRONYMS = { rcc: "RCC", pcc: "PCC", opc: "OPC", ppc: "PPC", sbc: "SBC", ssd: "SSD", dsr: "DSR", ca: "CA", fa: "FA", cm: "cementitious", ii: "II", iii: "III", iv: "IV", i: "I" };
const titleCase = (s) => String(s).split("_").filter(Boolean).map((w, i) => ACRONYMS[w.toLowerCase()] || (i === 0 ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
const UNIT_SUFFIX = [["_kN_m3", "kN/m³"], ["_kN_m2", "kN/m²"], ["_kN_m", "kN/m"], ["_kNm", "kNm"], ["_kN", "kN"], ["_mm", "mm"], ["_m3", "m³"], ["_m2", "m²"], ["_m", "m"], ["_percent", "%"]];
const LABELS = { w_c_ratio: "Free w/c ratio", known_std_dev: "Known std. deviation (N/mm²)", sg_cement: "Specific gravity – cement",
  sg_coarse: "Specific gravity – coarse agg.", sg_fine: "Specific gravity – fine agg.", sg_fly_ash: "Specific gravity – fly ash",
  fy: "fy – steel grade (N/mm²)", b_mm: "Width b (mm)", D_mm: "Overall depth D (mm)", Mu_kNm: "Factored moment Mu (kNm)",
  Vu_kN: "Factored shear Vu (kN)", Pu_kN: "Factored axial load Pu (kN)", increase_cm_for_fly_ash: "+10 % cementitious for fly ash ≥ 20 %",
  round_like_annex_a: "Round water & cement up (as in Annex A)", ca_reduction_percent: "Reduce coarse aggregate (%)",
  max_cement_content: "Max cement content (kg/m³)", specific_gravity: "Specific gravity", sbc_kN_m2: "Safe bearing capacity (kN/m²)" };
function prettyLabel(field) {
  if (LABELS[field]) return LABELS[field];
  for (const [suf, u] of UNIT_SUFFIX) if (field.endsWith(suf)) return `${titleCase(field.slice(0, -suf.length))} (${u})`;
  return titleCase(field);
}

function toast(msg, bad = false) {
  const t = h("div", { class: "toast" + (bad ? " bad" : "") }, msg);
  $("#toast-root").append(t);
  setTimeout(() => t.remove(), bad ? 7000 : 3500);
}

function errText(e) {
  const d = e && e.detail;
  if (Array.isArray(d)) return d.map((x) => `${(x.loc || []).filter((p) => p !== "body").join(".")}: ${x.msg}`).join("\n");
  return typeof d === "string" ? d : (e && e.message) || String(e);
}

async function api(path, { method = "GET", body, blob = false } = {}) {
  const opts = { method, headers: { "X-API-Key": S.key || "" } };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  if (r.status === 401) {
    if (S.me) logout();   // only an established session is logged out; a failed sign-in just shows the error
    throw { detail: S.me ? "Session expired or key revoked - sign in again." : "Invalid or revoked API key." };
  }
  if (!r.ok) {
    let e; try { e = await r.json(); } catch { e = { detail: r.statusText }; }
    e.status = r.status; throw e;
  }
  return blob ? r.blob() : r.json();
}

function loading(text = "Working…") { return h("div", { class: "loading" }, h("span", { class: "spinner" }), text); }
function errorBox(e) { return h("div", { class: "alert bad", style: { whiteSpace: "pre-wrap" } }, errText(e)); }

function statusBadge(status) {
  const map = { verified: ["ok", "verified"], ai_checked: ["accent", "AI-checked, needs human sign-off"], ocr_confirmed: ["accent", "OCR-confirmed"], partially_confirmed: ["warn", "partially confirmed"],
    unconfirmed: ["bad", "unconfirmed"], confirmed: ["ok", "confirmed"], review: ["warn", "needs review"] };
  const [cls, label] = map[status] || ["", status];
  return h("span", { class: "badge " + cls }, label);
}

function modal(title, content, { wide = false, actions = [] } = {}) {
  const close = () => bg.remove();
  const bg = h("div", { class: "modal-bg", onclick: (e) => { if (e.target === bg) close(); } },
    h("div", { class: "modal", style: wide ? { width: "min(1200px, 100%)" } : null, role: "dialog", "aria-modal": "true" },
      h("div", { class: "mh" }, h("b", {}, title), h("span", { class: "spacer" }), ...actions, h("button", { class: "small", onclick: close }, "Close")),
      h("div", { class: "mb" }, content)));
  document.addEventListener("keydown", function onKey(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); } });
  $("#modal-root").append(bg);
  return close;
}

async function pageImage(docId, page, dpi = 110) {
  const blob = await api(`/v1/source/page?doc_id=${encodeURIComponent(docId)}&page=${page}&dpi=${dpi}`, { blob: true });
  return URL.createObjectURL(blob);
}

/* Original-scan viewer with page stepping and zoom. */
function showSourcePage(docId, page, title) {
  let p = page, actual = false;
  const view = h("div", { class: "page-view", style: { maxHeight: "75vh" } }, loading("Loading page…"));
  const label = h("span", { class: "muted" });
  const load = async () => {
    label.textContent = `${docId} · PDF page ${p}`;
    view.replaceChildren(loading("Loading page…"));
    try { view.replaceChildren(h("img", { src: await pageImage(docId, p, 130), alt: `${docId} page ${p}` })); }
    catch (e) { view.replaceChildren(errorBox(e)); }
  };
  modal(title || "Source page", h("div", { class: "stack" }, label, view), {
    wide: true,
    actions: [
      h("button", { class: "small", onclick: () => { p = Math.max(1, p - 1); load(); } }, "‹ Prev"),
      h("button", { class: "small", onclick: () => { p += 1; load(); } }, "Next ›"),
      h("button", { class: "small", onclick: () => { actual = !actual; view.classList.toggle("actual", actual); } }, "Zoom"),
    ],
  });
  load();
}

// ------------------------------------------------------------------ pagination
/* Pager bar: "Showing a–b of n", prev, page numbers (with gaps), next. go(page) is called with a 0-based page. */
function pagerBar(page, pages, total, perPage, go, label = "items") {
  const from = total ? page * perPage + 1 : 0, to = Math.min(total, (page + 1) * perPage);
  const want = new Set([0, pages - 1, page - 1, page, page + 1].filter((p) => p >= 0 && p < pages));
  const nums = []; let last = -1;
  [...want].sort((a, b) => a - b).forEach((p) => {
    if (p - last > 1) nums.push(h("span", { class: "gap" }, "…"));
    nums.push(h("button", { class: "num-btn" + (p === page ? " on" : ""), "aria-current": p === page ? "page" : null, onclick: () => go(p) }, String(p + 1)));
    last = p;
  });
  return h("div", { class: "pager" }, h("span", { class: "muted" }, `Showing ${from}–${to} of ${total.toLocaleString("en-IN")} ${label}`),
    pages > 1 ? h("div", { class: "pages" },
      h("button", { "aria-label": "Previous page", disabled: page === 0, onclick: () => go(page - 1) }, icon("left")), nums,
      h("button", { "aria-label": "Next page", disabled: page >= pages - 1, onclick: () => go(page + 1) }, icon("right"))) : null);
}
/* Client-side pages over an already-fetched list. render(slice, offset) returns the element for one page. */
function paginate(items, perPage, render, label) {
  const wrap = h("div"); let page = 0;
  const pages = Math.max(1, Math.ceil(items.length / perPage));
  const draw = (scroll) => {
    wrap.replaceChildren(...[render(items.slice(page * perPage, (page + 1) * perPage), page * perPage),
      items.length > perPage ? pagerBar(page, pages, items.length, perPage, (p) => { page = p; draw(true); }, label) : null].filter(Boolean));
    const top = wrap.getBoundingClientRect().top;
    if (scroll && top < 130) window.scrollBy({ top: top - 140, behavior: "smooth" });
  };
  draw(false);
  return wrap;
}

// ------------------------------------------------------------------ OpenAPI-driven forms
function resolveRef(s) { return s && s.$ref ? S.oas.components.schemas[s.$ref.split("/").pop()] : s; }
function unwrapNullable(s) {
  if (s && s.anyOf) { const inner = s.anyOf.find((x) => x.type !== "null"); return { ...inner, nullable: true, default: s.default, title: s.title, description: s.description }; }
  return s;
}

/** Build a form from a component schema. Returns {el, value()}. */
function schemaForm(name, { exclude = [], values = {}, only = null } = {}) {
  const schema = S.oas.components.schemas[name];
  const getters = [];
  const grid = h("div", { class: "form-grid" });
  for (const [field, raw] of Object.entries(schema.properties || {})) {
    if (exclude.includes(field) || (only && !only.includes(field))) continue;
    let p = unwrapNullable(raw);
    const ref = resolveRef(p);
    if (ref !== p && ref && ref.properties) {   // nested object
      const sub = schemaFormFromSchema(ref, { ...(raw.default || {}), ...(values[field] || {}) });
      grid.append(h("fieldset", {}, h("legend", {}, prettyLabel(field)), sub.el));
      getters.push((o) => { o[field] = sub.value(); });
      continue;
    }
    if (p.type === "array") continue;
    const { el, get } = fieldInput(field, p, values[field] ?? raw.default ?? p.default, (schema.required || []).includes(field));
    grid.append(el);
    getters.push((o) => { const v = get(); if (v !== undefined) o[field] = v; });
  }
  return { el: grid, value: () => { const o = {}; getters.forEach((g) => g(o)); return o; } };
}
function schemaFormFromSchema(schema, values) {
  const getters = [];
  const grid = h("div", { class: "form-grid" });
  for (const [field, raw] of Object.entries(schema.properties || {})) {
    const p = unwrapNullable(raw);
    const { el, get } = fieldInput(field, p, values[field] ?? p.default, false);
    grid.append(el);
    getters.push((o) => { const v = get(); if (v !== undefined) o[field] = v; });
  }
  return { el: grid, value: () => { const o = {}; getters.forEach((g) => g(o)); return o; } };
}
function fieldInput(field, p, def, required) {
  const label = prettyLabel(field);
  const hint = p.description ? h("span", { class: "hint", title: p.description }, "?") : null;
  let input, get;
  if (p.enum) {
    input = h("select", {}, p.nullable ? h("option", { value: "" }, "—") : null,
      p.enum.map((v) => h("option", { value: v, selected: String(v) === String(def) }, typeof v === "string" ? titleCase(v) : v)));
    const numeric = typeof p.enum[0] === "number";
    get = () => input.value === "" ? undefined : (numeric ? Number(input.value) : input.value);
  } else if (p.type === "boolean") {
    input = h("input", { type: "checkbox", checked: !!def });
    get = () => input.checked;
    return { el: h("label", { class: "f check" }, input, label, hint), get };
  } else if (p.type === "number" || p.type === "integer") {
    input = h("input", { type: "number", step: p.type === "integer" ? "1" : "any", value: def ?? "", placeholder: p.nullable ? "optional" : "", required });
    get = () => input.value === "" ? undefined : Number(input.value);
  } else {
    input = h("input", { type: "text", value: def ?? "", placeholder: p.nullable ? "optional" : "", required });
    get = () => input.value.trim() === "" ? undefined : input.value.trim();
  }
  return { el: h("label", { class: "f" }, h("span", {}, label, required ? " *" : "", hint), input), get };
}

// ------------------------------------------------------------------ calculator result rendering
function refList(refs) {
  if (!refs || !refs.length) return null;
  return h("span", { class: "ref" }, refs.map((r) => h("span", { class: "badge " + ({ verified: "ok", ai_checked: "accent", ocr_confirmed: "accent", partially_confirmed: "warn" }[r.status] || "bad"),
    title: `${r.status}${r.note ? " - " + r.note : ""}`, style: { marginRight: "4px" } }, `${r.code}, ${r.item}`)));
}
function valueCell(v) {
  if (v && typeof v === "object") return h("dl", { class: "kv" }, Object.entries(v).flatMap(([k, x]) => [h("dt", {}, titleCase(k)), h("dd", {}, num(x, 3))]));
  return num(v, 3);
}
function renderCalc(res) {
  const out = res.outputs || {};
  const checks = res.checks || [];
  const failed = checks.filter((c) => !c.passed);
  const simple = Object.entries(out).filter(([k, v]) => typeof v !== "object" && k !== "all_checks_passed");
  const nested = Object.entries(out).filter(([, v]) => v && typeof v === "object");
  return h("div", { class: "stack" },
    h("div", { class: "row" }, h("h2", { style: { margin: 0 } }, "Result"),
      res.ok !== false && !failed.length ? h("span", { class: "badge ok" }, "✓ all checks passed") : h("span", { class: "badge bad" }, `✗ ${failed.length} check(s) failed`)),
    (res.warnings || []).length ? h("div", { class: "alert warn" }, h("b", {}, "Warnings"), h("ul", {}, res.warnings.map((w) => h("li", {}, w)))) : null,
    h("div", { class: "grid cols-2" },
      simple.length ? h("div", { class: "card" }, h("h3", {}, "Key outputs"),
        h("div", { class: "metrics" }, simple.map(([k, v]) => h("div", { class: "metric" }, h("span", { class: "l" }, prettyLabel(k)), h("b", { class: "v" }, num(v, 3)))))) : null,
      ...nested.map(([k, v]) => h("div", { class: "card" }, h("h3", {}, titleCase(k)), valueCell(v)))),
    checks.length ? h("div", { class: "card" }, h("h3", {}, "Code compliance checks"),
      checks.map((c) => h("div", { class: "check-row" }, h("span", { class: "badge " + (c.passed ? "ok" : "bad") }, c.passed ? "PASS" : "FAIL"),
        h("div", {}, h("div", {}, h("b", {}, c.name), " — ", c.detail), refList(c.refs))))) : null,
    h("details", { class: "card" }, h("summary", {}, h("b", {}, `Step-by-step working (${(res.steps || []).length} steps)`)),
      h("div", { class: "tbl-wrap", style: { marginTop: "12px" } }, h("table", { class: "t" },
        h("thead", {}, h("tr", {}, h("th", {}, "Step"), h("th", {}, "Formula"), h("th", { class: "num" }, "Value"), h("th", {}, "Unit"), h("th", {}, "Reference"))),
        h("tbody", {}, (res.steps || []).map((s) => h("tr", {}, h("td", {}, s.name), h("td", { class: "mono" }, s.formula || ""),
          h("td", { class: "num" }, typeof s.value === "object" ? JSON.stringify(s.value) : num(s.value, 4)), h("td", {}, s.unit || ""), h("td", {}, refList(s.refs))))))))
  );
}

// ------------------------------------------------------------------ icons (stroke icons, 24px grid)
const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  pin: '<path d="M12 21s-7-6.1-7-11.5A7 7 0 0 1 19 9.5C19 14.9 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  doc: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  receipt: '<path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
  inbox: '<path d="M4 13h4l2 3h4l2-3h4"/><path d="M5 5h14l1 8v6H4v-6z"/>',
  flask: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/><path d="M7.5 15h9"/>',
  beam: '<rect x="3" y="9" width="18" height="6" rx="1"/><path d="M6 15v4M18 15v4M7 12h10"/>',
  slab: '<path d="M3 10l9-5 9 5-9 5z"/><path d="M3 10v3l9 5 9-5v-3"/>',
  column: '<rect x="8" y="3" width="8" height="18" rx="1"/><path d="M8 7h8M8 17h8"/>',
  footing: '<path d="M10 3h4v9h-4zM4 12h16v5H4zM4 20h16"/>',
  rupee: '<path d="M6 4h12M6 9h12M14 20 7 13h2a4 4 0 0 0 0-9"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  check: '<path d="M9 12l2 2 4-4"/><circle cx="12" cy="12" r="9"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/>',
  shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  calc: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 11h2M12 11h2M8 15h2M12 15h2M16 11v4"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  book: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19V5"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>', chev: '<path d="m6 9 6 6 6-6"/>', left: '<path d="m15 6-6 6 6 6"/>', right: '<path d="m9 6 6 6-6 6"/>',
  pause: '<path d="M9 5v14M15 5v14"/>', play: '<path d="M7 5l12 7-12 7z"/>', menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  code: '<path d="m8 8-5 4 5 4M16 8l5 4-5 4"/>', out: '<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h11"/>', help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6M12 17h.01"/>',
  tick: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>', plus: '<path d="M12 5v14M5 12h14"/>',
};
function icon(name, cls = "i") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", cls); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || ""; return s;
}
const LOGO = () => { const d = h("span", { class: "logo" }); d.innerHTML = '<svg viewBox="0 0 32 32" width="30" height="30"><rect width="32" height="32" rx="8" fill="var(--accent)"/><path d="M8 23V12l8-4 8 4v11M12 23v-6h8v6" stroke="white" stroke-width="2.4" fill="none" stroke-linejoin="round"/></svg>'; return d; };
const BRAND_TXT = () => h("span", { class: "brand-txt" }, h("span", {}, "BuildMore", h("b", {}, " AI")), h("small", {}, "Concrete and Costs"));
const BLUMARGINS = "https://www.blumargins.com/";

// ------------------------------------------------------------------ pages & chrome
const PAGES = [
  { id: "search", label: "Search codes", ico: "search", scope: "retrieve" },
  { id: "mix", label: "Mix design", ico: "flask", scope: "calc" },
  { id: "design", label: "RCC design (aid)", ico: "beam", scope: "calc" },
  { id: "estimate", label: "Estimate / BOQ", ico: "rupee", scope: "estimate" },
  { id: "rates", label: "DSR rates", ico: "list", scope: "estimate" },
  { id: "tables", label: "Code tables", ico: "table", scope: "calc" },
  { id: "review", label: "Review queue", ico: "edit", scope: "admin" },
  { id: "learning", label: "Learning", ico: "layers", scope: "admin" },
];
const SOURCES = ["", "IS 456", "IS 10262", "IS 1200", "IS 2386", "CPWD DSR"];
const reviewCount = () => S.overview ? S.overview.review.sor_items + S.overview.review.basic_rates + (S.overview.review.dar_dsr || 0) : 0;

function renderChrome() {
  const cur = location.hash.replace(/^#\/?/, "").split("?")[0];
  // header: brand · search · rate location · review · account · CTA (the old utility bar is folded in here)
  const locs = (S.overview && S.overview.locations) || ["delhi"];
  const locSel = h("select", { "aria-label": "Rate location", onchange: (e) => { S.location = e.target.value; lsSet("crag_location", S.location); toast(`Rates now priced for ${titleCase(S.location)}`); } },
    locs.map((l) => h("option", { value: l, selected: l === S.location }, titleCase(l))));
  $("#utility").replaceChildren();
  const scope = h("select", { "aria-label": "Source" }, SOURCES.map((c) => h("option", { value: c }, c || "All")));
  const q = h("input", { type: "search", placeholder: "Search codes, clauses, DSR items…", "aria-label": "Search" });
  const go = (e) => { e.preventDefault(); if (q.value.trim()) location.hash = `#/search?q=${encodeURIComponent(q.value.trim())}&code=${encodeURIComponent(scope.value)}`; };
  const avatar = () => h("span", { class: "avatar" }, (S.me.name || "?").trim().slice(0, 2));
  const menu = h("div", { class: "menu" },
    h("div", { class: "who" }, avatar(), h("div", {}, h("b", {}, S.me.name), h("br"), h("small", {}, S.me.scopes.join(" · ")))),
    h("a", { href: "#/estimate" }, icon("receipt"), "My estimates"),
    can("admin") ? h("a", { href: "#/review" }, icon("edit"), "Review queue") : null,
    can("admin") ? h("a", { href: "#/learning" }, icon("layers"), "Learning (feedback)") : null,
    h("a", { href: "#/tables" }, icon("shield"), "Verification (code tables)"),
    h("a", { href: "/docs", target: "_blank" }, icon("code"), "API documentation"),
    S.me.desktop ? null : h("hr"),
    S.me.desktop ? null : h("button", { onclick: logout }, icon("out"), "Sign out"));
  const acct = h("div", { style: { position: "relative" } },
    h("button", { class: "hact", "aria-label": "Account", onclick: (e) => { e.stopPropagation(); menu.classList.toggle("open"); } }, avatar(),
      h("span", { class: "two" }, h("small", {}, "Signed in"), h("b", {}, S.me.name, icon("chev")))), menu);
  document.onclick = () => menu.classList.remove("open");
  const rc = reviewCount();
  $("#site-header").replaceChildren(h("div", { class: "wrap" },
    h("a", { class: "brand", href: "#/" }, LOGO(), BRAND_TXT()),
    h("form", { class: "hsearch", onsubmit: go }, scope, q, h("button", { type: "submit", "aria-label": "Search" }, icon("search"))),
    h("div", { class: "hacts" },
      h("label", { class: "hloc", title: "CPWD DSR 2023 rates are priced for this location" }, icon("pin"), h("span", { class: "lbl" }, "Rates for"), locSel),
      can("admin") ? h("a", { class: "hact", href: "#/review", title: "Rows awaiting review", "aria-label": "Review queue" }, icon("inbox"), h("span", { class: "dot" + (rc ? " on" : "") }, rc > 999 ? "999+" : rc)) : null,
      acct,
      can("estimate") ? h("a", { class: "btn primary hide-sm", href: "#/estimate" }, icon("plus"), "New estimate") : null)));
  // tools nav
  $("#catbar").replaceChildren(h("div", { class: "wrap" },
    h("a", { class: "all" + (cur === "" ? " active" : ""), href: "#/" }, icon("grid"), "Overview"),
    h("span", { class: "sep" }),
    PAGES.filter((p) => !["review", "learning"].includes(p.id) && (!p.scope || can(p.scope))).map((p) => h("a", { href: "#/" + p.id, class: cur === p.id ? "active" : "" }, icon(p.ico), p.label)),
    h("div", { class: "right" },
      h("a", { href: "#/search?code=CPWD%20DSR&q=design%20mix%20concrete", class: "" }, icon("book"), "Standards"),
      can("admin") ? h("a", { href: "#/review", class: "pill-amber" + (cur === "review" ? " active" : "") }, icon("edit"), `Review ${rc}`) : null)));
  // footer
  const col = (title, links) => h("div", {}, h("h4", {}, title), h("ul", {}, links.map(([t, href]) => h("li", {}, h("a", { href, target: href.startsWith("/") ? "_blank" : null }, t)))));
  $("#site-footer").replaceChildren(
    h("div", { class: "wrap top" },
      h("div", {}, h("a", { class: "brand", href: "#/" }, LOGO(), BRAND_TXT()),
        h("p", { class: "blurb" }, "Code-compliant mix design, RCC design and cost estimates for concrete works — every number cited to IS codes and the CPWD Delhi Schedule of Rates."),
        h("div", { class: "boxed" }, h("b", {}, h("a", { href: BLUMARGINS, target: "_blank", rel: "noopener" }, "BluMargins Technologies")), h("br"),
          "AI & End-to-End IT Services", h("br"), "Red Cross Road, Srinagar, Kashmir - 190001, India", h("br"),
          h("a", { href: "mailto:info@blumargins.com" }, "info@blumargins.com"))),
      col("Tools", [["Search codes", "#/search"], ["Mix design", "#/mix"], ["RCC design (aid)", "#/design"], ["Estimate / BOQ", "#/estimate"]]),
      col("Standards", [["IS 456 : 2000", "#/search?code=IS%20456&q=scope"], ["IS 10262 : 2019", "#/search?code=IS%2010262&q=mix%20proportioning"], ["IS 1200 (Parts 1–22)", "#/search?code=IS%201200&q=method%20of%20measurement"], ["IS 2386 (Parts 1–8)", "#/search?code=IS%202386&q=methods%20of%20test"], ["CPWD DSR 2023", "#/rates"]]),
      col("Data quality", [["Review queue", "#/review"], ["Code tables", "#/tables"], ["DSR rates", "#/rates"]]),
      col("Developers", [["API documentation", "/docs"], ["OpenAPI schema", "/openapi.json"], ["Health", "/health"], ["Help & overview", "#/"]])),
    h("div", { class: "mid" }, h("div", { class: "wrap" },
      h("div", {}, h("b", {}, icon("shield"), "Verified numbers"), "Rates are confirmed by up to three independent OCR readings; anything uncertain is flagged until a person checks it."),
      h("div", {}, h("b", {}, icon("calc"), "Deterministic calculators"), "Arithmetic is done by tested code using curated, signed-off code tables — never by the language model."),
      h("div", {}, h("b", {}, icon("lock"), "Runs on your machine"), "Documents, indexes and models stay local. Licensed standards are never published."))),
    h("div", { class: "bottom" }, h("div", { class: "wrap" }, h("span", {}, "Corpus"),
      ["IS 456", "IS 10262", "IS 1200", "IS 2386", "CPWD DSR 2023"].map((c) => h("span", { class: "chip-n" }, c)),
      h("span", { class: "copy" }, "Developed and designed by ", h("a", { href: BLUMARGINS, target: "_blank", rel: "noopener" }, "BluMargins Technologies"), ` · Signed in as ${S.me.name}`))));
}

async function route() {
  if (!S.me) return;
  const id = location.hash.replace(/^#\/?/, "").split("?")[0];
  renderChrome();
  const main = $("#main");
  const page = PAGES.find((p) => p.id === id);
  const content = h("div");
  fill(main, ...[page ? h("div", { class: "crumb" }, h("a", { href: "#/" }, "Home"), h("span", { class: "sep", "aria-hidden": "true" }, "›"), h("span", {}, page.label)) : null, content].filter(Boolean));
  content.append(loading("Loading…"));
  const fn = ROUTES[id] || pageHome;
  try { await fn(content); } catch (e) { content.replaceChildren(errorBox(e)); }
  window.scrollTo({ top: 0 });
  main.focus({ preventScroll: true });
}

// ---------- home
const DOC_CARDS = [
  { code: "IS 456", doc: "IS456_2000", page: 4, kicker: "Plain & reinforced concrete", title: "IS 456 : 2000", q: "durability requirements" },
  { code: "IS 10262", doc: "IS10262_2019", page: 1, kicker: "Mix proportioning", title: "IS 10262 : 2019", q: "selection of water cement ratio" },
  { code: "IS 1200", doc: "IS1200_P2_2025", page: 1, kicker: "Method of measurement", title: "IS 1200", q: "concrete works measurement" },
  { code: "IS 2386", doc: "IS2386_P1_1963", page: 4, kicker: "Tests for aggregates", title: "IS 2386", q: "sieve analysis" },
  { code: "CPWD DSR", doc: "CPWD_DSR_2023_V1", page: 1, kicker: "Schedule of rates", title: "CPWD DSR 2023", href: "#/rates" },
];
const TOOLS = [
  ["search", "Search codes", "#/search", "Hybrid search and cited answers over IS 456, 10262, 1200, 2386 and the DSR."],
  ["flask", "Mix design", "#/mix", "IS 10262 trial mix with every step and clause shown."],
  ["beam", "Beam", "#/design?k=beam", "Flexure, shear and deflection checks to IS 456."],
  ["slab", "Slab", "#/design?k=slab", "One-way slab thickness, steel and span/depth checks."],
  ["column", "Column", "#/design?k=column", "Short axially loaded column, longitudinal and lateral ties."],
  ["footing", "Footing", "#/design?k=footing", "Isolated footing sizing, bending and punching shear."],
  ["rupee", "Estimate", "#/estimate", "Elements to IS 1200 quantities to a DSR-priced BOQ, with Excel and PDF."],
  ["list", "DSR rates", "#/rates", "Look up any of the 2,955 CPWD DSR 2023 items by word or code."],
  ["table", "Code tables", "#/tables", "The IS values the calculators use, compared with the scan."],
  ["edit", "Review", "#/review", "Fix DSR rows the scanner was unsure about, beside the page."],
];
const STEPS = [
  ["Enter elements", "Footings, columns, beams and slabs with their sizes, numbers, grade and level."],
  ["IS 1200 quantities", "Concrete, formwork and steel measured the way IS 1200 says, element by element."],
  ["DSR-priced BOQ", "Each line priced from CPWD DSR 2023 for your location, cited to its page, ready to export."],
];
// Hero copy: key phrase of each title is rendered with the brand gradient.
const gradTitle = (t, phrase) => { const i = t.indexOf(phrase); return i < 0 ? t : [t.slice(0, i), h("span", { class: "grad" }, phrase), t.slice(i + phrase.length)]; };
const windowFrame = (label, mock) => h("div", { class: "window" }, h("div", { class: "wbar" }, h("i"), h("i"), h("i"), h("span", {}, label)), mock);
let heroTimer = null;
async function pageHome(main) {
  const o = S.overview = await api("/v1/overview");
  renderChrome();
  const docStats = (prefix) => o.documents.filter((d) => d.doc_id.startsWith(prefix));
  const slides = [
    { kicker: "Built on IS 456 · IS 10262 · IS 1200 · CPWD DSR 2023", title: "Estimate concrete works, by the code.", grad: "by the code.", win: "estimate / boq",
      text: "Draw up elements, get IS 1200 quantities and a DSR-priced bill of quantities with every rate cited to its page.", cta: ["Build an estimate", "#/estimate"], cta2: ["Browse DSR rates", "#/rates"],
      mock: h("div", { class: "mock float" }, h("div", { class: "mh" }, h("span", {}, "Example BOQ · G+1 frame"), h("span", { class: "badge ok" }, "rates confirmed")),
        [["5.33.2.1  RCC M25 design mix", "16.91 m³"], ["5.9.3  Shuttering – slabs", "80.00 m²"], ["5.22.6  TMT Fe-500D", "2,777.9 kg"], ["4.1.8  PCC 1:4:8", "2.59 m³"]].map(([a, b]) => h("div", { class: "ln" }, h("span", {}, a), h("b", {}, b))),
        h("div", { class: "ln" }, h("span", {}, "Grand total"), h("span", { class: "tot" }, "₹ 7,45,539.54"))) },
    { kicker: "IS 10262 : 2019 · reproduces Annex A exactly", title: "Mix designs you can audit.", grad: "you can audit.", win: "mix design",
      text: "Target strength, w/c, water, cementitious and aggregate contents — every step shown with the clause it comes from.", cta: ["Design a mix", "#/mix"], cta2: ["See code tables", "#/tables"],
      mock: h("div", { class: "mock float" }, h("div", { class: "mh" }, h("span", {}, "M40 · severe · 20 mm"), h("span", { class: "badge ok" }, "✓ 4 checks passed")),
        [["Cement", "412 kg/m³"], ["Water", "148 kg/m³"], ["Fine aggregate", "648.6 kg/m³"], ["Coarse aggregate", "1,234.6 kg/m³"], ["Free w/c", "0.36"]].map(([a, b]) => h("div", { class: "ln" }, h("span", {}, a), h("b", {}, b)))) },
    { kicker: `${o.rates_vote_confirmed_pct}% of DSR rates confirmed by ≥2 OCR readings`, title: "Every answer, cited to the page.", grad: "cited to the page.", win: "search",
      text: "Hybrid search over the standards and schedule of rates. Open the original scanned page behind any clause, table or rate.", cta: ["Search the codes", "#/search"], cta2: ["Browse DSR rates", "#/rates"],
      mock: h("div", { class: "mock float" }, h("div", { class: "mh" }, h("span", {}, "nominal cover, severe exposure"), h("span", { class: "badge accent" }, "8 results")),
        [["IS 456 : 2000, Table 16", "p.60"], ["IS 456 : 2000, cl. 26.4.2", "p.59"], ["IS 456 : 2000, Table 5", "p.33"], ["IS 456 : 2000, cl. 8.2.4.1", "p.31"]].map(([a, b]) => h("div", { class: "ln" }, h("span", {}, a), h("b", {}, b)))) },
  ];
  let cur = 0, playing = true;
  const slideEls = slides.map((s, i) => h("div", { class: "slide" + (i === 0 ? " on" : "") },
    h("div", {}, h("span", { class: "kicker" }, s.kicker), h("h2", {}, gradTitle(s.title, s.grad)), h("p", {}, s.text),
      h("div", { class: "hero-cta" },
        h("a", { class: "btn primary lg", href: s.cta[1] }, s.cta[0], icon("arrow")),
        h("a", { class: "btn outline lg", href: s.cta2[1] }, s.cta2[0])),
      h("div", { class: "ticks" }, ["Cited to the page", "Deterministic maths", "Runs on your machine"].map((t) => h("span", {}, icon("tick"), t)))),
    windowFrame(s.win, s.mock)));
  const bars = h("span", { class: "bars" }, slides.map((_, i) => h("i", { class: i === 0 ? "on" : "" })));
  const show = (n) => { cur = (n + slides.length) % slides.length; slideEls.forEach((el, i) => el.classList.toggle("on", i === cur)); [...bars.children].forEach((b, i) => b.classList.toggle("on", i === cur)); };
  const playBtn = h("button", { "aria-label": "Pause", onclick: () => { playing = !playing; playBtn.replaceChildren(icon(playing ? "pause" : "play")); } }, icon("pause"));
  clearInterval(heroTimer);
  heroTimer = setInterval(() => { if (!document.body.contains(bars)) return clearInterval(heroTimer); if (playing) show(cur + 1); }, 6000);
  const hero = h("section", { class: "hero" }, slideEls,
    h("div", { class: "hero-ctl" }, playBtn, h("button", { "aria-label": "Previous", onclick: () => show(cur - 1) }, icon("left")), bars, h("button", { "aria-label": "Next", onclick: () => show(cur + 1) }, icon("right"))));

  const cards = DOC_CARDS.map((d) => {
    const docs = docStats(d.doc.split("_")[0].startsWith("CPWD") ? "CPWD" : d.doc.replace(/_.*/, ""));
    const pages = docs.reduce((a, x) => a + (x.pages || 0), 0), chunks = docs.reduce((a, x) => a + (x.chunks || 0), 0);
    const img = h("img", { alt: "", loading: "lazy" });
    pageImage(d.doc, d.page, 60).then((u) => { img.src = u; }).catch(() => img.remove());
    return h("a", { class: "doc-card", href: d.href || `#/search?code=${encodeURIComponent(d.code)}&q=${encodeURIComponent(d.q)}` }, img,
      h("div", { class: "ov" }, h("small", {}, d.kicker), h("b", {}, d.title), h("span", {}, `${docs.length > 1 ? docs.length + " parts · " : ""}${pages} pages · ${chunks.toLocaleString("en-IN")} chunks`)));
  });
  const tiles = TOOLS.filter(([, , href]) => { const p = PAGES.find((x) => href.startsWith("#/" + x.id)); return !p || !p.scope || can(p.scope); })
    .map(([ic, label, href, desc]) => h("a", { class: "feature", href }, h("span", { class: "ic" }, icon(ic)), h("b", {}, label, icon("arrow")), h("p", {}, desc)));
  const tablesTotal = Object.values(o.code_tables).reduce((a, v) => a + v, 0);
  const statband = h("section", { class: "statband" },
    [[o.counts.sor_items.toLocaleString("en-IN"), "DSR 2023 items priced"], [`${o.rates_vote_confirmed_pct}%`, "rates confirmed by ≥2 OCR readings"],
      [String(o.documents.length), "standards & parts indexed"], [`${o.code_tables.verified || 0}/${tablesTotal}`, "code tables signed off"]]
      .map(([n, l]) => h("div", {}, h("div", { class: "n" }, n), h("div", { class: "l" }, l))));
  fill(main, hero, statband,
    h("section", { class: "section" }, h("div", { class: "sec-head" }, h("div", {}, h("span", { class: "eyebrow" }, "Tools"), h("h2", {}, "Everything for concrete works, in one place"),
      h("p", {}, "Search the standards, design mixes and RCC elements, and price a BOQ. Every number comes from the code, a checked table or a tested calculator."))),
      h("div", { class: "features tools" }, tiles)),
    can("estimate") ? h("section", { class: "section" }, h("div", { class: "sec-head" }, h("div", {}, h("span", { class: "eyebrow" }, "How an estimate works"), h("h2", {}, "From sizes to a priced BOQ in three steps")),
      h("a", { href: "#/estimate" }, "Start an estimate ", icon("arrow"))),
      h("div", { class: "steps" }, STEPS.map(([t, p], i) => h("div", { class: "step" }, h("span", { class: "num" }, String(i + 1).padStart(2, "0")), h("b", {}, t), h("p", {}, p))))) : null,
    h("section", { class: "section" }, h("div", { class: "sec-head" }, h("div", {}, h("span", { class: "eyebrow" }, "Corpus"), h("h2", {}, "Browse the standards"), h("p", {}, "Every standard with its own clauses, tables and amendments")),
      h("a", { href: "#/search" }, "Search all ", icon("arrow"))), h("div", { class: "doc-cards" }, cards)),
    h("section", { class: "section" }, h("div", { class: "sec-head" }, h("div", {}, h("span", { class: "eyebrow" }, "Why trust it"), h("h2", {}, "Numbers you can check"), h("p", {}, "Every value can be traced back to its source and checked by a person."))),
      h("div", { class: "features cols-4" },
        trustItem("doc", "Cited to the page", "Every clause, table and rate links to the original scanned PDF page."),
        trustItem("shield", "Three-way rate checks", `${o.rates_vote_confirmed_pct}% of ${o.counts.sor_items.toLocaleString("en-IN")} DSR rates confirmed by independent OCR readings.`),
        trustItem("calc", "Deterministic maths", "Calculators reproduce the IS 10262 worked example; the LLM never does arithmetic."),
        trustItem("edit", "Human sign-off", `${reviewCount()} rows and ${Object.entries(o.code_tables).filter(([k]) => k !== "verified").reduce((a, [, v]) => a + v, 0)} code tables await review.`))),
    o.recent_estimates.length ? h("section", { class: "section" }, h("div", { class: "sec-head" }, h("div", {}, h("span", { class: "eyebrow" }, "Your work"), h("h2", {}, "Recent estimates"), h("p", {}, "Saved automatically — reopen, export or print")),
      h("a", { href: "#/estimate" }, "All estimates ", icon("arrow"))),
      h("div", { class: "grid cols-4" }, o.recent_estimates.slice(0, 4).map((e) => h("a", { class: "card", href: "#/estimate?id=" + e.id, style: { color: "inherit", textDecoration: "none" } },
        h("small", {}, `${e.created.slice(0, 16)} · ${titleCase(e.location || "")}`), h("div", { style: { fontWeight: 600, margin: "4px 0 10px" } }, e.project),
        h("div", { class: "big-num", style: { fontSize: "22px" } }, inr(e.grand_total)), h("small", {}, `${e.lines} BOQ lines`, e.warnings ? ` · ${e.warnings} to verify` : ""))))) : null,
    o.llm_provider === "fake" ? h("div", { class: "alert info section" }, h("b", {}, "No LLM configured. "), "Search, calculators and estimates work fully. “Ask” answers stay extractive until you set CRAG_LLM_PROVIDER in .env.") : null);
}
function trustItem(ic, title, text) { return h("div", { class: "feature" }, h("div", { class: "ic" }, icon(ic)), h("b", {}, title), h("p", {}, text)); }
function pageHead(eyebrow, title, sub) { return h("div", { class: "page-head" }, h("span", { class: "eyebrow" }, eyebrow), h("h1", {}, title), sub ? h("p", { class: "sub" }, sub) : null); }

function stat(v, l, s) { return h("div", { class: "card stat" }, h("div", { class: "v" }, v), h("div", { class: "l" }, l), h("div", { class: "s muted" }, s)); }

// confidence score (calc/confidence.py): level badge + the factors behind it
function confidenceCard(c, versions) {
  if (!c) return null;
  const cls = { high: "ok", medium: "warn", low: "bad" }[c.level] || "info";
  return h("div", { class: "card" },
    h("div", { class: "row" }, h("h2", { style: { margin: 0 } }, "Confidence"), h("span", { class: `badge ${cls}` }, `${c.level} · ${num(c.score)}`),
      c.capped_by ? h("small", { class: "muted" }, "limited by ", c.capped_by.join(", ")) : null),
    h("table", { class: "t" }, h("tbody", {}, c.factors.map((f) => h("tr", {}, h("td", {}, f.name), h("td", { class: "num" }, num(f.score)), h("td", {}, h("small", {}, f.detail)))))),
    versions && versions.length ? h("p", { class: "muted", style: { marginBottom: 0 } }, "Code versions: ", versions.join(" · ")) : null,
    h("p", { class: "muted", style: { marginBottom: 0 } }, h("small", {}, c.basis)));
}

const ROUTES = { "": pageHome, search: pageSearch, mix: pageMix, design: pageDesign, estimate: pageEstimate,
  rates: pageRates, review: pageReview, tables: pageTables, learning: pageLearning };

// ---------- search & ask
async function pageSearch(main) {
  let mode = "search";
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const q = h("input", { type: "search", placeholder: "e.g. nominal cover for severe exposure, lap length in tension, rate for M30 design mix…", autofocus: true, value: params.get("q") || "" });
  const code = h("select", {}, SOURCES.map((c) => h("option", { value: c, selected: c === (params.get("code") || "") }, c || "All sources")));
  const k = h("select", {}, [5, 8, 12, 20].map((n) => h("option", { value: n, selected: n === 8 }, `${n} results`)));
  const out = h("div");
  const segBtns = ["search", "ask"].map((m) => h("button", { class: m === mode ? "on" : "", onclick: () => { mode = m; segBtns.forEach((b, i) => b.classList.toggle("on", ["search", "ask"][i] === m)); } }, m === "search" ? "Search" : "Ask (cited answer)"));
  const go = async () => {
    if (q.value.trim().length < 2) return;
    out.replaceChildren(loading(mode === "ask" ? "Retrieving sources and generating a cited answer…" : "Searching…"));
    const filters = code.value ? { code_no: code.value } : null;
    try {
      if (mode === "ask") {
        if (!can("generate")) throw { detail: "Your API key lacks the 'generate' scope." };
        out.replaceChildren(renderAnswer(await api("/v1/generate", { method: "POST", body: { query: q.value, top_k: Number(k.value), filters } })));
      } else {
        out.replaceChildren(renderResults(await api("/v1/retrieve", { method: "POST", body: { query: q.value, top_k: Number(k.value), filters } })));
      }
    } catch (e) { out.replaceChildren(errorBox(e)); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  fill(main, 
    pageHead("Standards & DSR", "Search & Ask", "Hybrid search (keyword + semantic + reranking) over IS 456, IS 10262, IS 1200, IS 2386, CPWD DSR 2023 and CPWD DAR 2023. Every result links to the original page."),
    h("div", { class: "card" }, h("div", { class: "row" }, h("div", { class: "seg" }, segBtns), h("div", { style: { flex: "1 1 320px" } }, q),
      h("div", { style: { width: "150px" } }, code), h("div", { style: { width: "120px" } }, k), h("button", { class: "primary", onclick: go }, "Go"))),
    h("div", { style: { marginTop: "16px" } }, out));
  q.focus();
  if (params.get("q")) go();
}
function resultCard(c, idx, qid) {
  const m = c.metadata || {};
  const via = (c.retrieval || []).filter((x) => !x.startsWith("from:"));
  const body = h("div", { class: "body clamp" }, c.text);
  const card = h("div", { class: "result", id: idx != null ? `src-${idx}` : null },
    h("div", { class: "head" }, idx != null ? h("span", { class: "badge accent" }, `S${idx}`) : null,
      h("span", { class: "cite" }, c.citation.ref),
      c.citation.verified ? null : h("span", { class: "badge warn", title: (m.review_reasons || []).join("; ") }, "⚠ unverified OCR"),
      via.includes("exact") ? h("span", { class: "badge info" }, "exact match") : null,
      via.includes("xref") ? h("span", { class: "badge" }, "cross-reference") : null,
      via.includes("amendment") ? h("span", { class: "badge info" }, "amendment") : null,
      m.rate != null ? h("span", { class: "badge ok" }, `${inr(m.rate)} / ${unitLabel(m.unit)}`) : null,
      h("span", { class: "spacer" }),
      feedbackButtons(qid, "chunk", c.citation.chunk_id),
      h("button", { class: "ghost small", onclick: () => showSourcePage(c.citation.doc_id, c.citation.page_start, c.citation.ref) }, "View page ↗")),
    body,
    c.table && c.table.rows && c.table.rows.length ? tableFromRows(c.table) : null);
  body.addEventListener("click", () => body.classList.toggle("clamp"));
  body.title = "Click to expand";
  return card;
}
function tableFromRows(t) {
  return h("details", { style: { marginTop: "8px" } }, h("summary", { class: "muted" }, `Table rows (${t.rows.length}) — OCR'd cells, verify against the page`),
    h("div", { class: "tbl-wrap", style: { marginTop: "6px", maxHeight: "320px" } }, h("table", { class: "t" }, h("tbody", {}, t.rows.map((r) => h("tr", {}, r.map((x) => h("td", {}, x))))))));
}
function renderResults(pack) {
  if (!pack.chunks.length) return h("div", { class: "empty" }, "No results.");
  return h("div", {},
    (pack.warnings || []).length ? h("div", { class: "alert warn", style: { marginBottom: "10px" } }, pack.warnings.join(" · ")) : null,
    pack.chunks.map((c) => resultCard(c, null, pack.query_id)));
}
// Learning loop: ratings go to /v1/feedback; an admin reviews them on the Learning page.
function feedbackButtons(qid, kind, chunkId) {
  if (!qid) return null;
  const wrap = h("span", { class: "rate", title: "Was this useful? Your rating helps the search and answers improve." });
  const send = async (rating, correction = null) => {
    try {
      await api("/v1/feedback", { method: "POST", body: { query_id: qid, kind, rating, chunk_id: chunkId || null, correction } });
      [...wrap.children].forEach((b) => b.classList.toggle("on", Number(b.dataset.r) === rating));
      toast(correction ? "Thanks — your correction was sent for review." : rating > 0 ? "Thanks — marked useful." : "Thanks — marked not useful.");
    } catch (e) { toast(errText(e), true); }
  };
  wrap.append(
    h("button", { class: "ghost small", "data-r": "1", "aria-label": "Useful", onclick: () => send(1) }, "👍"),
    h("button", { class: "ghost small", "data-r": "-1", "aria-label": "Not useful", onclick: () => (kind === "answer" ? correctionModal(send) : send(-1)) }, "👎"));
  return wrap;
}
function correctionModal(send) {
  const ta = h("textarea", { rows: 8, placeholder: "Optional: the answer you expected, with the clause / table / DSR item it comes from." });
  let close;
  const submit = h("button", { class: "primary small", onclick: async () => { await send(-1, ta.value.trim() || null); close(); } }, "Send");
  close = modal("What should the answer have been?", h("div", { class: "stack" },
    h("p", { class: "muted", style: { margin: 0 } }, "An admin reviews corrections before they are used as examples for future answers. Leave it empty to just mark the answer as not useful."), ta),
    { actions: [submit] });
  ta.focus();
}
function renderAnswer(r) {
  const html = esc(r.answer || "(no answer)").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\[S(\d+)\]/g, '<a href="#" class="s" data-s="$1">S$1</a>');
  const ans = h("div", { class: "answer", html });
  ans.addEventListener("click", (e) => {
    const a = e.target.closest("a.s"); if (!a) return; e.preventDefault();
    const el = document.getElementById("src-" + a.dataset.s);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("hl"); setTimeout(() => el.classList.remove("hl"), 1800); }
  });
  const v = r.verification || {};
  return h("div", { class: "stack" },
    h("div", { class: "card" },
      h("div", { class: "row", style: { marginBottom: "10px" } }, h("h2", { style: { margin: 0 } }, "Answer"),
        (r.exemplars_used || []).length ? h("span", { class: "badge info", title: "Engineer-approved answers to similar questions were shown to the model as style examples" }, `${r.exemplars_used.length} approved example(s) used`) : null,
        v.faithful_numbers ? h("span", { class: "badge ok", title: "every number in the answer appears in the sources" }, `✓ ${v.grounded}/${v.numbers_in_answer} numbers grounded`)
          : h("span", { class: "badge bad", title: "numbers not found in any source" }, `✗ ungrounded: ${(v.ungrounded_numbers || []).join(", ")}`),
        h("span", { class: "spacer" }), feedbackButtons(r.query_id, "answer")),
      ans),
    h("h2", {}, "Sources"),
    r.sources.map((s, i) => resultCard({ citation: s, text: "", metadata: {}, retrieval: [] }, i + 1, r.query_id)));
}

// ---------- mix design
async function pageMix(main) {
  const form = schemaForm("MixDesignInput", { values: { grade: "M30", exposure: "severe", w_c_ratio: 0.42 } });
  const out = h("div", { style: { marginTop: "16px" } });
  const run = async () => {
    out.replaceChildren(loading("Calculating…"));
    try { out.replaceChildren(renderMix(await api("/v1/mix-design", { method: "POST", body: form.value() }))); }
    catch (e) { out.replaceChildren(errorBox(e)); }
  };
  fill(main, 
    pageHead("IS 10262 : 2019", "Concrete mix design", "IS 10262 : 2019 (Section 2) with IS 456 Table 5 durability limits. Output is TRIAL MIX No. 1 — confirm by trial batches."),
    h("div", { class: "alert info", style: { marginBottom: "16px" } }, h("b", {}, "w/c ratio: "),
      "leave it blank to read it from IS 10262 Fig. 1 for your cement type, or enter the value from your own trial mixes."),
    h("div", { class: "card" }, form.el, h("div", { class: "row end", style: { marginTop: "14px" } },
      h("button", { onclick: () => { pageMix(main); } }, "Reset"), h("button", { class: "primary", onclick: run }, "Design mix"))),
    out);
}
function renderMix(res) {
  const o = res.outputs || {};
  const ssd = o.per_m3_ssd || {}, field = o.per_m3_field || {};
  const rows = [["Cement", "cement_kg"], ["Fly ash", "fly_ash_kg"], ["Water", "water_kg", "water_to_add_kg"], ["Fine aggregate", "fine_aggregate_kg"],
    ["Coarse aggregate", "coarse_aggregate_kg"], ["Chemical admixture", "admixture_kg"]];
  return h("div", { class: "stack" },
    h("div", { class: "grid cols-4" }, stat(num(o.target_strength_n_mm2), "Target strength f'ck (N/mm²)", ""),
      stat(num(o.w_c_ratio, 3), "Free w/c (w/cm)", ""), stat(num(o.cement_bags_per_m3), "Cement bags / m³", "50 kg bags"),
      stat(o.ratio_by_mass ? o.ratio_by_mass.split(" (")[0] : "—", "Ratio by mass", "cement : FA : CA")),
    h("div", { class: "card" }, h("h2", {}, "Quantities per m³ of concrete"),
      h("table", { class: "t" }, h("thead", {}, h("tr", {}, h("th", {}, "Material"), h("th", { class: "num" }, "SSD condition (kg)"), h("th", { class: "num", title: "dry aggregates (Annex A), or wet if surface moisture is entered (Annex B)" }, "Field, as batched (kg)"))),
        h("tbody", {}, rows.filter(([, a]) => ssd[a] || field[a]).map(([n, a, b]) => h("tr", {}, h("td", {}, n), h("td", { class: "num" }, num(ssd[a], 1)), h("td", { class: "num" }, num(field[b || a], 1))))))),
    confidenceCard(o.confidence, o.code_versions),
    renderCalc({ ...res, outputs: {} }));
}

// ---------- RCC design
async function pageDesign(main) {
  const kinds = {
    beam: { schema: "BeamInput", ep: "/v1/design/beam", label: "Beam", values: { b_mm: 230, D_mm: 500, span_m: 5, dead_load_kN_m: 15, live_load_kN_m: 10 } },
    slab: { schema: "SlabInput", ep: "/v1/design/slab", label: "One-way slab", values: { span_m: 3.5 } },
    column: { schema: "ColumnInput", ep: "/v1/design/column", label: "Short column (axial)", values: { b_mm: 400, D_mm: 450, Pu_kN: 1500 } },
    footing: { schema: "FootingInput", ep: "/v1/design/footing", label: "Isolated footing", values: { column_b_mm: 300, column_D_mm: 450, service_load_kN: 1200, sbc_kN_m2: 200 } },
  };
  let cur = new URLSearchParams(location.hash.split("?")[1] || "").get("k") || "beam";
  if (!kinds[cur]) cur = "beam";
  const body = h("div");
  const tabs = h("div", { class: "tabs" });
  const show = () => {
    tabs.replaceChildren(...Object.entries(kinds).map(([k, v]) => h("button", { class: k === cur ? "on" : "", onclick: () => { cur = k; show(); } }, v.label)));
    const kd = kinds[cur];
    const form = schemaForm(kd.schema, { values: kd.values });
    const out = h("div", { style: { marginTop: "16px" } });
    body.replaceChildren(h("div", { class: "card" }, form.el, h("div", { class: "row end", style: { marginTop: "14px" } },
      h("button", { class: "primary", onclick: async () => {
        out.replaceChildren(loading("Designing…"));
        try { out.replaceChildren(renderCalc(await api(kd.ep, { method: "POST", body: form.value() }))); } catch (e) { out.replaceChildren(errorBox(e)); }
      } }, `Design ${kd.label.toLowerCase()}`))), out);
  };
  fill(main, pageHead("IS 456 : 2000", "RCC element design (Design aid only)", "IS 456 : 2000 limit-state design aids with clause-referenced checks."),
    h("div", { class: "alert warn", style: { marginBottom: "18px" } }, h("b", {}, "Design aid only. "), "Not a checked structural design. Every result must be reviewed by a qualified structural engineer before use."),
    tabs, body);
  show();
}

// ---------- estimate builder
const EL_TYPES = ["footing", "pcc_bed", "column", "beam", "slab", "wall", "lintel", "chajja", "staircase_waist"];
const EXAMPLE_ELEMENTS = [
  { id: "PCC", type: "pcc_bed", length_m: 1.8, width_m: 1.8, depth_m: 0.1, count: 8, grade: "M10", level: "below_plinth" },
  { id: "F1", type: "footing", length_m: 1.6, width_m: 1.6, depth_m: 0.45, count: 8, grade: "M25", level: "below_plinth" },
  { id: "C1", type: "column", length_m: 3.2, width_m: 0.3, depth_m: 0.45, count: 8, grade: "M25", level: "above_plinth" },
  { id: "B1", type: "beam", length_m: 4.55, width_m: 0.23, depth_m: 0.33, count: 10, grade: "M25", level: "above_plinth" },
  { id: "S1", type: "slab", length_m: 10, width_m: 8, depth_m: 0.125, count: 1, grade: "M25", level: "above_plinth" },
];
async function pageEstimate(main) {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const out = h("div", { style: { marginTop: "16px" } });
  let rows = EXAMPLE_ELEMENTS.map((r) => ({ ...r }));
  const project = h("input", { value: "New estimate" });
  const location_ = h("input", { value: S.location });
  const mix = h("select", {}, h("option", { value: "design_mix" }, "Design mix (DSR 5.33.x)"), h("option", { value: "nominal_mix" }, "Nominal mix 1:1.5:3 (DSR 5.1–5.3)"));
  const pcc = h("select", {}, ["1:4:8", "1:5:10", "1:3:6", "1:2:4"].map((m) => h("option", { value: m }, m)));
  const cont = h("input", { type: "number", value: 3, step: "0.5", min: 0 });
  const floorsV = h("input", { type: "number", value: 0, min: 0 });
  const tbody = h("tbody");
  const cols = [["id", "ID", "text", 56], ["type", "Type", EL_TYPES, 118], ["length_m", "Length / height (m)", "number", 80], ["width_m", "Width (m)", "number", 72],
    ["depth_m", "Depth / thk (m)", "number", 76], ["count", "Nos", "number", 54], ["grade", "Grade", "grade", 64], ["level", "Level", ["below_plinth", "above_plinth"], 128],
    ["steel_kg_per_m3", "Steel kg/m³", "number", 76]];
  const drawRows = () => tbody.replaceChildren(...rows.map((r, i) => h("tr", {},
    cols.map(([k, , type]) => h("td", {}, cellInput(r, k, type))),
    h("td", {}, h("button", { class: "ghost small", title: "Remove", onclick: () => { rows.splice(i, 1); drawRows(); } }, "✕")))));
  function cellInput(r, k, type) {
    if (Array.isArray(type)) return h("select", { onchange: (e) => { r[k] = e.target.value; } }, type.map((t) => h("option", { value: t, selected: r[k] === t }, titleCase(t))));
    if (type === "grade") return h("input", { value: r[k] ?? "M25", list: "grades", oninput: (e) => { r[k] = e.target.value; } });
    return h("input", { type, value: r[k] ?? "", step: "any", placeholder: k === "steel_kg_per_m3" ? "default" : "",
      oninput: (e) => { r[k] = type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value; } });
  }
  drawRows();
  const build = async () => {
    out.replaceChildren(loading("Measuring quantities and pricing from DSR…"));
    const elements = rows.map((r) => Object.fromEntries(Object.entries(r).filter(([, v]) => v !== null && v !== "")));
    try {
      const est = await api("/v1/estimate", { method: "POST", body: { project: project.value, location: location_.value, mix_type: mix.value,
        pcc_mix: pcc.value, contingency_percent: Number(cont.value), floors_above_v: Number(floorsV.value || 0), elements } });
      out.replaceChildren(renderEstimate(est)); refreshHistory();
    } catch (e) { out.replaceChildren(errorBox(e)); }
  };
  const history = h("div");
  const refreshHistory = async () => {
    try {
      const list = await api("/v1/estimates?limit=100");
      history.replaceChildren(list.length ? paginate(list, 8, (rows) => h("table", { class: "t" }, h("tbody", {}, rows.map((e) => h("tr", { class: "click", onclick: async () => {
        out.replaceChildren(loading()); out.replaceChildren(renderEstimate(await api("/v1/estimate/" + e.id))); out.scrollIntoView({ behavior: "smooth" }); } },
        h("td", {}, h("b", {}, e.project), h("br"), h("small", {}, `${e.created.slice(0, 16)} · ${e.location} · ${e.lines} lines`)), h("td", { class: "num" }, inr(e.grand_total)))))), "saved")
        : h("div", { class: "muted" }, "None yet."));
    } catch (e) { history.replaceChildren(errorBox(e)); }
  };
  fill(main, 
    pageHead("IS 1200 · CPWD DSR 2023", "Estimate / Bill of Quantities", "Element geometry → IS 1200 quantities → CPWD DSR 2023 rates × location index → BOQ, cement statement, cost heads → Excel / PDF."),
    h("datalist", { id: "grades" }, ["M10", "M15", "M20", "M25", "M30", "M35", "M40", "M50"].map((g) => h("option", { value: g }))),
    h("div", { class: "est-layout" },
      h("div", { class: "card" },
        h("div", { class: "form-grid" }, lab("Project", project), lab("Location (cost index)", location_), lab("Concrete", mix), lab("PCC mix", pcc),
          lab("Contingency %", cont), lab("Storeys above floor V", floorsV)),
        h("h3", { style: { marginTop: "18px" } }, "Elements"),
        h("p", { class: "muted", style: { marginTop: 0 } }, "Beams: clear length between columns, depth below slab. Columns: length = height. Slabs/footings: depth = thickness. Leave steel blank to use the default thumb-rule density (flagged in the output)."),
        h("div", { class: "tbl-wrap" }, h("table", { class: "t edit" }, h("thead", {}, h("tr", {}, cols.map(([, l, , w]) => h("th", { style: { minWidth: w + "px" } }, l)), h("th"))), tbody)),
        h("div", { class: "row", style: { marginTop: "12px" } },
          h("button", { onclick: () => { rows.push({ id: `E${rows.length + 1}`, type: "beam", length_m: 3, width_m: 0.23, depth_m: 0.3, count: 1, grade: "M25", level: "above_plinth" }); drawRows(); } }, "+ Add element"),
          h("button", { onclick: () => { rows = []; drawRows(); } }, "Clear"),
          h("span", { class: "spacer" }), h("button", { class: "primary", onclick: build }, "Build estimate"))),
      h("div", { class: "card" }, h("h3", {}, "Saved estimates"), history)),
    out);
  refreshHistory();
  if (params.get("id")) { out.replaceChildren(loading()); try { out.replaceChildren(renderEstimate(await api("/v1/estimate/" + params.get("id")))); } catch (e) { out.replaceChildren(errorBox(e)); } }
}
function lab(text, input) { return h("label", { class: "f" }, h("span", {}, text), input); }
async function download(id, fmt) {
  try {
    const blob = await api(`/v1/estimate/${id}/export?format=${fmt}`, { blob: true });
    const a = h("a", { href: URL.createObjectURL(blob), download: `estimate_${id}.${fmt}` });
    document.body.append(a); a.click(); a.remove();
  } catch (e) { toast(errText(e), true); }
}
const HEAD_LABELS = { material: "Material", labour: "Labour", equipment: "Equipment (hire & running)", carriage: "Carriage",
  sundries: "Sundries", other: "Other", water_charges: "Water charges (1%)", gst: "GST (factor 0.2127)", cpoh: "Contractor's profit & overheads (15%)", cess: "Cess (1%)" };

// DAR split of the BOQ (estimate.cost_heads); estimates saved before DAR was added carry the old two-way split
function renderCostHeads(ch) {
  if (!ch || ch.error) return h("div", { class: "muted" }, ch ? ch.error : "not computed");
  if (!ch.heads) return h("dl", { class: "kv" },
    h("dt", {}, "Materials (basic cost)"), h("dd", {}, h("b", {}, inr(ch.materials_basic_cost)), ` (${ch.materials_share_percent}%)`),
    h("dt", {}, "Labour + T&P + formwork + overheads + GST"), h("dd", {}, h("b", {}, inr(ch.labour_equipment_overheads_gst_formwork))));
  return h("div", {},
    h("table", { class: "t" }, h("tbody", {}, Object.entries(ch.heads).map(([k, v]) => h("tr", {}, h("td", {}, HEAD_LABELS[k] || titleCase(k)),
      h("td", { class: "num" }, inr(v)), h("td", { class: "num" }, `${num(ch.share_percent[k])}%`))))),
    ch.not_analysed_items && ch.not_analysed_items.length ? h("p", { class: "muted" }, `No DAR analysis for ${ch.not_analysed_items.join(", ")} (${inr(ch.not_analysed_amount)} not split).`) : null,
    h("p", { class: "muted" }, h("small", {}, ch.basis)));
}

function renderEstimate(est) {
  const s = est.summary, q = est.quantities;
  const ch = est.cost_heads;
  return h("div", { class: "stack" },
    h("div", { class: "card" }, h("div", { class: "row" },
      h("div", {}, h("h2", { style: { margin: 0 } }, est.project), h("small", {}, `${est.sor} · ${est.location} · ${est.created} · id ${est.id}`)),
      h("span", { class: "spacer" }),
      h("div", { style: { textAlign: "right" } }, h("div", { class: "muted" }, "Grand total"), h("div", { class: "big-num" }, inr(s.grand_total))),
      h("div", { class: "row no-print" }, h("button", { onclick: () => download(est.id, "xlsx") }, "⬇ Excel"), h("button", { onclick: () => download(est.id, "pdf") }, "⬇ PDF"),
        h("button", { onclick: () => window.print() }, "Print")))),
    (est.warnings || []).length ? h("div", { class: "alert warn" }, h("b", {}, "Rates to verify before use"), h("ul", {}, est.warnings.map((w) => h("li", {}, w)))) : null,
    h("div", { class: "card" }, h("h2", {}, "Bill of Quantities"),
      h("div", { class: "tbl-wrap" }, h("table", { class: "t" },
        h("thead", {}, h("tr", {}, ["Sl", "DSR item", "Description", "Unit", "Qty", "Rate", "Amount", ""].map((x, i) => h("th", { class: i >= 4 && i <= 6 ? "num" : "" }, x)))),
        h("tbody", {}, est.boq.map((l) => h("tr", { class: l.rate_confirmed ? "" : "flag" },
          h("td", {}, l.sl), h("td", { class: "mono" }, l.item_code),
          h("td", {}, h("div", { class: "clamp", title: l.description }, l.description), h("small", {}, l.citation, " · from ", l.derived_from.join(", "))),
          h("td", {}, unitLabel(l.unit)), h("td", { class: "num" }, num(l.quantity)), h("td", { class: "num" }, inr(l.rate)), h("td", { class: "num" }, inr(l.amount)),
          h("td", {}, l.rate_confirmed ? h("span", { class: "badge ok", title: l.dar_match ? "Equal to its CPWD DAR analysis" : "Confirmed by OCR cross-check" }, l.dar_match ? "✓ DAR" : "✓") : h("span", { class: "badge warn" }, "verify"),
            l.matched_by === "search" ? h("span", { class: "badge info", title: "Item chosen by search - confirm it" }, "searched") : null)))),
        h("tfoot", {},
          h("tr", {}, h("td", { colspan: 6 }, "Sub-total"), h("td", { class: "num" }, inr(s.subtotal)), h("td")),
          h("tr", {}, h("td", { colspan: 6 }, `Contingency @ ${s.contingency_percent}%`), h("td", { class: "num" }, inr(s.contingency)), h("td")),
          h("tr", {}, h("td", { colspan: 6 }, "Grand total"), h("td", { class: "num" }, inr(s.grand_total)), h("td"))))),
      h("p", { class: "muted", style: { marginBottom: 0 } }, s.note)),
    confidenceCard(est.confidence, est.code_versions),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("h2", {}, "Quantities (IS 1200)"),
        h("div", { class: "tbl-wrap" }, h("table", { class: "t" },
          h("thead", {}, h("tr", {}, ["Element", "Type", "Nos", "Concrete m³", "Formwork m²", "Steel kg"].map((x, i) => h("th", { class: i >= 2 ? "num" : "" }, x)))),
          h("tbody", {}, q.rows.map((r) => h("tr", {}, h("td", {}, r.id), h("td", {}, titleCase(r.type)), h("td", { class: "num" }, r.count),
            h("td", { class: "num" }, num(r.concrete_m3)), h("td", { class: "num" }, num(r.formwork_m2)), h("td", { class: "num" }, num(r.steel_kg, 1))))),
          h("tfoot", {}, h("tr", {}, h("td", { colspan: 3 }, "Total"), h("td", { class: "num" }, num(q.totals.concrete_m3)), h("td", { class: "num" }, num(q.totals.formwork_m2)),
            h("td", { class: "num" }, num(q.totals.steel_kg, 1))))))),
      h("div", { class: "card" }, h("h2", {}, "Cost heads (CPWD DAR 2023)"), renderCostHeads(ch),
        est.cement_statement && est.cement_statement.length ? h("div", {}, h("h3", { style: { marginTop: "14px" } }, "Cement consumption (DSR coefficients)"),
          h("table", { class: "t" }, h("tbody", {}, est.cement_statement.map((c) => h("tr", {}, h("td", { class: "mono" }, c.item_code),
            h("td", { class: "num" }, `${num(c.quantity)} ${unitLabel(c.unit)} ×${c.coefficient_qtl_per_unit} qtl`), h("td", { class: "num" }, h("b", {}, `${num(c.cement_qtl)} qtl`))))),
            h("tfoot", {}, h("tr", {}, h("td", { colspan: 2 }, "Total cement"), h("td", { class: "num" }, `${num(est.cement_statement.reduce((a, c) => a + c.cement_qtl, 0))} qtl`))))) : null)),
    h("details", { class: "card" }, h("summary", {}, h("b", {}, "Assumptions & references")),
      h("ul", {}, est.assumptions.map((a) => h("li", {}, a))),
      h("div", {}, (est.references || []).map((r) => h("span", { class: "badge", style: { margin: "2px" } }, `${r.code}, ${r.item}`)))));
}

// ---------- DSR rates browser
async function pageRates(main) {
  const q = h("input", { type: "search", placeholder: "keywords (e.g. centering columns) or item code (e.g. 5.33)", value: "5.33" });
  const unit = h("select", {}, ["", "m3", "m2", "m", "kg", "each", "quintal", "t"].map((u) => h("option", { value: u }, unitLabel(u) || "Any unit")));
  const loc = h("input", { value: S.location, style: { width: "120px" } });
  const out = h("div", { style: { marginTop: "16px" } });
  const go = async () => {
    out.replaceChildren(loading("Searching DSR…"));
    try {
      const list = await api(`/v1/rates?q=${encodeURIComponent(q.value)}&unit=${unit.value}&limit=200`);
      if (!list.length) return out.replaceChildren(h("div", { class: "empty" }, "No DSR items match."));
      out.replaceChildren(
        h("p", { class: "muted", style: { margin: "0 0 10px" } }, list.length >= 200 ? "First 200 matches. Narrow the search to see others. " : "", "Click a row for details, OCR readings and the source page."),
        paginate(list, 25, (rows) => h("div", { class: "tbl-wrap" }, h("table", { class: "t" },
          h("thead", {}, h("tr", {}, h("th", {}, "Item"), h("th", {}, "Description"), h("th", {}, "Unit"), h("th", { class: "num" }, "DSR rate"), h("th", {}, "Check"))),
          h("tbody", {}, rows.map((it) => h("tr", { class: "click" + (it.needs_review ? " flag" : ""), onclick: () => rateDetail(it, loc.value) },
            h("td", { class: "mono" }, it.code), h("td", {}, h("div", { class: "clamp" }, it.full_description)), h("td", {}, unitLabel(it.unit) || it.unit_raw),
            h("td", { class: "num" }, inr(it.rate)), h("td", {}, it.needs_review ? h("span", { class: "badge warn" }, "verify") : h("span", { class: "badge ok" }, "✓"))))))),
          "items"));
    } catch (e) { out.replaceChildren(errorBox(e)); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  fill(main, pageHead("Schedule of rates", "CPWD DSR 2023 rates", "3,200 items of the Delhi Schedule of Rates 2023, read from the clean copy and cross-checked with the scan and CPWD DAR 2023. Item rates include materials, labour, T&P, water, CP&OH and GST @18%."),
    h("div", { class: "card" }, h("div", { class: "row" }, h("div", { style: { flex: "1 1 300px" } }, q), h("div", { style: { width: "130px" } }, unit),
      h("span", { class: "muted" }, "Location"), loc, h("button", { class: "primary", onclick: go }, "Search"))), out);
  go();
}
async function rateDetail(it, location_) {
  let priced = null;
  try { priced = await api(`/v1/rates/${encodeURIComponent(it.code)}?location=${encodeURIComponent(location_)}`); } catch (e) { priced = { error: errText(e) }; }
  modal(`DSR ${it.sor_year} item ${it.code}`, h("div", { class: "stack" },
    h("p", { style: { margin: 0 } }, it.full_description),
    h("dl", { class: "kv" },
      h("dt", {}, "Sub-head"), h("dd", {}, it.sub_head || "—"),
      h("dt", {}, "Unit"), h("dd", {}, `${unitLabel(it.unit) || "—"} (OCR read: ${it.unit_raw || "—"})`),
      h("dt", {}, "DSR rate (Delhi)"), h("dd", {}, h("b", {}, inr(it.rate))),
      h("dt", {}, `Rate at ${location_}`), h("dd", {}, priced.error ? h("span", { class: "badge warn" }, priced.error) : `${inr(priced.rate)} (cost index ${priced.cost_index})`),
      h("dt", {}, "Readings"), h("dd", { class: "mono", title: "scan English page / scan digit re-read / clean copy" }, (it.rate_readings || []).map((r) => r || "—").join("  /  "), ` · ${it.rate_votes}/3 agree`),
      h("dt", {}, "Status"), h("dd", {}, it.needs_review ? h("span", { class: "badge warn" }, "needs review: " + (it.review_reasons || []).join("; ")) : h("span", { class: "badge ok" }, it.corrected_by ? `verified by ${it.corrected_by}` : "confirmed")),
      h("dt", {}, "Source"), h("dd", {}, h("button", { class: "small", onclick: () => showSourcePage(it.doc_id, it.page, `DSR item ${it.code}`) }, `View PDF page ${it.page} ↗`)))));
}

// ---------- review queue
async function pageReview(main) {
  if (!can("admin")) { fill(main, h("div", { class: "alert warn" }, "The review queue needs an API key with the 'admin' scope.")); return; }
  const st = { tab: "sor", q: "", head: "", category: "", offset: 0, rows: [], total: 0, sel: -1 };
  const checker = h("input", { value: S.checker || (S.me && S.me.name) || "", placeholder: "your name", style: { width: "180px" },
    oninput: (e) => { S.checker = e.target.value; lsSet("crag_checker", e.target.value); } });
  const tabs = h("div", { class: "tabs" });
  const filters = h("div", { class: "row", style: { marginBottom: "12px" } });
  const list = h("div", { class: "list tbl-wrap" });
  const detail = h("div", { class: "card" }, h("div", { class: "empty" }, "Select a row to review it against the scanned page."));
  const pager = h("div");

  const load = async () => {
    list.replaceChildren(loading());
    const p = new URLSearchParams({ offset: st.offset, limit: 50 });
    if (st.q) p.set("q", st.q);
    if (st.tab === "sor" && st.head) p.set("head", st.head);
    if (st.tab === "basic" && st.category) p.set("category", st.category);
    try {
      const r = await api(`/v1/admin/review/${st.tab === "dar" ? "dar-dsr" : st.tab}?${p}`);
      st.rows = r.rows; st.total = r.total; st.sel = -1;
      drawList(); drawPager();
      detail.replaceChildren(h("div", { class: "empty" }, st.rows.length ? (st.tab === "dar" ? "Select an item to compare the DSR rate with its DAR analysis." : "Select a row to review it against the scanned page.") : "Nothing left to review here. 🎉"));
    } catch (e) { list.replaceChildren(errorBox(e)); }
  };
  const drawTabs = () => tabs.replaceChildren(...[["sor", "DSR items"], ["basic", "Basic rates"], ["dar", "DAR vs DSR"]].map(([k, l]) =>
    h("button", { class: st.tab === k ? "on" : "", onclick: () => { st.tab = k; st.offset = 0; drawTabs(); drawFilters(); load(); } }, l)));
  const drawFilters = () => {
    const q = h("input", { type: "search", placeholder: "search description / code", value: st.q, onkeydown: (e) => { if (e.key === "Enter") { st.q = e.target.value; st.offset = 0; load(); } } });
    const sel = st.tab === "dar" ? h("span", { class: "muted" }, "DSR rate ≠ its DAR analysis")
      : st.tab === "sor"
      ? h("select", { onchange: (e) => { st.head = e.target.value; st.offset = 0; load(); } }, h("option", { value: "" }, "All sub-heads"),
        Array.from({ length: 26 }, (_, i) => h("option", { value: i + 1, selected: String(i + 1) === String(st.head) }, `Sub-head ${i + 1}${[4, 5].includes(i + 1) ? " (concrete/RCC)" : ""}`)))
      : h("select", { onchange: (e) => { st.category = e.target.value; st.offset = 0; load(); } }, ["", "labour", "materials", "hire_charges"].map((c) => h("option", { value: c, selected: c === st.category }, c ? titleCase(c) : "All categories")));
    filters.replaceChildren(h("div", { style: { flex: "1 1 240px" } }, q), h("div", { style: { width: "220px" } }, sel),
      h("span", { class: "muted" }, "Checked by"), checker);
  };
  const drawPager = () => pager.replaceChildren(pagerBar(Math.floor(st.offset / 50), Math.max(1, Math.ceil(st.total / 50)), st.total, 50,
    (p) => { st.offset = p * 50; load(); }, "rows need review"));
  const drawList = () => {
    if (!st.rows.length) { list.replaceChildren(h("div", { class: "empty" }, "No rows.")); return; }
    if (st.tab === "dar") {
      list.replaceChildren(h("table", { class: "t" },
        h("thead", {}, h("tr", {}, h("th", {}, "Item"), h("th", {}, "Description"), h("th", { class: "num" }, "DSR"), h("th", { class: "num" }, "DAR"))),
        h("tbody", {}, st.rows.map((r, i) => h("tr", { class: "click" + (i === st.sel ? " sel" : ""), onclick: () => selectDar(i) },
          h("td", { class: "mono" }, r.code), h("td", {}, h("div", { class: "clamp" }, r.description)),
          h("td", { class: "num" }, num(r.dsr_rate)), h("td", { class: "num" }, num(r.dar_rate), r.dar_flagged ? h("div", {}, h("small", { class: "muted" }, "DAR flagged")) : null))))));
      return;
    }
    list.replaceChildren(h("table", { class: "t" },
      h("thead", {}, h("tr", {}, h("th", {}, "Page"), h("th", {}, st.tab === "sor" ? "Item" : "Code"), h("th", {}, "Description"), h("th", { class: "num" }, "Rate"), h("th", {}, "Issue"))),
      h("tbody", {}, st.rows.map((r, i) => h("tr", { class: "click" + (i === st.sel ? " sel" : ""), onclick: () => select(i) },
        h("td", { class: "num" }, r.page), h("td", { class: "mono" }, r.code), h("td", {}, h("div", { class: "clamp" }, r.description)),
        h("td", { class: "num" }, num(r.rate)), h("td", {}, h("small", {}, shortReason(r.review_reasons))))))));
  };
  // DAR vs DSR: the DSR scan page and the DAR page side by side; three ways to resolve
  const selectDar = async (i) => {
    st.sel = i; drawList();
    const r = st.rows[i];
    const rate = h("input", { type: "number", step: "0.05", placeholder: "correct rate (₹)" });
    const note = h("input", { placeholder: "optional note" });
    const dsrView = h("div", { class: "page-view" }, loading("Loading DSR page…"));
    const darView = h("div", { class: "page-view" }, loading("Loading DAR page…"));
    const decide = async (decision) => {
      if ((S.checker || "").trim().length < 2) { toast("Enter your name in “Checked by” first.", true); return; }
      if (decision === "other" && rate.value === "") { toast("Type the correct rate first.", true); rate.focus(); return; }
      try {
        const out = await api("/v1/admin/review/dar-dsr", { method: "POST", body: { key: r.key, decision, checked_by: S.checker.trim(),
          rate: rate.value === "" ? null : Number(rate.value), note: note.value || null } });
        toast(`${r.code}: DSR rate is now ₹${num(out.dsr_rate)} - written to config/corrections and applied.`);
        st.rows.splice(i, 1); st.total -= 1; drawPager();
        if (S.overview) { S.overview.review.dar_dsr -= 1; renderChrome(); }
        if (st.rows.length) selectDar(Math.min(i, st.rows.length - 1)); else load();
      } catch (e) { toast(errText(e), true); }
    };
    rate.addEventListener("keydown", (e) => { if (e.key === "Enter") decide("other"); });
    const darPage = r.dar_pages[1];
    detail.replaceChildren(h("div", { class: "stack" },
      h("div", { class: "row" }, h("b", { class: "mono" }, r.code), h("span", { class: "muted" }, `DSR p.${r.dsr_page} · DAR ${r.dar_doc_id.slice(-2)} p.${r.dar_pages[0]}${r.dar_pages[1] !== r.dar_pages[0] ? "–" + r.dar_pages[1] : ""}`)),
      h("div", {}, h("div", { class: "clamp" }, r.description)),
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("small", { class: "muted" }, "DSR (scanned, OCR)"), h("div", { class: "big-num" }, `₹${num(r.dsr_rate)}`), h("small", {}, `per ${r.dsr_unit || "?"}`),
          r.dsr_readings.length ? h("div", {}, h("small", { class: "muted" }, "OCR readings: "), r.dsr_readings.map((x) => h("span", { class: "chip" + (x ? "" : " muted") }, x || "—"))) : null),
        h("div", { class: "card" }, h("small", { class: "muted" }, "DAR (digital, worked out)"), h("div", { class: "big-num" }, `₹${num(r.dar_rate)}`), h("small", {}, `per ${r.dar_unit || "?"}`),
          h("div", {}, h("small", { class: "muted" }, r.dar_steps.map((s) => `${s.label} ${num(s.amount)}`).join(" · "))))),
      r.dar_flagged ? h("div", { class: "alert warn" }, "The DAR analysis itself failed a check: ", r.dar_reasons.join("; ")) : null,
      h("div", { class: "row end" },
        r.dar_rate != null ? h("button", { class: "primary", title: "Override the printed DSR rate with the DAR rate", onclick: () => decide("dar") }, `DAR is right → ₹${num(r.dar_rate)}`) : null,
        h("button", { title: "The DSR rate is right; this DAR analysis belongs to another item", onclick: () => decide("dsr") }, `DSR is right (₹${num(r.dsr_rate)})`)),
      h("div", { class: "form-grid" }, lab("Both wrong: correct rate (₹)", rate), lab("Note", note)),
      h("div", { class: "row end" }, h("button", { onclick: () => decide("other") }, "Save correct rate ⏎")),
      h("div", { class: "grid cols-2" },
        h("div", {}, h("div", { class: "row" }, h("small", { class: "muted" }, `DSR page ${r.dsr_page}`), h("span", { class: "spacer" }),
          h("button", { class: "small", onclick: () => showSourcePage(r.dsr_doc_id, r.dsr_page, `DSR ${r.code}`) }, "Open large ↗")), dsrView),
        h("div", {}, h("div", { class: "row" }, h("small", { class: "muted" }, `DAR page ${darPage}`), h("span", { class: "spacer" }),
          h("button", { class: "small", onclick: () => showSourcePage(r.dar_doc_id, darPage, `DAR ${r.code}`) }, "Open large ↗")), darView))));
    try { dsrView.replaceChildren(h("img", { src: await pageImage(r.dsr_doc_id, r.dsr_page, 120), alt: "DSR page" })); } catch (e) { dsrView.replaceChildren(errorBox(e)); }
    try { darView.replaceChildren(h("img", { src: await pageImage(r.dar_doc_id, darPage, 120), alt: "DAR page" })); } catch (e) { darView.replaceChildren(errorBox(e)); }
  };
  const select = async (i) => {
    st.sel = i; drawList();
    const r = st.rows[i];
    const rate = h("input", { type: "number", step: "0.01", value: r.rate ?? "" });
    const unit = h("select", {}, h("option", { value: "" }, `unchanged (${r.unit ? unitLabel(r.unit) : r.unit_raw || "none"})`),
      Object.keys(UNIT_LABELS).map((u) => h("option", { value: u }, unitLabel(u))));
    const code = h("input", { value: r.code });
    const note = h("input", { placeholder: "optional note" });
    const view = h("div", { class: "page-view" }, loading("Loading scanned page…"));
    const pageDoc = r.source === "clean" ? r.clean_doc_id : r.doc_id;      // added from the clean copy: its page is there
    const save = async (exclude = false) => {
      if ((S.checker || "").trim().length < 2) { toast("Enter your name in “Checked by” first.", true); return; }
      if (exclude && !confirm(`Remove row ${r.code} as "not an item"? It is recorded in the corrections file and can be undone there.`)) return;
      try {
        await api("/v1/admin/corrections", { method: "POST", body: { kind: st.tab, key: st.tab === "sor" ? r.item_key : r.db_key, checked_by: S.checker.trim(), exclude,
          rate: rate.value === "" ? null : Number(rate.value), unit: unit.value || null, code: code.value.trim() !== r.code ? code.value.trim() : null, note: note.value || null } });
        toast(exclude ? `Removed ${r.code} (recorded in config/corrections).` : `Saved ${r.code} - written to config/corrections and applied.`);
        st.rows.splice(i, 1); st.total -= 1; drawPager();
        if (S.overview) { S.overview.review[st.tab === "sor" ? "sor_items" : "basic_rates"] -= 1; renderChrome(); }
        if (st.rows.length) select(Math.min(i, st.rows.length - 1)); else load();
      } catch (e) { toast(errText(e), true); }
    };
    [rate, code, note].forEach((x) => x.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); }));
    detail.replaceChildren(h("div", { class: "stack" },
      h("div", { class: "row" }, h("b", { class: "mono" }, r.code), h("span", { class: "muted" }, `${pageDoc} · PDF page ${r.page}`), h("span", { class: "spacer" }),
        h("button", { class: "small", onclick: () => showSourcePage(pageDoc, r.page, `${r.code}`) }, "Open large ↗")),
      h("div", {}, r.full_description || r.description),
      h("div", { class: "alert warn" }, (r.review_reasons || []).join("; ") || "flagged"),
      r.rate_readings && r.rate_readings.length ? h("div", {}, h("small", { class: "muted" }, "Readings (scan English page / scan digit re-read / clean copy) — click to use: "),
        h("div", { class: "chips", style: { marginTop: "4px" } }, r.rate_readings.slice(0, 3).map((x) => x ? h("span", { class: "chip", onclick: () => { rate.value = x; rate.focus(); } }, x) : h("span", { class: "chip muted" }, "—")))) : null,
      r.clean_rate != null || r.dar_rate != null ? h("div", {}, h("small", { class: "muted" }, "Other printings — click to use: "),
        h("div", { class: "chips", style: { marginTop: "4px" } },
          r.clean_rate != null ? h("span", { class: "chip", title: "Clean copy of the original printing (Aug 2023), before later correction slips", onclick: () => { rate.value = r.clean_rate; rate.focus(); } }, `Clean copy (Aug 2023): ${r.clean_rate}`) : null,
          r.dar_rate != null ? h("span", { class: "chip", title: "CPWD DAR 2023 analysis (Sep 2023)", onclick: () => { rate.value = r.dar_rate; rate.focus(); } }, `DAR (Sep 2023): ${r.dar_rate}`) : null),
        h("small", { class: "muted" }, r.source === "clean_over_scan"
          ? `The clean copy's rate is used. The scan (corrected edition, to 10.11.2023) reads ${r.scan_rate}: if that is read correctly, a correction slip changed it - type it in to use it.`
          : "Clean copy and DAR readings, for comparison.")) : null,
      h("div", { class: "form-grid" }, lab("Item code", code), lab("Unit", unit), lab("Correct rate (₹)", rate), lab("Note", note)),
      h("div", { class: "row end" },
        h("button", { title: "Row is an OCR artefact (table header, note fragment) - remove it from the rates", onclick: () => save(true) }, "Not an item — remove"),
        h("button", { class: "primary", onclick: () => save() }, "Save & mark verified ⏎")),
      view));
    rate.focus(); rate.select();
    try {
      const scan = h("img", { src: await pageImage(pageDoc, r.page, 120), alt: "scanned page" });
      if (st.tab === "sor" && r.clean_doc_id && r.clean_page && r.source !== "clean") {
        const clean = h("img", { src: await pageImage(r.clean_doc_id, r.clean_page, 120), alt: "clean copy page" });
        view.replaceChildren(h("div", { class: "grid cols-2" },
          h("div", {}, h("small", { class: "muted" }, `Scan, corrected edition · p.${r.page}`), scan),
          h("div", {}, h("div", { class: "row" }, h("small", { class: "muted" }, `Clean copy, Aug 2023 · p.${r.clean_page}`), h("span", { class: "spacer" }),
            h("button", { class: "small", onclick: () => showSourcePage(r.clean_doc_id, r.clean_page, `${r.code} (clean copy)`) }, "Open large ↗")), clean)));
      } else view.replaceChildren(scan);
    } catch (e) { view.replaceChildren(errorBox(e)); }
  };
  fill(main, pageHead("Admin · data quality", "Review queue", "DSR rows the clean copy (HQ) does not confirm or does not have, and DSR rates that differ from their CPWD DAR analysis. Compare with the page, fix the value, save. Corrections are written to config/corrections/ (kept across re-ingest) and applied immediately."),
    tabs, filters, h("div", { class: "split" }, h("div", {}, list, pager), detail));
  drawTabs(); drawFilters(); load();
}
function shortReason(rs) {
  const r = (rs || [])[0] || "";
  if (r.startsWith("rate readings")) return "rate readings differ";
  if (r.startsWith("item code")) return "code uncertain";
  if (r.includes("outlier")) return "rate outlier";
  if (r.startsWith("unit")) return "unit unclear";
  return r.slice(0, 40);
}

// ---------- learning (admin)
async function pageLearning(main) {
  if (!can("admin")) { fill(main, h("div", { class: "alert warn" }, "The Learning page needs an API key with the 'admin' scope.")); return; }
  const st = { status: "new", kind: "" };
  const status = await api("/v1/admin/learning");
  const checker = h("input", { value: S.checker || (S.me && S.me.name) || "", placeholder: "your name", style: { width: "180px" },
    oninput: (e) => { S.checker = e.target.value; lsSet("crag_checker", e.target.value); } });
  const list = h("div", { class: "tbl-wrap" });
  const tabs = h("div", { class: "tabs" });
  const fb = status.feedback || {};
  const cnt = (kind, s) => (fb[kind] || {})[s] || 0;
  const ver = (c) => (c.version === "default" ? "shipped default" : `v${c.version} · ${c.promoted_on || ""}`);
  const cal = status.calibration;
  const load = async () => {
    list.replaceChildren(loading());
    const p = new URLSearchParams({ limit: 200 });
    if (st.status) p.set("status", st.status);
    if (st.kind) p.set("kind", st.kind);
    try { drawList(await api(`/v1/admin/feedback?${p}`)); } catch (e) { list.replaceChildren(errorBox(e)); }
  };
  const review = async (f, s) => {
    if ((S.checker || "").trim().length < 2) { toast("Enter your name in “Reviewed by” first.", true); return; }
    try { await api(`/v1/admin/feedback/${f.id}`, { method: "POST", body: { status: s, reviewed_by: S.checker.trim() } }); toast(`Feedback #${f.id} ${s}.`); load(); }
    catch (e) { toast(errText(e), true); }
  };
  const drawList = (rows) => {
    if (!rows.length) { list.replaceChildren(h("div", { class: "empty" }, "No feedback here.")); return; }
    list.replaceChildren(paginate(rows, 20, (rows) => h("table", { class: "t" },
      h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "By"), h("th", {}, "Rating"), h("th", {}, "Question / rated item"), h("th", {}, "Status"), h("th", {}, ""))),
      h("tbody", {}, rows.map((f) => h("tr", {},
        h("td", { class: "mono" }, f.ts.slice(0, 16)), h("td", {}, f.key_name || "—"),
        h("td", {}, f.rating > 0 ? "👍" : "👎", " ", h("small", { class: "muted" }, f.kind === "answer" ? "answer" : "source")),
        h("td", {}, h("div", {}, h("b", {}, f.query)),
          f.kind === "chunk" ? h("small", { class: "mono muted" }, f.chunk_id) : null,
          f.kind === "answer" && f.answer ? h("details", {}, h("summary", { class: "muted" }, "Answer given"), h("div", { style: { whiteSpace: "pre-wrap" } }, f.answer)) : null,
          f.correction ? h("div", { class: "alert info", style: { whiteSpace: "pre-wrap", marginTop: "6px" } }, h("b", {}, "Correction: "), f.correction) : null),
        h("td", {}, statusBadge(f.status === "approved" ? "verified" : f.status === "discarded" ? "unconfirmed" : "review"), f.reviewed_by ? h("small", { class: "muted" }, ` ${f.reviewed_by}`) : null),
        h("td", { style: { whiteSpace: "nowrap" } },
          f.status !== "approved" ? h("button", { class: "small primary", title: f.kind === "answer" ? "Use as an example for similar questions (and for fine-tuning)" : "Keep for training", onclick: () => review(f, "approved") }, "Approve") : null, " ",
          f.status !== "discarded" ? h("button", { class: "small", title: "Wrong or careless feedback - never used for learning", onclick: () => review(f, "discarded") }, "Discard") : null))))), "ratings"));
  };
  const drawTabs = () => tabs.replaceChildren(...[["new", "To review"], ["approved", "Approved"], ["discarded", "Discarded"], ["", "All"]].map(([k, l]) =>
    h("button", { class: st.status === k ? "on" : "", onclick: () => { st.status = k; drawTabs(); load(); } }, l)));
  fill(main, pageHead("Admin · feedback loop", "Learning", "The system improves from use: ratings on search results and answers, engineers' corrections and actual project costs. Every change is tested against the golden evaluation sets before it goes live, and can be rolled back. IS code values, DSR rates and calculator formulas never change here."),
    h("div", { class: "grid4", style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "12px", marginBottom: "18px" } },
      stat(String(fb.queries_logged || 0), "Queries logged", "searches + questions"),
      stat(String(cnt("chunk", "new") + cnt("answer", "new")), "Ratings to review", `${cnt("chunk", "approved") + cnt("answer", "approved")} approved · ${cnt("chunk", "discarded") + cnt("answer", "discarded")} discarded`),
      stat(ver(status.reranker), "Reranker", "crag learn rerank"),
      stat(ver(status.fusion), "Hybrid fusion", `dense ${status.fusion.weights.dense} : BM25 ${status.fusion.weights.bm25}`)),
    cal ? h("div", { class: `alert ${cal.status === "approved" ? "info" : "warn"}`, style: { marginBottom: "18px" } },
      h("b", {}, `Estimate calibration ${cal.status}`), cal.approved_by ? ` by ${cal.approved_by} on ${cal.approved_on}` : " — run crag learn approve-calibration --by <name> to use it",
      ": steel kg/m³ ", Object.entries(cal.steel_kg_per_m3 || {}).map(([k, v]) => `${k} ${v.default}→${v.value} (${v.projects} proj.)`).join(", ") || "none") : null,
    h("div", { class: "row", style: { marginBottom: "8px" } }, h("h2", { style: { margin: 0 } }, "Feedback"), h("span", { class: "spacer" }), h("span", { class: "muted" }, "Reviewed by"), checker),
    tabs, list,
    (status.history || []).length ? h("details", { style: { marginTop: "18px" } }, h("summary", {}, "Promotion history"),
      h("table", { class: "t" }, h("tbody", {}, status.history.slice().reverse().map((x) => h("tr", {},
        h("td", { class: "mono" }, (x.promoted_on || x.on || "").slice(0, 16)), h("td", {}, x.component), h("td", {}, x.action),
        h("td", {}, x.action === "promote" ? `v${x.version}` : `v${x.from_version} → ${x.to_version}`)))))) : null);
  drawTabs(); load();
}

// ---------- code tables
async function pageTables(main) {
  const list = await api("/v1/admin/code-tables");
  const checker = S.checker || (S.me && S.me.name) || "";
  fill(main, pageHead("Verification", "Code tables", "Values the calculators use. Each table is cross-checked automatically against the OCR text of the standard; sign it off after comparing with your licensed copy."),
    h("div", { class: "stack" }, list.map((t) => tableCard(t, checker))));
}
function tableCard(t, checker) {
  const v = t.verification || {};
  const status = v.status || (t.verified_by ? "verified" : "unconfirmed");
  const human = status === "verified";                  // ai_checked tables still need a person's sign-off
  const name = h("input", { value: checker, placeholder: "your name", "aria-label": "Your name" });
  return h("div", { class: "card" },
    h("div", { class: "row" }, h("h2", { style: { margin: 0 } }, `${t.source.code} — ${t.source.table ? "Table " + t.source.table : t.source.figure ? "Fig. " + t.source.figure : "cl. " + (t.source.clause || (t.source.clauses || []).join(", "))}`),
      statusBadge(status), v.checked ? h("span", { class: "badge" }, `${v.matched}/${v.checked} rows found in OCR text`) : null,
      h("span", { class: "spacer" }),
      t.source_page ? h("button", { class: "small", onclick: () => showSourcePage(t.source_page.doc_id, t.source_page.page, t.title) }, "Compare with page ↗") : null),
    h("p", { class: "muted", style: { margin: "6px 0 10px" } }, t.title),
    renderTableValues(t.values),
    (v.mismatches || []).length && !human ? h("div", { class: "alert warn", style: { marginTop: "10px" } }, h("b", {}, "Not found in the OCR text (check these against your copy):"),
      h("ul", {}, v.mismatches.map((m) => h("li", { class: "mono" }, m)))) : null,
    h("div", { class: "card-foot" },
      human ? h("span", { class: "badge ok" }, `Signed off by ${t.verified_by} on ${t.verified_on}`)
        : t.verified_by ? h("span", { class: "muted" }, `Checked by ${t.verified_by} on ${t.verified_on}. Not yet signed off by a person.`)
        : h("span", { class: "muted" }, "Not yet signed off."),
      can("admin") && !human ? h("div", { class: "signoff" }, name, h("button", { class: "primary small", onclick: async () => {
        if (name.value.trim().length < 2) return toast("Enter your name.", true);
        try { await api(`/v1/admin/code-tables/${t.name}/sign-off`, { method: "POST", body: { verified_by: name.value.trim() } }); toast("Signed off."); route(); }
        catch (e) { toast(errText(e), true); } } }, "I compared this with the standard — sign off")) : null));
}
function renderTableValues(v) {
  if (v.rows && v.columns) return h("div", { class: "tbl-wrap" }, h("table", { class: "t" }, h("thead", {}, h("tr", {}, v.columns.map((c) => h("th", {}, titleCase(c))))),
    h("tbody", {}, v.rows.map((r) => h("tr", {}, r.map((x) => h("td", {}, x ?? "—")))))));
  if (v.pt && v.grades) return h("div", { class: "tbl-wrap" }, h("table", { class: "t" }, h("thead", {}, h("tr", {}, h("th", {}, "100 As/bd"), Object.keys(v.grades).map((g) => h("th", { class: "num" }, g)))),
    h("tbody", {}, v.pt.map((p, i) => h("tr", {}, h("td", {}, p), Object.values(v.grades).map((col) => h("td", { class: "num" }, col[i])))))));
  if (v.grades) return h("table", { class: "t" }, h("tbody", {}, h("tr", {}, Object.entries(v.grades).map(([g, x]) => h("td", {}, h("small", {}, g), h("br"), h("b", {}, x))))));
  if (v.by_size_mm) return h("table", { class: "t" }, h("thead", {}, h("tr", {}, h("th", {}, "Max aggregate size"), h("th", {}, v.zones ? v.zones.map(titleCase).join(" / ") : "Value"))),
    h("tbody", {}, Object.entries(v.by_size_mm).map(([s, x]) => h("tr", {}, h("td", {}, `${s} mm`), h("td", { class: "mono" }, Array.isArray(x) ? x.join("  /  ") : x)))));
  if (v.bands) return h("table", { class: "t" }, h("thead", {}, h("tr", {}, h("th", {}, "Grades"), h("th", {}, "Value"))),
    h("tbody", {}, v.bands.map(([a, b, x]) => h("tr", {}, h("td", {}, b >= 999 ? `M${a} and above` : `M${a} – M${b}`), h("td", {}, x)))));
  if (v.curves) return h("div", { class: "alert info" }, "Figure values: digitize the curves into config/code_tables/is10262_2019.yaml (fig_1_wc_vs_strength.curves). ",
    Object.entries(v.curves).map(([k, pts]) => h("div", {}, `${titleCase(k)}: ${pts.length ? pts.length + " points" : "empty"}`)));
  return h("pre", { class: "mono", style: { whiteSpace: "pre-wrap", margin: 0 } }, Object.entries(v).map(([k, x]) => `${k}: ${typeof x === "object" ? JSON.stringify(x) : x}`).join("\n"));
}

// ------------------------------------------------------------------ auth / boot
function logout() {
  S.key = null; S.me = null; lsSet("crag_key", null);
  showLogin();
}
function showLogin(msg) {
  $("#app").style.display = "none";
  let login = $("#login");
  if (login) login.remove();
  const key = h("input", { type: "password", placeholder: "crag_…", autocomplete: "off" });
  const err = h("div");
  const submit = async () => {
    S.key = key.value.trim();
    try { await boot(); lsSet("crag_key", S.key); login.remove(); }
    catch (e) { err.replaceChildren(errorBox(e)); S.key = null; }
  };
  key.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  login = h("div", { class: "login", id: "login" }, h("div", { class: "card stack" },
    h("div", { class: "brand" }, LOGO(), BRAND_TXT()),
    h("div", {}, h("h1", { style: { fontSize: "26px", marginTop: "14px" } }, "Welcome back"),
      h("p", { class: "tagline" }, "Mix design, RCC design aids and DSR-priced estimates, every number cited to the code.")),
    h("p", { class: "muted", style: { margin: "18px 0 0", fontSize: "13px" } }, "Sign in with your API key. Create one in the terminal:"),
    h("code", { style: { display: "block", padding: "10px 12px", background: "var(--panel-2)", borderRadius: "10px" } }, "crag apikey create <your-name> --scopes retrieve,generate,calc,estimate,admin"),
    msg ? h("div", { class: "alert warn" }, msg) : null,
    h("label", { class: "f" }, h("span", {}, "API key"), key), err,
    h("button", { class: "primary", onclick: submit }, "Sign in")));
  document.body.append(login);
  key.focus();
}
async function boot() {
  S.me = await api("/v1/whoami");
  if (!S.oas) S.oas = await (await fetch("/openapi.json")).json();
  try { S.overview = await api("/v1/overview"); } catch { /* optional */ }
  $("#app").style.display = "";
  route();
}

window.addEventListener("hashchange", route);
// The desktop build answers /v1/whoami without a key, so try that first and only then ask for one.
boot().catch(() => showLogin(S.key ? "Saved key is no longer valid." : undefined));
