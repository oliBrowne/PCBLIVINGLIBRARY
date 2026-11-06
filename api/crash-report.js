// Automatic crash reports from ecadlib (opt-in: `ecadlib report-bug --auto on` or the setup page). Each distinct
// crash becomes one public GitHub issue; a crash seen again only updates that issue's counts. Nothing free-form gets
// in: the file and function names must be ecadlib's own (api/_crash-report-allow.json, built from the code by
// scripts/build_crash_report_allow.py), "where" is a command name or one of a few fixed places, the error is a Python
// or ecadlib error class, and the versions have fixed shapes. So no text a user typed, no file name of theirs and no
// error message can reach an issue. Settings (Vercel environment variables):
//   CRASH_REPORT_TOKEN  a fine-grained GitHub token with Issues: read and write on the repository (required)
//   CRASH_REPO          owner/name (default oliBrowne/PCBLIVINGLIBRARY)
//   KV_REST_API_URL + KV_REST_API_TOKEN (or UPSTASH_REDIS_REST_URL + _TOKEN): a Redis store (Upstash, free tier) for
//                       rate limits that hold across the function's instances; without one they hold per instance only
const crypto = require("crypto");
const ALLOW = require("./_crash-report-allow.json");

const REPO = process.env.CRASH_REPO || "oliBrowne/PCBLIVINGLIBRARY";
const LABEL = "auto-report";
const MAX_BODY = 16000;
const LIMITS = {          // [count, seconds]
  perIp: [20, 3600],      // reports from one address in an hour
  all: [300, 3600],       // reports of any kind in an hour
  newIssues: [10, 3600],  // new issues in an hour
};
const PLACES = new Set(["MCP server", "Altium / KiCad menu", "web UI", "web UI job", "command line", ...ALLOW.commands]);
const ERRORS = new Set(ALLOW.errors);
const RX = {
  version: /^\d{1,3}\.\d{1,3}\.\d{1,3}(?:(?:a|b|rc)\d{1,3})?$/,
  installed: /^(pip|Windows app)$/,
  os: /^(Windows|Linux|macOS|Darwin|FreeBSD|Other)-[0-9A-Za-z._-]{1,50}$/,
  python: /^3\.\d{1,2}\.\d{1,3}(?:(?:a|b|rc)\d{1,2})?$/,
  error: /^[A-Z][A-Za-z0-9]{1,40}$/,
};
const KEYS = new Set(["schema", "where", "frames", ...Object.keys(RX)]);
const FRAME_KEYS = new Set(["file", "line", "function"]);
const otherError = (e) => /^[A-Z][A-Za-z0-9]{1,40}(Error|Exception|Warning|Timeout)$/.test(e);   // from a dependency

function validate(b) {
  if (!b || typeof b !== "object" || Array.isArray(b)) return "not a JSON object";
  if (b.schema !== 1) return "unknown schema";
  for (const k of Object.keys(b)) if (!KEYS.has(k)) return `unknown field ${k.slice(0, 40)}`;
  for (const [k, rx] of Object.entries(RX)) {
    if (typeof b[k] !== "string" || !rx.test(b[k])) return `bad ${k}`;
  }
  if (!ERRORS.has(b.error) && !otherError(b.error)) return "bad error";
  if (typeof b.where !== "string" || !PLACES.has(b.where)) return "bad where";
  if (!Array.isArray(b.frames) || b.frames.length > 30) return "bad frames";
  for (const f of b.frames) {
    if (!f || typeof f !== "object" || Object.keys(f).some((k) => !FRAME_KEYS.has(k))) return "bad frame";
    const fns = typeof f.file === "string" && Object.prototype.hasOwnProperty.call(ALLOW.files, f.file) ? ALLOW.files[f.file] : null;
    if (!fns || typeof f.function !== "string" || !fns.includes(f.function) ||
        !Number.isInteger(f.line) || f.line < 1 || f.line > 999999) return "bad frame";
  }
  return null;
}

// One crash = the error type and the ecadlib functions it went through (not line numbers: they move between versions).
function signature(p) {
  const key = [p.error, ...p.frames.map((f) => `${f.file}:${f.function}`)].join("|");
  return crypto.createHash("sha1").update(key).digest("hex").slice(0, 12);
}

function title(p, sig) {
  const last = p.frames.length ? p.frames[p.frames.length - 1] : null;
  return (`[crash ${sig}] ${p.error}` + (last ? ` in ${last.function} (${last.file})` : ` in ${p.where}`)).slice(0, 250);
}

const START = "<!-- crash-stats -->", END = "<!-- /crash-stats -->";

function statsBlock(stats) {
  const code = (xs) => xs.map((x) => "`" + x + "`").join(", ");
  return [START, `Times reported: ${stats.count}`, `Versions: ${code(stats.versions)}`, `Systems: ${code(stats.systems)}`, END].join("\n");
}

function body(p, stats) {
  const frames = p.frames.length
    ? p.frames.map((f) => `- \`${f.file}:${f.line}\` in \`${f.function}\``).join("\n")
    : "- (no lines in ecadlib's own code)";
  return [
    "Automatic crash report from ecadlib: someone who switched crash reports on hit this bug. It holds only the",
    "error's type, where it happened, the lines in ecadlib's own code and the versions; the error message stays on",
    "their computer. Anyone is welcome to fix it.",
    "",
    `**Error:** \`${p.error}\` in \`${p.where}\``,
    "",
    "**ecadlib code it went through** (newest last, from the first report):",
    frames,
    "",
    statsBlock(stats),
    "",
  ].join("\n");
}

function readStats(text) {
  const t = text || "";
  const a = t.indexOf(START), z = t.indexOf(END, a + 1);
  const block = a >= 0 && z > a ? t.slice(a, z) : "";
  const line = (name) => (block.match(new RegExp(`^${name}: (.*)$`, "m")) || [])[1] || "";
  const list = (s) => s.split(",").map((x) => x.trim().replace(/^`|`$/g, "")).filter(Boolean);
  return { count: parseInt(line("Times reported"), 10) || 1, versions: list(line("Versions")), systems: list(line("Systems")) };
}

// Only the stats block changes; a maintainer's own edits above or below it stay. No block (someone removed it): one
// is added at the end, on its own line.
function withStats(text, stats) {
  const t = text || "";
  const a = t.indexOf(START), z = t.indexOf(END, a + 1);
  if (a >= 0 && z > a) return t.slice(0, a) + statsBlock(stats) + t.slice(z + END.length);
  return t.replace(/\s*$/, "") + "\n\n" + statsBlock(stats) + "\n";
}

function addTo(list, v, max) {
  return list.includes(v) ? list : [...list, v].slice(-max);
}

// Rate limits: a Redis store when one is set up (atomic across instances), else this instance's memory.
function memoryStore() {
  const m = new Map();
  const live = (k) => { const e = m.get(k); if (e && e.until <= Date.now()) { m.delete(k); return null; } return e; };
  return {
    async incr(k, ttl) { const e = live(k) || { n: 0, until: Date.now() + ttl * 1000 }; e.n += 1; m.set(k, e); return e.n; },
    async once(k, ttl) { if (live(k)) return false; m.set(k, { n: 1, until: Date.now() + ttl * 1000 }); return true; },
  };
}

function redisStore(url, token, fetchImpl = fetch) {
  const cmd = async (...args) => {
    const r = await fetchImpl(url.replace(/\/$/, ""), { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify(args) });
    if (!r.ok) throw new Error(`store ${r.status}`);
    return (await r.json()).result;
  };
  return {
    async incr(k, ttl) { const n = await cmd("INCR", k); if (n === 1) await cmd("EXPIRE", k, ttl); return n; },
    async once(k, ttl) { return (await cmd("SET", k, "1", "NX", "EX", ttl)) === "OK"; },
  };
}

let MEMORY = null;
function store() {
  const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
  if (url && token) return redisStore(url, token);
  return MEMORY || (MEMORY = memoryStore());
}

const over = async (st, key, [max, ttl]) => (await st.incr(`crash:${key}`, ttl)) > max;
const ipKey = (ip) => crypto.createHash("sha256").update(`crash-report|${ip || "?"}`).digest("hex").slice(0, 16);  // never the address itself

// gh: { search(q) -> {total_count, items}, create(title, body, labels) -> issue, update(n, fields), comment(n, text),
//       released(version) -> bool }
async function handle(input, gh, opts = {}) {
  const st = opts.store || memoryStore();
  const bad = validate(input);
  if (bad) return { status: 400, json: { error: bad } };
  if (await over(st, `ip:${ipKey(opts.ip)}`, LIMITS.perIp) || await over(st, "all", LIMITS.all)) {
    return { status: 429, json: { error: "too many crash reports, try later" } };
  }
  const p = { ...input, frames: input.frames.map((f) => ({ file: f.file, line: f.line, function: f.function })) };
  const sig = signature(p);
  const system = `${p.os} / Python ${p.python} (${p.installed})`;
  // Only issues this endpoint filed carry the label (people outside the project cannot add labels), so an issue
  // someone else titled "[crash ...]" is never touched.
  const found = await gh.search(`repo:${REPO} is:issue label:${LABEL} in:title "crash ${sig}"`);
  const issue = (found.items || []).find((i) => (i.title || "").startsWith(`[crash ${sig}]`) &&
    (i.labels || []).some((l) => (l && l.name ? l.name : l) === LABEL));
  if (issue) {
    const s = readStats(issue.body);
    // a version is listed (and announced on a closed issue) only once it is a real release
    const listed = s.versions.includes(p.version) || !(await gh.released(p.version)) ? s.versions : addTo(s.versions, p.version, 20);
    const stats = { count: s.count + 1, versions: listed, systems: addTo(s.systems, system, 10) };
    await gh.update(issue.number, { body: withStats(issue.body, stats) });
    if (issue.state === "closed" && listed !== s.versions && await st.once(`crash:said:${sig}:${p.version}`, 30 * 86400)) {
      await gh.comment(issue.number, `Reported again on ecadlib \`${p.version}\` (\`${system}\`) after this issue was closed.`);
    }
    return { status: 200, json: { issue: issue.number, new: false } };
  }
  // one filing per crash at a time: search can lag behind a just-created issue by a minute
  if (!(await st.once(`crash:filing:${sig}`, 300))) return { status: 202, json: { error: "this crash is being filed" } };
  if (await over(st, "new", LIMITS.newIssues)) return { status: 429, json: { error: "too many new crash reports this hour" } };
  const versions = (await gh.released(p.version)) ? [p.version] : [];
  const created = await gh.create(title(p, sig), body(p, { count: 1, versions, systems: [system] }), [LABEL, "bug"]);
  return { status: 201, json: { issue: created.number, new: true } };
}

function github(token, fetchImpl = fetch) {
  const call = async (method, path, data) => {
    const r = await fetchImpl(`https://api.github.com${path}`, {
      method, headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "User-Agent": "ecadlib-crash-report",
        ...(data ? { "Content-Type": "application/json" } : {}) },
      body: data ? JSON.stringify(data) : undefined,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(`GitHub ${method} ${path}: ${r.status}`); e.status = r.status; throw e; }
    return j;
  };
  const releases = new Map();
  return {
    search: (q) => call("GET", `/search/issues?q=${encodeURIComponent(q)}&per_page=10`),
    create: (t, b, labels) => call("POST", `/repos/${REPO}/issues`, { title: t, body: b, labels }),   // no label, no issue
    update: (n, fields) => call("PATCH", `/repos/${REPO}/issues/${n}`, fields),
    comment: (n, text) => call("POST", `/repos/${REPO}/issues/${n}/comments`, { body: text }),
    released: async (v) => {
      if (!releases.has(v)) {
        releases.set(v, await call("GET", `/repos/${REPO}/releases/tags/v${encodeURIComponent(v)}`).then(() => true,
          (e) => { if (e.status === 404) return false; throw e; }));
      }
      return releases.get(v);
    },
  };
}

function clientIp(req) {
  const h = (req.headers || {})["x-forwarded-for"] || (req.headers || {})["x-real-ip"] || "";
  return String(h).split(",")[0].trim() || (req.socket && req.socket.remoteAddress) || "";
}

module.exports = async function (req, res) {
  if (req.method !== "POST") { res.setHeader("Allow", "POST"); return res.status(405).json({ error: "POST only" }); }
  const token = process.env.CRASH_REPORT_TOKEN;
  if (!token) return res.status(503).json({ error: "crash reports are not set up" });
  let input = req.body;
  if (typeof input === "string") { try { input = JSON.parse(input); } catch (e) { input = null; } }
  if (JSON.stringify(input || "").length > MAX_BODY) return res.status(413).json({ error: "too large" });
  try {
    const out = await handle(input, github(token), { store: store(), ip: clientIp(req) });
    return res.status(out.status).json(out.json);
  } catch (e) {
    return res.status(502).json({ error: "GitHub did not take the report" });
  }
};
module.exports.handle = handle;
module.exports.validate = validate;
module.exports.signature = signature;
module.exports.readStats = readStats;
module.exports.withStats = withStats;
module.exports.memoryStore = memoryStore;
module.exports.redisStore = redisStore;
