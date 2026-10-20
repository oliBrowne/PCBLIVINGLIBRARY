"""End-to-end "golden path": the real `ecadlib` CLI in subprocesses, the way a user runs it, from an empty folder.
Run:  python3 -m tests.run_all test_e2e      (about a minute; the KiCad checks skip without kicad-cli / pcbnew)

    ecadlib init --yes                                   empty folder -> ecadlib.toml
    ecadlib project altium_project/Board.PrjPcb -o ...   BOM from an Altium project (.PrjPcb + 2 .SchDoc)
    ecadlib librarian <BOM> -o lib/<board> [--offline]   x3: a KiCad 7 .kicad_sch project, the Altium BOM, an .xlsx
                                                         (the .xlsx online: Claude reads its datasheet part)
    ecadlib release-check lib/parts/plan.json --allow DSPART16 --allow LM358DR --allow EEPART1    (after review)
    ecadlib release-check lib/kicad_board/plan.json --allow LM358DR
    ecadlib kicad-export / kicad-dblib / export-parts / dblib-sync / altium365 push (--dry-run and against FakeNexar)

and then proves each output plugs into its tool: KiCad loads every symbol and footprint through the library tables
the export wrote and a board with every part passes DRC (needs KiCad), the .kicad_dbl's SQLite maps to real symbols
and footprints, every .PcbLib / .SchLib reads back with pyaltiumlib and our reader and holds every name the DbLib, the
parts table and the Altium 365 release reference. Also: `ecadlib mcp` over stdio, a second run of the whole path (nothing changes) and
a librarian killed mid-run then re-run (recovers).

Sources are offline and recorded (tests/e2e_support.py): real KiCad library files from gitlab.com, a SYNTHETIC
EasyEDA payload, SYNTHETIC Claude answers from a local fake API (ANTHROPIC_BASE_URL); every other request goes to a
dead proxy. Nothing here was opened in Altium Designer or Altium 365: the Altium checks are file-level only."""
import atexit, contextlib, csv, json, re, shutil, sqlite3, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, sexpr
from tests import e2e_support as E

_GP = {}
# held for a person to look (datasheet reader, symbol matched with an ordering suffix, EasyEDA conversion): the golden
# path's reviewer checked them and released them anyway
REVIEWED = ("DSPART16", "LM358DR", "EEPART1")
ALLOW = [x for n in REVIEWED for x in ("--allow", n)]


def _tmp(prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, d, True)
    return d


def golden_path(work, fa, nx, tls, first=True):
    """The whole path in `work` (an empty folder on the first run). -> {step: CompletedProcess}."""
    work.mkdir(parents=True, exist_ok=True)
    env = E.clean_env(ANTHROPIC_API_KEY="sk-e2e-test", ANTHROPIC_BASE_URL=fa.url)
    out = {}
    if first:
        assert not any(work.iterdir()), "the golden path starts in an empty folder"
    rules = (work / "ecadlib.toml").read_text() if not first else None
    out["init"] = E.ecadlib(work, "init", "--yes", "--unofficial", env=env, check=first)
    assert (work / "ecadlib.toml").is_file()
    if not first:                  # init again: the rules file is never replaced without --force (a .proposed is written)
        assert out["init"].returncode in (0, 1) and (work / "ecadlib.toml").read_text() == rules, out["init"].stdout[-2000:]
    if first:                                                 # the user's own files arrive
        shutil.copytree(E.FIX / "kicad_sch", work / "kicad_project")
        from tests.test_altium_project import board
        board(work / "altium_project")
        E.write_parts_xlsx(work / "parts.xlsx")
        E.seed_cache(work / "cache")                          # an earlier online run, recorded
        E.seed_datasheet(work / "cache", "DSPART16")
    out["project"] = E.ecadlib(work, "project", "altium_project/Board.PrjPcb", "-o", "boms/altium_bom.csv", env=env)
    for name, bom in (("kicad_board", "kicad_project/board.kicad_sch"), ("altium_board", "boms/altium_bom.csv"),
                      ("parts", "parts.xlsx")):
        # --offline sends nothing to the model either (UX-15): the BOM with a datasheet part runs without it, the
        # part sources answered from the recorded cache (anything else fails at once at clean_env's dead proxy)
        offline = () if name == "parts" else ("--offline",)
        p = E.ecadlib(work, "librarian", bom, "-o", f"lib/{name}", *offline, "--cache", "cache", env=env, check=False)
        assert p.returncode in (0, 2), (name, p.returncode, p.stdout[-3000:], p.stderr[-3000:])  # 2 = parts held
        assert re.search(r"\n\d+ parts?: (\d+ ready|\d+ held)", p.stdout) and "written:" in p.stdout, p.stdout[-2000:]
        out[f"librarian:{name}"] = p
    out["release-check"] = E.ecadlib(work, "release-check", "lib/parts/plan.json", *ALLOW, env=env, check=False)
    out["release-check:kicad_board"] = E.ecadlib(work, "release-check", "lib/kicad_board/plan.json", "--allow", "LM358DR",
                                                 env=env, check=False)
    for name in ("kicad_board", "altium_board", "parts"):
        lib = "e2e" if name == "parts" else f"e2e_{name}"
        out[f"kicad-export:{name}"] = E.ecadlib(work, "kicad-export", f"lib/{name}/plan.json", "-o", "kicad", "--name", lib,
                                                "--offline", "--cache", "cache", env=env)
    out["kicad-dblib"] = E.ecadlib(work, "kicad-dblib", "lib/parts/plan.json", "-o", "kicad", "--name", "e2e", env=env)
    out["export-parts"] = E.ecadlib(work, "export-parts", "lib/parts/plan.json", "--format", "csv", "-o", "parts_table.csv", env=env)
    out["export-parts-json"] = E.ecadlib(work, "export-parts", "lib/parts/plan.json", "--format", "json", "-o", "parts_table.json",
                                         env=env)
    out["dblib-sync"] = E.ecadlib(work, "dblib-sync", "lib/parts/plan.json", "--to", "altium", "--name", "e2e", env=env)
    out["a365-dry"] = E.ecadlib(work, "altium365", "push", "lib/parts/plan.json", "--workspace", nx.workspace, "--dry-run",
                                "-o", "a365/push_dry_run.json", env=env)
    nenv = dict(env, NEXAR_TOKEN="nexar-e2e", NEXAR_API_URL=nx.url + "/graphql", NEXAR_FILES_URL=nx.url,
                REQUESTS_CA_BUNDLE=str(tls[0]))
    out["a365-push"] = E.ecadlib(work, "altium365", "push", "lib/parts/plan.json", "--workspace", nx.workspace,
                                 "-o", "a365/push_report.json", env=nenv)
    return out


def gp():
    """The golden path, run once per test process (every test below looks at its outputs)."""
    if not _GP:
        root = _tmp("ecadlib-e2e-")
        tls = E.self_signed(root)
        assert tls, "openssl is needed for the local HTTPS fakes"
        with E.FakeAnthropic() as fa, E.FakeNexar(tls) as nx:
            steps = golden_path(root / "work", fa, nx, tls)
            _GP.update(root=root, work=root / "work", steps=steps, tls=tls, claude=list(fa.kinds),
                       uploads=dict(nx.uploads), releases=list(nx.releases), folders=list(nx.folders))
    return _GP


def plan(name="parts"):
    return {i["name"]: i for i in json.loads((gp()["work"] / "lib" / name / "plan.json").read_text())}


# ---------------------------------------------------------------- 1. the path itself
def test_golden_path_runs_from_an_empty_folder():
    g = gp()
    w, s = g["work"], g["steps"]
    assert "wrote altium_bom.csv" in s["project"].stdout or "altium_bom.csv" in s["project"].stdout, s["project"].stdout
    alt = list(csv.DictReader(open(w / "boms" / "altium_bom.csv", encoding="utf-8-sig")))
    assert {r["Manufacturer Part Number"] for r in alt} >= {"LM358DR", "TPS7A0233PDBVR", "RC0603FR-0710KL"}, alt
    p = plan()
    st = {n: (i["status"], str((i.get("source") or {}).get("symbol") or "").split(":")[0],
              str((i.get("source") or {}).get("footprint") or "").split(":")[0]) for n, i in p.items()}
    # every kind of source took part: the official KiCad library (exact name, x wildcard, ordering suffix), EasyEDA,
    # the datasheet reader (fake Claude), and the release check held what nothing could build
    assert st["STM32F103C8T6"] == ("ready", "kicad", "kicad"), st
    assert st["LM358DR"] == ("ready", "kicad", "kicad") and p["LM358DR"].get("allow"), st     # suffix match, reviewed
    assert st["RES-0603-10k"] == ("ready", "kicad", "kicad") and st["CAP-0603-100nF"] == ("ready", "kicad", "kicad"), st
    assert st["EEPART1"] == ("ready", "easyeda", "easyeda") and p["EEPART1"].get("allow"), st  # converted, reviewed
    assert st["DSPART16"][0] == "ready" and p["DSPART16"].get("allow"), st        # read by Claude, released after review
    for n, doubt in (("LM358DR", "ordering suffix"), ("EEPART1", "EasyEDA C2002 conversion")):
        assert any(doubt in f for f in p[n]["allow"]["failures"]), p[n]["allow"]       # what the reviewer signed off
    assert st["TPS7A0233PDBVR"][0] == "held" and st["NOWHERE1"][0] == "held", st
    assert ("datasheet", "DSPART16") in g["claude"], g["claude"]
    tps = " ".join(p["TPS7A0233PDBVR"].get("hold_reasons", []))
    assert "no symbol" in tps, tps
    # the KiCad schematic project: hierarchical sheets, the DNP part kept out of the build
    k = plan("kicad_board")
    assert {"LM358DR", "RES-0603-10k", "RES-0402-4k7", "CAP-0603-1uF"} <= set(k), sorted(k)
    assert k["LM358DR"]["status"] == "ready" and k["LM358DR"].get("allow"), k["LM358DR"]    # released after review
    assert all(" Traceback" not in x.stderr for x in s.values()), [n for n, x in s.items() if "Traceback" in x.stderr]


# ---------------------------------------------------------------- 2a. KiCad
def _kicad_dir(tmp):
    """The exported KiCad folder as KiCad here can read it (KiCad 7: a KiCad 7 copy of the KiCad 8 export)."""
    from tests import test_kicad_real as KR
    src = gp()["work"] / "kicad"
    if (KR.kicad_major() or 8) >= 8:
        return Path(shutil.copytree(src, tmp / "kicad"))
    return E.kicad7_copy(src, tmp / "kicad")


def _table_libs(table):
    t = sexpr.parse(Path(table).read_text())
    return {sexpr.first(l, "name")[1]: (sexpr.first(l, "type")[1], sexpr.first(l, "uri")[1]) for l in sexpr.find(t, "lib")}


def test_kicad_tables_and_database_library_map_to_real_files():
    """Without KiCad: the tables name every exported library by ${KIPRJMOD}, and every row of the .kicad_dbl's SQLite
    points at a symbol that is in the .kicad_sym and a footprint that is in the .pretty."""
    w = gp()["work"] / "kicad"
    fp = _table_libs(w / "fp-lib-table")
    sym = _table_libs(w / "sym-lib-table")
    for lib in ("e2e", "e2e_kicad_board", "e2e_altium_board"):
        assert fp[lib] == ("KiCad", f"${{KIPRJMOD}}/{lib}.pretty"), fp
        assert sym[lib] == ("KiCad", f"${{KIPRJMOD}}/{lib}.kicad_sym"), sym
    assert sym["e2e-db"] == ("Database", "${KIPRJMOD}/e2e.kicad_dbl"), sym
    dbl = json.loads((w / "e2e.kicad_dbl").read_text())
    assert "${CWD}/e2e.sqlite" in dbl["source"]["connection_string"], dbl["source"]
    syms = {s[1] for s in sexpr.find(sexpr.parse((w / "e2e.kicad_sym").read_text()), "symbol") if ":" not in s[1]}
    fps = {f.stem for f in (w / "e2e.pretty").glob("*.kicad_mod")}
    rows = []
    with contextlib.closing(sqlite3.connect(w / "e2e.sqlite")) as db:
        for lib in dbl["libraries"]:
            q = f'SELECT * FROM "{lib["table"]}"'
            cur = db.execute(q)
            cols = [c[0] for c in cur.description]
            for r in cur.fetchall():
                rows.append(dict(zip(cols, r)))
            for f in lib["fields"]:
                assert f["column"] in cols, (lib["table"], f, cols)
            assert lib["key"] in cols and lib["symbols"] in cols and lib["footprints"] in cols, lib
    ready = {n for n, i in plan().items() if i["status"] == "ready"}
    assert {r["Part ID"] for r in rows} == ready, ({r["Part ID"] for r in rows}, ready)
    for r in rows:
        assert r["Symbol"].startswith("e2e:") and r["Symbol"][4:] in syms, (r, sorted(syms))
        assert r["Footprint"].startswith("e2e:") and r["Footprint"][4:] in fps, (r, sorted(fps))
        assert r["MPN"] and r["Manufacturer"] and r["Datasheet"], r


def test_kicad_loads_every_symbol_and_footprint_and_drc_passes():
    from tests import test_kicad_real as KR
    if not KR.kicad_cli():
        return KR._skip("kicad-cli")
    tmp = _tmp("ecadlib-e2e-kicad-")
    k = _kicad_dir(tmp)
    plans = [plan(n) for n in ("parts", "kicad_board", "altium_board")]
    n_sym = n_fp = 0
    for lib, pl in zip(("e2e", "e2e_kicad_board", "e2e_altium_board"), plans):
        r = KR.cli("sym", "export", "svg", k / f"{lib}.kicad_sym", "-o", tmp / f"svg_{lib}")
        assert r.returncode == 0, (lib, r.stdout[-1500:], r.stderr[-1500:])
        names = {s[1] for s in sexpr.find(sexpr.parse((k / f"{lib}.kicad_sym").read_text()), "symbol") if ":" not in s[1]}
        drawn = {re.sub(r"(_\d+)?\.svg$", "", f.name) for f in (tmp / f"svg_{lib}").glob("*.svg")}
        assert {n.lower() for n in names} == drawn, (names, drawn)
        assert {n for n, i in pl.items() if i["status"] == "ready"} <= names, (lib, names)
        n_sym += len(names)
        r = KR.cli("fp", "upgrade", "--force", k / f"{lib}.pretty", "-o", tmp / f"up_{lib}.pretty")
        assert r.returncode == 0, (lib, r.stdout[-1500:], r.stderr[-1500:])
        r = KR.cli("fp", "export", "svg", k / f"{lib}.pretty", "-o", tmp / f"fpsvg_{lib}")
        assert r.returncode == 0, (lib, r.stdout[-1500:], r.stderr[-1500:])
        fps = sorted(f.stem for f in (k / f"{lib}.pretty").glob("*.kicad_mod"))
        assert sorted(f.stem for f in (tmp / f"fpsvg_{lib}").glob("*.svg")) == fps
        assert sorted(f.stem for f in (tmp / f"up_{lib}.pretty").glob("*.kicad_mod")) == fps
        n_fp += len(fps)
    print(f"      KiCad {KR.kicad_major()}: {n_sym} symbols and {n_fp} footprints loaded by kicad-cli")
    if not KR.pcbnew_python():
        return KR._skip("pcbnew")
    # a board with every part of the .xlsx BOM, placed through the project's fp-lib-table by the footprint the
    # database library gives it; KiCad's DRC library check finds each one in the table, unchanged
    with contextlib.closing(sqlite3.connect(k / "e2e.sqlite")) as db:
        refs = sorted({r[0] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                       for r in db.execute(f'SELECT "Footprint" FROM "{t[0]}"')})
    names = [r.split(":", 1)[1] for r in refs]
    res = KR.pcbnew_job("libcheck", dir=str(k), lib="e2e", fps=names, bogus=True)
    detail = res.pop("_detail")
    assert res == {"not_a_lib:X": {"lib_footprint_issues": 1}, "e2e:NOT_IN_LIB": {"lib_footprint_issues": 1}}, (res, detail)
    # DRC (clearances, courtyards, pads; one net per pad number) on a board of every exported footprint
    for lib in ("e2e", "e2e_kicad_board", "e2e_altium_board"):
        v = KR.pcbnew_job("drc", lib=str(k / f"{lib}.pretty"), board=str(tmp / f"drc_{lib}.kicad_pcb"))
        assert v == {}, (lib, v)
    print(f"      pcbnew: {len(names)} parts placed via fp-lib-table, DRC clean on 3 boards")


# ---------------------------------------------------------------- 2b. Altium (files only: nothing opened in Altium)
def _pyaltiumlib():
    try:
        import logging
        logging.getLogger("pyaltiumlib").setLevel(logging.CRITICAL)
        import pyaltiumlib
        return pyaltiumlib
    except ImportError:
        print("      (pyaltiumlib not installed: its half skipped)")
        return None


def test_altium_libraries_read_back_and_hold_every_referenced_name():
    w = gp()["work"]
    pal = _pyaltiumlib()
    pcb, sch = {}, {}
    for f in sorted((w / "altium").rglob("*.PcbLib")) + sorted((w / "lib").rglob("*.PcbLib")):
        mine = {fp["name"] for fp in AL.read_pcblib(f)}
        assert mine, f
        if pal:
            theirs = {p.Name for p in pal.read(str(f)).Parts}
            assert theirs == mine, (f, theirs, mine)
        pcb[f] = mine
    for f in sorted((w / "altium").rglob("*.SchLib")) + sorted((w / "lib").rglob("*.SchLib")):
        mine = {s["name"] for s in AL.read_schlib(f)[1]}
        assert mine, f
        if pal:
            parts = pal.read(str(f)).Parts
            assert {p.Name for p in parts} == mine, (f, mine)
            assert all(any(type(r).__name__ == "SchPin" for r in p.Records) for p in parts), f
        sch[f] = mine
    assert len(pcb) >= 6 and len(sch) >= 6, (len(pcb), len(sch))
    # the .DbLib's table (CSV/XLSX next to it) -> each Library Ref / Footprint Ref is in the file it names
    rows = list(csv.DictReader(open(w / "altium" / "e2e.csv", encoding="utf-8-sig")))
    dblib = (w / "altium" / "e2e.DbLib").read_text(encoding="utf-8", errors="replace")
    assert rows and "e2e" in dblib
    for r in rows:
        sp, fpth = w / "altium" / r["Library Path"].replace("\\", "/"), w / "altium" / r["Footprint Path"].replace("\\", "/")
        assert r["Library Ref"] in sch[sp], (r["Library Ref"], sp)
        assert r["Footprint Ref"] in pcb[fpth], (r["Footprint Ref"], fpth)
    assert {r["Part Number"] if "Part Number" in r else r[next(iter(r))] for r in rows}
    # the parts table (PLM / ERP): its Altium refs and files agree with the libraries
    parts = list(csv.DictReader(open(w / "parts_table.csv", encoding="utf-8-sig")))
    assert {p["Part ID"] for p in parts} == {n for n, i in plan().items() if i["status"] == "ready"}
    for p in parts:
        lib_dir = w / "lib" / "parts"
        assert p["Symbol Ref"] in sch[lib_dir / p["Symbol File"]], p
        assert p["Footprint Ref"] in pcb[lib_dir / p["Footprint File"]], p
        assert p["KiCad Symbol"] and p["KiCad Footprint"] and p["Manufacturer Part Number"], p
    js = json.loads((w / "parts_table.json").read_text())
    assert js["format"] == "ecadlib-parts" and [x["Part ID"] for x in js["parts"]] == [p["Part ID"] for p in parts]
    print(f"      {len(pcb)} PcbLib + {len(sch)} SchLib read back ({'pyaltiumlib and ' if pal else ''}ecadlib); "
          f"{len(rows)} DbLib rows and {len(parts)} parts-table rows resolve")


def test_altium365_release_package_is_consistent():
    """What `altium365 push` sent (to the local FakeNexar): each release names files that were uploaded in the same
    run, those files are our libraries byte for byte and hold the item names, and every part carries its parameters."""
    g = gp()
    w, rel, up = g["work"], g["releases"], g["uploads"]
    p = plan()
    ready = {n for n, i in p.items() if i["status"] == "ready"}
    assert {r["componentItemName"] for r in rel} == ready, ({r["componentItemName"] for r in rel}, ready)
    dry = json.loads((w / "a365" / "push_dry_run.json").read_text())
    rep = json.loads((w / "a365" / "push_report.json").read_text())
    acts = lambda d: {r["name"]: r["action"] for r in (d["results"] if isinstance(d, dict) else d)}
    assert acts(dry) == acts(rep), (acts(dry), acts(rep))
    assert {n for n, a in acts(rep).items() if a == "release"} == ready, acts(rep)
    for r in rel:
        it = p[r["componentItemName"]]
        par = {x["name"]: x["value"] for x in r["parameters"]}
        assert par.get("Manufacturer Part Number") and par.get("Manufacturer"), (r["componentItemName"], par)
        assert par.get("ComponentLink2URL", "").startswith("http"), (r["componentItemName"], par)
        # one folder name under the Components folder (by id): a "/" in componentReleaseFolder is refused by Altium 365
        top = next(f["id"] for f in g["folders"] if f["name"] == "Components" and not f["parent"])
        assert "/" not in r["componentReleaseFolder"] and r["componentParentFolderId"] == top, r
        for kind, ext, reader in (("symbol", "SchLib", lambda f: AL.read_schlib(f)[1]), ("footprint", "PcbLib", AL.read_pcblib)):
            files = r[f"{kind}Files"]
            assert len(files) == 1 and files[0]["relativePath"].endswith("." + ext), files
            name = files[0]["relativePath"].split("\\")[-1]
            local = w / "lib" / "parts" / it[kind]["file"]
            assert name == local.name and up[name] == local.read_bytes(), (name, local)
            # the item is named after the model inside the file; -SYM / -FP when that name is the component's own
            item = r[f"{kind}ItemName"].removesuffix("-SYM" if kind == "symbol" else "-FP")
            assert item in {x["name"] for x in reader(local)}, (r[f"{kind}ItemName"], local)
    print(f"      Altium 365 (FakeNexar): {len(rel)} releases, {len(up)} uploaded files, all consistent")


# ---------------------------------------------------------------- 2d. MCP
def test_mcp_server_over_stdio():
    root = _tmp("ecadlib-e2e-mcp-")
    (root / "ws" / "uploads").mkdir(parents=True)
    E.seed_cache(root / "ws" / "http_cache")
    E.write_parts_xlsx(root / "ws" / "uploads" / "parts.xlsx")
    (root / "ws" / "ecadlib.toml").write_text("[providers]\nunofficial = true\n")   # the EasyEDA fixture part
    m = E.MCPClient(root, "--workspace", "ws", "--offline")
    try:
        r = m.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "ecadlib-e2e", "version": "1"}})
        assert r["result"]["serverInfo"]["name"] == "ecadlib" and r["result"]["capabilities"].get("tools") is not None, r
        m.send("notifications/initialized", notify=True)
        tools = {t["name"]: t for t in m.send("tools/list")["result"]["tools"]}
        assert {"workspace_info", "triage_bom", "librarian_run", "held_parts", "release_check"} <= set(tools), sorted(tools)
        assert all(t.get("description") and t.get("inputSchema", {}).get("type") == "object" for t in tools.values())
        info = json.loads(m.call("workspace_info")["content"][0]["text"])
        assert info["offline"] is True and Path(info["workspace"]).name == "ws", info
        res = m.call("librarian_run", bom="uploads/parts.xlsx")
        assert not res.get("isError"), res
        lib = json.loads(res["content"][0]["text"])
        status = {x["part"]: x["status"] for x in lib["review"]}
        assert status["STM32F103C8T6"] == "ready" and status["EEPART1"] == "held" and status["NOWHERE1"] == "held", status
        held = json.loads(m.call("held_parts", bom="uploads/parts.xlsx")["content"][0]["text"])
        assert {h["name"] for h in held["held"]} == {n for n, s in status.items() if s == "held"}, held
        ee = next(h for h in held["held"] if h["name"] == "EEPART1")      # built from EasyEDA, waits for a person
        assert any(r.startswith("verify: EasyEDA C2002 conversion") for r in ee["hold_reasons"]), ee
        bad = m.call("held_parts", bom="uploads/missing.csv")
        assert bad.get("isError") and "Traceback" not in bad["content"][0]["text"], bad      # a clear error, no crash
        assert (root / "ws" / "librarian" / "parts" / "plan.json").is_file()
    finally:
        rc = m.close()
    assert rc == 0, rc


# ---------------------------------------------------------------- 3. again, and after a crash
LOGS = re.compile(r"(^|/)(datasheet/api_log|timing)\.jsonl$")   # append-only logs: API requests (held parts are asked
                                                                  # again), each run's step times (ecadlib.timing)
REFRESHED = re.compile(r"^(ecadlib\.toml\.proposed|lib/[^/]+/librarian_state\.json|lib/parts/review\.(md|csv|html)"
                       # change control: the parts released by run 1's --allow (their revision 1 is recorded then) are
                       # recorded as released in library.lock.json on run 2; the usage index and the publish lines
                       r"|lib/(parts|kicad_board)/(library\.ledger\.jsonl|library\.usage\.json|library\.lock\.json"
                       r"|\.ecadlib/blobs/.*))$")


def test_second_run_changes_nothing():
    """The whole path again on the same folder, twice. Run 2 changes only: the API log, the librarian's per-run state
    (which parts it skipped), review.md/.csv (written before the --allow on run 1, so now up to date), init's
    proposal (ecadlib.toml itself is kept) and the ledger (the parts released by --allow get their revision 1). Run 3 changes nothing but the log: no duplicate parts, no file rewritten
    with a new timestamp, no reordered plan.json."""
    # its own folder: re-running the path on the shared gp() folder would change what the other tests read, and make
    # run 1 here whatever state an earlier test in this process left behind
    root = _tmp("ecadlib-e2e-again-")
    tls = E.self_signed(root)
    work = root / "work"
    with E.FakeAnthropic() as fa, E.FakeNexar(tls) as nx:
        golden_path(work, fa, nx, tls)
        claude, releases = list(fa.kinds), list(nx.releases)
    snaps = [E.snapshot(work)]
    for _ in range(2):
        with E.FakeAnthropic() as fa, E.FakeNexar(tls) as nx:
            golden_path(work, fa, nx, tls, first=False)
            snaps.append(E.snapshot(work))
            # the datasheet answer for DSPART16 is reused; parts still held (no answer last time) are asked again
            assert ("datasheet", "DSPART16") not in fa.kinds and set(fa.kinds) <= set(claude), (fa.kinds, claude)
            assert len(nx.releases) == len(releases), "the push releases the same parts again (FakeNexar has none)"
    diff = lambda a, b: sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    d12, d23 = diff(snaps[0], snaps[1]), diff(snaps[1], snaps[2])
    assert not [k for k in d12 if not (LOGS.search(k) or REFRESHED.search(k))], d12
    assert not [k for k in d23 if not LOGS.search(k)], d23
    names = [i["name"] for i in json.loads((work / "lib" / "parts" / "plan.json").read_text())]
    assert len(names) == len(set(names)) == 8, names
    rows = list(csv.DictReader(open(work / "altium" / "e2e.csv", encoding="utf-8-sig")))
    assert len(rows) == len({tuple(sorted(r.items())) for r in rows}) == 6, rows
    tables = (work / "kicad" / "sym-lib-table").read_text()
    assert tables.count("(lib ") == 4, tables                  # 3 libraries + the database library, once each


def test_killed_librarian_recovers_on_the_next_run():
    """SIGKILL the librarian mid-build: while it waits for Claude's answer on DSPART16, after the KiCad and EasyEDA parts'
    files are written and before the plan is. The next run completes and matches an uninterrupted one."""
    g = gp()
    root = _tmp("ecadlib-e2e-kill-")
    shutil.copy(g["work"] / "parts.xlsx", root / "parts.xlsx")
    shutil.copy(g["work"] / "ecadlib.toml", root / "ecadlib.toml")
    E.seed_cache(root / "cache")
    E.seed_datasheet(root / "cache", "DSPART16")
    args = ("librarian", "parts.xlsx", "-o", "lib/parts", "--cache", "cache")   # not --offline: Claude reads DSPART16
    with E.FakeAnthropic(hold={"DSPART16"}) as slow:
        env = E.clean_env(ANTHROPIC_API_KEY="sk-e2e-test", ANTHROPIC_BASE_URL=slow.url)
        out, killed = E.ecadlib_killed(root, *args, env=env, when=slow.held.is_set)
        assert killed, out
    assert not (root / "lib" / "parts" / "plan.json").exists(), "killed before the plan was written"
    assert list((root / "lib" / "parts" / "models").rglob("*.PcbLib")), "killed after some parts were built"
    with E.FakeAnthropic() as fa:
        env = E.clean_env(ANTHROPIC_API_KEY="sk-e2e-test", ANTHROPIC_BASE_URL=fa.url)
        p = E.ecadlib(root, *args, env=env, check=False)
        assert p.returncode == 2 and "8 parts:" in p.stdout, (p.returncode, p.stdout[-2000:], p.stderr[-2000:])
        E.ecadlib(root, "release-check", "lib/parts/plan.json", *ALLOW, env=env, check=False)
    key = lambda i: (i["status"], i.get("source"), i.get("symbol"), i.get("footprint"), i.get("hold_reasons"))
    want = {n: key(i) for n, i in plan().items()}
    got = {i["name"]: key(i) for i in json.loads((root / "lib" / "parts" / "plan.json").read_text())}
    assert got == want, (got, want)
    # the same library files (their content as read back; each file also carries the time it was written)
    a, b = (sorted(f.relative_to(m).as_posix() for f in m.rglob("*") if f.is_file())
            for m in (g["work"] / "lib" / "parts" / "models", root / "lib" / "parts" / "models"))
    assert a == b, (a, b)
    for rel in a:
        fa, fb = g["work"] / "lib" / "parts" / "models" / rel, root / "lib" / "parts" / "models" / rel
        if rel.endswith(".PcbLib"):
            assert AL.read_pcblib(fa) == AL.read_pcblib(fb), rel
        elif rel.endswith(".SchLib"):
            assert AL.read_schlib(fa)[1] == AL.read_schlib(fb)[1], rel
        else:
            assert fa.read_bytes() == fb.read_bytes(), rel
    assert not list((root / "lib").rglob("*.tmp")), list((root / "lib").rglob("*.tmp"))
