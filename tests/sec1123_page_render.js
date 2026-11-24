// Security review 2026-11-23, 2.7: run the main page's own script (ecadlib/ui/static/index.html) against a stand-in
// DOM and feed each of its renderers the server's answers with ONE value at a time replaced by markup. No value from
// a file (a triage report, a plan, a part name, a count) may reach innerHTML unescaped.
//   node tests/sec1123_page_render.js <cases.json>     -> one JSON line {leaves, thrown, leaks, renderers}
// cases.json: {page, payload, raw_keys: [keys the server builds as markup itself], cases: {renderer: data}}
const fs = require("fs"), vm = require("vm");
const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const html = fs.readFileSync(cfg.page, "utf8");
const src = html.slice(html.lastIndexOf("<script>") + 8, html.lastIndexOf("</script>"));

// ---- a DOM that takes anything: every property is another stand-in, calls return one, text is ""
function any(store = {}) {
  return new Proxy(function () {}, {
    get(t, k) {
      if (k in store) return store[k];
      if (k === Symbol.toPrimitive) return () => "";
      if (k === Symbol.iterator) return function* () {};
      if (k === "then" || k === "toJSON") return undefined;
      if (k === "length") return 0;
      return (store[k] = any());
    },
    set(t, k, v) { store[k] = v; return true; },
    apply() { return any(); },
  });
}
const made = [];
function element(sel) {
  const listeners = {};
  const store = { innerHTML: "", textContent: "", value: "", hidden: false, disabled: false, checked: false, title: "",
    dataset: {}, id: sel && sel[0] === "#" ? sel.slice(1) : "", listeners,
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    insertAdjacentHTML: (where, markup) => { store.innerHTML += markup; },
    prepend: () => {}, appendChild: () => {}, remove: () => {}, focus: () => {}, click: () => {},
    showModal: () => {}, close: () => {}, scrollIntoView: () => {}, setAttribute: () => {},
    querySelector: s => element(s), querySelectorAll: () => [], closest: () => null };
  const el = any(store);
  made.push(store);
  return el;
}
const byId = new Map();
const $el = sel => { if (!byId.has(sel)) byId.set(sel, element(sel)); return byId.get(sel); };
const API = {};                                             // path -> the answer fetch gives
const ctx = vm.createContext({
  console, JSON, Math, Object, Array, String, Number, Set, Map, Promise, Error, RegExp, Date, encodeURIComponent,
  decodeURIComponent, parseInt, parseFloat, isNaN,
  document: { querySelector: $el, getElementById: id => $el("#" + id), querySelectorAll: () => [],
    createElement: () => element(""), addEventListener: () => {}, activeElement: null },
  window: { matchMedia: () => ({ matches: true }), addEventListener: () => {}, open: () => {} },
  location: { hash: "" }, localStorage: { getItem: () => null, setItem: () => {} },
  IntersectionObserver: class { observe() {} }, CSS: { escape: s => String(s) },
  setTimeout: () => 0, clearTimeout: () => {}, confirm: () => true, prompt: () => null,
  fetch: async path => {
    const a = API[String(path).split("?")[0]];
    const bad = a === undefined || (a && a.__error);
    return { ok: !bad, status: bad ? 400 : 200, statusText: "x", json: async () => (bad ? { error: (a && a.__error) || "no answer" } : a) };
  },
  API, $el,
});
vm.runInContext(src, ctx, { filename: "index.html" });

// ---- the renderers, by the name of the case that feeds them (they run inside the page's script scope)
vm.runInContext(`
const fire = async (sel, type = "click") => { for (const fn of ($el(sel).listeners[type] || [])) await fn({ target: $el(sel), preventDefault() {} }); };
const BOM = { bom: "uploads/b.csv", name: "b.csv", rows: [], counts: {}, mapping: {}, reports: ["", ""], finished: null };
const RENDER = {
  triage: d => showResult(d),
  history: d => { history = d.triages; renderHistory(); },
  info: async d => { API["/api/info"] = d; await loadInfo(); },
  specs: async d => { API["/api/specs"] = d; await loadSpecs(); },
  parts: async d => { parts = d; pfilter = ""; renderParts(); for (const p of d.parts) showPanel(p, false); },
  part_history: async d => { API["/api/change/history"] = d; popen = "X"; await loadPartHistory("X"); popen = null; },
  librarian_summary: async d => { API["/api/parts"] = { __error: "no plan" }; await showLibrarian(d); $el("#x1").innerHTML = libNext(d); },
  status: async d => { API["/api/status"] = d; await loadStatus(); },
  doctor: async d => { API["/api/doctor"] = d; await fire("#doctorbtn"); },
  dblib: async d => { API["/api/export/dblib"] = d; await fire("#dblib"); },
  kicad: d => { $el("#x2").innerHTML = kicadOut(d); },
  models: async d => { API["/api/altium/models"] = d; await fire("#models"); },
  plan: async d => { API["/api/altium365/plan"] = d; await fire("#plan"); },
  workspaces: async d => { info.altium365.token = true; API["/api/altium365/workspaces"] = d; await fire("#plan"); info.altium365.token = false; },
  push: async d => { API["/api/altium365/push"] = d; planned = planned || { ready: 1 }; await fire("#dry"); await fire("#push"); },
  approvals: async d => { API["/api/approvals/status"] = d; await fire("#apprstatus"); },
  dupes: async d => { API["/api/dedupe"] = d; await fire("#dedupe"); },
  candidates: d => showCandidates(d),
  preview: async d => { $el("#spec").value = "{}"; API["/api/footprint/preview"] = d; await preview(); },
  rules: d => { rulesProp = d; showRules(); },
  rules_save: async d => { API["/api/rules/save"] = d; await saveRules(false); },
  style: async d => { API["/api/style"] = d; await loadStyle(); },
  ai: d => { showAi(d); styleAi(d); },
  ai_cost: d => showAi(null, d),
  lines: d => jobLines($el("#busylines"), d.lines),
  errors: d => { banner($el("#x3"), d.error); showErr(new Error(d.error)); nextStep($el("#x4"), esc(d.error), [d.error, d.error]); },
};
globalThis.RUN = async (name, d, base) => {
  current = JSON.parse(JSON.stringify(BOM)); info = JSON.parse(JSON.stringify(base.info)); libbusy = false; busy = false; pending.clear();
  API["/api/info"] = base.info; API["/api/parts"] = { plan: null }; API["/api/change/history"] = base.part_history;
  API["/api/altium365/plan"] = base.plan;
  await RENDER[name](d);
};
globalThis.RENDERERS = Object.keys(RENDER);
`, ctx);

const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r)); };
const leaves = (v, path, out) => {
  if (v !== null && typeof v === "object") { for (const k of Object.keys(v)) if (!cfg.raw_keys.includes(k)) leaves(v[k], path.concat([k]), out); }
  else if (typeof v === "number" || typeof v === "string" || v === null) out.push(path);
  return out;
};
const NEEDLE = "<img src=x";
function leaked() {
  let hit = null;
  for (const s of made) { if (typeof s.innerHTML === "string" && s.innerHTML.includes(NEEDLE)) hit = hit || s.innerHTML; s.innerHTML = ""; }
  return hit;
}

(async () => {
  const out = { leaves: 0, thrown: 0, leaks: [], renderers: [], missing: ctx.RENDERERS.filter(n => !(n in cfg.cases)) };
  await settle(); leaked();
  for (const [name, data] of Object.entries(cfg.cases)) {
    if (!ctx.RENDERERS.includes(name)) { out.missing.push("no renderer: " + name); continue; }
    out.renderers.push(name);
    const run = async (d, what) => {
      try { await ctx.RUN(name, d, cfg.cases); await settle(); } catch (e) { out.thrown++; }
      const hit = leaked();
      if (hit) { const i = hit.indexOf(NEEDLE); out.leaks.push([name, what, hit.slice(Math.max(0, i - 60), i + 40)]); }
    };
    await run(JSON.parse(JSON.stringify(data)), "(as given)");   // hand-made cases carry the payload already
    for (const path of leaves(data, [], [])) {
      const d = JSON.parse(JSON.stringify(data));
      let o = d;
      for (const k of path.slice(0, -1)) o = o[k];
      o[path[path.length - 1]] = cfg.payload;
      out.leaves++;
      await run(d, path.join("."));
    }
  }
  console.log(JSON.stringify(out));
  process.exit(0);
})();
