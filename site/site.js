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

// what each tool's window really looks like: Altium's dark UI around a cream schematic sheet, KiCad's PCB editor on navy
const ICONS = {
  doc: "M4 2h6l3 3v9H4zM10 2v3h3", open: "M2 5h4l1 1h7v7H2z", save: "M3 3h9l2 2v8H3zM5 3v3h6V3M5 13V9h6v4", undo: "M5 4L2 7l3 3M2 7h8a3 3 0 010 6H8",
  redo: "M11 4l3 3-3 3M14 7H6a3 3 0 000 6h2", zin: "M7 3a4 4 0 110 8 4 4 0 010-8zM10 10l4 4M5 7h4M7 5v4", fit: "M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4",
  sel: "M4 2v11l3-3 2 4 2-1-2-4h4z", wire: "M2 12h5V4h7", bus: "M2 11h5V5h7M2 13h7V7h5", label: "M3 4h7l3 4-3 4H3z", chip: "M5 3h6v10H5zM3 5h2M3 8h2M3 11h2M11 5h2M11 8h2M11 11h2",
  pwr: "M8 3v6M4 9h8M5.5 11h5M7 13h2", route: "M2 13l4-4h4l4-4", via: "M8 4a4 4 0 110 8 4 4 0 010-8zM8 6.5a1.5 1.5 0 110 3 1.5 1.5 0 010-3", text: "M3 3h10M8 3v10",
  drc: "M3 8l3 3 7-7", cube: "M8 2l5 3v6l-5 3-5-3V5zM3 5l5 3 5-3M8 8v6", grid: "M2 2h12v12H2zM6 2v12M10 2v12M2 6h12M2 10h12", mm: "M2 12V5M2 6l2-1 2 1v6M6 6l2-1 2 1v6", zone: "M3 3h10v10H3zM3 8l5-5M3 13L13 3M8 13l5-5",
};
const ico = (k) => (k === "|" ? '<b class="tsep"></b>' : `<i class="tb"><svg viewBox="0 0 16 16"><path d="${ICONS[k]}"/></svg></i>`);
const tools = (list) => list.map(ico).join("");
const pins = (x, y, n, dir, step = 14, nums = []) => Array.from({ length: n }, (_, i) => {
  const yy = y + i * step, x2 = x + dir * 12;
  return `<path class="pin" d="M${x} ${yy}H${x2}"/><text class="pinno" x="${(x + x2) / 2}" y="${yy - 1.5}" text-anchor="middle">${nums[i] ?? ""}</text>`;
}).join("");
const sheet = () => {
  let g = "";
  for (let x = 30; x < 380; x += 10) g += `M${x} 30V246`;
  for (let y = 40; y < 246; y += 10) g += `M20 ${y}H380`;
  const zx = [65, 155, 245, 335].map((x, i) => `<text class="zt" x="${x}" y="28.5" text-anchor="middle">${i + 1}</text>`).join("");
  const zy = ["A", "B", "C", "D"].map((l, i) => `<text class="zt" x="17" y="${58 + i * 54}" text-anchor="middle">${l}</text>`).join("");
  return `<rect class="sheet" x="14" y="24" width="372" height="228"/><path class="grid" d="${g}"/><rect class="zone" x="20" y="30" width="360" height="216"/>${zx}${zy}
    <path class="zone" d="M110 24V30M200 24V30M290 24V30M14 84H20M14 138H20M14 192H20"/>
    <g><rect class="tblk" x="272" y="216" width="108" height="30"/><path class="zone" d="M272 226H380M272 236H380M326 226V246"/>
      <text class="zt" x="275" y="223">Title  Board.SchDoc</text><text class="zt" x="275" y="233">Size  A4</text><text class="zt" x="329" y="233">Rev  1</text><text class="zt" x="275" y="243">Sheet 1 of 1</text><text class="zt" x="329" y="243">ecadlib demo</text></g>`;
};
const abar = () => `<g class="abar"><rect x="150" y="4" width="100" height="15" rx="2"/>${["sel", "wire", "bus", "pwr", "label", "chip", "text"].map((k, i) => `<g transform="translate(${155 + i * 13.5} 6.5) scale(.62)"><path d="${ICONS[k]}"/></g>`).join("")}</g>`;
const kpad = (x, y, w, h) => `<rect class="pad" x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.min(w, h) * 0.25}"/>`;
const khole = (cx, cy) => `<circle class="thpad" cx="${cx}" cy="${cy}" r="5"/><circle class="hole" cx="${cx}" cy="${cy}" r="3"/>`;

const SCENES = {
  altium: {
    app: "altium",
    title: "Board.PrjPcb  ·  Altium Designer",
    docs: `<span class="on"><i class="dic"></i>Board.SchDoc</span><span><i class="dic pcb"></i>Board.PcbDoc</span>`,
    tools: "",
    status: `<span>X:1250mil Y:880mil</span><span>Grid:100mil</span><span class="sp"></span><span>Panels</span>`,
    menu: ["File", "Edit", "View", "Project", "Place", "Design", "Tools", "Reports", "ecadlib", "Window", "Help"],
    canvas: `${sheet()}
      <g><rect class="body" x="46" y="62" width="48" height="70"/>${pins(46, 70, 4, -1, 16, [3, 2, 8, 4])}${pins(94, 70, 4, 1, 16, [1, 5, 6, 7])}
        <text class="pinnm" x="49" y="72">+IN</text><text class="pinnm" x="49" y="88">-IN</text><text class="pinnm" x="49" y="104">V+</text><text class="pinnm" x="49" y="120">V-</text>
        <text class="pinnm" x="91" y="72" text-anchor="end">OUT</text><text class="des" x="46" y="58">U1</text><text class="cmt" x="46" y="142">LM358DR</text></g>
      <g><rect class="body" x="150" y="46" width="9" height="24"/><text class="des" x="163" y="56">R1</text><text class="cmt" x="163" y="64">10k</text><path class="wire" d="M154.5 46V36M154.5 70V86H106"/></g>
      <g><path class="plate" d="M146 152H163M146 158H163"/><path class="wire" d="M154.5 152V118H106M154.5 158V176"/><text class="des" x="168" y="153">C1</text><text class="cmt" x="168" y="161">100n</text></g>
      <path class="wire" d="M34 70H24V200H154.5M154.5 176V200"/><circle class="junc" cx="154.5" cy="200" r="1.8"/><circle class="junc" cx="154.5" cy="118" r="1.8"/>
      <g><path class="pwr" d="M84 200V208M76 208H92M79 211H89M82 214H86"/><path class="wire" d="M84 200H24"/><text class="netl" x="96" y="212">GND</text></g>
      <text class="netl" x="114" y="84">VOUT</text>
      <g class="new" id="wfnew"><rect class="body" x="250" y="64" width="62" height="96"/>${pins(250, 72, 6, -1, 14, [2, 3, 5, 6, 7, 8])}${pins(312, 72, 6, 1, 14, [9, 10, 11, 12, 14, 15])}
        <text class="pinnm" x="253" y="74">VIN</text><text class="pinnm" x="253" y="88">EN</text><text class="pinnm" x="253" y="102">PG</text><text class="pinnm" x="309" y="74" text-anchor="end">SW</text><text class="pinnm" x="309" y="88" text-anchor="end">FB</text>
        <text class="des" x="250" y="60">U2</text><text class="cmt" x="250" y="170">TPS62133RGTR</text><path class="wire" d="M238 72H206V118H154.5"/></g>
      ${abar()}`,
    panel: `<h4>Components <span>≡</span></h4><div class="lib">ecadlib.DbLib <span>▾</span></div><div class="search">Search</div><div class="cols"><span>Design Item ID</span></div><div id="rows">${row("LM358DR", "Op amp, SOIC-8", 4)}${row("RC0603FR-0710KL", "10k 1% 0603")}${row("GRM155R71C104KA88D", "100n 16V 0402")}</div>`,
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
    app: "kicad",
    title: "board — PCB Editor",
    docs: tools(["sel", "|", "route", "via", "zone", "|", "text", "chip", "|", "drc"]),
    tools: tools(["doc", "open", "save", "|", "undo", "redo", "|", "zin", "fit", "|", "grid", "mm", "|", "drc", "cube"]) + '<span class="layer"><i></i>F.Cu (PgUp)</span>',
    status: `<span>Z 2.43</span><span>X 104.1400  Y 81.2800</span><span>dx 0.0000  dy 0.0000</span><span>grid 0.2500</span><span class="sp"></span><span>mm</span>`,
    menu: ["File", "Edit", "View", "Place", "Route", "Inspect", "Tools", "Preferences", "Help"],
    canvas: `<rect class="edge" x="40" y="30" width="320" height="200" rx="10"/>
      ${khole(56, 46)}${khole(344, 46)}${khole(56, 214)}${khole(344, 214)}
      <g>${[0, 1, 2, 3].map((i) => kpad(86 + i * 10, 70, 6, 12) + kpad(86 + i * 10, 102, 6, 12)).join("")}<rect class="silk" x="82" y="64" width="44" height="56" rx="1"/><circle class="silkdot" cx="80" cy="66" r="1.4"/><text class="silkt" x="82" y="60">U1</text><text class="fabt" x="104" y="94" text-anchor="middle">LM358</text></g>
      <g>${kpad(170, 150, 8, 8)}${kpad(186, 150, 8, 8)}${kpad(170, 176, 8, 8)}${kpad(186, 176, 8, 8)}<text class="silkt" x="198" y="160">C1</text><text class="silkt" x="198" y="184">C2</text></g>
      <path class="trk" d="M89 114V138H174V150M119 114V128H190V150"/>
      <path class="btrk" d="M99 70V52H220V196H190V184"/><circle class="via" cx="99" cy="52" r="3.2"/><circle class="vhole" cx="99" cy="52" r="1.4"/><path class="trk" d="M99 52V70"/>
      <g class="new" id="wfnew">${[0, 1, 2, 3].map((i) => kpad(246 + i * 13, 82, 7, 15) + kpad(246 + i * 13, 126, 7, 15)).join("")}<rect class="silk" x="240" y="76" width="55" height="71" rx="1"/><rect class="court" x="236" y="72" width="63" height="79"/><circle class="silkdot" cx="238" cy="80" r="1.4"/><text class="silkt" x="240" y="69">U3</text><text class="fabt" x="267.5" y="115" text-anchor="middle">LM358DR</text><path class="rat" d="M249 141L190 158M262 141L194 180M126 76L249 82"/></g>`,
    panel: `<h4>Appearance</h4><div class="ptabs"><span class="on">Layers</span><span>Objects</span><span>Nets</span></div>
      <div class="layers">${[["F.Cu", "#c83434", 1], ["B.Cu", "#4d7fc4"], ["F.Silkscreen", "#f2eda1"], ["F.Fab", "#afafaf"], ["F.Courtyard", "#ff26e2"], ["Edge.Cuts", "#d0d2cd"]].map(([n, c, on]) => `<div class="${on ? "on" : ""}"><i style="background:${c}"></i>${n}<span>◉</span></div>`).join("")}</div>
      <h4>Footprint libraries</h4><div id="rows">${row("Package_SO", "KiCad built-in", 4)}${row("Resistor_SMD", "KiCad built-in")}</div>`,
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
  win.dataset.app = S.app;
  $("#wintitle").textContent = S.title;
  $("#wftools").innerHTML = S.tools; $("#wfdocs").innerHTML = S.docs; $("#wfstatus").innerHTML = S.status;
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
