"""Packages that plug into the tools: KiCad PCM zip (kicad_pcm), the pcbnew action plugin, the Altium hand-off folder
(altium_pkg), and paths that survive moving the library folder.   python3 -m tests.test_tool_packages

Offline checks always run. With KiCad installed (kicad-cli and a Python with pcbnew), the PCM package is installed the way
KiCad's PCM does it into a throwaway 3rd-party folder + config dir, and real KiCad must then resolve every footprint
through the global table rows the PCM adds (DRC library check), find the 3D model through ${KICADn_3RD_PARTY} (STEP
export), load the symbol library, and load + register the action plugin. Nothing here runs Altium."""
import csv, io, json, os, re, shutil, subprocess, sys, tempfile, types, zipfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_lib as AL, altium_pkg as AP, footprint_gen as F, kicad_export as X, kicad_pcm as KP, sexpr

ROOT = Path(__file__).parent.parent
_PLAN = {}


def _tmp():
    return Path(tempfile.mkdtemp(prefix="ecadlib-pkg-"))


def _plan():
    """tests.test_kicad_export's librarian run (made once), copied per test: (plan.json, http)."""
    if "dir" not in _PLAN:
        from tests import test_kicad_export as TX
        d = _tmp()
        plan, _, http = TX._plan(d)
        _PLAN.update(dir=plan.parent, http=http)
    d = _tmp() / "plan"
    shutil.copytree(_PLAN["dir"], d)
    return d / "plan.json", _PLAN["http"]


def _export(with_model=True):
    """kicad-export of the plan into a fresh folder; with_model adds a STEP model to the SOIC-8 footprint."""
    plan, http = _plan()
    out = plan.parent.parent / "k"
    rep = X.export(plan, out, "lib", http=http, log=lambda *a: None)
    assert not rep["errors"], rep["errors"]
    if with_model:
        from ecadlib import step_gen as SG
        spec = json.loads((ROOT / "ecadlib" / "package_specs" / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
        (out / "lib.3dshapes").mkdir(exist_ok=True)
        (out / "lib.3dshapes" / "SOIC-8.step").write_text(SG.generate(spec)[0])
        fp = out / "lib.pretty" / "SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod"
        (out / "lib.3dshapes" / "SOIC-8_3.9x4.9mm_P1.27mm.step").unlink(missing_ok=True)
        from ecadlib import sexpr                # replace the model kicad-export took from the .PcbLib by this one
        tree = sexpr.parse(fp.read_text(), keep_quotes=True)
        t = sexpr.dumps([c for c in tree if not (isinstance(c, list) and c[:1] == ["model"])]).rstrip()
        fp.write_text(t[:-1] + '\t(model "${KIPRJMOD}/lib.3dshapes/SOIC-8.step"\n\t\t(offset (xyz 0 0 0))\n'
                      '\t\t(scale (xyz 1 1 1))\n\t\t(rotate (xyz 0 0 0))\n\t)\n)\n')
    return plan, out, rep


# ---------------------------------------------------------------- KiCad PCM package (offline)
def test_pcm_package_layout_and_metadata():
    _, exp, xrep = _export()
    out = exp.parent / "pcm"
    rep = KP.build(exp, out, identifier="com.example.my-lib", kicad=8, log=lambda *a: None)
    assert rep["problems"] == [], rep["problems"]
    z = zipfile.ZipFile(rep["zip"])
    names = set(z.namelist())
    assert {"metadata.json", "symbols/lib.kicad_sym", "3dmodels/lib.3dshapes/SOIC-8.step"} <= names, names
    assert {f"footprints/lib.pretty/{n}.kicad_mod" for n in xrep["footprints"]} <= names
    assert all(n.split("/")[0] in KP.PKG_DIRS or "/" not in n for n in names), names
    meta = json.loads(z.read("metadata.json"))
    assert meta == json.loads((out / "metadata.json").read_text())
    assert meta["type"] == "library" and meta["identifier"] == "com.example.my-lib" and len(meta["versions"]) == 1
    v = meta["versions"][0]
    assert v["kicad_version"] == "8.0" and v["status"] == "stable" and "download_url" not in v      # single, no URL inside
    assert re.match(KP.VER_RE, v["version"]) and v["install_size"] == rep["install_size"] > 0
    # symbols point at the PCM nickname, footprints' models at the installed 3rd party folder
    sym = z.read("symbols/lib.kicad_sym").decode()
    fields = re.findall(r'\(property "Footprint" "([^"]*)"', sym)
    assert fields and all(f.startswith("PCM_lib:") for f in fields if f), fields
    assert {f.split(":", 1)[1] for f in fields if f} <= set(xrep["footprints"])
    fp = z.read("footprints/lib.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod").decode()
    assert '(model "${KICAD8_3RD_PARTY}/3dmodels/com_example_my-lib/lib.3dshapes/SOIC-8.step"' in fp, fp[-300:]
    assert "KIPRJMOD" not in "".join(z.read(n).decode(errors="replace") for n in names if n.endswith((".kicad_mod", ".kicad_sym")))
    # --prefix "" (a KiCad set up without the PCM_ prefix) and --kicad 9
    r2 = KP.build(exp, exp.parent / "pcm2", prefix="", kicad=9, log=lambda *a: None)
    z2 = zipfile.ZipFile(r2["zip"])
    assert '"Footprint" "lib:' in z2.read("symbols/lib.kicad_sym").decode() and r2["nickname"] == "lib"
    assert "${KICAD9_3RD_PARTY}/3dmodels/local_ecadlib_lib/" in z2.read("footprints/lib.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod").decode()


def test_pcm_metadata_checks():
    good = KP.metadata("lib", "com.example.lib", "1.2.3", 8, "CC-BY-SA-4.0", "me")
    assert KP.check_metadata(good, schema_path=False)[0] == []
    for k, v in (("identifier", "9bad"), ("identifier", "a_b"), ("type", "lib"), ("tags", ["Upper"])):
        assert KP.check_metadata(dict(good, **{k: v}), schema_path=False)[0], (k, v)
    bad = json.loads(json.dumps(good))
    bad["versions"][0]["version"] = "1.2.3.4"
    assert any("version" in e for e in KP.check_metadata(bad, schema_path=False)[0])
    try:
        KP.build(ROOT / "nope", _tmp())
        raise AssertionError("missing source accepted")
    except FileNotFoundError:
        pass
    _, exp, _ = _export(False)
    for kw in ({"identifier": "has space"}, {"pkg_version": "v1"}, {"base_url": "file:///tmp/x"}, {"kicad": 6}):
        try:
            KP.build(exp, exp.parent / "x", log=lambda *a: None, **kw)
            raise AssertionError(f"accepted {kw}")
        except ValueError:
            pass


def _schema():
    """KiCad's published PCM schema: the one a KiCad install ships (identical to gitlab.com/kicad 7.0's), else skip."""
    p = next((p for p in KP.SCHEMA_PATHS if Path(p).is_file()), None)
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return None
    return p


def test_pcm_metadata_validates_against_kicad_schema():
    schema = _schema()
    if not schema:
        return print("      (KiCad's pcm.v1.schema.json or jsonschema not installed: skipped)")
    _, exp, _ = _export(False)
    out = exp.parent / "pcm"
    rep = KP.build(exp, out, base_url="https://example.com/kicad", schema=schema, log=lambda *a: None)
    assert rep["problems"] == [] and any("schema" in c for c in rep["checks"]), rep
    import jsonschema
    full = json.loads(Path(schema).read_text())
    for f, d in (("metadata.json", "Package"), ("packages.json", "PackageArray"), ("repository.json", "Repository")):
        jsonschema.Draft7Validator(dict(full, **{"$ref": f"#/definitions/{d}"})).validate(json.loads((out / f).read_text()))
    pk = json.loads((out / "packages.json").read_text())["packages"][0]["versions"][0]
    data = Path(rep["zip"]).read_bytes()
    import hashlib
    assert pk["download_sha256"] == hashlib.sha256(data).hexdigest() and pk["download_size"] == len(data)
    assert pk["download_url"] == "https://example.com/kicad/" + Path(rep["zip"]).name
    repo = json.loads((out / "repository.json").read_text())
    assert repo["packages"]["sha256"] == hashlib.sha256((out / "packages.json").read_bytes()).hexdigest()
    bad = json.loads((out / "metadata.json").read_text())
    bad["identifier"] = "1nope"
    assert any(e.startswith("schema:") for e in KP.check_metadata(bad, schema)[0])     # the schema check is live


def test_pcm_rebuild_is_idempotent_and_versions_changes():
    import datetime
    _, exp, _ = _export()
    out = exp.parent / "pcm"
    d1, d2 = datetime.date(2026, 10, 16), datetime.date(2026, 11, 2)
    r1 = KP.build(exp, out, today=d1, base_url="https://x.example/k", log=lambda *a: None)
    snap = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()}
    r2 = KP.build(exp, out, today=d2, base_url="https://x.example/k", log=lambda *a: None)    # nothing changed: same version
    assert r1["version"] == r2["version"] == "2026.10.16" and r1["sha256"] == r2["sha256"]
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in out.iterdir()} == snap   # no file rewritten
    f = exp / "lib.pretty" / "R_0603_1608Metric.kicad_mod"
    f.write_text(f.read_text().replace("(attr smd)", "(attr smd)\n\t(tags \"changed\")", 1))
    r3 = KP.build(exp, out, today=d2, base_url="https://x.example/k", log=lambda *a: None)
    assert r3["version"] == "2026.11.2" and r3["sha256"] != r1["sha256"]
    assert json.loads((out / "repository.json").read_text())["packages"]["update_timestamp"] > r1["update_timestamp"]
    assert KP.build(exp, out, pkg_version="3.1.0", log=lambda *a: None)["version"] == "3.1.0"


def test_pcm_from_plan_matches_export_folder():
    plan, http = _plan()
    a = KP.build(plan, plan.parent / "pa", "lib", http=http, log=lambda *x: None)
    _, exp, _ = _export(False)
    b = KP.build(exp, exp.parent / "pb", log=lambda *x: None)
    za, zb = zipfile.ZipFile(a["zip"]), zipfile.ZipFile(b["zip"])
    assert za.namelist() == zb.namelist() and all(za.read(n) == zb.read(n) for n in za.namelist() if n != "NOTICE.txt")


def test_pcm_install_does_what_kicad_does():
    _, exp, _ = _export()
    rep = KP.build(exp, exp.parent / "pcm", identifier="com.example.lib", log=lambda *a: None)
    tp = exp.parent / "3rd"
    inst = KP.install(rep["zip"], tp)
    assert inst["symbols"] == [str((tp / "symbols/com_example_lib/lib.kicad_sym").resolve())]
    assert inst["footprints"] == [str((tp / "footprints/com_example_lib/lib.pretty").resolve())]
    assert (tp / "3dmodels/com_example_lib/lib.3dshapes/SOIC-8.step").is_file()
    assert not (tp / "metadata.json").exists() and not list(tp.rglob("NOTICE.txt"))       # root files are not installed
    rows = KP.global_table_rows(inst, tp, 8)
    assert rows == {"sym": [("PCM_lib", "${KICAD8_3RD_PARTY}/symbols/com_example_lib/lib.kicad_sym")],
                    "fp": [("PCM_lib", "${KICAD8_3RD_PARTY}/footprints/com_example_lib/lib.pretty")]}
    evil = exp.parent / "evil.zip"                                 # KiCad refuses entries that escape the folder
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("metadata.json", json.dumps(KP.metadata("x", "com.x", "1", 8, "MIT", "me")))
        z.writestr("symbols/../../../escape.txt", "x")
    try:
        KP.install(evil, exp.parent / "3rd2")
        raise AssertionError("path traversal accepted")
    except ValueError as e:
        assert "outside" in str(e)
    assert not (exp.parent / "escape.txt").exists()
    assert KP.check_metadata(KP.metadata("x", "com.x", "1", 8, "MIT", "me"), "/nonexistent/schema.json")[0] == [
        "schema file /nonexistent/schema.json not found"]


def test_kicad7_conversion_of_kicad8_files():
    spec = json.loads((ROOT / "ecadlib" / "package_specs" / "SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    d = _tmp()
    fp, _ = AL.footprint_from_spec(spec)
    AL.write_pcblib(d / "x.PcbLib", [fp])
    k8 = X.footprint_from_pcblib(d / "x.PcbLib", spec["name"], [])
    assert '(property "Reference"' in k8
    k7 = KP.footprint_kicad7(k8)
    t = sexpr.parse(k7)
    assert sexpr.first(t, "version")[1] == "20221018" and not list(sexpr.find(t, "generator_version"))
    assert {x[1] for x in sexpr.find(t, "fp_text")} >= {"reference", "value"} and not any(
        p[1] in ("Reference", "Value") for p in sexpr.find(t, "property"))
    assert sexpr.pads(sexpr.parse(k8)) == sexpr.pads(t)
    assert KP.footprint_kicad7(F.build(spec)[0]) == F.build(spec)[0]          # KiCad 7 files are left alone
    sym = '(kicad_symbol_lib (version 20231120) (generator "ecadlib") (generator_version "8.0")\n  (symbol "A" (exclude_from_sim no)))'
    assert KP.symbol_kicad7(sym) == '(kicad_symbol_lib (version 20220914) (generator "ecadlib")\n  (symbol "A"))'


# ---------------------------------------------------------------- KiCad itself
def _kicad():
    from tests import test_kicad_real as R
    return R


def test_pcm_installed_library_resolves_in_kicad():
    R = _kicad()
    if not (R.pcbnew_python() and R.kicad_cli()):
        return R._skip("kicad-cli / pcbnew (KiCad's Python module)")
    major = R.kicad_major() or 7
    _, exp, xrep = _export()
    base = exp.parent
    rep = KP.build(exp, base / "pcm", kicad=major, log=lambda *a: None)
    tp, cfg = base / "3rd", base / "cfg"
    inst = KP.install(rep["zip"], tp)
    rows = KP.global_table_rows(inst, tp, major)
    ver = f"{major}.0"
    (cfg / ver).mkdir(parents=True)
    for kind, tab in (("fp", "fp_lib_table"), ("sym", "sym_lib_table")):         # what KiCad's PCM traversers add
        body = "".join(f'  (lib (name "{n}")(type "KiCad")(uri "{u}")(options "")(descr "Added by Plugin and Content Manager"))\n'
                       for n, u in rows[kind])
        (cfg / ver / f"{kind}-lib-table").write_text(f"({tab}\n  (version 7)\n{body})\n")
    pretty = inst["footprints"][0]
    fps = [["PCM_lib", n, pretty] for n in sorted(xrep["footprints"])]
    env = dict(os.environ, KICAD_CONFIG_HOME=str(cfg), **{KP.third_party_var(major): str(tp)})

    def job(d, env):
        d.mkdir()
        r = subprocess.run([R.pcbnew_python(), str(R.JOB), "pcmcheck", json.dumps({"dir": str(d), "fps": fps, "bogus": True})],
                           capture_output=True, text=True, timeout=600, env=env)
        assert r.returncode == 0, r.stderr[-2000:]
        return json.loads(r.stdout.strip().splitlines()[-1])
    got = job(base / "b1", env)
    assert all(not got.get(f"PCM_lib:{n}") for _, n, _ in fps), got            # every footprint found, same as library
    assert got.get("not_a_lib:X") and got.get("PCM_lib:NOT_IN_LIB"), got        # ... and the check is live
    assert got["_models"]["PCM_lib:SOIC-8_3.9x4.9mm_P1.27mm"] == [
        f"${{{KP.third_party_var(major)}}}/3dmodels/{inst['install_dir']}/lib.3dshapes/SOIC-8.step"]
    empty = base / "cfg_empty"
    (empty / ver).mkdir(parents=True)
    ctl = job(base / "b2", dict(env, KICAD_CONFIG_HOME=str(empty)))            # control: without the PCM rows ...
    assert all(ctl.get(f"PCM_lib:{n}") for _, n, _ in fps), ctl                  # ... KiCad finds none of them
    solids = sum(f.read_text().count("MANIFOLD_SOLID_BREP(") for f in (exp / "lib.3dshapes").glob("*.step"))

    def step(env):
        r = subprocess.run([R.kicad_cli(), "pcb", "export", "step", got["_board"], "-o", str(base / "b.step"), "-f"],
                           capture_output=True, text=True, timeout=600, env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        return (base / "b.step").read_text(errors="replace").count("MANIFOLD_SOLID_BREP(")
    assert step(env) == solids + 1                                               # every model through ${KICADn_3RD_PARTY}
    assert step(dict(env, **{KP.third_party_var(major): str(base / "nowhere")})) == 1     # control: board only


def test_pcm_installed_symbols_load_in_kicad():
    R = _kicad()
    if not R.kicad_cli():
        return R._skip("kicad-cli")
    major = R.kicad_major() or 7
    _, exp, xrep = _export(False)
    rep = KP.build(exp, exp.parent / "pcm", kicad=major, log=lambda *a: None)
    inst = KP.install(rep["zip"], exp.parent / "3rd")
    lib = Path(inst["symbols"][0])
    (exp.parent / "svg").mkdir()
    r = R.cli("sym", "export", "svg", lib, "-o", exp.parent / "svg")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(list((exp.parent / "svg").glob("*.svg"))) == xrep["symbols"] >= 4
    up = exp.parent / "up.kicad_sym"
    assert R.cli("sym", "upgrade", lib, "-o", up, "--force").returncode == 0
    fps = {p.stem for p in Path(inst["footprints"][0]).glob("*.kicad_mod")}
    fields = re.findall(r'\(property "Footprint" "PCM_lib:([^"]+)"', up.read_text())   # as KiCad wrote it back
    assert fields and set(fields) <= fps, (fields, fps)


def test_kicad7_footprints_of_every_spec_load_in_pcbnew():
    R = _kicad()
    if not R.pcbnew_python() or (R.kicad_major() or 7) >= 8:
        return R._skip("KiCad 7's pcbnew")
    d = _tmp()
    (d / "k7.pretty").mkdir()
    n = 0
    for p in R.SPECS:
        s = json.loads(p.read_text())
        fp, _ = AL.footprint_from_spec(s)
        AL.write_pcblib(d / "x.PcbLib", [fp])
        (d / "k7.pretty" / f"{s['name']}.kicad_mod").write_text(KP.footprint_kicad7(X.footprint_from_pcblib(d / "x.PcbLib", s["name"], [])))
        n += 1
    got = R.pcbnew_job("load", lib=str(d / "k7.pretty"))
    assert len(got) == n and all(v and v["pads"] for v in got.values()), [k for k, v in got.items() if not v]
    assert all(v["reference"] == "REF**" for v in got.values()), {k: v["reference"] for k, v in got.items()}


# ---------------------------------------------------------------- the pcbnew action plugin
def _plugin_module():
    fake = types.ModuleType("pcbnew")
    fake.registered = []
    fake.ActionPlugin = type("ActionPlugin", (), {"register": lambda self: fake.registered.append(self)})
    with mock.patch.dict(sys.modules, {"pcbnew": fake}):
        src = ROOT / "ecadlib" / "assets" / "kicad_action_plugin.py"
        mod = types.ModuleType("ecadlib_kicad_action_plugin")
        mod.__file__ = str(src)
        exec(compile(src.read_text(), str(src), "exec"), mod.__dict__)
    return mod, fake


def test_action_plugin_helpers():
    from ecadlib import altium_menu as AM
    mod, fake = _plugin_module()
    names = []
    for p in fake.registered:
        p.defaults()
        names.append(p.name)
    assert names == ["ecadlib", "ecadlib: add part", "ecadlib: check project BOM", "ecadlib: open review queue"], names
    assert fake.registered[0].show_toolbar_button and fake.registered[0].icon_file_name.endswith("ecadlib.png")
    assert not any(p.show_toolbar_button for p in fake.registered[1:])
    d = _tmp()
    c = mod.job_command(["ecadlib"], "J", "add-part", "b.kicad_pcb", " LM358DR ", "TI")
    assert c == ["ecadlib", "kicad-menu", "run-job", "J", "--action", "add-part", "--project", "b.kicad_pcb",
                 "--mpn", "LM358DR", "--manufacturer", "TI"], c
    assert mod.job_command(["e"], "J", "check-bom", "b.kicad_pcb")[-2:] == ["--project", "b.kicad_pcb"]
    for bad in ("", "a;rm -rf /", "x" * 200, "--help"):
        try:
            mod.job_command(["ecadlib"], "J", "add-part", "b", bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    # the window reads the files ecadlib writes (UTF-16 with a BOM)
    AM.write_result(d / "result.ini", {"status": "held", "message": "LM358DR is held", "fix": "Pick a symbol",
                                       **AM.rows_result([AM.row_text("symbol", "none yet"), AM.row_text("release check", "ready", True)], "Held")})
    res = mod.read_ini(str(d / "result.ini"), "result")
    assert res["big"] == "Held" and res["fix"] == "Pick a symbol"
    assert mod.rows_of(res) == [("symbol", "none yet", False), ("release check", "ready", True)]
    assert mod.read_ini(str(d / "missing.ini"), "result") == {}
    job = mod.new_job(str(d), "fill")
    assert os.path.isdir(job) and job.startswith(str(d / "ecadlib-work" / "jobs"))
    (d / "fp-lib-table").write_text('(fp_lib_table\n  (version 7)\n  (lib (name "Other")(type "KiCad")(uri "x")(options "")(descr ""))\n)\n')
    ch = mod.ensure_tables(str(d))
    assert len(ch) == 2 and mod.ensure_tables(str(d)) == []
    fp = sexpr.parse((d / "fp-lib-table").read_text())
    assert [sexpr.first(l, "name")[1] for l in sexpr.find(fp, "lib")] == ["Other", "ecadlib"]
    assert sexpr.first(next(sexpr.find(sexpr.parse((d / "sym-lib-table").read_text()), "lib")), "uri")[1] == "${KIPRJMOD}/ecadlib/ecadlib.kicad_sym"
    assert mod.ecadlib_command({"ECADLIB_CMD": "py -m ecadlib"}) == ["py", "-m", "ecadlib"]
    assert mod.ecadlib_command({}, which=lambda x: "/bin/ecadlib" if x == "ecadlib" else None) == ["/bin/ecadlib"]
    assert mod.ecadlib_command({}, which=lambda x: None) is None
    src = (ROOT / "ecadlib" / "assets" / "kicad_action_plugin.py").read_text()
    assert "cwd=self.project" not in src           # python -m ecadlib in the project folder imports its ecadlib/ library


def test_action_plugin_package_loads_and_registers_in_pcbnew():
    R = _kicad()
    rep = KP.plugin_build(_tmp() / "plug")
    meta = json.loads(zipfile.ZipFile(rep["zip"]).read("metadata.json"))
    assert meta["type"] == "plugin" and rep["problems"] == [] and zipfile.ZipFile(rep["zip"]).namelist() == ["metadata.json", "plugins/__init__.py", "plugins/ecadlib.png", "resources/icon.png"]
    if not R.pcbnew_python():
        return R._skip("pcbnew (KiCad's Python module)")
    tp = Path(rep["zip"]).parent / "3rd"
    KP.install(rep["zip"], tp)
    code = ("import json, pcbnew\ngot = []\n"
            "def spy(p):\n    got.append([p.GetName(), p.GetCategoryName(), type(p).__module__])\n"
            "    return None\n"                     # pcbnew's own register_action needs KiCad's app for a second plugin
            "pcbnew.PYTHON_ACTION_PLUGINS.register_action = spy\n"
            f"pcbnew.LoadPlugins(None, None, {str(tp / 'plugins')!r})\n"
            "print(json.dumps({'got': got, 'trace': pcbnew.FULL_BACK_TRACE, 'not_loaded': pcbnew.NOT_LOADED_WIZARDS}))\n")
    r = subprocess.run([R.pcbnew_python(), "-c", code], capture_output=True, text=True, timeout=300, cwd=tempfile.gettempdir(),
                       env=dict(os.environ, KICAD_CONFIG_HOME=str(tp.parent / "cfg")))
    assert r.returncode == 0, r.stderr[-2000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert [g[0] for g in out["got"]] == ["ecadlib", "ecadlib: add part", "ecadlib: check project BOM", "ecadlib: open review queue"] \
        and all(g[1:] == ["Library", "local_ecadlib_add-part"] for g in out["got"]) and not out["trace"], (out, r.stderr[-1000:])


# ---------------------------------------------------------------- Altium hand-off
def test_altium_pkg_from_plan():
    plan, _ = _plan()
    out = plan.parent.parent / "alt"
    rep = AP.build(plan, out, "mylib", log=lambda *a: None)
    assert rep["problems"] == [], rep["problems"]
    lp = (out / "mylib.LibPkg").read_bytes().decode()
    assert "\r\n" in lp and lp.startswith("[Design]\r\nVersion=1.0\r\n") and "[Preferences]\r\n" in lp
    docs = re.findall(r"\[Document(\d+)\]\r\nDocumentPath=([^\r]+)\r\n", lp)
    assert [int(i) for i, _ in docs] == list(range(1, len(docs) + 1)) and len(docs) == 8
    paths = [p for _, p in docs]
    assert all(re.match(r"^(SchLib|PcbLib)\\[^\\/:]+\.(SchLib|PcbLib)$", p) for p in paths), paths
    assert [p.split("\\")[0] for p in paths] == ["SchLib"] * 4 + ["PcbLib"] * 4                  # symbols first
    for block in lp.split("[Document")[1:]:                                                      # same keys as Altium writes
        keys = [l.split("=", 1)[0] for l in block.split("\r\n")[1:] if "=" in l]
        assert keys == ["DocumentPath"] + [k.split("=")[0] for k in AP.DOCUMENT], keys
    # the parts' build recipes come along from the plan, so Altium can rebuild the libraries (ecadlib_build.pas)
    assert rep["native_build"] and (out / "ecadlib_build.txt").is_file()
    assert (out / "mylib_Install.PrjScr").read_bytes() == (b"[Design]\r\nVersion=1.0\r\n\r\n[Document1]\r\nDocumentPath=mylib_Install.pas"
                                                          b"\r\n\r\n[Document2]\r\nDocumentPath=ecadlib_build.pas\r\n")
    pas = (out / "mylib_Install.pas").read_text()
    for proc in ("InstallDatabaseLibrary", "BuildIntegratedLibrary", "InstallFileLibraries", "UninstallAll"):
        assert re.search(rf"^Procedure {proc};$", pas, re.M), proc
    assert "GetRunningScriptProjectName" in pas and "'mylib.DbLib'" in pas and "'mylib.LibPkg'" in pas
    assert len(re.findall(r"\bBegin\b", pas)) == len(re.findall(r"\bEnd\b", pas)), "unbalanced Begin/End"
    for p in paths:
        assert f"Result.Add('{p}');" in pas
    assert re.search(r"[A-Za-z]:\\|/tmp/|/home/", pas + lp) is None                              # nothing absolute
    db = (out / "mylib.DbLib").read_text()
    assert "DatabasePathRelative=1" in db and "LibraryDatabasePath=mylib.xlsx" in db
    assert rep["symbols"] == 4 and rep["footprints"] == 4 and rep["links"] >= 2 and rep["dblib_rows"] == 4
    # again: nothing rewritten
    snap = {p: p.stat().st_mtime_ns for p in out.rglob("*") if p.is_file() and p.name != "altium_pkg.json"}
    rep2 = AP.build(plan, out, "mylib", log=lambda *a: None)
    assert rep2["written"] == [] and {p: p.stat().st_mtime_ns for p in snap} == snap


def test_altium_pkg_from_models_folder_and_broken_links():
    plan, _ = _plan()
    models = plan.parent / "models"
    out = plan.parent.parent / "alt2"
    rep = AP.build(models, out, log=lambda *a: None)
    assert rep["problems"] == [] and rep["dblibs"] == [] and (out / "SchLib").is_dir(), rep
    assert sorted(p.name for p in (out / "PcbLib").iterdir()) == sorted(p.name for p in (models / "PcbLib").iterdir())
    (out / "PcbLib" / "SOIC-8_3.9x4.9mm_P1.27mm.PcbLib").unlink()                   # a symbol's footprint goes missing
    probs, _ = AP.check_folder(out, "ecadlib.LibPkg", [])
    assert any("SOIC-8_3.9x4.9mm_P1.27mm" in p and "not in the folder" in p for p in probs), probs
    rep = AP.build(out, out, log=lambda *a: None)                                  # rebuilt from what is there
    assert any("links footprint SOIC-8_3.9x4.9mm_P1.27mm" in p for p in rep["problems"]), rep["problems"]
    a3 = plan.parent.parent / "alt3"
    AP.build(plan, a3, "lib", log=lambda *a: None)
    rows = list(csv.DictReader((a3 / "lib.csv").open(newline="")))
    rows[0]["Library Path"] = "C:\\libs\\x.SchLib"
    with (a3 / "lib.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    probs, _ = AP.check_folder(a3, "lib.LibPkg", ["lib.DbLib"])
    assert any("is absolute" in p for p in probs), probs
    try:
        AP.build(_tmp(), _tmp(), log=lambda *a: None)
        raise AssertionError("empty folder accepted")
    except ValueError:
        pass


def test_altium_export_excel_dblib_is_relative():
    from ecadlib import altium_export as AE
    d = _tmp()
    with mock.patch("sys.stdout", io.StringIO()):
        assert not AE.main([str(ROOT / "examples" / "sample_bom.csv"), "-o", str(d)])
    db = (d / "ecadlib_excel.DbLib").read_text()
    assert "LibraryDatabaseType=Microsoft Excel" in db and "LibraryDatabasePath=parts.xlsx" in db and "DatabasePathRelative=1" in db
    assert "DatabasePathRelative=0" in (d / "ecadlib_sqlite.DbLib").read_text()     # ODBC string: no relative form


# ---------------------------------------------------------------- paths survive moving the folder
LIB_FILES = (".kicad_sym", ".kicad_mod", ".kicad_dbl", ".DbLib", ".LibPkg", ".pas", ".PrjScr", "-lib-table")


def test_paths_survive_moving_the_library_folder():
    from ecadlib import kicad_dblib as KD
    plan, http = _plan()
    a = plan.parent.parent / "A"
    a.mkdir()
    X.export(plan, a / "kicad", "lib", http=http, log=lambda *x: None)
    KD.build(plan, a / "kicad", "lib")
    KP.build(a / "kicad", a / "pcm", log=lambda *x: None)
    AP.build(plan, a / "altium", "lib", log=lambda *x: None)
    b = plan.parent.parent / "somewhere" / "else" / "B"
    b.parent.mkdir(parents=True)
    shutil.move(str(a), str(b))
    olds = {str(a), str(plan.parent), str(plan.parent.parent)}
    texts = {p: p.read_text(errors="replace") for p in b.rglob("*") if p.is_file() and p.name.endswith(LIB_FILES)}
    assert len(texts) > 10
    for p, t in texts.items():
        assert not any(o in t for o in olds), p                                   # no absolute path to where it was built
    for kind in ("fp", "sym"):
        uris = [sexpr.first(l, "uri")[1] for l in sexpr.find(sexpr.parse((b / "kicad" / f"{kind}-lib-table").read_text()), "lib")]
        assert uris and all(u.startswith("${KIPRJMOD}/") for u in uris), uris
    dbl = json.loads((b / "kicad" / "lib.kicad_dbl").read_text())
    assert "${CWD}/lib.sqlite" in dbl["source"]["connection_string"]
    for z in zipfile.ZipFile(next((b / "pcm").glob("*.zip"))).namelist():
        assert not z.startswith("/") and ".." not in z
    probs, facts = AP.check_folder(b / "altium", "lib.LibPkg", ["lib.DbLib"])     # every Altium reference resolves from B
    assert probs == [] and facts["dblib_rows"] == 4, probs
    R = _kicad()
    if not R.pcbnew_python():
        return R._skip("pcbnew (KiCad's Python module)")
    k = b / "kicad"
    if (R.kicad_major() or 7) < 8:
        for f in (k / "lib.pretty").glob("*.kicad_mod"):
            f.write_text(R.kicad7_footprint(f.read_text()))
    fps = sorted(p.stem for p in (k / "lib.pretty").glob("*.kicad_mod"))
    got = R.pcbnew_job("libcheck", dir=str(k), lib="lib", fps=fps, bogus=True)     # the moved project's table, in KiCad
    assert all(not got.get(f"lib:{n}") for n in fps) and got.get("lib:NOT_IN_LIB"), got


# ---------------------------------------------------------------- front ends
def test_cli_and_workspace_and_mcp():
    from ecadlib import cli, mcp_server as M
    from ecadlib.app import Workspace
    assert {"kicad-pcm", "altium-pkg"} <= set(cli.COMMANDS)
    plan, _ = _plan()
    _, exp, _ = _export(False)
    with mock.patch("sys.stdout", io.StringIO()) as so:
        assert cli.main(["kicad-pcm", str(exp), "--out", str(exp.parent / "c1")]) == 0
        assert cli.main(["altium-pkg", str(plan.parent / "models"), "--out", str(exp.parent / "c2"), "--json"]) == 0
    assert "Install from File" in so.getvalue() and '"libpkg": "ecadlib.LibPkg"' in so.getvalue()
    with mock.patch("sys.stderr", io.StringIO()) as se:
        assert cli.main(["kicad-pcm", str(exp.parent / "missing"), "--out", str(exp.parent / "c3")]) == 2
        assert cli.main(["altium-pkg", str(exp.parent / "missing")]) == 2
    assert "no such plan.json" in se.getvalue()
    assert "tool_packages" in {t["name"] for t in M.TOOLS}
    with tempfile.TemporaryDirectory() as w:
        ws = Workspace(w, offline=True)
        bom = ws.save_upload("bom.csv", b"Designator,MPN\nR1,X\n")
        shutil.copytree(plan.parent, ws._librarian_dir(ws._bom_path(bom)))
        r = M.call_tool(ws, "tool_packages", {"bom": bom, "name": "Lib", "kicad": 7})
        assert r["kicad"]["problems"] == [] and r["altium"]["problems"] == [], r
        assert (Path(w) / r["kicad"]["zip"]).is_file() and r["kicad"]["nickname"] == "PCM_Lib" and r["altium"]["documents"] == 8
        assert (Path(w) / r["altium"]["folder"] / "Lib.LibPkg").is_file()


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
