// ecadlib landing page: Hairline figures, PCB traces drawn on scroll, and a footprint player fed by
// footprints.json (made by scripts/build_site_footprints.py from ecadlib's own generator).
import * as H from "./hairline.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}, parent) => {
  const el = document.createElementNS(NS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(el);
  return el;
};
const still = matchMedia("(prefers-reduced-motion: reduce)");
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
/** The page's one curve, cubic-bezier(.32, .72, 0, 1), as a function of progress. */
const ease = (() => {
  const x1 = 0.32, y1 = 0.72, x2 = 0, y2 = 1;
  const bx = (t) => 3 * x1 * t * (1 - t) ** 2 + 3 * x2 * t * t * (1 - t) + t ** 3;
  const by = (t) => 3 * y1 * t * (1 - t) ** 2 + 3 * y2 * t * t * (1 - t) + t ** 3;
  return (p) => { let lo = 0, hi = 1; for (let i = 0; i < 24; i++) { const m = (lo + hi) / 2; if (bx(m) < p) lo = m; else hi = m; } return by((lo + hi) / 2); };
})();

/* ---------- theme: follows the system until the button is pressed ---------- */
const root = document.documentElement;
try { const t = localStorage.getItem("ecadlib-theme"); if (t === "light" || t === "dark") root.dataset.theme = t; } catch {}
const dark = () => root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
const themeBtn = $("#themebtn");
const labelTheme = () => { themeBtn.textContent = dark() ? "light" : "dark"; };
labelTheme();
themeBtn.addEventListener("click", () => {
  root.dataset.theme = dark() ? "light" : "dark";
  try { localStorage.setItem("ecadlib-theme", root.dataset.theme); } catch {}
  labelTheme();
});

/* ---------- figures ---------- */
const heroRead = $("#heroread"), heroAz = $("#heroaz");
H.board($("#fig-board"), { intensity: 0.55, onRead: (t) => { heroRead.textContent = t; } });
if (!still.matches) H.placeParts();
for (const el of $$("[data-fig]")) {
  const out = el.dataset.read ? $("#" + el.dataset.read) : null;
  H[el.dataset.fig](el, { play: true, onRead: out ? (t) => { out.textContent = t; } : undefined });
}

// The camera: it swings in from the side while the board is drawn, then scrolling the hero turns it.
let az = 45;
const setAz = (d) => { az = d; H.turn(d); heroAz.textContent = String(Math.round(d)).padStart(2, "0"); };
setAz(78);
requestAnimationFrame(() => setTimeout(() => setAz(45), 120));
let ticking = false;
addEventListener("scroll", () => {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    const p = clamp(scrollY / innerHeight, 0, 1);
    setAz(45 - 32 * p);
    $(".nav").classList.toggle("scrolled", scrollY > 8);
  });
}, { passive: true });

/* ---------- reveal on scroll, and the nav's current section ---------- */
const seen = new IntersectionObserver((es) => es.forEach((e) => {
  if (e.isIntersecting) { e.target.classList.add("in"); seen.unobserve(e.target); }
}), { rootMargin: "0px 0px -8% 0px" });
$$(".reveal").forEach((el) => seen.observe(el));
const links = $$(".nav nav a");
const spy = new IntersectionObserver((es) => es.forEach((e) => {
  if (e.isIntersecting) links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + e.target.id));
}), { rootMargin: "-45% 0px -50% 0px" });
$$("section[id]").forEach((s) => spy.observe(s));

/* ---------- PCB traces: a few buses run in from the margins and end on vias ---------- */
function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
function traces(host) {
  host.querySelector(":scope > svg.traces")?.remove();
  const W = document.documentElement.clientWidth, Hh = host.clientHeight;
  const margin = (W - Math.min(W, 1200)) / 2 + 30;      // the gutter beside the 1200px column; the traces stay in it
  if (Hh < 10 || margin < 80) return;
  const r = rng([...host.dataset.traces].reduce((a, c) => a * 31 + c.charCodeAt(0), 7));
  const svg = svgEl("svg", { class: "traces", viewBox: `0 0 ${W} ${Hh}`, preserveAspectRatio: "none", "aria-hidden": "true" });
  const buses = 4;
  for (let b = 0; b < buses; b++) {
    const left = b % 2 === 0, n = 1 + Math.floor(r() * 3), y0 = Hh * (0.12 + 0.76 * r()), up = r() < 0.5 ? -1 : 1;
    const run1 = margin * (0.2 + 0.35 * r()), drop = 14 + r() * Math.min(60, margin * 0.25), run2 = 10 + r() * margin * 0.2;
    for (let k = 0; k < n; k++) {
      const off = k * 7, sx = left ? -10 : W + 10, dir = left ? 1 : -1;
      const x1 = sx + dir * (run1 + (up > 0 ? off : -off)), y = y0 + off;
      const x2 = x1 + dir * drop, y2 = y + up * drop, x3 = x2 + dir * (run2 + off * 0.5);
      const d = `M${sx} ${y}H${x1}L${x2} ${y2}H${x3}`;
      const p = svgEl("path", { d, pathLength: 1 }, svg);
      p.style.transitionDelay = `${(b * 0.15 + k * 0.08).toFixed(2)}s`;
      svgEl("circle", { cx: x3 + dir * 3.5, cy: y2, r: 3.5 }, svg);
      if (k === 0 && !still.matches && r() < 0.8) {
        const dot = svgEl("circle", { class: "sig", r: 1.6 }, svg);
        const mo = svgEl("animateMotion", { dur: `${(5 + r() * 5).toFixed(1)}s`, repeatCount: "indefinite", path: d, keyPoints: "0;1", keyTimes: "0;1", calcMode: "spline", keySplines: ".32 .72 0 1", begin: `${(r() * 3).toFixed(1)}s` }, dot);
        mo.setAttribute("rotate", "0");
      }
    }
  }
  host.prepend(svg);
  traceSeen.observe(svg);
}
const traceSeen = new IntersectionObserver((es) => es.forEach((e) => {
  if (e.isIntersecting) { e.target.classList.add("on"); traceSeen.unobserve(e.target); }
}), { rootMargin: "0px 0px -15% 0px" });
const drawTraces = () => $$("[data-traces]").forEach(traces);
drawTraces();
let rt = 0;
addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(drawTraces, 200); });

/* ---------- footprint player ---------- */
const fp = { list: [], i: 0, timers: [], token: 0, view: null, raf: 0, visible: false, started: false };
const fsvg = $("#fpsvg"), fspec = $("#fpspec"), fstate = $("#fpstate"), fname = $("#fpname"), fcoord = $("#fpcoord"), fipc = $("#fpipc"), fpkgs = $("#fppkgs");
const CYCLE = 9000;

const later = (ms, fn) => { const t = fp.token; fp.timers.push(setTimeout(() => { if (t === fp.token) fn(); }, still.matches ? 0 : ms)); };
const setView = (v) => { fp.view = v; fsvg.setAttribute("viewBox", v.map((n) => n.toFixed(3)).join(" ")); };
function moveView(to, ms) {
  cancelAnimationFrame(fp.raf);
  if (still.matches) return setView(to);
  const from = fp.view.slice(), t0 = performance.now(), tok = fp.token;
  const step = (now) => {
    if (tok !== fp.token) return;
    const k = ease(clamp((now - t0) / ms, 0, 1));
    setView(from.map((a, i) => a + (to[i] - a) * k));
    if (k < 1) fp.raf = requestAnimationFrame(step);
  };
  fp.raf = requestAnimationFrame(step);
}
const niceStep = (span) => [0.05, 0.1, 0.25, 0.5, 1, 2.5].find((s) => span / s <= 60) ?? 5;

function specText(f) {
  const json = JSON.stringify(f.spec, null, 2)
    .replace(/\{\n\s+"nom": ([\d.]+)\n\s+\}/g, '{ "nom": $1 }')
    .replace(/\{\n\s+"(min|nom)": ([\d.]+),\n\s+"(max|tol)": ([\d.]+)\n\s+\}/g, '{ "$1": $2, "$3": $4 }')
    .replace(/\{\n\s+"length": (\{[^}]*\}),\n\s+"width": (\{[^}]*\})\n\s+\}/g, '{ "length": $1, "width": $2 }');
  return `// ${f.name}\n` + json;
}
function paintSpec(text, upto) {
  const shown = text.slice(0, upto).replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/"([a-z_]+)":/g, '<span class="k">"$1"</span>:');
  fspec.innerHTML = shown + (upto < text.length ? '<span class="caret"></span>' : "");
}

function play(i) {
  fp.token++;
  fp.timers.forEach(clearTimeout); fp.timers = [];
  cancelAnimationFrame(fp.raf);
  fp.i = (i + fp.list.length) % fp.list.length;
  const f = fp.list[fp.i];
  $$("button", fpkgs).forEach((b, k) => {
    b.setAttribute("aria-selected", String(k === fp.i));
    const bar = b.querySelector(".bar");
    bar.getAnimations().forEach((a) => a.cancel());
    if (k === fp.i && !still.matches) bar.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], { duration: CYCLE, easing: "linear", fill: "forwards" });
  });
  fname.textContent = f.name;
  fstate.textContent = "reading"; fstate.classList.remove("done");
  fipc.innerHTML = "";

  // the board, at the package's scale
  const [cx0, cy0, cx1, cy1] = f.courtyard, w = (cx1 - cx0) * 1.9, h = Math.max(w * 0.8, (cy1 - cy0) * 1.7);
  const vw = Math.max(w, h * 1.25), vh = vw * 0.8;
  fp.home = [-vw / 2, -vh / 2, vw, vh];
  fsvg.replaceChildren();
  fsvg.style.setProperty("--sw", (vw / 380).toFixed(4));
  setView(fp.home);
  const s = niceStep(vw), big = 2.4 * vw, minor = [], major = [];
  for (let k = -Math.ceil(big / s); k <= Math.ceil(big / s); k++) {
    const v = (k * s).toFixed(4), into = k % 5 === 0 ? major : minor;
    into.push(`M${v} ${-big}V${big}M${-big} ${v}H${big}`);
  }
  svgEl("path", { class: "grid", d: minor.join("") }, fsvg);
  svgEl("path", { class: "grid major", d: major.join("") }, fsvg);
  const o = s * 0.8;
  svgEl("path", { class: "origin", d: `M${-o} 0H${o}M0 ${-o}V${o}` }, fsvg);
  const crt = svgEl("path", { class: "draw crtyd", pathLength: 1, d: `M${cx0} ${cy0}H${cx1}V${cy1}H${cx0}Z` }, fsvg);
  const fab = svgEl("path", { class: "draw fab", pathLength: 1, d: "M" + f.fab.map((p) => p.join(" ")).join("L") + "Z" }, fsvg);
  const silk = f.silk.map((l) => svgEl("path", { class: "draw silk", pathLength: 1, d: `M${l[0][0]} ${l[0][1]}L${l[1][0]} ${l[1][1]}` }, fsvg));
  const marks = f.marks.map((m) => svgEl("path", { class: "silkfill", d: "M" + m.map((p) => p.join(" ")).join("L") + "Z" }, fsvg));
  const p1 = f.pads.find((p) => p.num === "1") || f.pads[0];
  const pads = f.pads.map((p) => {
    const r = p.shape === "roundrect" ? Math.min(p.w, p.h) * 0.25 : 0;
    const el = svgEl("rect", { class: "pad" + (p === p1 ? " p1" : ""), x: p.x - p.w / 2, y: p.y - p.h / 2, width: p.w, height: p.h, rx: r }, fsvg);
    el.dataset.num = p.num;
    return { p, el, d: Math.hypot(p.x - p1.x, p.y - p1.y) };
  });
  const far = Math.max(...pads.map((q) => q.d)) || 1;
  const nums = pads.filter(({ p }) => p.num && p.num !== String(f.spec.pin_count + 1) && Math.min(p.w, p.h) > vw / 80).map(({ p }) => {
    const t = svgEl("text", { class: "padnum", x: p.x, y: p.y, "font-size": Math.min(Math.min(p.w, p.h) * 0.62, vw / 34).toFixed(3) }, fsvg);
    t.textContent = p.num;
    return t;
  });

  // dimensions: the overall span under the courtyard, and the pitch from pad 1 to pad 2
  const dims = [], fs = (vw / 46).toFixed(3), tk = vw / 90;
  const dimH = (x0, x1, y, label, below = true) => {
    dims.push(svgEl("path", { class: "dim", d: `M${x0} ${y}H${x1}M${x0} ${y - tk}V${y + tk}M${x1} ${y - tk}V${y + tk}` }, fsvg));
    const t = svgEl("text", { class: "dimtxt", x: (x0 + x1) / 2, y: y + (below ? tk * 3.4 : -tk * 1.6), "font-size": fs, "text-anchor": "middle" }, fsvg);
    t.textContent = label; dims.push(t);
  };
  const dimV = (y0, y1, x, label) => {
    dims.push(svgEl("path", { class: "dim", d: `M${x} ${y0}V${y1}M${x - tk} ${y0}H${x + tk}M${x - tk} ${y1}H${x + tk}` }, fsvg));
    const t = svgEl("text", { class: "dimtxt", x: x - tk * 1.6, y: (y0 + y1) / 2, "font-size": fs, "text-anchor": "end", "dominant-baseline": "central" }, fsvg);
    t.textContent = label; dims.push(t);
  };
  const outer = f.pads.filter((p) => p.num !== String(f.spec.pin_count + 1));
  const xs = Math.min(...outer.map((p) => p.x - p.w / 2)), xe = Math.max(...outer.map((p) => p.x + p.w / 2));
  dimH(xs, xe, cy1 + vh * 0.07, (xe - xs).toFixed(2));
  const p2 = f.pads.find((p) => p.num === "2");
  if (p2 && f.spec.pitch && Math.abs(p2.x - p1.x) < 1e-6) dimV(p1.y, p2.y, cx0 - vw * 0.05, f.spec.pitch.toFixed(2));
  else if (p2 && f.spec.pitch && Math.abs(p2.y - p1.y) < 1e-6) dimH(p1.x, p2.x, cy0 - vh * 0.07, f.spec.pitch.toFixed(2), false);

  // the timeline: type the spec, then draw it layer by layer, then the camera looks at pin 1
  const text = specText(f), typeMs = 1100;
  if (still.matches) paintSpec(text, text.length);
  else {
    const t0 = performance.now(), tok = fp.token;
    const typing = (now) => {
      if (tok !== fp.token) return;
      const n = Math.round(text.length * clamp((now - t0) / typeMs, 0, 1));
      paintSpec(text, n);
      if (n < text.length) requestAnimationFrame(typing);
    };
    requestAnimationFrame(typing);
  }
  later(typeMs, () => { fstate.textContent = "generating"; crt.classList.add("on"); });
  later(typeMs + 250, () => fab.classList.add("on"));
  pads.forEach(({ el, d }) => later(typeMs + 450 + (d / far) * 750, () => el.classList.add("on")));
  later(typeMs + 1400, () => { silk.forEach((l) => l.classList.add("on")); marks.forEach((m) => m.classList.add("on")); });
  later(typeMs + 1700, () => nums.forEach((t) => t.classList.add("on")));
  later(typeMs + 2000, () => {
    dims.forEach((d) => d.classList.add("on"));
    const ipc = [["Z", f.ipc.Z], ["G", f.ipc.G], ["X", f.ipc.X], ["density", f.density]];
    fipc.innerHTML = ipc.map(([k, v]) => `<div><dt>${k}</dt><dd>${typeof v === "number" ? v.toFixed(2) : v}</dd></div>`).join("");
    fstate.textContent = `${f.pads.length} pads · ${f.checks ? f.checks + " notes" : "checks pass"}`;
    fstate.classList.add("done");
  });
  if (!still.matches) {
    const zw = fp.home[2] * 0.42, zh = zw * 0.8;
    later(typeMs + 2900, () => moveView([p1.x - zw / 2, p1.y - zh / 2, zw, zh], 1500));
    later(typeMs + 5400, () => moveView(fp.home, 1500));
    later(CYCLE, () => { if (fp.visible) play(fp.i + 1); else fp.started = false; });
  }
}

// the pointer reads out board coordinates in mm, as Altium's status bar does, and lights the pad under it
fsvg.addEventListener("pointermove", (e) => {
  const m = fsvg.getScreenCTM();
  if (!m) return;
  const pt = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
  fcoord.textContent = `x ${pt.x.toFixed(3)} y ${(-pt.y).toFixed(3)} mm`;
  const hit = e.target.closest?.(".pad");
  $$(".pad.hot", fsvg).forEach((p) => p !== hit && p.classList.remove("hot"));
  if (hit) { hit.classList.add("hot"); fcoord.textContent += ` · pad ${hit.dataset.num || "EP"}`; }
});
fsvg.addEventListener("pointerleave", () => $$(".pad.hot", fsvg).forEach((p) => p.classList.remove("hot")));

fetch("footprints.json").then((r) => r.json()).then((list) => {
  fp.list = list;
  fpkgs.innerHTML = list.map((f, k) => `<button role="tab" aria-selected="false" data-k="${k}">${f.name.split("_").slice(0, /^[RC]_/.test(f.name) ? 2 : 1).join(" ")}<span class="bar"></span></button>`).join("");
  fpkgs.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) play(+b.dataset.k); });
  const io = new IntersectionObserver(([e]) => {
    fp.visible = e.isIntersecting;
    if (fp.visible && !fp.started) { fp.started = true; play(fp.i); }
  }, { threshold: 0.25 });
  io.observe($(".fpwrap"));
}).catch(() => { fstate.textContent = "footprints.json did not load"; });

/* ---------- model config ---------- */
const AI = {
  claude: `<span class="c"># ecadlib.toml: the default, the same as having no [ai] block</span>
[ai]
provider = <span class="s">"anthropic"</span>       <span class="c"># export ANTHROPIC_API_KEY=...</span>
model = <span class="s">"claude-opus-5-5"</span>`,
  openai: `[ai]
provider = <span class="s">"openai"</span>          <span class="c"># export OPENAI_API_KEY=...</span>
model = <span class="s">"gpt-5"</span>`,
  openrouter: `[ai]                           <span class="c"># any model OpenRouter serves</span>
provider = <span class="s">"openrouter"</span>      <span class="c"># export OPENROUTER_API_KEY=...</span>
model = <span class="s">"qwen/qwen-2.5-72b-instruct"</span>`,
  ollama: `[ai]                           <span class="c"># on this machine: free, no key, nothing leaves it</span>
provider = <span class="s">"ollama"</span>          <span class="c"># ollama pull qwen2.5vl:7b</span>
model = <span class="s">"qwen2.5vl:7b"</span>`,
};
const aiconf = $("#aiconf");
const showAI = (k) => {
  aiconf.innerHTML = AI[k];
  aiconf.style.animation = "none"; void aiconf.offsetWidth; aiconf.style.animation = "";
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.p === k)));
};
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showAI(b.dataset.p)));
showAI("claude");

/* ---------- copy ---------- */
$$("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
  const text = $("#" + b.dataset.copy).textContent.split("\n").map((l) => l.replace(/\s+#.*$/, "")).filter((l) => l && !l.startsWith("#")).join("\n");
  try { await navigator.clipboard.writeText(text); b.textContent = "copied"; } catch { b.textContent = "select and copy"; }
  setTimeout(() => { b.textContent = "copy"; }, 1600);
}));

/* ---------- workflow: an unseen hand works through Altium and KiCad ---------- */
const win = $("#win"), wfsteps = $("#wfsteps"), menubar = $("#menubar"), wfpanel = $("#wfpanel"), wfcanvas = $("#wfcanvas");
const wfdlg = $("#wfdlg"), wfdrop = $("#wfdrop"), cursor = $("#wfcursor"), wfplay = $("#wfplay");
const wf = { scene: "altium", run: 0, fast: false, paused: false, visible: false, step: 0 };
class Stop extends Error {}
const wait = (ms) => new Promise((ok, no) => {
  const run = wf.run;
  if (wf.fast) return ok();
  const tick = (left) => {
    if (run !== wf.run) return no(new Stop());
    if (wf.paused || !wf.visible) return setTimeout(() => tick(left), 200);
    if (left <= 0) return ok();
    setTimeout(() => tick(left - 50), 50);
  };
  tick(ms);
});
const rel = (el) => { const a = el.getBoundingClientRect(), w = win.getBoundingClientRect(); return [a.left - w.left + a.width / 2, a.top - w.top + a.height / 2]; };
async function point(el, dx = 0, dy = 0) {
  const [x, y] = rel(el);
  cursor.style.left = `${x + dx}px`; cursor.style.top = `${y + dy}px`;
  await wait(950);
}
async function click(el) {
  await point(el);
  if (!wf.fast) {
    const r = document.createElement("span"); r.className = "ripple";
    r.style.left = cursor.style.left; r.style.top = cursor.style.top; win.appendChild(r);
    setTimeout(() => r.remove(), 700);
  }
  el.classList.add("press"); await wait(160); el.classList.remove("press");
}
async function type(el, text) {
  el.classList.add("focus");
  for (let i = 1; i <= text.length; i++) { el.textContent = text.slice(0, i); await wait(55); }
  el.classList.remove("focus");
}
function openMenu(anchor, items) {
  $$(".menu", win).forEach((m) => m.remove());
  const m = document.createElement("div"); m.className = "menu";
  m.innerHTML = items.map((t) => (t === "-" ? '<div class="sep"></div>' : `<div>${t}</div>`)).join("");
  const a = anchor.getBoundingClientRect(), w = win.getBoundingClientRect();
  m.style.left = `${Math.min(a.left - w.left, w.width - 220)}px`; m.style.top = `${a.bottom - w.top + 2}px`;
  win.appendChild(m);
  anchor.classList.add("hot");
  return m;
}
const closeMenus = () => { $$(".menu", win).forEach((m) => m.remove()); $$(".menubar > span", win).forEach((s) => s.classList.remove("hot")); };
function dialog(title, body) {
  wfdlg.innerHTML = `<div class="dh"><span>${title}</span><span>×</span></div><div class="db">${body}</div>`;
  wfdlg.hidden = false;
  return wfdlg;
}
const closeDialog = () => { wfdlg.hidden = true; };
async function log(lines) {
  const ul = $(".log", wfdlg), meter = $(".meter i", wfdlg);
  for (let i = 0; i < lines.length; i++) {
    const [a, b, ok] = lines[i];
    ul.insertAdjacentHTML("beforeend", `<li class="${ok ? "ok" : ""}"><span>${a}</span><span>${b}</span></li>`);
    meter.style.width = `${((i + 1) / lines.length) * 100}%`;
    await wait(650);
  }
}
const chip = (pins) => `<svg viewBox="0 0 26 22"><rect x="7" y="3" width="12" height="16" rx="1.5"/><path d="${Array.from({ length: pins }, (_, i) => `M3 ${6 + i * 3.4}H7M19 ${6 + i * 3.4}H23`).join("")}"/></svg>`;
const row = (name, desc, pins = 2, add = false) => `<div class="row${add ? " add" : ""}">${chip(pins)}<div><b>${name}</b><span>${desc}</span></div></div>`;

const SCENES = {
  altium: {
    title: "Board.PrjPcb · Schematic",
    menu: ["File", "Edit", "View", "Project", "Place", "Design", "Tools", "Reports", "ecadlib", "Window", "Help"],
    canvas: `<rect class="w" x="8" y="8" width="384" height="244" rx="2"/><path class="w" d="M262 222H392M262 222V252M330 222V252"/>
      <g><rect class="s" x="40" y="60" width="54" height="70" rx="2"/><path class="w" d="M28 72H40M28 86H40M28 100H40M28 114H40M94 72H106M94 86H106M94 100H106M94 114H106"/><text class="t" x="40" y="54">U1</text></g>
      <g><rect class="s" x="150" y="44" width="10" height="26" rx="1"/><text class="t" x="164" y="60">R1</text><path class="w" d="M155 44V32M155 70V86H106"/></g>
      <g><path class="w" d="M146 150H164M146 156H164M155 150V114H106M155 156V172"/><text class="t" x="168" y="156">C1</text></g>
      <path class="w" d="M28 72H18V200H150M150 172V200"/>
      <g class="new" id="wfnew"><rect class="s hi" x="250" y="60" width="66" height="96" rx="2"/><path class="w hi" d="M238 74H250M238 88H250M238 102H250M238 116H250M238 130H250M238 144H250M316 74H328M316 88H328M316 102H328M316 116H328M316 130H328M316 144H328"/><text class="t" x="250" y="54">U2 · TPS62133</text><path class="w" d="M238 74H200V114H155"/></g>`,
    panel: `<h4>Components</h4><div class="search">Search</div><div id="rows">${row("LM358DR", "Op amp, SOIC-8", 4)}${row("RC0603FR-0710KL", "10k 1% 0603")}${row("GRM155R71C104KA88D", "100n 16V 0402")}</div>`,
    steps: [
      ["Check the project's BOM", "ecadlib > Check project BOM reads the open project and marks what your library already has."],
      ["Add a part by MPN", "ecadlib > Add part from MPN asks for the part number, and the manufacturer if you know it."],
      ["ecadlib finds or draws it", "Official KiCad libraries, EasyEDA, then the datasheet reader. A release check runs before anything lands."],
      ["Place it from Components", "The part arrives in your library (a DbLib or Altium 365) with symbol, footprint and parameters."],
    ],
    async play(k) {
      const ours = $$(".menubar > span", win)[8];
      const menu = ["Add part from MPN…", "Check project BOM", "-", "Open review queue"];
      if (k === 0) {
        await click(ours); let m = openMenu(ours, menu); await wait(400);
        const item = m.children[1]; item.classList.add("hot"); await click(item); closeMenus();
        dialog("ecadlib · Check project BOM", `<div class="big">24 lines</div><ul class="log"></ul><div class="meter"><i></i></div><div class="btns"><span>Later</span><span class="primary">Fill the library now</span></div>`);
        await log([["already in your library", "21", true], ["a source can supply", "2"], ["needs a footprint", "1"]]);
        await click($(".btns .primary", wfdlg)); closeDialog(); m = null;
      } else if (k === 1) {
        await click(ours); const m = openMenu(ours, menu); await wait(400);
        m.children[0].classList.add("hot"); await click(m.children[0]); closeMenus();
        dialog("ecadlib · Add part from MPN", `<label>MPN<div class="in" id="in1"></div></label><label>Manufacturer (optional)<div class="in" id="in2"></div></label><div class="btns"><span>Cancel</span><span class="primary">OK</span></div>`);
        await point($("#in1", wfdlg)); await type($("#in1", wfdlg), "TPS62133RGTR");
        await point($("#in2", wfdlg)); await type($("#in2", wfdlg), "Texas Instruments");
        await click($(".btns .primary", wfdlg));
      } else if (k === 2) {
        dialog("ecadlib · TPS62133RGTR", `<ul class="log"></ul><div class="meter"><i></i></div>`);
        await log([["KiCad official libraries", "no match"], ["EasyEDA", "symbol, 17 pins", true], ["datasheet reader", "VQFN-16, 3 × 3 mm", true], ["IPC-7351B footprint", "17 pads", true], ["release check", "ready", true]]);
        await wait(700); closeDialog();
      } else {
        $("#rows", wfpanel).insertAdjacentHTML("afterbegin", row("TPS62133RGTR", "Buck converter, VQFN-16", 6, true));
        await wait(500);
        const r = $(".row.add", wfpanel); await point(r);
        const [x, y] = rel(r); wfdrop.textContent = "TPS62133RGTR"; wfdrop.hidden = false;
        wfdrop.style.left = `${x - 60}px`; wfdrop.style.top = `${y - 10}px`;
        await wait(60);
        const target = $("#wfnew", wfcanvas); const [tx, ty] = rel(target);
        wfdrop.style.left = `${tx - 50}px`; wfdrop.style.top = `${ty - 10}px`;
        cursor.style.left = `${tx}px`; cursor.style.top = `${ty}px`; await wait(950);
        wfdrop.hidden = true; target.classList.add("on"); await wait(2200);
      }
    },
  },
  kicad: {
    title: "board.kicad_pcb · PCB Editor",
    menu: ["File", "Edit", "View", "Place", "Route", "Inspect", "Tools", "Preferences", "Help"],
    canvas: `<rect class="w" x="40" y="30" width="320" height="200" rx="10"/>
      <circle class="w" cx="56" cy="46" r="5"/><circle class="w" cx="344" cy="46" r="5"/><circle class="w" cx="56" cy="214" r="5"/><circle class="w" cx="344" cy="214" r="5"/>
      <g>${[0, 1, 2, 3].map((i) => `<rect class="pad" x="${86 + i * 10}" y="70" width="6" height="12" rx="1"/><rect class="pad" x="${86 + i * 10}" y="102" width="6" height="12" rx="1"/>`).join("")}<rect class="w" x="82" y="64" width="44" height="56" rx="2"/></g>
      <g><rect class="pad" x="170" y="150" width="8" height="8" rx="1"/><rect class="pad" x="186" y="150" width="8" height="8" rx="1"/><rect class="pad" x="170" y="176" width="8" height="8" rx="1"/><rect class="pad" x="186" y="176" width="8" height="8" rx="1"/></g>
      <path class="w hi" d="M89 114V138H174V150M119 114V128H190V150"/>
      <g class="new" id="wfnew">${[0, 1, 2, 3].map((i) => `<rect class="pad" x="${246 + i * 13}" y="82" width="7" height="15" rx="1"/><rect class="pad" x="${246 + i * 13}" y="126" width="7" height="15" rx="1"/>`).join("")}<rect class="w hi" x="240" y="76" width="55" height="71" rx="2"/><path class="rat" d="M249 141L190 158M262 141L194 180M126 76L249 82"/></g>`,
    panel: `<h4>Libraries</h4><div class="search">Filter</div><div id="rows">${row("Device", "KiCad built-in")}${row("Package_SO", "KiCad built-in", 4)}${row("Resistor_SMD", "KiCad built-in")}</div>`,
    steps: [
      ["Install the library package", "Plugin and Content Manager > Install from File. Your library arrives as PCM_ecadlib, with 3D models."],
      ["Run ecadlib: add part", "Tools > External Plugins > ecadlib: add part, right in the PCB editor."],
      ["Type the part number", "ecadlib runs the librarian and writes the part into the project's own library tables."],
      ["The footprint is placed", "It drops onto the board with its ratsnest, and the symbol is ready in the schematic."],
    ],
    async play(k) {
      const tools = $$(".menubar > span", win)[6];
      if (k === 0) {
        dialog("Plugin and Content Manager", `<ul class="log"><li><span>ecadlib library</span><span>symbols · footprints · 3D</span></li></ul><div class="btns"><span class="primary">Install from File…</span></div>`);
        await click($(".btns .primary", wfdlg));
        $(".log", wfdlg).insertAdjacentHTML("beforeend", `<li class="ok"><span>ecadlib-mylib.zip</span><span>installed</span></li>`);
        await wait(900); closeDialog();
        $("#rows", wfpanel).insertAdjacentHTML("afterbegin", row("PCM_ecadlib", "7 parts · installed", 4, true));
        await wait(1300);
      } else if (k === 1) {
        await click(tools); const m = openMenu(tools, ["Update PCB from Schematic", "Footprint Editor", "-", "External Plugins ▸"]);
        await wait(400); m.children[3].classList.add("hot"); await point(m.children[3]);
        const sub = document.createElement("div"); sub.className = "menu";
        const a = m.getBoundingClientRect(), w = win.getBoundingClientRect();
        sub.style.left = `${Math.min(a.right - w.left - 8, w.width - 220)}px`; sub.style.top = `${a.bottom - w.top - 34}px`;
        sub.innerHTML = "<div>ecadlib: add part</div>"; win.appendChild(sub); await wait(300);
        sub.children[0].classList.add("hot"); await click(sub.children[0]); closeMenus();
      } else if (k === 2) {
        dialog("ecadlib: add part", `<label>MPN<div class="in" id="in1"></div></label><ul class="log"></ul><div class="meter"><i></i></div><div class="btns"><span>Cancel</span><span class="primary">Add</span></div>`);
        await point($("#in1", wfdlg)); await type($("#in1", wfdlg), "LM358DR");
        await click($(".btns .primary", wfdlg));
        await log([["footprint", "Package_SO:SOIC-8", true], ["symbol", "LM358, 2 units", true], ["project tables", "${KIPRJMOD}/ecadlib", true]]);
        await wait(600); closeDialog();
      } else {
        const target = $("#wfnew", wfcanvas); await point(target);
        target.classList.add("on"); await wait(2600);
      }
    },
  },
};

function setScene(name) {
  const S = SCENES[name];
  wf.scene = name;
  $$(".wftabs [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.scene === name)));
  $("#wintitle").textContent = S.title;
  menubar.innerHTML = S.menu.map((m) => `<span class="${m === "ecadlib" ? "ours" : ""}">${m}</span>`).join("");
  wfcanvas.innerHTML = S.canvas;
  wfpanel.innerHTML = S.panel;
  closeMenus(); closeDialog(); wfdrop.hidden = true;
  wfsteps.innerHTML = S.steps.map(([h, p], k) => `<li data-k="${k}"><span class="bar"></span><p class="mono n">step ${k + 1}</p><h3>${h}</h3><p>${p}</p></li>`).join("");
}
function markStep(k) {
  $$("li", wfsteps).forEach((li, i) => { li.classList.toggle("on", i === k); li.classList.toggle("done", i < k); });
}
async function runScene(name, from = 0) {
  const run = ++wf.run;
  setScene(name);
  try {
    if (from > 0) { wf.fast = true; for (let k = 0; k < from; k++) await SCENES[name].play(k); wf.fast = false; }
    for (let k = from; k < 4; k++) {
      markStep(k);
      await SCENES[name].play(k);
      await wait(500);
    }
    markStep(4);
    await wait(2500);
    if (run === wf.run && !still.matches) runScene(name === "altium" ? "kicad" : "altium");
  } catch (e) { wf.fast = false; if (!(e instanceof Stop)) throw e; }
}
async function stillScene(name) {
  ++wf.run; setScene(name); wf.fast = true;
  for (let k = 0; k < 4; k++) await SCENES[name].play(k);
  wf.fast = false; closeDialog(); markStep(3);
}
$$(".wftabs [role=tab]").forEach((b) => b.addEventListener("click", () => (still.matches ? stillScene(b.dataset.scene) : runScene(b.dataset.scene))));
wfsteps.addEventListener("click", (e) => { const li = e.target.closest("li"); if (li && !still.matches) runScene(wf.scene, +li.dataset.k); });
wfplay.addEventListener("click", () => { wf.paused = !wf.paused; wfplay.textContent = wf.paused ? "play" : "pause"; wfplay.setAttribute("aria-pressed", String(!wf.paused)); });
if (still.matches) { wfplay.hidden = true; stillScene("altium"); }
else {
  setScene("altium");
  new IntersectionObserver(([e]) => {
    wf.visible = e.isIntersecting;
    if (wf.visible && !wf.started) { wf.started = true; runScene("altium"); }
  }, { threshold: 0.3 }).observe(win);
}

/* ---------- illustrate: the first time anything comes into view it is drawn, outlines by pen and text by hand ---------- */
const INK_MS = 1700;
// pen overlays sit on a fixed layer and follow their element every frame, so reveals, sticky bars and scrolling never leave them behind
const pens = new Map();
const follow = () => {
  pens.forEach((svg, el) => { const r = el.getBoundingClientRect(); svg.style.transform = `translate(${r.left}px, ${r.top}px)`; });
  if (pens.size) requestAnimationFrame(follow);
};
function inkSvg(svg, delay = 0) {
  const lines = $$("path:not(.dash), line, polyline, rect, circle", svg).filter((el) => !el.closest(".sig"));
  lines.forEach((el, i) => {
    // pathLength dashes need vector-effect off, so hold the on-screen width by hand and nothing jumps when it comes back
    const k = el.getScreenCTM()?.a || 1, w = parseFloat(getComputedStyle(el).strokeWidth) || 1;
    el.setAttribute("pathLength", "1");
    el.style.strokeWidth = `${w / k}`;
    el.style.animationDelay = `${delay + Math.min(i * 14, 900)}ms`;
  });
  svg.classList.add("inking");
  setTimeout(() => { svg.classList.remove("inking"); lines.forEach((el) => { el.removeAttribute("pathLength"); el.style.strokeWidth = ""; el.style.animationDelay = ""; }); }, delay + INK_MS + 1000);
}
function sidesOf(el) {
  const cs = getComputedStyle(el);
  const on = (s) => parseFloat(cs[`border${s}Width`]) > 0 && cs[`border${s}Style`] !== "none" && !/rgba\(.*,\s*0\)|transparent/.test(cs[`border${s}Color`]);
  return { t: on("Top"), r: on("Right"), b: on("Bottom"), l: on("Left"), rad: parseFloat(cs.borderTopLeftRadius) || 0, fill: cs.backgroundColor };
}
function inkBox(el, delay = 0) {
  const r = el.getBoundingClientRect(), s = sidesOf(el);
  if (r.width < 4 || r.height < 4 || !(s.t || s.r || s.b || s.l)) { el.classList.remove("undrawn"); return; }
  const svg = svgEl("svg", { class: "inkbox", "aria-hidden": "true", width: r.width, height: r.height, viewBox: `0 0 ${r.width} ${r.height}` });
  svg.style.transform = `translate(${r.left}px, ${r.top}px)`;
  const W = r.width - 0.5, H = r.height - 0.5, o = 0.5;
  const d = s.t && s.r && s.b && s.l ? null : [s.t && `M${o} ${o}H${W}`, s.r && `M${W} ${o}V${H}`, s.b && `M${o} ${H}H${W}`, s.l && `M${o} ${o}V${H}`].filter(Boolean).join("");
  const pen = d ? svgEl("path", { d, pathLength: 1 }, svg) : svgEl("rect", { x: o, y: o, width: r.width - 1, height: r.height - 1, rx: Math.max(0, s.rad - 0.5), pathLength: 1 }, svg);
  pen.style.animationDelay = `${delay}ms`;
  document.body.appendChild(svg);
  if (!pens.size) requestAnimationFrame(follow);
  pens.set(el, svg);
  el.classList.remove("undrawn");
  el.classList.add("drawing");
  // the pen settles to the border's own colour before the real border comes back, so there is no snap
  setTimeout(() => { svg.classList.add("settle"); el.classList.add("inked"); }, delay + INK_MS - 200);
  setTimeout(() => { svg.remove(); pens.delete(el); el.classList.remove("drawing", "inked"); }, delay + INK_MS + 800);
}
function write(el, delay = 0) {
  el.style.animationDelay = `${delay}ms`;
  el.classList.replace("unwritten", "writing");
  setTimeout(() => { el.classList.remove("writing"); el.style.animationDelay = ""; }, delay + 1400);
}
if (!still.matches) {
  const SKIP = "svg, script, style, .win *, .sr, h1, h2, .reveal h1 *, .reveal h2 *";
  const boxes = new Set(), texts = new Set();
  $$("body *").forEach((el) => {
    if (el.closest(SKIP) || el.matches(".inkbox")) return;
    const s = sidesOf(el);
    if (s.t || s.r || s.b || s.l) boxes.add(el);
  });
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walker.nextNode());) {
    if (!n.nodeValue.trim()) continue;
    let el = n.parentElement;
    if (!el || el.closest(SKIP)) continue;
    while (el.parentElement && getComputedStyle(el).display.startsWith("inline")) el = el.parentElement;
    texts.add(el);
  }
  [...texts].forEach((el) => { for (let a = el.parentElement; a; a = a.parentElement) if (texts.has(a)) { texts.delete(el); break; } });
  boxes.forEach((el) => el.classList.add("undrawn"));
  texts.forEach((el) => el.classList.add("unwritten"));
  const ink = new IntersectionObserver((es) => {
    es.filter((e) => e.isIntersecting).map((e) => e.target)
      .sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1))
      .forEach((t, i) => {
        ink.unobserve(t);
        const delay = Math.min(i * 45, 1100);
        if (t.matches("[data-fig], #fig-board")) { const svg = t.querySelector(":scope > svg"); if (svg) inkSvg(svg, delay); }
        else if (t.id === "wfcanvas") inkSvg(t, delay);
        if (boxes.has(t)) inkBox(t, delay);
        if (texts.has(t)) {
          write(t, delay + 120);
          // boxes inside a line of text (inline code, pills) are clipped until it is written, so draw them with it
          $$(".undrawn", t).forEach((b) => { ink.unobserve(b); inkBox(b, delay + 200); });
        }
      });
  }, { rootMargin: "0px 0px -6% 0px" });
  // the marquee draws as one strip when it arrives, not pill by pill as each slides in
  const rail = $(".rail");
  const inRail = (el) => rail && rail.contains(el) && el !== rail;
  if (rail) new IntersectionObserver(([e], o) => {
    if (!e.isIntersecting) return;
    o.disconnect();
    $$(".undrawn, .unwritten", rail).forEach((el, i) => { const d = Math.min(i * 60, 1200); if (el.matches(".undrawn")) inkBox(el, d); else write(el, d + 120); });
  }).observe(rail);
  new Set([...$$("[data-fig], #fig-board, #wfcanvas"), ...boxes, ...texts]).forEach((el) => { if (!inRail(el)) ink.observe(el); });
}
