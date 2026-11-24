// Review 2026-11-23, 2.5 and 2.9: what the crash-report endpoint (api/crash-report.js) lets a stranger do, with a fake
// GitHub and a fake store (no network). Run by tests/test_security1123_endpoint.py; argv[2] = another crash-report.js
// to check (the tests use it to show the old file fails).
const assert = require("assert");
const path = require("path");
const ep = require(process.argv[2] || path.join(__dirname, "..", "api", "crash-report.js"));
const ALLOW = require(path.join(__dirname, "..", "api", "_crash-report-allow.json"));

function fakeGitHub() {
  const issues = [];
  return {
    issues,
    async search(q) { const m = q.match(/"crash ([0-9a-f]+)"/);
      return { items: issues.filter((i) => i.title.startsWith(`[crash ${m[1]}]`)).map((i) => ({ ...i, labels: i.labels.map((name) => ({ name })) })) }; },
    async create(title, body, labels) { const i = { number: issues.length + 1, title, body, labels, state: "open" }; issues.push(i); return i; },
    async update(n, f) { Object.assign(issues[n - 1], f); return issues[n - 1]; },
    async comment() { return {}; },
    async released() { return true; },
  };
}
const good = () => ({ schema: 1, version: "0.4.1", installed: "pip", os: "Windows-11", python: "3.13.1", where: "web UI",
  error: "KeyError", frames: [{ file: "ecadlib/userconfig.py", line: 12, function: "check_name" }] });
const FILES = Object.keys(ALLOW.files);
const crash = (n) => ({ ...good(), frames: [{ file: FILES[n + 3], line: 1, function: "<module>" }] });   // a distinct crash

(async () => {
  // 1. the error name: a word the sender made up never reaches the issue; a known one stays
  let gh = fakeGitHub();
  let r = await ep.handle({ ...good(), error: "OwnerShipsMalwareSeeEvilExampleComError" }, gh);
  assert.strictEqual(r.status, 201, JSON.stringify(r));
  let text = gh.issues[0].title + gh.issues[0].body;
  assert.ok(!/Malware|Evil|OwnerShips/i.test(text), text);
  assert.ok(gh.issues[0].title.includes("OtherError") && gh.issues[0].body.includes("`OtherError`"), text);
  for (const e of ["BuyPillsAtEvilExampleTimeout", "JaneDoeOfAcmeIsAFraudException", "VisitEvilWarning"]) {
    gh = fakeGitHub(); await ep.handle({ ...good(), error: e }, gh);
    assert.ok(gh.issues.length === 1 && !gh.issues[0].title.includes(e) && !gh.issues[0].body.includes(e), e);
  }
  for (const e of ["KeyError", "RulesError", "JSONDecodeError", "ReadTimeout", "CalledProcessError"]) {   // Python's, ecadlib's, a library's
    gh = fakeGitHub(); await ep.handle({ ...good(), error: e }, gh);
    assert.ok(gh.issues[0].title.includes(` ${e} in `), e + ": " + gh.issues[0].title);
  }
  assert.strictEqual((await ep.handle({ ...good(), error: "KeyError: my part" }, fakeGitHub())).status, 400);

  // 2. the system: a fixed form only, whatever follows the family name
  const forms = [["Windows-visit.evil-example.com.for.the.fix", "Windows"], ["Windows-11", "Windows-11"], ["Windows-10-10.0.19045-SP0", "Windows-10"],
    ["Windows-2022Server", "Windows-2022Server"], ["Windows-8.1", "Windows-8.1"], ["macOS-14.5-arm64-arm-64bit", "macOS-14.5"],
    ["Linux-6.8.0-45-generic-x86_64-with-glibc2.39", "Linux-6.8"], ["Linux-call.555.1234.now", "Linux"], ["Linux-5551234567", "Linux"],
    ["Darwin-23.5.0", "Other"], ["FreeBSD-14.1-RELEASE", "Other"], ["Other-unknown", "Other"], ["Windows", "Windows"]];
  for (const [sent, kept] of forms) {
    assert.strictEqual(ep.osForm(sent), kept, sent);
    gh = fakeGitHub(); r = await ep.handle({ ...good(), os: sent }, gh);
    assert.strictEqual(r.status, 201, sent);
    assert.ok(gh.issues[0].body.includes("Systems: `" + kept + " / Python 3.13.1 (pip)`"), sent + ": " + gh.issues[0].body);
  }
  for (const bad of ["Windows <script>", "Solaris-11", "windows-11", "Windows-" + "9".repeat(60), "", 11]) {
    assert.strictEqual((await ep.handle({ ...good(), os: bad }, fakeGitHub())).status, 400, String(bad));
  }
  // a second system on a known crash is listed in its fixed form too
  gh = fakeGitHub(); await ep.handle(good(), gh); await ep.handle({ ...good(), os: "Linux-6.8.0-evil.example-generic" }, gh);
  assert.ok(gh.issues[0].body.includes("`Linux-6.8 / Python 3.13.1 (pip)`") && !gh.issues[0].body.includes("evil"), gh.issues[0].body);

  // 3. one address cannot use up everyone's new issues: 25 made-up crashes -> its own small allowance, others still file
  gh = fakeGitHub(); let st = ep.memoryStore(); const codes = {};
  for (let n = 0; n < 25; n++) { r = await ep.handle(crash(n), gh, { store: st, ip: "2.2.2.2" }); codes[r.status] = (codes[r.status] || 0) + 1; }
  const [perHour] = ep.LIMITS.newPerIp, [allHour] = ep.LIMITS.newIssues;
  assert.ok(perHour < allHour, "the per-address limit is below the shared one");
  assert.strictEqual(gh.issues.length, perHour, JSON.stringify(codes));
  r = await ep.handle(crash(40), gh, { store: st, ip: "9.9.9.9" });
  assert.strictEqual(r.status, 201, "a real user's new crash right after: " + JSON.stringify(r));
  // ... and a refused sender does not hold a crash's filing either: someone else files that very crash at once
  r = await ep.handle(crash(24), gh, { store: st, ip: "9.9.9.8" });
  assert.strictEqual(r.status, 201, JSON.stringify(r));
  // an address that reports a crash already filed is not counted as filing a new one
  gh = fakeGitHub(); st = ep.memoryStore();
  await ep.handle(good(), gh, { store: st, ip: "5.5.5.5" });
  for (let n = 0; n < 10; n++) assert.strictEqual((await ep.handle(good(), gh, { store: st, ip: "6.6.6.6" })).status, 200);
  assert.strictEqual((await ep.handle(crash(1), gh, { store: st, ip: "6.6.6.6" })).status, 201);
  // the day has a ceiling per address: after the hour's window, the same address gets at most the day's allowance
  gh = fakeGitHub(); st = ep.memoryStore(); let t = 1800000000000;
  for (let hour = 0; hour < 6; hour++) for (let n = 0; n < 5; n++) await ep.handle(crash(hour * 5 + n), gh, { store: st, ip: "2.2.2.2", now: t + hour * 3600e3 });
  assert.strictEqual(gh.issues.length, ep.LIMITS.newPerIpDay[0]);
  // IPv6: what one subscriber is given (a /56: 256 networks of /64) is one address (second review, I4)
  gh = fakeGitHub(); st = ep.memoryStore();
  for (let n = 0; n < 12; n++) await ep.handle(crash(n), gh, { store: st, ip: `2001:db8:1:2${(n + 16).toString(16)}::${(n + 1).toString(16)}` });
  assert.strictEqual(gh.issues.length, perHour);
  assert.strictEqual((await ep.handle(crash(20), gh, { store: st, ip: "2001:db8:1:300::1" })).status, 201);
  assert.strictEqual(ep.ipKey("2001:DB8:1:2ff::1", "s"), ep.ipKey("2001:db8:0001:0200:ffff::9", "s"));
  assert.notStrictEqual(ep.ipKey("2001:db8:1:2ff::1", "s"), ep.ipKey("2001:db8:1:3ff::1", "s"));
  // the address is the one the host itself saw, not one the sender wrote into a header
  const rq = (headers) => ep.clientIp({ headers, socket: { remoteAddress: "10.0.0.1" } });
  assert.strictEqual(rq({ "x-forwarded-for": "9.9.9.9, 8.8.8.8", "x-real-ip": "7.7.7.7", "x-vercel-forwarded-for": "6.6.6.6" }), "6.6.6.6");
  assert.strictEqual(rq({ "x-forwarded-for": "9.9.9.9, 8.8.8.8", "x-real-ip": "7.7.7.7" }), "7.7.7.7");
  assert.strictEqual(rq({ "x-forwarded-for": "9.9.9.9, 8.8.8.8" }), "8.8.8.8");       // the nearest proxy's word, not the sender's
  assert.strictEqual(rq({}), "10.0.0.1");

  // 4. a store failure between INCR and EXPIRE: the address is counted again from zero in the next hour
  const db = new Map(); let failExpire = true;
  const fetchImpl = async (url, o) => { const [cmd, k, v] = JSON.parse(o.body);
    if (cmd === "INCR") { const e = db.get(k) || { n: 0 }; e.n += 1; db.set(k, e); return { ok: true, json: async () => ({ result: e.n }) }; }
    if (cmd === "EXPIRE") { if (failExpire && k.includes(":ip:")) { failExpire = false; return { ok: false, status: 500 }; } db.get(k).ttl = v; return { ok: true, json: async () => ({ result: 1 }) }; }
    if (cmd === "SET") { if (db.has(k)) return { ok: true, json: async () => ({ result: null }) }; db.set(k, { n: 1, ttl: v }); return { ok: true, json: async () => ({ result: "OK" }) }; }
    return { ok: true, json: async () => ({ result: "OK" }) }; };
  const rs = ep.redisStore("https://kv.example", "t", fetchImpl);
  gh = fakeGitHub(); t = 1800000000000;
  await assert.rejects(ep.handle(good(), gh, { store: rs, ip: "4.4.4.4", now: t }), /store 500/);
  for (let n = 0; n < 30; n++) await ep.handle(good(), gh, { store: rs, ip: "4.4.4.4", now: t });
  assert.strictEqual((await ep.handle(good(), gh, { store: rs, ip: "4.4.4.4", now: t })).status, 429);
  const stuck = [...db.entries()].filter(([k, e]) => k.includes(":ip:") && e.ttl === undefined);
  assert.strictEqual(stuck.length, 1, "the counter that lost its expiry");
  r = await ep.handle(good(), gh, { store: rs, ip: "4.4.4.4", now: t + 3600e3 });
  assert.ok(r.status === 200 || r.status === 201, "an hour later the address reports again: " + JSON.stringify(r));

  // 5. the address is stored under a keyed hash: not the address, not its plain SHA-256, and not without the secret
  const crypto = require("crypto");
  const seen = []; const spy = { async incr(k) { seen.push(k); return 1; }, async once(k) { seen.push(k); return true; } };
  await ep.handle(good(), fakeGitHub(), { store: spy, ip: "203.0.113.7", secret: "s3cret-one" });
  const plain = crypto.createHash("sha256").update("crash-report|203.0.113.7").digest("hex").slice(0, 16);
  assert.ok(seen.some((k) => k.includes(":ip:")) && !seen.some((k) => k.includes("203.0.113.7") || k.includes(plain)), JSON.stringify(seen));
  assert.strictEqual(ep.ipKey("203.0.113.7", "s3cret-one"), ep.ipKey("203.0.113.7", "s3cret-one"));
  assert.notStrictEqual(ep.ipKey("203.0.113.7", "s3cret-one"), ep.ipKey("203.0.113.7", "s3cret-two"));
  assert.notStrictEqual(ep.ipKey("203.0.113.7", "s3cret-one"), ep.ipKey("203.0.113.8", "s3cret-one"));
  assert.notStrictEqual(ep.ipKey("203.0.113.7"), plain);                        // no secret passed: still keyed
  assert.strictEqual(ep.secretFrom({ CRASH_REPORT_SALT: "salt", CRASH_REPORT_TOKEN: "tok" }), "salt");
  const derived = ep.secretFrom({ CRASH_REPORT_TOKEN: "ghp_fake_token_for_tests" });
  assert.ok(derived && derived.length === 32 && !derived.toString("hex").includes(Buffer.from("ghp_fake").toString("hex")));
  assert.strictEqual(ep.secretFrom({}), null);

  // 6. the HTTP wrapper: only JSON sent by a program. A web page's cross-site POST (text/plain, or with an Origin) is
  //    refused before anything is counted; without the token the endpoint stays off (#229)
  const res = () => ({ code: 0, headers: {}, status(c) { this.code = c; return this; }, json(j) { this.j = j; return this; }, setHeader(k, v) { this.headers[k] = v; } });
  process.env.CRASH_REPORT_TOKEN = "ghp_fake_token_for_tests";
  let out = res(); await ep({ method: "POST", headers: { "content-type": "text/plain" }, body: JSON.stringify({ ...good(), error: "lower" }) }, out);
  assert.strictEqual(out.code, 415, JSON.stringify(out.j));
  out = res(); await ep({ method: "POST", headers: { "content-type": "application/json", origin: "https://evil.example" }, body: { ...good(), error: "lower" } }, out);
  assert.strictEqual(out.code, 403, JSON.stringify(out.j));
  out = res(); await ep({ method: "POST", headers: {}, body: { ...good(), error: "lower" } }, out);
  assert.strictEqual(out.code, 415);
  out = res(); await ep({ method: "POST", headers: { "content-type": "application/json; charset=utf-8", "user-agent": "ecadlib-crash-report" }, body: { ...good(), error: "lower" } }, out);
  assert.strictEqual(out.code, 400, "what ecadlib sends reaches the checks: " + JSON.stringify(out.j));
  delete process.env.CRASH_REPORT_TOKEN;
  out = res(); await ep({ method: "POST", headers: { "content-type": "application/json" }, body: good() }, out);
  assert.strictEqual(out.code, 503);
  console.log("security 11-23 endpoint: all checks passed");
})().catch((e) => { console.error(e); process.exit(1); });
