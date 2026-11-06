// Checks api/crash-report.js with a fake GitHub (no network). Run by tests/test_crash_report_endpoint.py.
const assert = require("assert");
const path = require("path");
const ep = require(path.join(__dirname, "..", "api", "crash-report.js"));

function fakeGitHub(released = ["0.4.1", "0.4.2", "0.5.0"]) {
  const issues = [];
  const log = [];
  return {
    issues, log,
    async search(q) {
      log.push(["search", q]);
      const m = q.match(/"crash ([0-9a-f]+)"/);
      const labelled = (i) => !q.includes("label:auto-report") || i.labels.includes("auto-report");
      if (m) return { items: issues.filter((i) => i.title.startsWith(`[crash ${m[1]}]`) && labelled(i)).map((i) => ({ ...i, labels: i.labels.map((name) => ({ name })) })) };
      return { total_count: issues.filter((i) => i.labels.includes("auto-report")).length };
    },
    async create(title, body, labels) { const i = { number: issues.length + 1, title, body, labels, state: "open" }; issues.push(i); return i; },
    async update(n, f) { Object.assign(issues[n - 1], f); log.push(["update", n]); return issues[n - 1]; },
    async comment(n, text) { log.push(["comment", n, text]); return {}; },
    async released(v) { return released.includes(v); },
  };
}

const good = () => ({ schema: 1, version: "0.4.1", installed: "pip", os: "Windows-11", python: "3.13.1", where: "web UI",
  error: "KeyError", frames: [{ file: "ecadlib/userconfig.py", line: 12, function: "check_name" }, { file: "ecadlib/ui/server.py", line: 40, function: "do_GET" }] });

(async () => {
  // 1. a valid report makes one public issue with only the allowed fields
  let gh = fakeGitHub();
  let r = await ep.handle(good(), gh);
  assert.strictEqual(r.status, 201, JSON.stringify(r));
  assert.strictEqual(gh.issues.length, 1);
  const i = gh.issues[0];
  assert.ok(i.title.startsWith("[crash ") && i.title.includes("KeyError") && i.title.includes("do_GET"), i.title);
  assert.ok(i.body.includes("ecadlib/userconfig.py:12") && i.body.includes("Times reported: 1") && i.body.includes("Versions: `0.4.1`"), i.body);
  assert.deepStrictEqual(i.labels, ["auto-report", "bug"]);

  // 2. the same crash again (other line numbers, other version): same issue, counts go up, no new issue
  const st = ep.memoryStore();
  const again = good(); again.version = "0.4.2"; again.frames[0].line = 99; again.os = "Linux-6.8";
  r = await ep.handle(again, gh, { store: st });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(gh.issues.length, 1);
  assert.ok(gh.issues[0].body.includes("Times reported: 2") && gh.issues[0].body.includes("Versions: `0.4.1`, `0.4.2`"), gh.issues[0].body);
  assert.ok(gh.issues[0].body.includes("ecadlib/userconfig.py:12"));          // the first report's lines stay

  // 3. a closed issue seen on a new release gets one comment; a listed version or a version never released, none
  gh.issues[0].state = "closed";
  const v3 = good(); v3.version = "0.5.0";
  await ep.handle(v3, gh, { store: st });
  await ep.handle(v3, gh, { store: st });
  for (let n = 0; n < 5; n++) { const fake = good(); fake.version = `9.9.${n}`; await ep.handle(fake, gh, { store: st }); }
  assert.strictEqual(gh.log.filter((x) => x[0] === "comment").length, 1);
  assert.ok(!gh.issues[0].body.includes("9.9."), gh.issues[0].body);           // unreleased versions are never listed

  // 4. a maintainer's edits to the body stay; a removed stats block comes back on its own line
  gh.issues[0].body = gh.issues[0].body + "\nFixed in #300, see notes.\n";
  await ep.handle(good(), gh, { store: st });
  assert.ok(gh.issues[0].body.includes("Fixed in #300"), gh.issues[0].body);
  const n0 = ep.readStats(gh.issues[0].body).count;
  gh.issues[0].body = "Someone rewrote this. Times reported: 3";
  await ep.handle(good(), gh, { store: st });
  assert.ok(gh.issues[0].body.startsWith("Someone rewrote this. Times reported: 3\n\n<!-- crash-stats -->"), gh.issues[0].body);
  await ep.handle(good(), gh, { store: st });
  assert.strictEqual(ep.readStats(gh.issues[0].body).count, 3);                 // counts from its own block again
  assert.ok(n0 > 2);
  assert.strictEqual(ep.readStats(null).count, 1);

  // 5. an issue someone else titled "[crash <sig>]" (no label) is never edited: a new labelled one is filed
  gh = fakeGitHub();
  await ep.handle(good(), gh);
  gh.issues[0].labels = [];
  const before = gh.issues[0].body;
  r = await ep.handle(good(), gh);
  assert.strictEqual(r.status, 201);
  assert.strictEqual(gh.issues[0].body, before);

  // 6. anything outside the allowed values is refused: free text can never reach an issue
  const bads = [
    (b) => { b.error = "KeyError: 'my secret part'"; },
    (b) => { b.error = "JaneDoe_SSN_123456789"; },
    (b) => { b.where = "x\n@everyone look"; },
    (b) => { b.where = "project: Acme Secret Radar Board rev B"; },
    (b) => { b.where = "web UI: /api/parts/ACME-SECRET"; },
    (b) => { b.frames[0].file = "C:/Users/jane/acme/board.py"; },
    (b) => { b.frames[0].file = "ecadlib/../../etc/passwd.py"; },
    (b) => { b.frames[0].file = "ecadlib/Acme_Secret_Project/jane.doe.py"; },
    (b) => { b.frames[0].file = "__proto__"; },
    (b) => { b.frames[0].function = "run`<img src=x>`"; },
    (b) => { b.frames[0].function = "JaneDoeSecretProject"; },
    (b) => { b.frames[0].line = "12"; },
    (b) => { b.frames[0].line = 1000000; },
    (b) => { b.frames[0].note = "x"; },
    (b) => { b.os = "Windows <script>"; },
    (b) => { b.os = "Call Jane Doe 555 1234 visit evil.example"; },
    (b) => { b.version = "1.0-buy-cheap-pills-at-evil.example"; },
    (b) => { b.message = "ACME secret project"; },
    (b) => { b.schema = 2; },
    (b) => { b.frames = new Array(31).fill({ file: "ecadlib/userconfig.py", line: 1, function: "check_name" }); },
  ];
  for (const mut of bads) {
    const b = good(); mut(b);
    r = await ep.handle(b, fakeGitHub());
    assert.strictEqual(r.status, 400, JSON.stringify(b));
  }
  for (const where of ["triage", "MCP server", "Altium / KiCad menu", "web UI job", "command line"]) {
    const b = good(); b.where = where;
    assert.strictEqual(ep.validate(b), null, where);
  }
  for (const error of ["RulesError", "ParserError", "ReadTimeout"]) {               // ecadlib's own, a dependency's
    const b = good(); b.error = error; assert.strictEqual(ep.validate(b), null, error);
  }

  // 7. limits: new issues an hour, reports from one address, and the same crash filed twice at once
  gh = fakeGitHub();
  let s = ep.memoryStore();
  const errs = ["KeyError", "ValueError", "TypeError", "IndexError", "OSError", "RuntimeError", "NameError", "EOFError",
    "MemoryError", "ImportError", "LookupError", "SystemError"];
  for (let n = 0; n < 12; n++) { const b = good(); b.error = errs[n]; r = await ep.handle(b, gh, { store: s, ip: `10.0.0.${n}` }); }
  assert.strictEqual(gh.issues.length, 10);
  assert.strictEqual(r.status, 429);

  gh = fakeGitHub(); s = ep.memoryStore();
  const got = [];
  for (let n = 0; n < 25; n++) got.push((await ep.handle(good(), gh, { store: s, ip: "1.2.3.4" })).status);
  assert.strictEqual(got.filter((x) => x === 429).length, 5);
  assert.strictEqual(ep.readStats(gh.issues[0].body).count, 20);

  gh = fakeGitHub(); s = ep.memoryStore();
  gh.search = async () => ({ items: [], total_count: 0 });                         // search has not caught up yet
  const all = await Promise.all([1, 2, 3, 4].map((n) => ep.handle(good(), gh, { store: s, ip: `9.9.9.${n}` })));
  assert.strictEqual(gh.issues.length, 1, JSON.stringify(all));

  // 8. the Redis store speaks the REST protocol (INCR + EXPIRE, SET NX EX)
  const sent = [];
  const fakeFetch = async (url, o) => { const a = JSON.parse(o.body); sent.push(a); return { ok: true, json: async () => ({ result: a[0] === "INCR" ? 1 : "OK" }) }; };
  const rs = ep.redisStore("https://kv.example/", "t", fakeFetch);
  assert.strictEqual(await rs.incr("k", 60), 1);
  assert.strictEqual(await rs.once("o", 60), true);
  assert.deepStrictEqual(sent, [["INCR", "k"], ["EXPIRE", "k", 60], ["SET", "o", "1", "NX", "EX", 60]]);

  // 9. the HTTP wrapper: POST only, and off without a token
  const res = () => ({ code: 0, headers: {}, status(c) { this.code = c; return this; }, json(j) { this.j = j; return this; }, setHeader(k, v) { this.headers[k] = v; } });
  let out = res(); await ep({ method: "GET" }, out); assert.strictEqual(out.code, 405);
  delete process.env.CRASH_REPORT_TOKEN;
  out = res(); await ep({ method: "POST", body: good() }, out); assert.strictEqual(out.code, 503);
  console.log("crash-report endpoint: all checks passed");
})().catch((e) => { console.error(e); process.exit(1); });
