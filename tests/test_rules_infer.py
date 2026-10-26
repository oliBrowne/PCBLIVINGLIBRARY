"""Offline tests for ecadlib.rules_infer (`ecadlib init`, the UI Setup card, MCP propose_rules / save_rules).
Run:  python3 -m tests.test_rules_infer

Synthetic libraries in four house styles (tests/rules_fixtures.py) are written with ecadlib's own Altium and KiCad
writers; each style lists the settings a person would expect. The accuracy over all of them is printed."""
import contextlib, io, json, tempfile
from pathlib import Path

from ecadlib import altium365 as A, altium_lib as AL, bom_triage as T, mcp_server as M, rules as RU, rules_infer as I
from ecadlib.app import Workspace
from tests import rules_fixtures as RF
from tests.test_ui import serve, wait_job

BOM = ("Designator,Qty,Value,Footprint,Manufacturer,MPN,Description\n"
       "R1,1,10k,R_0603_1608Metric,Yageo,RC0603FR-0710KL,Resistor 10k 1% 0603\n"
       "C1,1,100nF,C_0402_1005Metric,Samsung,CL05B104KO5NNNC,Capacitor 100nF 16V X7R 0402\n"
       "R2,1,4.7k,R_0402_1005Metric,Yageo,RC0402JR-074K7L,4.7k 5% 0402\n"
       "U1,1,LM358DR,SOIC-8_3.9x4.9mm_P1.27mm,TI,LM358DR,Op amp\n")


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **k)


def test_inference_accuracy_on_house_styles():
    hits = total = 0
    with tempfile.TemporaryDirectory() as d:
        for name, make in RF.STYLES.items():
            paths, expect = make(Path(d) / name)
            prop = I.propose(paths)
            h, t, misses = RF.score(prop, expect)
            hits, total = hits + h, total + t
            assert not misses, (name, misses)
            assert not prop["errors"] and not prop["questions"], (name, prop["errors"], prop["questions"])
            RU.validate(RU.parse_text(prop["toml"]))                       # the file it would write is valid
            for s in prop["settings"]:
                assert s["evidence"] and 0 < s["confidence"] <= 1 and s["level"] in ("high", "medium", "low"), s
    print(f"      inference accuracy on the synthetic house styles: {hits}/{total} settings")
    assert hits == total


def test_new_parts_match_the_scanned_library():
    """Rules inferred from a library name new BOM parts the way that library does."""
    with tempfile.TemporaryDirectory() as d:
        b = Path(d) / "bom.csv"
        b.write_text(BOM)
        lines, _ = T.read_bom(b)
        want = {"altium_classic": ({"RES_0603_10K_1%", "RES_0402_4K7_5%", "CAP_0402_100N_16V_X7R", "LM358DR"},
                                   {"RESC1608X55N", "RESC1005X40N", "CAPC1005X55N", "SOIC127P600X175-8N"}),
                "glued_dblib": ({"R0603_10K_1%", "R0402_4K7_5%", "C0402_100N_16V_X7R", "LM358DR"},
                                {"R0603", "R0402", "C0402", "SOIC-8_3.9x4.9mm_P1.27mm"}),
                "mpn_a365": ({"RC0603FR-0710KL", "RC0402JR-074K7L", "CL05B104KO5NNNC", "LM358DR"},
                             {"R_0603_1608Metric", "R_0402_1005Metric", "C_0402_1005Metric", "SOIC-8_3.9x4.9mm_P1.27mm"})}
        for style, (names, fps) in want.items():
            paths, _ = RF.STYLES[style](Path(d) / style)
            rules = RU.load(RU.parse_text(I.propose(paths)["toml"]))      # through the written file, as a user gets it
            items = A.plan(lines, rules=rules)
            assert {i["name"] for i in items} == names, (style, [i["name"] for i in items])
            assert {i["footprint"]["ref"] for i in items} == fps, (style, [i["footprint"]["ref"] for i in items])
            if style == "glued_dblib":
                assert {i["folder"] for i in items} == {"Components/Res", "Components/Caps", "Components/Semis"}
            if style == "mpn_a365":
                assert {i["folder"] for i in items} == {"Library/Passives/Resistors", "Library/Passives/Capacitors", "Library/ICs"}
                assert all({"name": "Lifecycle", "value": "Active"} in i["parameters"] for i in items)


def _mixed(d):
    """6 resistors named RES_<size>_<value>_<tol>, 5 named by MPN: split evidence -> a question."""
    syms = [RF.sym(f"RES_{s}_{v}_{t}", "R", {"MPN": RF.MPN_R[v]}, None) for s, v, t in RF.RES[:6]]
    syms += [RF.sym(RF.MPN_R[v] + "X", "R", {"MPN": RF.MPN_R[v] + "X"}, None) for s, v, t in RF.RES[:5]]
    Path(d).mkdir(parents=True, exist_ok=True)
    AL.write_schlib(Path(d) / "Mixed.SchLib", syms)
    return Path(d)


def test_split_evidence_asks_one_question():
    with tempfile.TemporaryDirectory() as d:
        lib = _mixed(Path(d) / "lib")
        prop = I.propose([lib])
        q = {x["key"]: x for x in prop["questions"]}
        assert "naming.by_category.Resistors" in q, prop["settings"]
        assert q["naming.by_category.Resistors"]["default"] == "{cat3}_{package}_{value}_{tolerance}"
        assert "{mpn}" in q["naming.by_category.Resistors"]["choices"]
        s = next(x for x in prop["settings"] if x["key"] == "naming.by_category.Resistors")
        assert s["level"] == "medium" and s["alternatives"][0]["value"] == "{mpn}"
        # --yes: best guess, no questions asked
        out = Path(d) / "ecadlib.toml"
        assert quiet(I.main, ["--from", str(lib), "-o", str(out), "--yes"], inp=lambda *_: (_ for _ in ()).throw(AssertionError("asked"))) == 0
        assert RU.load(out)["naming"]["by_category"]["Resistors"] == "{cat3}_{package}_{value}_{tolerance}"
        # interactive: the person picks the second choice
        out2 = Path(d) / "two.toml"
        answers = iter(["", "", "", "", "", "", "2"])     # Enter for the 6 setup questions, then the 2nd naming choice
        assert quiet(I.main, ["--from", str(lib), "-o", str(out2)], inp=lambda *_: next(answers)) == 0
        assert RU.load(out2)["naming"]["by_category"]["Resistors"] == "{mpn}"
        assert "you chose this value" in out2.read_text()


def test_init_never_overwrites_without_confirmation():
    with tempfile.TemporaryDirectory() as d:
        paths, _ = RF.style_glued_dblib(Path(d) / "lib")
        out = Path(d) / "ecadlib.toml"
        out.write_text("# mine\n[footprint]\ndensity = \"L\"\n")
        rc = quiet(I.main, ["--from", str(paths[0]), "-o", str(out), "--yes"])
        assert rc == 1 and out.read_text().startswith("# mine")              # kept; proposal next to it
        prop = Path(str(out) + ".proposed").read_text()
        assert 'density = "M"' in prop and "Supplier PN" in prop
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            rc = I.main(["--from", str(paths[0]), "-o", str(out)], inp=lambda q, *_: "n" if q.startswith("replace") else "")
        assert rc == 1 and "-density = \"L\"" in log.getvalue() and out.read_text().startswith("# mine")   # diff shown
        assert quiet(I.main, ["--from", str(paths[0]), "-o", str(out)], inp=lambda q, *_: "y" if q.startswith("replace") else "") == 0
        assert RU.load(out)["footprint"]["density"] == "M" and Path(str(out) + ".bak").read_text().startswith("# mine")
        assert quiet(I.main, ["--from", str(paths[0]), "-o", str(out), "--yes"]) == 0     # same again: nothing to do
        # --force replaces without asking; --json writes nothing
        out.write_text("[footprint]\ndensity = \"L\"\n")
        assert quiet(I.main, ["--from", str(paths[0]), "-o", str(out), "--yes", "--force"]) == 0 and 'density = "M"' in out.read_text()
        with contextlib.redirect_stdout(io.StringIO()) as o:
            I.main(["--from", str(paths[0]), "-o", str(Path(d) / "x.toml"), "--json"])
        assert json.loads(o.getvalue())["settings"] and not (Path(d) / "x.toml").exists()


def test_a365_sources_and_field_maps():
    comps = [{"name": f"RES_0603_{v}", "folder": "Lib/R", "parameters": {"MPN": RF.MPN_R[v], "Manufacturer": "Yageo"},
              "symbol": "Resistor", "footprint": "R0603"} for _, v, _ in RF.RES]
    prop = I.propose([], a365=[comps])                                     # a365_sync hands over a list of dicts
    got = {s["key"]: s["value"] for s in prop["settings"]}
    assert got["naming.by_category.Resistors"] == "{cat3}_{package}_{value}" and got["parameters.names"] == {"Manufacturer Part Number": "MPN"}
    assert got["folders.map"] == {"Resistors": "Lib/R"} or RU.folder_for("Resistors", RU.load(prop["rules"])) == "Lib/R"
    nodes = [{"name": "TPS62133RGTR", "folder": {"path": "Components/ICs"}, "details": {"parameters": [
        {"name": "Mfr Part Number", "value": "TPS62133RGTR"}], "symbols": [{"name": "TPS62133RGTR"}], "footprints": [{"name": "QFN50P300X300X100-17N"}]}}]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "nodes.json").write_text(json.dumps(nodes))
        (Path(d) / "a365_state.json").write_text(json.dumps({"RES_0603_10K": {"revision": "r1", "symbols": [], "footprints": []}}))
        lib = I.scan([Path(d) / "nodes.json", Path(d)])
        assert {c["name"] for c in lib.components} >= {"TPS62133RGTR", "RES_0603_10K"}
        assert next(c for c in lib.components if c["name"] == "TPS62133RGTR")["params"] == {"Mfr Part Number": "TPS62133RGTR"}
        # a DbLib whose data file is not here: parameter names from its field maps
        from ecadlib.altium_export import dblib_text
        (Path(d) / "x.DbLib").write_text(dblib_text("Provider=Microsoft.ACE.OLEDB.12.0;Data Source=C:\\gone\\parts.mdb",
                                                    {"Resistors": ["Part Number", "Library Ref", "Mfr PN", "Mfr", "Vendor PN"]}), newline="")
        prop = I.propose([Path(d) / "x.DbLib"])
        names = next(s for s in prop["settings"] if s["key"] == "parameters.names")["value"]
        assert names == {"Manufacturer Part Number": "Mfr PN", "Manufacturer": "Mfr", "Supplier Part Number 1": "Vendor PN"}, names
        assert any("field map" in w for w in prop["warnings"])


def test_workspace_ui_and_mcp_setup():
    with tempfile.TemporaryDirectory() as d:
        paths, _ = RF.style_altium_classic(Path(d) / "house")
        ws = Workspace(Path(d) / "ws", [str(paths[0])], providers=["digikey"])
        assert ws.rules_info()["exists"] is False and ws.info()["rules"] is None
        prop = ws.propose_rules()                                          # default: the --lib folders
        assert {s["key"] for s in prop["settings"]} >= {"naming.footprint", "layers.courtyard"} and "diff" not in prop
        r = ws.save_rules(prop["toml"])
        assert r["saved"] and r["path"] == "ecadlib.toml" and ws.info()["rules"] == "ecadlib.toml"
        assert ws.rules()["layers"]["courtyard"] == "Mechanical1"
        r = ws.save_rules(prop["toml"].replace('courtyard = "Mechanical1"', 'courtyard = "Mechanical15"'))
        assert r["saved"] is False and r["needs_confirm"] and '+courtyard = "Mechanical15"' in r["diff"]
        assert ws.rules()["layers"]["courtyard"] == "Mechanical1"          # not replaced without overwrite
        try:
            ws.save_rules("[footprint]\ndensity='Z'\n", True)
            raise AssertionError("invalid rules saved")
        except ValueError as e:
            assert "footprint.density" in str(e)
        # tools follow the workspace file: generated footprints land on the house layers
        spec = json.loads(Path("ecadlib/package_specs/R_0603_1608Metric.json").read_text())
        ws.approve_footprint(spec)
        fp = AL.read_pcblib(ws.models / "PcbLib" / "R_0603_1608Metric.PcbLib")[0]
        assert 57 in {x["layer"] for x in fp["records"] if x["type"] == "track"}
        # MCP: propose (short evidence), save -> needs_confirm, then overwrite
        call = lambda name, args: M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        assert {"propose_rules", "save_rules"} <= {t["name"] for t in M.TOOLS}
        res = call("propose_rules", {"paths": [str(paths[0])]})["result"]
        out = json.loads(res["content"][0]["text"])
        assert not res["isError"] and out["toml"] and "rules" not in out and all(len(s["evidence"]) <= 3 for s in out["settings"])
        moved = call("save_rules", {"toml": out["toml"].replace("Mechanical1\"", "Mechanical4\""), "overwrite": True})["result"]
        assert moved["isError"] and "layers.courtyard" in moved["content"][0]["text"]   # a release gate (R-FX11-5)
        res = json.loads(call("save_rules", {"toml": out["toml"].replace("Mechanical2\"", "Mechanical4\"")})["result"]["content"][0]["text"])
        assert res["needs_confirm"] and not res["saved"]
        res = json.loads(call("save_rules", {"toml": out["toml"].replace("Mechanical2\"", "Mechanical4\""), "overwrite": True})["result"]["content"][0]["text"])
        assert res["saved"] and res["backup"] == "ecadlib.toml.bak" and ws.rules()["layers"]["assembly"] == "Mechanical4"
        assert call("save_rules", {})["result"]["isError"]
        # web UI: Setup card, propose as a job, save with confirmation
        srv, http = serve(ws)
        try:
            st, page = http("GET", "/")
            assert b'id="rulesscan"' in page and b"/api/rules/propose" in page and b"/api/rules/save" in page
            st, info = http("GET", "/api/rules")
            assert st == 200 and info["exists"] and info["rules"]["layers"]["assembly"] == "Mechanical4"
            st, j = http("POST", "/api/rules/propose", {"paths": [str(paths[0])], "choices": {}})
            res = wait_job(http, j["job"])
            assert res["state"] == "done" and res["result"]["settings"] and "Mechanical2" in res["result"]["diff"], res.get("error")
            st, r = http("POST", "/api/rules/save", {"text": res["result"]["toml"]})
            assert st == 200 and r["needs_confirm"]
            st, r = http("POST", "/api/rules/save", {"text": res["result"]["toml"], "overwrite": True})
            assert st == 200 and r["saved"]
            assert http("POST", "/api/rules/propose", {"paths": "nope"})[0] == 400
            assert http("POST", "/api/rules/save", {"text": "[x]\n"})[0] == 400
            assert http("POST", "/api/rules/save", {"text": "a"}, {"Origin": "https://evil.example"})[0] == 403
        finally:
            srv.shutdown()


def test_workspace_follows_the_rules_file_its_tools_find():
    """`ecadlib init` writes ./ecadlib.toml; `ecadlib ui` started there must not show "no rules" and plan by the
    defaults while the DbLib export (which searches from its folder up and the current folder) uses the file."""
    import os, sqlite3
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        Path(d, "ecadlib.toml").write_text('[parameters.names]\n"Manufacturer Part Number" = "MPN"\n')
        os.chdir(d)
        try:
            ws = Workspace("ecadlib_workspace", [str(Path(cwd) / "examples" / "kicad_lib")], providers=["digikey"], offline=True)
            rel = ws.save_upload("bom.csv", (Path(cwd) / "examples" / "sample_bom.csv").read_bytes())
            assert ws.rules()["parameters"]["names"] == {"Manufacturer Part Number": "MPN"}
            assert ws.info()["rules"].endswith("ecadlib.toml") and ws.rules_info()["exists"]
            # the UI's DbLib export needs the librarian's plan now (round 3, R-FX-3); the draft writer, given the rules
            # file the workspace finds, still names its columns by it
            from ecadlib import altium_export as AX
            rf = ws.rules_in_use()
            assert rf and not quiet(AX.main, [str(ws.root / rel), "-o", str(ws.root / "altium"), "--draft",
                                              "--rules", str(rf)])
            con = sqlite3.connect(ws.root / "altium" / "parts.sqlite")
            t = con.execute("select name from sqlite_master").fetchone()[0]
            assert "MPN" in [c[1] for c in con.execute(f'pragma table_info("{t}")')]
            con.close()
            r = ws.save_rules("[footprint]\ndensity = 'L'\n")                 # saving still writes the workspace's own
            assert r["saved"] and r["path"] == "ecadlib.toml" and ws.rules()["parameters"]["names"] == {}
            assert Path(d, "ecadlib.toml").read_text().startswith("[parameters.names]")
        finally:
            os.chdir(cwd)


def test_propose_given_one_path_string():
    """An agent passing paths as one string must scan that folder, not '/', 'h', 'o', ... (a scan of the root)."""
    with tempfile.TemporaryDirectory() as d:
        paths, _ = RF.style_altium_classic(Path(d) / "house")
        ws = Workspace(Path(d) / "ws", [], providers=["digikey"])
        call = lambda args: M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                          "params": {"name": "propose_rules", "arguments": args}})
        out = json.loads(call({"paths": str(paths[0])})["result"]["content"][0]["text"])
        assert out["paths"] == [str(paths[0])] and out["settings"] and not any("not found" in w for w in out["warnings"])


def test_init_prints_a_numeric_setting():
    """Regression (found by tests/test_e2e.py): `ecadlib init` over a library whose IC symbols have 100 mil pins
    proposes symbols.pin_length = 100 (a number) and crashed printing it ('int' object is not subscriptable)."""
    from ecadlib import kicad_import as K
    src = Path(__file__).parent / "fixtures" / "e2e" / "kicad" / "kicad-symbols" / "MCU_ST_STM32F1.kicad_symdir"
    chain, _ = K.load_symbol(str(src / "STM32F103C8Tx.kicad_sym"), None)
    sym = K.convert_symbol(chain, "STM32F103C8T6", "LQFP-48", [], None, [])
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d) / "MCU.SchLib", [sym])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = I.main(["--from", d, "--yes", "-o", str(Path(d) / "ecadlib.toml")])
        assert rc == 0, buf.getvalue()
        assert any(ln.split()[:2] == ["symbols.pin_length", "100"] for ln in buf.getvalue().splitlines()), buf.getvalue()
