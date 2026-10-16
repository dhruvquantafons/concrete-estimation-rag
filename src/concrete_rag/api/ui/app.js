/* Concrete Estimator UI - plain JS, no build step. Talks to the same-origin REST API with an API key. */
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
  const map = { verified: ["ok", "verified"], ocr_confirmed: ["accent", "OCR-confirmed"], partially_confirmed: ["warn", "partially confirmed"],
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
  return h("span", { class: "ref" }, refs.map((r) => h("span", { class: "badge " + ({ verified: "ok", ocr_confirmed: "accent", partially_confirmed: "warn" }[r.status] || "bad"),
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
        h("dl", { class: "kv" }, simple.flatMap(([k, v]) => [h("dt", {}, prettyLabel(k)), h("dd", {}, h("b", {}, num(v, 3)))]))) : null,
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
};
function icon(name, cls = "i") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", cls); s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name] || ""; return s;
}
const LOGO = () => { const d = h("span", { class: "logo" }); d.innerHTML = '<svg viewBox="0 0 32 32" width="30" height="30"><rect width="32" height="32" rx="8" fill="var(--accent)"/><path d="M8 23V12l8-4 8 4v11M12 23v-6h8v6" stroke="white" stroke-width="2.4" fill="none" stroke-linejoin="round"/></svg>'; return d; };

// ------------------------------------------------------------------ pages & chrome
const PAGES = [
  { id: "search", label: "Search codes", ico: "search", scope: "retrieve" },
  { id: "mix", label: "Mix design", ico: "flask", scope: "calc" },
  { id: "design", label: "RCC design", ico: "beam", scope: "calc" },
  { id: "estimate", label: "Estimate / BOQ", ico: "rupee", scope: "estimate" },
  { id: "rates", label: "DSR rates", ico: "list", scope: "estimate" },
  { id: "tables", label: "Code tables", ico: "table", scope: "calc" },
  { id: "review", label: "Review queue", ico: "edit", scope: "admin" },
];
const SOURCES = ["", "IS 456", "IS 10262", "IS 1200", "IS 2386", "CPWD DSR"];
const reviewCount = () => S.overview ? S.overview.review.sor_items + S.overview.review.basic_rates : 0;

function renderChrome() {
  const cur = location.hash.replace(/^#\/?/, "").split("?")[0];
  // utility bar
  const locs = (S.overview && S.overview.locations) || ["delhi"];
  const locSel = h("select", { "aria-label": "Rate location", onchange: (e) => { S.location = e.target.value; lsSet("crag_location", S.location); toast(`Rates now priced for ${titleCase(S.location)}`); } },
    locs.map((l) => h("option", { value: l, selected: l === S.location }, titleCase(l))));
  $("#utility").replaceChildren(h("div", { class: "wrap" },
    h("span", { class: "loc" }, icon("pin"), "Rates for", locSel, h("span", { class: "hide-sm" }, "· CPWD DSR 2023")),
    h("span", { class: "spacer" }),
    h("a", { class: "hide-sm", href: "/docs", target: "_blank" }, icon("code"), "API docs"),
    h("a", { class: "hide-sm", href: "#/tables" }, icon("shield"), "Verification"),
    h("a", { href: "#/" , class: "hide-sm" }, icon("help"), "Help")));
  // header
  const scope = h("select", { "aria-label": "Source" }, SOURCES.map((c) => h("option", { value: c }, c || "All")));
  const q = h("input", { type: "search", placeholder: "Search IS codes, clauses, tables and DSR items", "aria-label": "Search" });
  const go = (e) => { e.preventDefault(); if (q.value.trim()) location.hash = `#/search?q=${encodeURIComponent(q.value.trim())}&code=${encodeURIComponent(scope.value)}`; };
  const menu = h("div", { class: "menu" },
    h("div", { class: "who" }, h("b", {}, S.me.name), h("br"), h("small", {}, S.me.scopes.join(" · "))),
    h("a", { href: "#/estimate" }, icon("receipt"), "My estimates"),
    can("admin") ? h("a", { href: "#/review" }, icon("edit"), "Review queue") : null,
    h("a", { href: "/docs", target: "_blank" }, icon("code"), "API documentation"),
    h("button", { onclick: logout }, icon("out"), "Sign out"));
  const acct = h("div", { style: { position: "relative" } },
    h("button", { class: "hact", onclick: (e) => { e.stopPropagation(); menu.classList.toggle("open"); } }, icon("user"),
      h("span", { class: "two lbl" }, h("small", {}, `Hello, ${S.me.name}`), h("b", {}, "Account ", icon("chev")))), menu);
  document.onclick = () => menu.classList.remove("open");
  const rc = reviewCount();
  $("#site-header").replaceChildren(h("div", { class: "wrap" },
    h("a", { class: "brand", href: "#/" }, LOGO(), h("span", {}, "Concrete", h("b", {}, "Estimator"))),
    h("form", { class: "hsearch", onsubmit: go }, scope, q, h("button", { type: "submit", "aria-label": "Search" }, icon("search"))),
    h("div", { class: "hacts" }, acct,
      can("estimate") ? h("a", { class: "hact", href: "#/estimate" }, icon("receipt"), h("span", { class: "lbl" }, "Estimates")) : null,
      can("admin") ? h("a", { class: "hact", href: "#/review", title: "Rows awaiting review" }, h("span", { class: "dot" + (rc ? " on" : "") }, rc > 999 ? "999+" : rc), icon("inbox"), h("span", { class: "lbl" }, "Review")) : null)));
  // category bar
  $("#catbar").replaceChildren(h("div", { class: "wrap" },
    h("a", { class: "all" + (cur === "" ? " active" : ""), href: "#/" }, icon("grid"), "All tools"),
    PAGES.filter((p) => p.id !== "review" && (!p.scope || can(p.scope))).map((p) => h("a", { href: "#/" + p.id, class: cur === p.id ? "active" : "" }, p.label)),
    h("div", { class: "right" },
      h("a", { href: "#/search?code=CPWD%20DSR&q=design%20mix%20concrete", class: "" }, icon("book"), "Standards"),
      can("admin") ? h("a", { href: "#/review", class: "pill-amber" + (cur === "review" ? " active" : "") }, icon("edit"), `Review ${rc}`) : null)));
  // footer
  const col = (title, links) => h("div", {}, h("h4", {}, title), h("ul", {}, links.map(([t, href]) => h("li", {}, h("a", { href, target: href.startsWith("/") ? "_blank" : null }, t)))));
  $("#site-footer").replaceChildren(
    h("div", { class: "wrap top" },
      h("div", {}, h("a", { class: "brand", href: "#/" }, LOGO(), h("span", {}, "Concrete", h("b", {}, "Estimator"))),
        h("p", { class: "blurb" }, "Code-compliant mix design, RCC design and cost estimates for concrete works — every number cited to IS codes and the CPWD Delhi Schedule of Rates."),
        h("div", { class: "boxed" }, h("b", { style: { color: "#fff" } }, "LLM: ", S.overview ? S.overview.llm_provider : "—"), h("br"),
          S.overview && S.overview.llm_provider === "fake" ? "Running offline. Answers are extractive until an LLM is configured in .env." : "Answers are generated with citations and number checks.")),
      col("Tools", [["Search codes", "#/search"], ["Mix design", "#/mix"], ["RCC design", "#/design"], ["Estimate / BOQ", "#/estimate"]]),
      col("Standards", [["IS 456 : 2000", "#/search?code=IS%20456&q=scope"], ["IS 10262 : 2019", "#/search?code=IS%2010262&q=mix%20proportioning"], ["IS 1200 (Parts 1–22)", "#/search?code=IS%201200&q=method%20of%20measurement"], ["IS 2386 (Parts 1–8)", "#/search?code=IS%202386&q=methods%20of%20test"], ["CPWD DSR 2023", "#/rates"]]),
      col("Data quality", [["Review queue", "#/review"], ["Code tables", "#/tables"], ["DSR rates", "#/rates"]]),
      col("Developers", [["API documentation", "/docs"], ["OpenAPI schema", "/openapi.json"], ["Health", "/health"]])),
    h("div", { class: "mid" }, h("div", { class: "wrap" },
      h("div", {}, h("b", {}, icon("shield"), "Verified numbers"), "Rates are confirmed by up to three independent OCR readings; anything uncertain is flagged until a person checks it."),
      h("div", {}, h("b", {}, icon("calc"), "Deterministic calculators"), "Arithmetic is done by tested code using curated, signed-off code tables — never by the language model."),
      h("div", {}, h("b", {}, icon("lock"), "Runs on your machine"), "Documents, indexes and models stay local. Licensed standards are never published."))),
    h("div", { class: "bottom" }, h("div", { class: "wrap" }, h("span", {}, "Corpus"),
      ["IS 456", "IS 10262", "IS 1200", "IS 2386", "CPWD DSR 2023"].map((c) => h("span", { class: "chip-n" }, c)),
      h("span", { class: "copy" }, `Signed in as ${S.me.name}`))));
}

async function route() {
  if (!S.me) return;
  const id = location.hash.replace(/^#\/?/, "").split("?")[0];
  renderChrome();
  const main = $("#main");
  const page = PAGES.find((p) => p.id === id);
  const content = h("div");
  main.replaceChildren(...[page ? h("div", { class: "crumb" }, h("a", { href: "#/" }, "Home"), "›", page.label) : null, content].filter(Boolean));
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
  ["search", "Search codes", "#/search"], ["flask", "Mix design", "#/mix"], ["beam", "Beam", "#/design?k=beam"], ["slab", "Slab", "#/design?k=slab"],
  ["column", "Column", "#/design?k=column"], ["footing", "Footing", "#/design?k=footing"], ["rupee", "Estimate", "#/estimate"], ["list", "DSR rates", "#/rates"],
  ["table", "Code tables", "#/tables"], ["edit", "Review", "#/review"],
];
let heroTimer = null;
async function pageHome(main) {
  const o = S.overview = await api("/v1/overview");
  renderChrome();
  const docStats = (prefix) => o.documents.filter((d) => d.doc_id.startsWith(prefix));
  const slides = [
    { kicker: "Built on IS 456 · IS 10262 · IS 1200 · CPWD DSR 2023", title: "Estimate concrete works, by the code.",
      text: "Draw up elements, get IS 1200 quantities and a DSR-priced bill of quantities with every rate cited to its page.", cta: ["Build an estimate", "#/estimate"],
      mock: h("div", { class: "mock float" }, h("div", { class: "mh" }, h("span", {}, "Example BOQ · G+1 frame"), h("span", { class: "badge ok" }, "rates confirmed")),
        [["5.33.2.1  RCC M25 design mix", "16.91 m³"], ["5.9.3  Shuttering – slabs", "80.00 m²"], ["5.22.6  TMT Fe-500D", "2,777.9 kg"], ["4.1.8  PCC 1:4:8", "2.59 m³"]].map(([a, b]) => h("div", { class: "ln" }, h("span", {}, a), h("b", {}, b))),
        h("div", { class: "ln" }, h("span", {}, "Grand total"), h("span", { class: "tot" }, "₹ 7,45,539.54"))) },
    { kicker: "IS 10262 : 2019 · reproduces Annex A exactly", title: "Mix designs you can audit.",
      text: "Target strength, w/c, water, cementitious and aggregate contents — every step shown with the clause it comes from.", cta: ["Design a mix", "#/mix"],
      mock: h("div", { class: "mock float" }, h("div", { class: "mh" }, h("span", {}, "M40 · severe · 20 mm"), h("span", { class: "badge ok" }, "✓ 4 checks passed")),
        [["Cement", "412 kg/m³"], ["Water", "148 kg/m³"], ["Fine aggregate", "648.6 kg/m³"], ["Coarse aggregate", "1,234.6 kg/m³"], ["Free w/c", "0.36"]].map(([a, b]) => h("div", { class: "ln" }, h("span", {}, a), h("b", {}, b)))) },
    { kicker: `${o.rates_vote_confirmed_pct}% of DSR rates confirmed by ≥2 OCR readings`, title: "Every answer, cited to the page.",
      text: "Hybrid search over the standards and schedule of rates. Open the original scanned page behind any clause, table or rate.", cta: ["Search the codes", "#/search"],
      mock: h("div", { class: "mock float" }, h("div", { class: "mh" }, h("span", {}, "nominal cover, severe exposure"), h("span", { class: "badge accent" }, "8 results")),
        [["IS 456 : 2000, Table 16", "p.60"], ["IS 456 : 2000, cl. 26.4.2", "p.59"], ["IS 456 : 2000, Table 5", "p.33"], ["IS 456 : 2000, cl. 8.2.4.1", "p.31"]].map(([a, b]) => h("div", { class: "ln" }, h("span", {}, a), h("b", {}, b)))) },
  ];
  let cur = 0, playing = true;
  const slideEls = slides.map((s, i) => h("div", { class: "slide" + (i === 0 ? " on" : "") },
    h("div", {}, h("span", { class: "kicker" }, s.kicker), h("h2", {}, s.title), h("p", {}, s.text),
      h("a", { class: "btn-white", href: s.cta[1], style: { display: "inline-flex", gap: "8px", alignItems: "center" } }, s.cta[0], icon("arrow"))), s.mock));
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
    .map(([ic, label, href]) => h("a", { class: "tile", href }, h("span", { class: "sq" }, icon(ic)), label));
  main.replaceChildren(hero,
    h("section", { class: "section" }, h("div", { class: "sec-head" }, h("div", {}, h("h2", {}, "Browse the standards"), h("p", {}, "Every standard with its own clauses, tables and amendments")),
      h("a", { href: "#/search" }, "Search all ", icon("arrow"))), h("div", { class: "doc-cards" }, cards)),
    h("section", { class: "section" }, h("div", { class: "sec-head" }, h("h2", {}, "Tools")), h("div", { class: "tiles" }, tiles)),
    h("section", { class: "section" }, h("div", { class: "trust" },
      trustItem("doc", "Cited to the page", "Every clause, table and rate links to the original scanned PDF page."),
      trustItem("shield", "Three-way rate checks", `${o.rates_vote_confirmed_pct}% of ${o.counts.sor_items.toLocaleString("en-IN")} DSR rates confirmed by independent OCR readings.`),
      trustItem("calc", "Deterministic maths", "Calculators reproduce the IS 10262 worked example; the LLM never does arithmetic."),
      trustItem("edit", "Human sign-off", `${reviewCount()} rows and ${Object.entries(o.code_tables).filter(([k]) => k !== "verified").reduce((a, [, v]) => a + v, 0)} code tables await review.`))),
    o.recent_estimates.length ? h("section", { class: "section" }, h("div", { class: "sec-head" }, h("div", {}, h("h2", {}, "Recent estimates"), h("p", {}, "Saved automatically — reopen, export or print")),
      h("a", { href: "#/estimate" }, "All estimates ", icon("arrow"))),
      h("div", { class: "grid cols-4" }, o.recent_estimates.slice(0, 4).map((e) => h("a", { class: "card", href: "#/estimate?id=" + e.id, style: { color: "inherit", textDecoration: "none" } },
        h("small", {}, `${e.created.slice(0, 16)} · ${titleCase(e.location || "")}`), h("div", { style: { fontWeight: 600, margin: "4px 0 10px" } }, e.project),
        h("div", { class: "big-num", style: { fontSize: "22px" } }, inr(e.grand_total)), h("small", {}, `${e.lines} BOQ lines`, e.warnings ? ` · ${e.warnings} to verify` : ""))))) : null,
    o.llm_provider === "fake" ? h("div", { class: "alert info section" }, h("b", {}, "No LLM configured. "), "Search, calculators and estimates work fully. “Ask” answers stay extractive until you set CRAG_LLM_PROVIDER in .env.") : null);
}
function trustItem(ic, title, text) { return h("div", {}, h("div", { class: "ic" }, icon(ic)), h("b", {}, title), h("p", {}, text)); }

function stat(v, l, s) { return h("div", { class: "card stat" }, h("div", { class: "v" }, v), h("div", { class: "l" }, l), h("div", { class: "s muted" }, s)); }

const ROUTES = { "": pageHome, search: pageSearch, mix: pageMix, design: pageDesign, estimate: pageEstimate,
  rates: pageRates, review: pageReview, tables: pageTables };

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
  main.replaceChildren(
    h("h1", {}, "Search & Ask"),
    h("p", { class: "sub" }, "Hybrid search (keyword + semantic + reranking) over IS 456, IS 10262, IS 1200, IS 2386 and CPWD DSR 2023. Every result links to the original scanned page."),
    h("div", { class: "card" }, h("div", { class: "row" }, h("div", { class: "seg" }, segBtns), h("div", { style: { flex: "1 1 320px" } }, q),
      h("div", { style: { width: "150px" } }, code), h("div", { style: { width: "120px" } }, k), h("button", { class: "primary", onclick: go }, "Go"))),
    h("div", { style: { marginTop: "16px" } }, out));
  q.focus();
  if (params.get("q")) go();
}
function resultCard(c, idx) {
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
      m.rate != null ? h("span", { class: "badge ok" }, `${inr(m.rate)} / ${m.unit || ""}`) : null,
      h("span", { class: "spacer" }),
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
    pack.chunks.map((c) => resultCard(c)));
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
        h("span", { class: "badge" }, `${r.provider} · ${r.model || ""}`),
        v.faithful_numbers ? h("span", { class: "badge ok", title: "every number in the answer appears in the sources" }, `✓ ${v.grounded}/${v.numbers_in_answer} numbers grounded`)
          : h("span", { class: "badge bad", title: "numbers not found in any source" }, `✗ ungrounded: ${(v.ungrounded_numbers || []).join(", ")}`)),
      ans),
    h("h2", {}, "Sources"),
    r.sources.map((s, i) => resultCard({ citation: s, text: "", metadata: {}, retrieval: [] }, i + 1)));
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
  main.replaceChildren(
    h("h1", {}, "Concrete mix design"),
    h("p", { class: "sub" }, "IS 10262 : 2019 (Section 2) with IS 456 Table 5 durability limits. Output is TRIAL MIX No. 1 — confirm by trial batches."),
    h("div", { class: "alert info", style: { marginBottom: "16px" } }, h("b", {}, "w/c ratio is required "),
      "until the IS 10262 Fig. 1 strength-vs-w/c curves are digitized (Code tables page). Use your trial data or read it from Fig. 1 for your cement."),
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
      h("table", { class: "t" }, h("thead", {}, h("tr", {}, h("th", {}, "Material"), h("th", { class: "num" }, "SSD condition (kg)"), h("th", { class: "num" }, "Field, dry aggregates (kg)"))),
        h("tbody", {}, rows.filter(([, a]) => ssd[a] || field[a]).map(([n, a, b]) => h("tr", {}, h("td", {}, n), h("td", { class: "num" }, num(ssd[a], 1)), h("td", { class: "num" }, num(field[b || a], 1))))))),
    renderCalc({ ...res, outputs: {} }));
}

// ---------- RCC design
async function pageDesign(main) {
  const kinds = {
    beam: { schema: "BeamInput", ep: "/v1/design/beam", label: "Beam", values: { b_mm: 230, D_mm: 450, span_m: 5, dead_load_kN_m: 15, live_load_kN_m: 10 } },
    slab: { schema: "SlabInput", ep: "/v1/design/slab", label: "One-way slab", values: { span_m: 3.5 } },
    column: { schema: "ColumnInput", ep: "/v1/design/column", label: "Short column (axial)", values: { b_mm: 300, D_mm: 450, Pu_kN: 1500 } },
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
  main.replaceChildren(h("h1", {}, "RCC element design"),
    h("p", { class: "sub" }, "IS 456 : 2000 limit-state design aids with clause-referenced checks. Results must be reviewed by a qualified structural engineer."),
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
  const cols = [["id", "ID", "text", 70], ["type", "Type", EL_TYPES, 130], ["length_m", "Length / height (m)", "number", 95], ["width_m", "Width (m)", "number", 85],
    ["depth_m", "Depth / thk (m)", "number", 90], ["count", "Nos", "number", 60], ["grade", "Grade", "grade", 75], ["level", "Level", ["below_plinth", "above_plinth"], 120],
    ["steel_kg_per_m3", "Steel kg/m³", "number", 85]];
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
      const list = await api("/v1/estimates?limit=20");
      history.replaceChildren(list.length ? h("table", { class: "t" }, h("tbody", {}, list.map((e) => h("tr", { class: "click", onclick: async () => {
        out.replaceChildren(loading()); out.replaceChildren(renderEstimate(await api("/v1/estimate/" + e.id))); out.scrollIntoView({ behavior: "smooth" }); } },
        h("td", {}, h("b", {}, e.project), h("br"), h("small", {}, `${e.created.slice(0, 16)} · ${e.location} · ${e.lines} lines`)), h("td", { class: "num" }, inr(e.grand_total))))))
        : h("div", { class: "muted" }, "None yet."));
    } catch (e) { history.replaceChildren(errorBox(e)); }
  };
  main.replaceChildren(
    h("h1", {}, "Estimate / Bill of Quantities"),
    h("p", { class: "sub" }, "Element geometry → IS 1200 quantities → CPWD DSR 2023 rates × location index → BOQ, cement statement, cost heads → Excel / PDF."),
    h("datalist", { id: "grades" }, ["M10", "M15", "M20", "M25", "M30", "M35", "M40", "M50"].map((g) => h("option", { value: g }))),
    h("div", { class: "grid", style: { gridTemplateColumns: "minmax(0,3fr) minmax(240px,1fr)" } },
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
          h("td", {}, l.unit), h("td", { class: "num" }, num(l.quantity)), h("td", { class: "num" }, inr(l.rate)), h("td", { class: "num" }, inr(l.amount)),
          h("td", {}, l.rate_confirmed ? h("span", { class: "badge ok" }, "✓") : h("span", { class: "badge warn" }, "verify"))))),
        h("tfoot", {},
          h("tr", {}, h("td", { colspan: 6 }, "Sub-total"), h("td", { class: "num" }, inr(s.subtotal)), h("td")),
          h("tr", {}, h("td", { colspan: 6 }, `Contingency @ ${s.contingency_percent}%`), h("td", { class: "num" }, inr(s.contingency)), h("td")),
          h("tr", {}, h("td", { colspan: 6 }, "Grand total"), h("td", { class: "num" }, inr(s.grand_total)), h("td"))))),
      h("p", { class: "muted", style: { marginBottom: 0 } }, s.note)),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("h2", {}, "Quantities (IS 1200)"),
        h("div", { class: "tbl-wrap" }, h("table", { class: "t" },
          h("thead", {}, h("tr", {}, ["Element", "Type", "Nos", "Concrete m³", "Formwork m²", "Steel kg"].map((x, i) => h("th", { class: i >= 2 ? "num" : "" }, x)))),
          h("tbody", {}, q.rows.map((r) => h("tr", {}, h("td", {}, r.id), h("td", {}, titleCase(r.type)), h("td", { class: "num" }, r.count),
            h("td", { class: "num" }, num(r.concrete_m3)), h("td", { class: "num" }, num(r.formwork_m2)), h("td", { class: "num" }, num(r.steel_kg, 1))))),
          h("tfoot", {}, h("tr", {}, h("td", { colspan: 3 }, "Total"), h("td", { class: "num" }, num(q.totals.concrete_m3)), h("td", { class: "num" }, num(q.totals.formwork_m2)),
            h("td", { class: "num" }, num(q.totals.steel_kg, 1))))))),
      h("div", { class: "card" }, h("h2", {}, "Cost heads (indicative)"),
        ch && !ch.error ? h("div", {}, h("dl", { class: "kv" },
          h("dt", {}, "Materials (basic cost)"), h("dd", {}, h("b", {}, inr(ch.materials_basic_cost)), ` (${ch.materials_share_percent}%)`),
          h("dt", {}, "Labour + T&P + formwork + overheads + GST"), h("dd", {}, h("b", {}, inr(ch.labour_equipment_overheads_gst_formwork)))),
          h("p", { class: "muted" }, ch.basis)) : h("div", { class: "muted" }, ch ? ch.error : "not computed"),
        est.cement_statement && est.cement_statement.length ? h("div", {}, h("h3", { style: { marginTop: "14px" } }, "Cement consumption (DSR coefficients)"),
          h("table", { class: "t" }, h("tbody", {}, est.cement_statement.map((c) => h("tr", {}, h("td", { class: "mono" }, c.item_code),
            h("td", { class: "num" }, `${num(c.quantity)} ${c.unit} × ${c.coefficient_qtl_per_unit} qtl`), h("td", { class: "num" }, h("b", {}, `${num(c.cement_qtl)} qtl`))))),
            h("tfoot", {}, h("tr", {}, h("td", { colspan: 2 }, "Total cement"), h("td", { class: "num" }, `${num(est.cement_statement.reduce((a, c) => a + c.cement_qtl, 0))} qtl`))))) : null)),
    h("details", { class: "card" }, h("summary", {}, h("b", {}, "Assumptions & references")),
      h("ul", {}, est.assumptions.map((a) => h("li", {}, a))),
      h("div", {}, (est.references || []).map((r) => h("span", { class: "badge", style: { margin: "2px" } }, `${r.code}, ${r.item}`)))));
}

// ---------- DSR rates browser
async function pageRates(main) {
  const q = h("input", { type: "search", placeholder: "keywords (e.g. centering columns) or item code (e.g. 5.33)", value: "5.33" });
  const unit = h("select", {}, ["", "m3", "m2", "m", "kg", "each", "quintal", "t"].map((u) => h("option", { value: u }, u || "Any unit")));
  const loc = h("input", { value: S.location, style: { width: "120px" } });
  const out = h("div", { style: { marginTop: "16px" } });
  const go = async () => {
    out.replaceChildren(loading("Searching DSR…"));
    try {
      const list = await api(`/v1/rates?q=${encodeURIComponent(q.value)}&unit=${unit.value}&limit=100`);
      if (!list.length) return out.replaceChildren(h("div", { class: "empty" }, "No DSR items match."));
      out.replaceChildren(h("div", { class: "tbl-wrap" }, h("table", { class: "t" },
        h("thead", {}, h("tr", {}, h("th", {}, "Item"), h("th", {}, "Description"), h("th", {}, "Unit"), h("th", { class: "num" }, "DSR rate"), h("th", {}, "Check"))),
        h("tbody", {}, list.map((it) => h("tr", { class: "click" + (it.needs_review ? " flag" : ""), onclick: () => rateDetail(it, loc.value) },
          h("td", { class: "mono" }, it.code), h("td", {}, h("div", { class: "clamp" }, it.full_description)), h("td", {}, it.unit || it.unit_raw),
          h("td", { class: "num" }, inr(it.rate)), h("td", {}, it.needs_review ? h("span", { class: "badge warn" }, "verify") : h("span", { class: "badge ok" }, "✓"))))))),
        h("p", { class: "muted" }, `${list.length} items. Click a row for details, OCR readings and the source page.`));
    } catch (e) { out.replaceChildren(errorBox(e)); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  main.replaceChildren(h("h1", {}, "CPWD DSR 2023 rates"),
    h("p", { class: "sub" }, "2,955 items parsed from the scanned Delhi Schedule of Rates (w.e.f. 16.10.2023). Item rates include materials, labour, T&P, water, CP&OH and GST @18%."),
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
      h("dt", {}, "Unit"), h("dd", {}, `${it.unit || "—"} (printed: ${it.unit_raw || "—"})`),
      h("dt", {}, "DSR rate (Delhi)"), h("dd", {}, h("b", {}, inr(it.rate))),
      h("dt", {}, `Rate at ${location_}`), h("dd", {}, priced.error ? h("span", { class: "badge warn" }, priced.error) : `${inr(priced.rate)} (cost index ${priced.cost_index})`),
      h("dt", {}, "OCR readings"), h("dd", { class: "mono" }, (it.rate_readings || []).map((r) => r || "—").join("  /  "), ` · ${it.rate_votes}/3 agree`),
      h("dt", {}, "Status"), h("dd", {}, it.needs_review ? h("span", { class: "badge warn" }, "needs review: " + (it.review_reasons || []).join("; ")) : h("span", { class: "badge ok" }, it.corrected_by ? `verified by ${it.corrected_by}` : "confirmed")),
      h("dt", {}, "Source"), h("dd", {}, h("button", { class: "small", onclick: () => showSourcePage(it.doc_id, it.page, `DSR item ${it.code}`) }, `View PDF page ${it.page} ↗`)))));
}

// ---------- review queue
async function pageReview(main) {
  if (!can("admin")) { main.replaceChildren(h("div", { class: "alert warn" }, "The review queue needs an API key with the 'admin' scope.")); return; }
  const st = { tab: "sor", q: "", head: "", category: "", offset: 0, rows: [], total: 0, sel: -1 };
  const checker = h("input", { value: S.checker || (S.me && S.me.name) || "", placeholder: "your name", style: { width: "180px" },
    oninput: (e) => { S.checker = e.target.value; lsSet("crag_checker", e.target.value); } });
  const tabs = h("div", { class: "tabs" });
  const filters = h("div", { class: "row", style: { marginBottom: "12px" } });
  const list = h("div", { class: "list tbl-wrap" });
  const detail = h("div", { class: "card" }, h("div", { class: "empty" }, "Select a row to review it against the scanned page."));
  const pager = h("div", { class: "row", style: { marginTop: "8px" } });

  const load = async () => {
    list.replaceChildren(loading());
    const p = new URLSearchParams({ offset: st.offset, limit: 50 });
    if (st.q) p.set("q", st.q);
    if (st.tab === "sor" && st.head) p.set("head", st.head);
    if (st.tab === "basic" && st.category) p.set("category", st.category);
    try {
      const r = await api(`/v1/admin/review/${st.tab}?${p}`);
      st.rows = r.rows; st.total = r.total; st.sel = -1;
      drawList(); drawPager();
      detail.replaceChildren(h("div", { class: "empty" }, st.rows.length ? "Select a row to review it against the scanned page." : "Nothing left to review here. 🎉"));
    } catch (e) { list.replaceChildren(errorBox(e)); }
  };
  const drawTabs = () => tabs.replaceChildren(...[["sor", "DSR items"], ["basic", "Basic rates"]].map(([k, l]) =>
    h("button", { class: st.tab === k ? "on" : "", onclick: () => { st.tab = k; st.offset = 0; drawTabs(); drawFilters(); load(); } }, l)));
  const drawFilters = () => {
    const q = h("input", { type: "search", placeholder: "search description / code", value: st.q, onkeydown: (e) => { if (e.key === "Enter") { st.q = e.target.value; st.offset = 0; load(); } } });
    const sel = st.tab === "sor"
      ? h("select", { onchange: (e) => { st.head = e.target.value; st.offset = 0; load(); } }, h("option", { value: "" }, "All sub-heads"),
        Array.from({ length: 26 }, (_, i) => h("option", { value: i + 1, selected: String(i + 1) === String(st.head) }, `Sub-head ${i + 1}${[4, 5].includes(i + 1) ? " (concrete/RCC)" : ""}`)))
      : h("select", { onchange: (e) => { st.category = e.target.value; st.offset = 0; load(); } }, ["", "labour", "materials", "hire_charges"].map((c) => h("option", { value: c, selected: c === st.category }, c ? titleCase(c) : "All categories")));
    filters.replaceChildren(h("div", { style: { flex: "1 1 240px" } }, q), h("div", { style: { width: "220px" } }, sel),
      h("span", { class: "muted" }, "Checked by"), checker);
  };
  const drawPager = () => pager.replaceChildren(h("span", { class: "muted" }, `${st.total} rows need review · showing ${st.total ? st.offset + 1 : 0}–${st.offset + st.rows.length}`),
    h("span", { class: "spacer" }),
    h("button", { class: "small", disabled: st.offset === 0, onclick: () => { st.offset = Math.max(0, st.offset - 50); load(); } }, "‹ Prev"),
    h("button", { class: "small", disabled: st.offset + 50 >= st.total, onclick: () => { st.offset += 50; load(); } }, "Next ›"));
  const drawList = () => {
    if (!st.rows.length) { list.replaceChildren(h("div", { class: "empty" }, "No rows.")); return; }
    list.replaceChildren(h("table", { class: "t" },
      h("thead", {}, h("tr", {}, h("th", {}, "Page"), h("th", {}, st.tab === "sor" ? "Item" : "Code"), h("th", {}, "Description"), h("th", { class: "num" }, "Rate"), h("th", {}, "Issue"))),
      h("tbody", {}, st.rows.map((r, i) => h("tr", { class: "click" + (i === st.sel ? " sel" : ""), onclick: () => select(i) },
        h("td", { class: "num" }, r.page), h("td", { class: "mono" }, r.code), h("td", {}, h("div", { class: "clamp" }, r.description)),
        h("td", { class: "num" }, num(r.rate)), h("td", {}, h("small", {}, shortReason(r.review_reasons))))))));
  };
  const select = async (i) => {
    st.sel = i; drawList();
    const r = st.rows[i];
    const rate = h("input", { type: "number", step: "0.01", value: r.rate ?? "" });
    const unit = h("select", {}, h("option", { value: "" }, `unchanged (${r.unit || r.unit_raw || "none"})`),
      ["m3", "m2", "m", "kg", "t", "quintal", "l", "each", "day", "hour", "month", "km", "bag", "job", "point"].map((u) => h("option", { value: u }, u)));
    const code = h("input", { value: r.code, disabled: st.tab !== "sor" });
    const note = h("input", { placeholder: "optional note" });
    const view = h("div", { class: "page-view" }, loading("Loading scanned page…"));
    const save = async (exclude = false) => {
      if ((S.checker || "").trim().length < 2) { toast("Enter your name in “Checked by” first.", true); return; }
      if (exclude && !confirm(`Remove row ${r.code} as "not an item"? It is recorded in the corrections file and can be undone there.`)) return;
      try {
        await api("/v1/admin/corrections", { method: "POST", body: { kind: st.tab, key: st.tab === "sor" ? r.item_key : r.db_key, checked_by: S.checker.trim(), exclude,
          rate: rate.value === "" ? null : Number(rate.value), unit: unit.value || null, code: st.tab === "sor" && code.value !== r.code ? code.value : null, note: note.value || null } });
        toast(exclude ? `Removed ${r.code} (recorded in config/corrections).` : `Saved ${r.code} - written to config/corrections and applied.`);
        st.rows.splice(i, 1); st.total -= 1; drawPager();
        if (S.overview) { S.overview.review[st.tab === "sor" ? "sor_items" : "basic_rates"] -= 1; renderChrome(); }
        if (st.rows.length) select(Math.min(i, st.rows.length - 1)); else load();
      } catch (e) { toast(errText(e), true); }
    };
    [rate, code, note].forEach((x) => x.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); }));
    detail.replaceChildren(h("div", { class: "stack" },
      h("div", { class: "row" }, h("b", { class: "mono" }, r.code), h("span", { class: "muted" }, `${r.doc_id} · PDF page ${r.page}`), h("span", { class: "spacer" }),
        h("button", { class: "small", onclick: () => showSourcePage(r.doc_id, r.page, `${r.code}`) }, "Open large ↗")),
      h("div", {}, r.full_description || r.description),
      h("div", { class: "alert warn" }, (r.review_reasons || []).join("; ") || "flagged"),
      r.rate_readings && r.rate_readings.length ? h("div", {}, h("small", { class: "muted" }, "OCR readings (English page / digit re-read / Hindi page) — click to use: "),
        h("div", { class: "chips", style: { marginTop: "4px" } }, r.rate_readings.map((x) => x ? h("span", { class: "chip", onclick: () => { rate.value = x; rate.focus(); } }, x) : h("span", { class: "chip muted" }, "—")))) : null,
      h("div", { class: "form-grid" }, lab("Item code", code), lab("Unit", unit), lab("Correct rate (₹)", rate), lab("Note", note)),
      h("div", { class: "row end" },
        h("button", { title: "Row is an OCR artefact (table header, note fragment) - remove it from the rates", onclick: () => save(true) }, "Not an item — remove"),
        h("button", { class: "primary", onclick: () => save() }, "Save & mark verified ⏎")),
      view));
    rate.focus(); rate.select();
    try { view.replaceChildren(h("img", { src: await pageImage(r.doc_id, r.page, 120), alt: "scanned page" })); } catch (e) { view.replaceChildren(errorBox(e)); }
  };
  main.replaceChildren(h("h1", {}, "Review queue"),
    h("p", { class: "sub" }, "Rows where the three OCR readings disagreed or the item code was uncertain. Compare with the scanned page, fix the value, save. Corrections are written to config/corrections/cpwd_dsr_2023.yaml (kept across re-ingest) and applied immediately."),
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

// ---------- code tables
async function pageTables(main) {
  const list = await api("/v1/admin/code-tables");
  const checker = S.checker || (S.me && S.me.name) || "";
  main.replaceChildren(h("h1", {}, "Code tables"),
    h("p", { class: "sub" }, "Values the calculators use. Each table is cross-checked automatically against the OCR text of the standard; sign it off after comparing with your licensed copy."),
    h("div", { class: "stack" }, list.map((t) => tableCard(t, checker))));
}
function tableCard(t, checker) {
  const v = t.verification || {};
  const status = t.verified_by ? "verified" : v.status;
  const name = h("input", { value: checker, placeholder: "your name", style: { width: "180px" } });
  return h("div", { class: "card" },
    h("div", { class: "row" }, h("h2", { style: { margin: 0 } }, `${t.source.code} — ${t.source.table ? "Table " + t.source.table : t.source.figure ? "Fig. " + t.source.figure : "cl. " + (t.source.clause || (t.source.clauses || []).join(", "))}`),
      statusBadge(status), v.checked ? h("span", { class: "badge" }, `${v.matched}/${v.checked} rows found in OCR text`) : null,
      h("span", { class: "spacer" }),
      t.source_page ? h("button", { class: "small", onclick: () => showSourcePage(t.source_page.doc_id, t.source_page.page, t.title) }, "Compare with page ↗") : null),
    h("p", { class: "muted", style: { margin: "6px 0 10px" } }, t.title),
    renderTableValues(t.values),
    (v.mismatches || []).length && !t.verified_by ? h("div", { class: "alert warn", style: { marginTop: "10px" } }, h("b", {}, "Not found in the OCR text (check these against your copy):"),
      h("ul", {}, v.mismatches.map((m) => h("li", { class: "mono" }, m)))) : null,
    h("div", { class: "row", style: { marginTop: "12px" } },
      t.verified_by ? h("span", { class: "badge ok" }, `Signed off by ${t.verified_by} on ${t.verified_on}`) : h("span", { class: "muted" }, "Not yet signed off."),
      h("span", { class: "spacer" }),
      can("admin") && !t.verified_by ? [name, h("button", { class: "primary small", onclick: async () => {
        if (name.value.trim().length < 2) return toast("Enter your name.", true);
        try { await api(`/v1/admin/code-tables/${t.name}/sign-off`, { method: "POST", body: { verified_by: name.value.trim() } }); toast("Signed off."); route(); }
        catch (e) { toast(errText(e), true); } } }, "I compared this with the standard — sign off")] : null));
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
    h("div", { class: "brand" }, LOGO(), h("span", {}, "Concrete", h("b", {}, "Estimator"))),
    h("h1", { style: { fontSize: "24px", marginTop: "10px" } }, "Sign in"),
    h("p", { class: "muted", style: { margin: 0 } }, "Use your API key. Create one in the terminal:"),
    h("code", { style: { display: "block", padding: "8px", background: "var(--panel-2)", borderRadius: "6px" } }, "crag apikey create <your-name> --scopes retrieve,generate,calc,estimate,admin"),
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
if (S.key) boot().catch(() => showLogin("Saved key is no longer valid.")); else showLogin();
