"""Integration: the same BOM through every export route, through the MCP server and the CLI, and odd BOM files through
the whole flow. test_e2e proves each output plugs into its tool; these prove the routes agree with each other.

    ecadlib init --yes --unofficial
    ecadlib librarian examples/sample_bom.csv --offline --cache cache   (recorded KiCad files: tests/e2e_support.py)
    ecadlib release-check plan.json --allow AO3401A                     (a family-table pinout a person signed off)
    kicad-export / kicad-dblib / dblib-sync / export-parts / altium365 push --dry-run / altium-pkg / kicad-pcm

Every route must ship exactly the parts the release check passed, no more (a held part leaking out) and no fewer.
Offline and free: every request that is not in the recorded cache fails at once at a dead proxy (e2e_support.clean_env).
Run:  python3 -m tests.run_all test_integration_flows"""
import atexit, base64, csv, json, shutil, sqlite3, tempfile, zipfile
from contextlib import closing
from pathlib import Path

from tests import e2e_support as E

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "examples" / "sample_bom.csv"
SIGNED_OFF = "AO3401A"           # held for a person (pinout from the family table) and released by --allow
_WS = {}


def _tmp(prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, d, True)
    return d


def _ws():
    """One workspace: sample_bom.csv through the librarian and the release check, then every export route (once per
    test process; the tests below only read it)."""
    if _WS:
        return _WS
    w = _tmp("ecadlib-flows-")
    E.seed_cache(w / "cache")
    shutil.copy(SAMPLE, w / "bom.csv")
    env = E.clean_env()
    run = lambda *a: E.ecadlib(w, *a, env=env, check=False)
    steps = {"init": run("init", "--yes", "--unofficial"),
             "librarian": run("librarian", "bom.csv", "-o", "lib/b", "--offline", "--cache", "cache"),
             "release-check": run("release-check", "lib/b/plan.json", "--allow", SIGNED_OFF)}
    plan = "lib/b/plan.json"
    steps.update({
        "kicad-export": run("kicad-export", plan, "-o", "kicad", "--name", "b", "--offline", "--cache", "cache"),
        "kicad-dblib": run("kicad-dblib", plan, "-o", "kicad", "--name", "b"),
        "dblib-sync": run("dblib-sync", plan, "--to", "altium", "--name", "b"),
        "export-parts": run("export-parts", plan, "--format", "json", "-o", "parts.json"),
        "a365-dry": run("altium365", "push", plan, "--workspace", "https://example.365.altium.com/", "--dry-run",
                        "-o", "a365.json"),
        "altium-pkg": run("altium-pkg", plan, "--out", "pkg"),
        "kicad-pcm": run("kicad-pcm", plan, "--out", "pcm"),
    })
    _WS.update(work=w, steps=steps, plan=json.loads((w / plan).read_text(encoding="utf-8")))
    return _WS


def _rows(f):
    with open(f, encoding="utf-8-sig", newline="") as fh:      # closed: Windows can't delete an open file
        return list(csv.DictReader(fh))


def _ready():
    return {i["name"] for i in _ws()["plan"] if i["status"] == "ready"}


def test_the_flow_runs_offline_without_a_crash():
    s = _ws()["steps"]
    want = {"librarian": (0, 2), "release-check": (0, 2)}       # 2 = parts held, which this BOM has offline
    for name, p in s.items():
        assert p.returncode in want.get(name, (0,)), (name, p.returncode, p.stdout[-2000:], p.stderr[-2000:])
        assert "Traceback" not in p.stderr + p.stdout, (name, p.stderr[-3000:])
    ready, items = _ready(), {i["name"]: i for i in _ws()["plan"]}
    # the recorded KiCad library and the built-in standard parts give these; EasyEDA is not in the cache, so its
    # parts are held, and the family-table part is out only because a person signed it off
    assert {"STM32F103C8T6", "RES-0603-10k", "CAP-0402-100nF", SIGNED_OFF} <= ready, sorted(ready)
    assert items[SIGNED_OFF].get("allow"), items[SIGNED_OFF]
    assert len(ready) < len(items), "nothing is held: the release check did not run"


def test_every_export_route_ships_exactly_the_released_parts():
    w, ready, plan = _ws()["work"], _ready(), {i["name"]: i for i in _ws()["plan"]}
    routes = {}
    rep = json.loads((w / "kicad" / "kicad_export.json").read_text(encoding="utf-8"))
    routes["kicad-export"] = {p["part"] for p in rep["parts"]}
    with closing(sqlite3.connect(w / "kicad" / "b.sqlite")) as db:
        tables = [t[0] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        routes["kicad-dblib"] = {r[0] for t in tables for r in db.execute(f'SELECT "Part ID" FROM "{t}"')}
    routes["dblib-sync"] = {r["Part Number"] for r in _rows(w / "altium" / "b.csv")}
    routes["export-parts"] = {p["Part ID"] for p in json.loads((w / "parts.json").read_text(encoding="utf-8"))["parts"]}
    dry = json.loads((w / "a365.json").read_text(encoding="utf-8"))
    routes["a365 push"] = {r["name"] for r in (dry["results"] if isinstance(dry, dict) else dry) if r["action"] == "release"}
    routes["altium-pkg"] = {r["Part Number"] for r in _rows(w / "pkg" / "ecadlib.csv")}
    for route, parts in routes.items():
        assert parts == ready, (route, "held parts shipped:", sorted(parts - ready), "released parts missing:",
                                sorted(ready - parts))
    # the library files that go out are the released parts' own: no symbol or footprint of a held part
    want_fp = {plan[n]["footprint"]["ref"] for n in ready}
    pkg = json.loads((w / "pkg" / "altium_pkg.json").read_text(encoding="utf-8"))
    docs = {Path(d.replace("\\", "/")).stem for d in pkg["documents"]}
    assert docs == want_fp | {Path(plan[n]["symbol"]["file"]).stem for n in ready}, (sorted(docs), sorted(want_fp))
    (z,) = list((w / "pcm").glob("*.zip"))
    with zipfile.ZipFile(z) as zf:
        pcm_fps = {Path(n).stem for n in zf.namelist() if n.endswith(".kicad_mod")}
    assert pcm_fps == want_fp, (sorted(pcm_fps), sorted(want_fp))
    assert {Path(f).stem for f in (w / "kicad" / "b.pretty").glob("*.kicad_mod")} == want_fp


def test_held_parts_say_why_on_every_route():
    """A held part is left out by name, with its reason, by the routes that report what they skipped."""
    w, ready = _ws()["work"], _ready()
    held = {i["name"] for i in _ws()["plan"] if i["status"] == "held"}
    rep = json.loads((w / "kicad" / "kicad_export.json").read_text(encoding="utf-8"))
    skipped = {s["part"]: s for s in rep["skipped"]}
    assert set(skipped) == held, (sorted(skipped), sorted(held))
    dry = json.loads((w / "a365.json").read_text(encoding="utf-8"))
    acts = {r["name"]: r["action"] for r in (dry["results"] if isinstance(dry, dict) else dry)}
    assert {n for n, a in acts.items() if a != "release"} == held and set(acts) == held | ready, acts
    pkg = json.loads((w / "pkg" / "altium_pkg.json").read_text(encoding="utf-8"))
    assert pkg["sync"] == f"{len(ready)} added, {len(held)} held", pkg["sync"]


def test_mcp_server_builds_the_same_library_as_the_cli():
    """The MCP tools on the same BOM (uploaded, as a remote client does) agree with the CLI part by part: the
    librarian's statuses, what the release check holds and what the Altium and KiCad exports write."""
    cli = {i["name"]: i["status"] for i in _ws()["plan"]}
    root = _tmp("ecadlib-flows-mcp-")
    (root / "ws").mkdir()
    E.seed_cache(root / "ws" / "http_cache")
    (root / "ws" / "ecadlib.toml").write_text("[providers]\nunofficial = true\n")
    m = E.MCPClient(root, "--workspace", "ws", "--offline")
    try:
        m.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "ecadlib-flows", "version": "1"}})
        m.send("notifications/initialized", notify=True)
        text = _ok_text
        bom = json.loads(text(m.call("upload_bom", filename="bom.csv",
                                     content_base64=base64.b64encode(SAMPLE.read_bytes()).decode())))["bom"]
        lib = json.loads(text(m.call("librarian_run", bom=bom)))
        rc = json.loads(text(m.call("release_check", bom=bom, allow=[SIGNED_OFF], user="Flows Tester")))
        mcp = {x["part"]: x["status"] for x in rc["review"]}
        assert {x["part"] for x in lib["review"]} == set(cli), (sorted(x["part"] for x in lib["review"]), sorted(cli))
        assert mcp == cli, {k: (mcp.get(k), cli.get(k)) for k in set(mcp) | set(cli) if mcp.get(k) != cli.get(k)}
        ready = {n for n, s in cli.items() if s == "ready"}
        alt = json.loads(text(m.call("export_altium_dblib", bom=bom)))
        assert alt["written"] == len(ready) and set(alt["held"]) == set(cli) - ready, alt
        kd = json.loads(text(m.call("kicad_dblib", bom=bom)))
        assert sum(kd["tables"].values()) == len(ready), kd["tables"]
        assert {s["part"] for s in kd["skipped"]} == set(cli) - ready, kd["skipped"]
        push = json.loads(text(m.call("altium365_push", bom=bom, workspace="https://example.365.altium.com/", dry_run=True)))
        assert push["dry_run"] is True
        assert {r["name"] for r in push["results"] if r["action"] == "release"} == ready, push["results"]
        parts = json.loads(text(m.call("export_parts", bom=bom, format="json")))
        assert parts["parts"] == len(ready), parts
    finally:
        rc = m.close()
    assert rc == 0, rc


def _ok_text(r):
    """The text of an MCP tool result that is no error and no crash."""
    assert not r.get("isError"), r["content"][0]["text"][:2000]
    assert "Traceback" not in r["content"][0]["text"], r["content"][0]["text"][:3000]
    return r["content"][0]["text"]


def test_odd_bom_files_run_the_whole_flow():
    """The BOM files people send (semicolons + UTF-8 BOM, tab + cp1252, a workbook with title rows, house column names
    with their own rules) go through the librarian, the release check and both exports with no crash, and each
    export ships the parts the check passed."""
    fx = ROOT / "tests" / "fixtures" / "bom_inputs"
    env = E.clean_env()
    for bom in ("semicolon_utf8bom.csv", "tab_cp1252.txt", "workbook_title_rows.xlsx", "house_columns.csv"):
        w = _tmp("ecadlib-flows-bom-")
        E.seed_cache(w / "cache")
        shutil.copy(fx / bom, w / bom)
        if bom == "house_columns.csv":
            shutil.copy(fx / "house_columns.toml", w / "ecadlib.toml")
        steps = [E.ecadlib(w, *a, env=env, check=False) for a in (
            ("librarian", bom, "-o", "lib", "--offline", "--cache", "cache"),
            ("release-check", "lib/plan.json"),
            ("kicad-export", "lib/plan.json", "-o", "kicad", "--name", "x", "--offline", "--cache", "cache"),
            ("dblib-sync", "lib/plan.json", "--to", "altium", "--name", "x"))]
        for p in steps:
            assert p.returncode in (0, 2) and "Traceback" not in p.stderr, (bom, p.args, p.returncode, p.stderr[-2000:])
        plan = json.loads((w / "lib" / "plan.json").read_text(encoding="utf-8"))
        assert plan, bom
        ready = {i["name"] for i in plan if i["status"] == "ready"}
        rep = json.loads((w / "kicad" / "kicad_export.json").read_text(encoding="utf-8"))
        assert {p["part"] for p in rep["parts"]} == ready, (bom, rep["parts"], ready)
        rows = _rows(w / "altium" / "x.csv") if ready else []
        assert {r["Part Number"] for r in rows} == ready, (bom, rows, ready)


def test_dedupe_merge_keeps_each_kind_of_part_on_its_own_footprint():
    """The sensor-node BOM's LEDs, ferrite bead and inductors are built offline from the standard packages: LED_0603
    and L_0603 have the same pads, but the LED's has its cathode mark and the parts are of different kinds, so
    `dedupe --merge` leaves both (was: the ferrite bead was moved onto LED_0603, its polarity mark and LED 3D body,
    and released). Every export then still ships each part on its own footprint."""
    w = _tmp("ecadlib-flows-dedupe-")
    E.seed_cache(w / "cache")
    shutil.copy(ROOT / "examples" / "sensor_node_bom.csv", w / "bom.csv")
    # the BOM has no Datasheet column: without this every part is held for it and nothing would be released
    (w / "ecadlib.toml").write_text('[parameters]\nrequired = ["Manufacturer", "Manufacturer Part Number", "Description"]\n')
    env = E.clean_env()
    run = lambda *a: E.ecadlib(w, *a, env=env, check=False)
    assert run("librarian", "bom.csv", "-o", "lib", "--offline", "--cache", "cache").returncode in (0, 2)
    before = {i["name"]: i["footprint"].get("ref") for i in json.loads((w / "lib" / "plan.json").read_text(encoding="utf-8"))}
    assert before["IND-0603-220R@100MHz"] == "L_0603_1608Metric" and before["150060GS75000"] == "LED_0603_1608Metric", before
    d = run("dedupe", "lib/plan.json", "--merge")
    assert d.returncode in (0, 1) and "Traceback" not in d.stderr, d.stderr[-2000:]
    plan = {i["name"]: i for i in json.loads((w / "lib" / "plan.json").read_text(encoding="utf-8"))}
    assert {n: i["footprint"].get("ref") for n, i in plan.items()} == before, d.stdout[-2000:]
    assert run("release-check", "lib/plan.json").returncode in (0, 2)
    assert run("kicad-export", "lib/plan.json", "-o", "kicad", "--name", "s", "--offline", "--cache", "cache").returncode == 0
    with open(w / "kicad" / "s.kicad_sym", encoding="utf-8") as fh:
        sym = fh.read()
    assert '"Footprint" "s:L_0603_1608Metric"' in sym and '"Footprint" "s:LED_0603_1608Metric"' in sym


def test_a_plan_or_an_all_held_plan_gets_a_clear_message():
    """A plan.json given where a BOM goes (`altium365 plan`, `triage`), and `altium-pkg` on a plan whose parts are all
    held: one line saying what is wrong and what to run, exit 2 (was: "internal error ... please report it", and
    "run the librarian first" after the librarian had run)."""
    w = _tmp("ecadlib-flows-msg-")
    E.seed_cache(w / "cache")
    shutil.copy(ROOT / "examples" / "sensor_node_bom.csv", w / "bom.csv")
    env = E.clean_env()
    assert E.ecadlib(w, "librarian", "bom.csv", "-o", "lib", "--offline", "--cache", "cache", env=env,
                     check=False).returncode == 2
    plan = json.loads((w / "lib" / "plan.json").read_text(encoding="utf-8"))
    assert plan and all(i["status"] == "held" for i in plan), "the default rules hold every part of a BOM with no datasheets"
    for args in (("altium365", "plan", "lib/plan.json"), ("triage", "lib/plan.json", "--offline")):
        p = E.ecadlib(w, *args, env=env, check=False)
        out = p.stdout + p.stderr
        assert p.returncode == 2 and "this is a library plan (plan.json), not a BOM" in out, (args, p.returncode, out)
        assert "internal error" not in out and "Traceback" not in out, out
    p = E.ecadlib(w, "altium-pkg", "lib/plan.json", "--out", "pkg", env=env, check=False)
    assert p.returncode == 2 and f"all {len(plan)} part(s) are held by the release check" in p.stderr, p.stderr
    assert "ecadlib review lib/plan.json" in p.stderr and "run the librarian" not in p.stderr, p.stderr
