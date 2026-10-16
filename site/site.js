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
H.board($("#fig-board"), { play: true, intensity: 0.55, onRead: (t) => { heroRead.textContent = t; } });
for (const el of $$("[data-fig]")) {
  const out = el.dataset.read ? $("#" + el.dataset.read) : null;
  H[el.dataset.fig](el, { play: true, onRead: out ? (t) => { out.textContent = t; } : undefined });
}

// The camera: it swings in from the side on load, then scrolling the hero turns it.
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
