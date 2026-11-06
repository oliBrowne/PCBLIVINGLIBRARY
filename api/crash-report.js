// Automatic crash reports from ecadlib (opt-in: `ecadlib report-bug --auto on` or the setup page). Each distinct
// crash becomes one public GitHub issue; a crash seen again only updates that issue's counts. Every field must match a
// strict pattern (error type, ecadlib file / line / function, versions), so nothing a user typed, no file name of theirs
// and no error message can reach the issue. Settings (Vercel environment variables):
//   CRASH_REPORT_TOKEN  a fine-grained GitHub token with Issues: read and write on the repository (required)
//   CRASH_REPO          owner/name (default oliBrowne/PCBLIVINGLIBRARY)
const crypto = require("crypto");

const REPO = process.env.CRASH_REPO || "oliBrowne/PCBLIVINGLIBRARY";
const LABEL = "auto-report";
const MAX_NEW_PER_HOUR = 10;          // new issues; a flood of made-up crashes stops here
const MAX_BODY = 16000;
const RX = {
  version: /^[0-9][0-9A-Za-z.+-]{0,39}$/, installed: /^(pip|Windows app)$/, os: /^[\w .()+-]{1,60}$/,
  python: /^[0-9][0-9A-Za-z.+-]{0,19}$/, where: /^[\w .:/()+-]{1,80}$/, error: /^[A-Za-z_][\w.]{0,79}$/,
};
const FRAME = { file: /^ecadlib\/[\w/.-]{1,120}\.py$/, function: /^[\w<>.]{1,80}$/ };

function validate(b) {
  if (!b || typeof b !== "object" || Array.isArray(b)) return "not a JSON object";
  if (b.schema !== 1) return "unknown schema";
  for (const [k, rx] of Object.entries(RX)) {
    if (typeof b[k] !== "string" || !rx.test(b[k])) return `bad ${k}`;
  }
  if (!Array.isArray(b.frames) || b.frames.length > 30) return "bad frames";
  for (const f of b.frames) {
    if (!f || typeof f.file !== "string" || !FRAME.file.test(f.file) || f.file.includes("..") ||
        typeof f.function !== "string" || !FRAME.function.test(f.function) ||
        !Number.isInteger(f.line) || f.line < 1 || f.line > 1e6) return "bad frame";
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
  return `[crash ${sig}] ${p.error}` + (last ? ` in ${last.function} (${last.file})` : ` in ${p.where}`);
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
    "<!-- crash-stats: updated by api/crash-report.js -->",
    `Times reported: ${stats.count}`,
    `Versions: ${stats.versions.join(", ")}`,
    `Systems: ${stats.systems.join("; ")}`,
    "",
  ].join("\n");
}

function readStats(text) {
  const line = (name) => ((text || "").match(new RegExp(`^${name}: (.*)$`, "m")) || [])[1] || "";
  const list = (s, sep) => s.split(sep).map((x) => x.trim()).filter(Boolean);
  return { count: parseInt(line("Times reported"), 10) || 1, versions: list(line("Versions"), ","), systems: list(line("Systems"), ";") };
}

function addTo(list, v, max) {
  return list.includes(v) ? list : [...list, v].slice(-max);
}

// gh: { search(q) -> {total_count, items}, create(title, body, labels) -> issue, update(n, fields), comment(n, text) }
async function handle(input, gh, now = new Date()) {
  const bad = validate(input);
  if (bad) return { status: 400, json: { error: bad } };
  const p = { ...input, frames: input.frames.map((f) => ({ file: f.file, line: f.line, function: f.function })) };
  const sig = signature(p);
  const system = `${p.os} / Python ${p.python} (${p.installed})`;
  const found = await gh.search(`repo:${REPO} is:issue in:title "crash ${sig}"`);
  const issue = (found.items || []).find((i) => (i.title || "").startsWith(`[crash ${sig}]`));
  if (issue) {
    const s = readStats(issue.body);
    const newVersion = !s.versions.includes(p.version);
    const stats = { count: s.count + 1, versions: addTo(s.versions, p.version, 20), systems: addTo(s.systems, system, 10) };
    const kept = (issue.body || "").split("<!-- crash-stats")[0];          // the first report's text stays as it was
    const fresh = body(p, stats);
    await gh.update(issue.number, { body: kept + fresh.slice(fresh.indexOf("<!-- crash-stats")) });
    if (issue.state === "closed" && newVersion) {
      await gh.comment(issue.number, `Reported again on ecadlib ${p.version} (${system}) after this issue was closed.`);
    }
    return { status: 200, json: { issue: issue.number, new: false } };
  }
  const hourAgo = new Date(now.getTime() - 3600e3).toISOString().slice(0, 19) + "Z";
  const recent = await gh.search(`repo:${REPO} is:issue label:${LABEL} created:>=${hourAgo}`);
  if ((recent.total_count || 0) >= MAX_NEW_PER_HOUR) return { status: 429, json: { error: "too many new crash reports this hour" } };
  const created = await gh.create(title(p, sig), body(p, { count: 1, versions: [p.version], systems: [system] }), [LABEL, "bug"]);
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
  return {
    search: (q) => call("GET", `/search/issues?q=${encodeURIComponent(q)}&per_page=10`),
    create: async (t, b, labels) => {
      try { return await call("POST", `/repos/${REPO}/issues`, { title: t, body: b, labels }); }
      catch (e) { if (e.status !== 422) throw e; return call("POST", `/repos/${REPO}/issues`, { title: t, body: b }); }   // labels refused
    },
    update: (n, fields) => call("PATCH", `/repos/${REPO}/issues/${n}`, fields),
    comment: (n, text) => call("POST", `/repos/${REPO}/issues/${n}/comments`, { body: text }),
  };
}

module.exports = async function (req, res) {
  if (req.method !== "POST") { res.setHeader("Allow", "POST"); return res.status(405).json({ error: "POST only" }); }
  const token = process.env.CRASH_REPORT_TOKEN;
  if (!token) return res.status(503).json({ error: "crash reports are not set up" });
  let input = req.body;
  if (typeof input === "string") { try { input = JSON.parse(input); } catch (e) { input = null; } }
  if (JSON.stringify(input || "").length > MAX_BODY) return res.status(413).json({ error: "too large" });
  try {
    const out = await handle(input, github(token));
    return res.status(out.status).json(out.json);
  } catch (e) {
    return res.status(502).json({ error: "GitHub did not take the report" });
  }
};
module.exports.handle = handle;
module.exports.validate = validate;
module.exports.signature = signature;
