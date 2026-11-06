// Checks api/crash-report.js with a fake GitHub (no network). Run by tests/test_crash_report_endpoint.py.
const assert = require("assert");
const path = require("path");
const ep = require(path.join(__dirname, "..", "api", "crash-report.js"));

function fakeGitHub() {
  const issues = [];
  const log = [];
  return {
    issues, log,
    async search(q) {
      log.push(["search", q]);
      const m = q.match(/"crash ([0-9a-f]+)"/);
      if (m) return { items: issues.filter((i) => i.title.startsWith(`[crash ${m[1]}]`)) };
      return { total_count: issues.filter((i) => i.labels.includes("auto-report")).length };
    },
    async create(title, body, labels) { const i = { number: issues.length + 1, title, body, labels, state: "open" }; issues.push(i); return i; },
    async update(n, f) { Object.assign(issues[n - 1], f); log.push(["update", n]); return issues[n - 1]; },
    async comment(n, text) { log.push(["comment", n, text]); return {}; },
  };
}

const good = () => ({ schema: 1, version: "0.4.1", installed: "pip", os: "Windows-11", python: "3.13.1", where: "web UI: /api/status",
  error: "KeyError", frames: [{ file: "ecadlib/app.py", line: 12, function: "status" }, { file: "ecadlib/ui/server.py", line: 40, function: "do_GET" }] });

(async () => {
  // 1. a valid report makes one public issue with only the allowed fields
  let gh = fakeGitHub();
  let r = await ep.handle(good(), gh);
  assert.strictEqual(r.status, 201);
  assert.strictEqual(gh.issues.length, 1);
  const i = gh.issues[0];
  assert.ok(i.title.startsWith("[crash ") && i.title.includes("KeyError") && i.title.includes("do_GET"), i.title);
  assert.ok(i.body.includes("ecadlib/app.py:12") && i.body.includes("Times reported: 1") && i.body.includes("Versions: 0.4.1"));
  assert.deepStrictEqual(i.labels, ["auto-report", "bug"]);

  // 2. the same crash again (other line numbers, other version): same issue, counts go up, no new issue
  const again = good(); again.version = "0.4.2"; again.frames[0].line = 99; again.os = "Linux-6";
  r = await ep.handle(again, gh);
  assert.strictEqual(r.status, 200);
  assert.strictEqual(gh.issues.length, 1);
  assert.ok(gh.issues[0].body.includes("Times reported: 2") && gh.issues[0].body.includes("Versions: 0.4.1, 0.4.2"), gh.issues[0].body);
  assert.ok(gh.issues[0].body.includes("ecadlib/app.py:12"));          // the first report's lines stay

  // 3. a closed issue seen on a new version gets one comment; on a version already listed, none
  gh.issues[0].state = "closed";
  const v3 = good(); v3.version = "0.5.0";
  await ep.handle(v3, gh);
  await ep.handle(v3, gh);
  assert.strictEqual(gh.log.filter((x) => x[0] === "comment").length, 1);

  // 4. anything outside the patterns is refused: free text can never reach an issue
  const bads = [
    (b) => { b.error = "KeyError: 'my secret part'"; },
    (b) => { b.where = "x\n@everyone look"; },
    (b) => { b.frames[0].file = "C:/Users/jane/acme/board.py"; },
    (b) => { b.frames[0].file = "ecadlib/../../etc/passwd.py"; },
    (b) => { b.frames[0].function = "run`<img src=x>`"; },
    (b) => { b.frames[0].line = "12"; },
    (b) => { b.os = "Windows <script>"; },
    (b) => { b.message = undefined; b.schema = 2; },
    (b) => { b.frames = new Array(31).fill({ file: "ecadlib/a.py", line: 1, function: "f" }); },
  ];
  for (const mut of bads) {
    const b = good(); mut(b);
    r = await ep.handle(b, fakeGitHub());
    assert.strictEqual(r.status, 400, JSON.stringify(b));
  }
  // extra fields (a message) are dropped, never published
  const extra = good(); extra.message = "ACME secret project"; extra.frames[0].note = "x";
  gh = fakeGitHub();
  await ep.handle(extra, gh);
  assert.ok(!JSON.stringify(gh.issues).includes("ACME"));

  // 5. the hourly cap on new issues
  gh = fakeGitHub();
  for (let n = 0; n < 12; n++) { const b = good(); b.error = `Err${n}`; r = await ep.handle(b, gh); }
  assert.strictEqual(gh.issues.length, 10);
  assert.strictEqual(r.status, 429);

  // 6. the HTTP wrapper: POST only, and off without a token
  const res = () => ({ code: 0, headers: {}, status(c) { this.code = c; return this; }, json(j) { this.j = j; return this; }, setHeader(k, v) { this.headers[k] = v; } });
  let out = res(); await ep({ method: "GET" }, out); assert.strictEqual(out.code, 405);
  delete process.env.CRASH_REPORT_TOKEN;
  out = res(); await ep({ method: "POST", body: good() }, out); assert.strictEqual(out.code, 503);
  console.log("crash-report endpoint: all checks passed");
})().catch((e) => { console.error(e); process.exit(1); });
