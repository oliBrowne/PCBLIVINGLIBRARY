// ecadlib landing page: Hairline figures, PCB traces drawn on scroll, and a footprint player fed by
// footprints.json (made by scripts/build_site_footprints.py from ecadlib's own generator).
import * as H from "./hairline.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs = {}, parent) => {
  const el = document.createElementNS(NS, tag);
  for (const k in attrs) if (attrs[k] != null) el.setAttribute(k, attrs[k]);
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

/* ---------- figures (the hero is hero.js) ---------- */
for (const el of $$("[data-fig]")) {
  const out = el.dataset.read ? $("#" + el.dataset.read) : null;
  H[el.dataset.fig](el, { play: true, onRead: out ? (t) => { out.textContent = t; } : undefined });
}

let ticking = false;
addEventListener("scroll", () => {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
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
  const buses = 4, cu = Math.floor(r() * buses);        // one bus per section is copper, the brand accent
  for (let b = 0; b < buses; b++) {
    const cls = b === cu ? "cu" : null;
    const left = b % 2 === 0, n = 1 + Math.floor(r() * 3), y0 = Hh * (0.12 + 0.76 * r()), up = r() < 0.5 ? -1 : 1;
    const run1 = margin * (0.2 + 0.35 * r()), drop = 14 + r() * Math.min(60, margin * 0.25), run2 = 10 + r() * margin * 0.2;
    for (let k = 0; k < n; k++) {
      const off = k * 7, sx = left ? -10 : W + 10, dir = left ? 1 : -1;
      const x1 = sx + dir * (run1 + (up > 0 ? off : -off)), y = y0 + off;
      const x2 = x1 + dir * drop, y2 = y + up * drop, x3 = x2 + dir * (run2 + off * 0.5);
      const d = `M${sx} ${y}H${x1}L${x2} ${y2}H${x3}`;
      const p = svgEl("path", { d, pathLength: 1, class: cls }, svg);
      p.style.transitionDelay = `${(b * 0.15 + k * 0.08).toFixed(2)}s`;
      svgEl("circle", { cx: x3 + dir * 3.5, cy: y2, r: 3.5, class: cls }, svg);
      if (k === 0 && !still.matches && r() < 0.8) {
        const dot = svgEl("circle", { class: cls ? "sig cu" : "sig", r: 1.6 }, svg);
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

// what the close-up looks at, per package, measured from the generated footprint itself
const rectOf = (p) => [p.x - p.w / 2, p.y - p.h / 2, p.x + p.w / 2, p.y + p.h / 2];
const boxOf = (rs, m = 0) => [Math.min(...rs.map((r) => r[0])) - m, Math.min(...rs.map((r) => r[1])) - m, Math.max(...rs.map((r) => r[2])) + m, Math.max(...rs.map((r) => r[3])) + m];
const ptRect = (x, y, r) => Math.hypot(Math.max(r[0] - x, 0, x - r[2]), Math.max(r[1] - y, 0, y - r[3]));
// the layer tabs follow the work: the layer being drawn is the current one, and a close-up shows its layer alone
const layerTo = (l) => $$(".layertabs [data-l]").forEach((t) => t.classList.toggle("on", t.dataset.l === l));
const solo = (l) => { if (l) fsvg.dataset.solo = l; else delete fsvg.dataset.solo; };
function focusOf(f, pads, silk, crt) {
  const num = (n) => pads.filter(({ p }) => p.num === String(n));
  const ep = String(f.spec.pin_count + 1), cnt = f.spec.pin_count, pitch = f.spec.pitch;
  if (/^QFN/.test(f.name)) {
    const [e] = num(ep);
    return { els: [e.el], box: boxOf([rectOf(e.p)], 0.3), layer: "top", label: `exposed pad ${e.p.w.toFixed(2)} × ${e.p.h.toFixed(2)} mm, pad ${ep}` + (f.spec.exposed_pad?.paste_coverage ? `, paste ${Math.round(f.spec.exposed_pad.paste_coverage * 100)}%` : "") };
  }
  if (/^SOT-223/.test(f.name)) {
    const twos = num(2);
    return { els: twos.map((q) => q.el), box: boxOf(twos.map((q) => rectOf(q.p)), 0.3), layer: "top", label: "the tab is pad 2 as well: one net, one number" };
  }
  if (/^LQFP|^QFP/.test(f.name)) {
    const q = [...num(1), ...num(2), ...num(cnt), ...num(cnt - 1)];
    const gap = pitch - f.ipc.X;
    return { els: q.map((x) => x.el), box: boxOf(q.map((x) => rectOf(x.p)), 0.2), layer: "top", label: `${pitch.toFixed(2)} pitch, ${f.ipc.X.toFixed(2)} pads, ${gap.toFixed(2)} mm copper between` };
  }
  if (f.spec.package_type === "chip") {
    const edge = Math.max(...pads.map(({ p }) => p.x + p.w / 2)), cx1 = f.courtyard[2];
    return { els: [crt], box: [edge - 0.6, f.courtyard[1] - 0.1, cx1 + 0.15, f.courtyard[3] + 0.1], layer: "mech15", label: `courtyard ${(cx1 - edge).toFixed(2)} mm past copper, density ${f.density}` };
  }
  if (/^SOT-23$/.test(f.name) && silk.length) {
    let best = { d: Infinity, pad: pads[0] };
    f.silk.forEach(([a, b]) => { for (let k = 0; k <= 24; k++) { const x = a[0] + ((b[0] - a[0]) * k) / 24, y = a[1] + ((b[1] - a[1]) * k) / 24; pads.forEach((q) => { const d = ptRect(x, y, rectOf(q.p)); if (d < best.d) best = { d, pad: q }; }); } });
    return { els: [...silk, best.pad.el], box: boxOf([rectOf(best.pad.p), ...f.silk.map(([a, b]) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])])], 0.15), layer: "overlay", label: `silkscreen held ${best.d.toFixed(2)} mm off copper` };
  }
  // gull-wing default: the land span across the body, toe to toe and heel to heel
  const a = num(1)[0], b = pads.find(({ p }) => p !== a.p && Math.abs(p.y - a.p.y) < 1e-6 && p.x > 0) || pads[pads.length - 1];
  const ra = rectOf(a.p), rb = rectOf(b.p), t = a.p.h * 0.35;
  // Z across the outer toes just above the row, G across the inner heels just below it
  const yz = ra[1] - a.p.h * 0.55, yg = ra[3] + a.p.h * 0.55;
  const lines = `M${ra[0]} ${yz}H${rb[2]}M${ra[0]} ${yz - t}V${yz + t}M${rb[2]} ${yz - t}V${yz + t}M${ra[2]} ${yg}H${rb[0]}M${ra[2]} ${yg - t}V${yg + t}M${rb[0]} ${yg - t}V${yg + t}`;
  return { els: [a.el, b.el], box: boxOf([ra, rb], 0.3), layer: "top", label: `Z ${f.ipc.Z.toFixed(2)} toe to toe, G ${f.ipc.G.toFixed(2)} heel to heel`, lines };
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
  solo(null); layerTo("top");

  // the board, at the package's scale
  const [cx0, cy0, cx1, cy1] = f.courtyard, w = (cx1 - cx0) * 1.9, h = Math.max(w * 0.8, (cy1 - cy0) * 1.7);
  const vw = Math.max(w, h * 1.25), vh = vw * 0.8;
  fp.home = [-vw / 2, -vh / 2, vw, vh];
  fsvg.replaceChildren();
  // stroke widths in mm as plain numbers: calc() on a unitless number is not a valid stroke-width in Safari or Firefox,
  // which then draw the default width of 1 user unit (1 mm), and the silk turns into slabs over the pads
  fsvg.style.setProperty("--sw", (vw / 380).toFixed(4));
  fsvg.style.setProperty("--sw-silk", ((vw / 380) * 1.2).toFixed(4));
  fsvg.style.setProperty("--sw-focus", ((vw / 380) * 3).toFixed(4));
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
  later(typeMs, () => { fstate.textContent = "generating"; crt.classList.add("on"); layerTo("mech15"); });
  later(typeMs + 250, () => { fab.classList.add("on"); layerTo("mech13"); });
  later(typeMs + 450, () => layerTo("top"));
  pads.forEach(({ el, d }) => later(typeMs + 450 + (d / far) * 750, () => el.classList.add("on")));
  later(typeMs + 1400, () => { silk.forEach((l) => l.classList.add("on")); marks.forEach((m) => m.classList.add("on")); layerTo("overlay"); });
  later(typeMs + 2000, () => layerTo("top"));
  later(typeMs + 1700, () => nums.forEach((t) => t.classList.add("on")));
  later(typeMs + 2000, () => {
    dims.forEach((d) => d.classList.add("on"));
    const ipc = [["Z", f.ipc.Z], ["G", f.ipc.G], ["X", f.ipc.X], ["density", f.density]];
    fipc.innerHTML = ipc.map(([k, v]) => `<div><dt>${k}</dt><dd>${typeof v === "number" ? v.toFixed(2) : v}</dd></div>`).join("");
    fstate.textContent = `${f.pads.length} pads · ${f.checks ? f.checks + " notes" : "checks pass"}`;
    fstate.classList.add("done");
  });
  if (!still.matches) {
    // each package gets its own close-up: the one thing the generator got right that you would check by hand
    const fo = focusOf(f, pads, silk, crt);
    const bw = fo.box[2] - fo.box[0], bh = fo.box[3] - fo.box[1];
    const zw = Math.max(bw, bh * 1.6, fp.home[2] * 0.3) * 1.3, zh = zw * 0.8;
    // the caption sits along the bottom, so the subject sits a little high
    const zx = (fo.box[0] + fo.box[2]) / 2 - zw / 2, zy = (fo.box[1] + fo.box[3]) / 2 - zh * 0.42;
    const cap = svgEl("g", { class: "focuscap" }, fsvg), fsz = zw / 30;
    if (fo.lines) svgEl("path", { class: "capdim", d: fo.lines }, cap);
    const capt = svgEl("text", { x: zx + zw * 0.04, y: zy + zh * 0.93, "font-size": fsz.toFixed(4) }, cap);
    capt.textContent = fo.label;
    later(typeMs + 2900, () => { moveView([zx, zy, zw, zh], 1500); fo.els.forEach((el) => el.classList.add("focus")); layerTo(fo.layer); solo(fo.layer); });
    later(typeMs + 3900, () => {
      const b = capt.getBBox(), pad = fsz * 0.45;
      cap.insertBefore(svgEl("rect", { x: b.x - pad, y: b.y - pad * 0.6, width: b.width + pad * 2, height: b.height + pad * 1.2, rx: pad * 0.5 }), capt);
      cap.classList.add("on");
    });
    later(typeMs + 5600, () => { cap.classList.remove("on"); solo(null); layerTo("top"); fo.els.forEach((el) => el.classList.remove("focus")); moveView(fp.home, 1500); });
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

/* ---------- illustrate: the first time anything comes into view it is drawn, outlines by pen and text by hand ---------- */
const INK_MS = 1700;
// pen overlays sit on a fixed layer and follow their element every frame, so reveals, sticky bars and scrolling never leave them behind
const pens = new Map();
const follow = () => {
  pens.forEach((svg, el) => { const r = el.getBoundingClientRect(); svg.style.transform = `translate(${r.left}px, ${r.top}px)`; });
  if (pens.size) requestAnimationFrame(follow);
};
function inkSvg(svg, delay = 0) {
  // pathLength dashes need vector-effect off, so hold the on-screen width by hand and nothing jumps when it comes back
  const pen = (el) => {
    if (el.hasAttribute("pathLength") || el.closest(".sig") || el.matches(".dash")) return;
    const k = el.getScreenCTM()?.a || 1, w = parseFloat(getComputedStyle(el).strokeWidth) || 1;
    el.setAttribute("pathLength", "1");
    el.style.strokeWidth = `${w / k}`;
  };
  const SHAPES = "path, line, polyline, rect, circle";
  const all = () => $$(SHAPES, svg);
  all().forEach(pen);
  // figures rebuild their paths when the pointer or the scroll camera moves them; new paths join the same pen stroke
  const mo = new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => {
    if (n.nodeType !== 1) return;
    if (n.matches(SHAPES)) pen(n);
    $$(SHAPES, n).forEach(pen);
  })));
  mo.observe(svg, { childList: true, subtree: true });
  svg.style.animationDelay = `${delay}ms`;
  svg.classList.add("inking");
  setTimeout(() => {
    mo.disconnect();
    svg.classList.remove("inking"); svg.style.animationDelay = "";
    all().forEach((el) => { if (el.style.strokeWidth) { el.removeAttribute("pathLength"); el.style.strokeWidth = ""; } });
  }, delay + INK_MS + 150);
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
  const SKIP = "svg, script, style, .wk-stage *, .sr, .xh, h1, h2, .reveal h1 *, .reveal h2 *";   // .xh: the hero draws itself
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
        if (t.matches("[data-fig]")) { const svg = t.querySelector(":scope > svg"); if (svg) inkSvg(svg, delay); }
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
  new Set([...$$("[data-fig]"),...boxes, ...texts]).forEach((el) => { if (!inRail(el)) ink.observe(el); });
}
