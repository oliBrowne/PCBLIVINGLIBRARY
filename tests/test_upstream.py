"""Offline tests for ecadlib.upstream: provenance manifest, KiCad upstream diffs, --update, Altium 365 drift.
Run:  python3 -m tests.test_upstream"""
import csv, json, re, tempfile
from pathlib import Path
from urllib.parse import unquote

from ecadlib import altium365 as A, kicad_import as K, upstream as U
from ecadlib.httpcache import CachedHTTP, redact

FIX = Path(__file__).parent / "fixtures" / "kicad"
USB = (FIX / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text()
REG = (FIX / "Regulator_Linear_AMS1117.kicad_sym").read_text()
STEP = "ISO-10303-21;\nDATA;\n#1=CARTESIAN_POINT('',(-4.5,-3.6,0.));\n#2=CARTESIAN_POINT('',(4.5,3.6,3.26));\nENDSEC;\n"
FP_PATH = "Connector_USB.pretty/USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod"
STEP_PATH = "Connector_USB.3dshapes/USB_C_Receptacle_HRO_TYPE-C-31-M-12.step"
SPEC = {"mpn": "TYPE-C-31-M-12", "footprint": "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", "footprint_as": "USB_C_16P",
        "symbol": "Regulator_Linear:AMS1117-3.3", "pin_map": {"1": "A1", "2": "A4", "3": "A9"}}   # pin_map: any pads will do here
# upstream edits: pad A4 moved and widened, pad B12 removed, courtyard taller; symbol pin 1 renamed
USB2 = re.sub(r'\t\(pad "B12".*?\n\t\)\n', "", USB.replace('(pad "A4" smd roundrect\n\t\t(at -2.45 -4.045)\n\t\t(size 0.6 1.45)',
              '(pad "A4" smd roundrect\n\t\t(at -2.5 -4.045)\n\t\t(size 0.7 1.45)'), count=1, flags=re.S).replace("-5.27)", "-5.5)")
REG2 = REG.replace('(name "GND"', '(name "ADJ"', 1)


def seed(http, url, text, status=200):
    (http.dir / f"{http._key('GET', redact(url), None)}.json").write_text(json.dumps({"url": url, "status": status, "text": text}))


def seeded_http(d, fp=USB, sym=REG):
    http = CachedHTTP(Path(d) / "cache", offline=True)
    seed(http, K.GITLAB.format(repo="kicad-footprints", path=FP_PATH), fp)
    seed(http, K.GITLAB.format(repo="kicad-packages3D", path=STEP_PATH), STEP)
    for n in ("AMS1117-3.3", "AP1117-15"):
        seed(http, K.GITLAB.format(repo="kicad-symbols", path=f"Regulator_Linear.kicad_symdir/{n}.kicad_sym"), "", 404)
    seed(http, K.GITLAB.format(repo="kicad-symbols", path="Regulator_Linear.kicad_sym"), sym)
    seed(http, U.API.format(repo="kicad-footprints") + f"/commits?path={U.quote(FP_PATH)}&ref_name=master&per_page=1",
         json.dumps([{"id": "c0ffee" * 6 + "abcd", "created_at": "2026-03-05T13:42:14.000+00:00", "title": "old"}]))
    return http


class FakeResp:
    def __init__(self, status, body=b"", headers=None):
        self.status_code, self.content, self.headers = status, body if isinstance(body, bytes) else json.dumps(body).encode(), headers or {}

    def json(self):
        return json.loads(self.content)


class FakeGitLab:
    """gitlab.com REST v4 for kicad/libraries/*: files (HEAD, raw), commits?path=, commits/<id>/diff."""
    def __init__(self, files, history=None, diffs=None, fail=False):
        self.files, self.history, self.diffs, self.fail, self.calls = files, history or {}, diffs or {}, fail, []

    def request(self, method, url, params=None, headers=None, timeout=None):
        self.calls.append((method, url, params))
        if self.fail:
            return FakeResp(503)
        repo, rest = re.match(r"https://gitlab\.com/api/v4/projects/kicad%2Flibraries%2F([^/]+)/repository/(.+)$", url).groups()
        m = re.match(r"files/([^/]+)(/raw)?$", rest)
        if m:
            text = self.files.get((repo, unquote(m.group(1))))
            if text is None:
                return FakeResp(404)
            return FakeResp(200, text.encode() if m.group(2) else b"", {"x-gitlab-blob-id": U.digests(text)[1], "x-gitlab-last-commit-id": "c2"})
        if rest == "commits":
            c = self.history.get((repo, params["path"]))
            return FakeResp(200, [c] if c else [])
        m = re.match(r"commits/([^/]+)/diff$", rest)
        return FakeResp(200, self.diffs.get(m.group(1), []) if params.get("page", 1) == 1 else [])


def upstream_now(fp=USB, sym=REG, step=STEP):
    files = {("kicad-footprints", FP_PATH): fp, ("kicad-symbols", "Regulator_Linear.kicad_sym"): sym}
    if step:
        files[("kicad-packages3D", STEP_PATH)] = step
    return files


def convert(d):
    for k in ("PcbLib", "SchLib"):
        (Path(d) / "out" / k).mkdir(parents=True, exist_ok=True)
    rep = K.import_part(SPEC, Path(d) / "out", seeded_http(d), step=False)
    assert rep["ok"], rep
    return json.loads((Path(d) / "out" / "manifest.json").read_text())


def run_main(argv, gl):
    old = U.GitLab
    U.GitLab = lambda token=None: old(session=gl)
    try:
        return U.main(argv)
    finally:
        U.GitLab = old


# ---------------------------------------------------------------- provenance
def test_manifest_written_on_conversion_local_fixtures():
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d) / "SOT-223-3.kicad_mod"
        fp.write_text('(footprint "SOT-223-3" (layer "F.Cu")\n' + "".join(
            f'  (pad "{n}" smd rect (at {x} 3.15) (size 0.95 2) (layers "F.Cu" "F.Paste" "F.Mask"))\n' for n, x in (("1", -2.3), ("2", 0), ("3", 2.3)))
            + '  (pad "2" smd rect (at 0 -3.15) (size 3.25 2) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
            + '  (fp_rect (start -4 -4.5) (end 4 4.5) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))\n)')
        for k in ("PcbLib", "SchLib"):
            (Path(d) / "out" / k).mkdir(parents=True)
        spec = {"mpn": "AMS1117-3.3", "footprint": str(fp), "symbol": f"{FIX}/Regulator_Linear_AMS1117.kicad_sym:AMS1117-3.3", "height_mm": 1.8}
        rep = K.import_part(spec, Path(d) / "out", None)
        assert rep["ok"] and not any("provenance" in n for n in rep["notes"]), rep
        man = json.loads((Path(d) / "out" / "manifest.json").read_text())
        f, s = man["entries"]["footprint:SOT-223-3"], man["entries"]["symbol:AMS1117-3.3"]
        assert f["kind"] == "footprint" and f["kicad_ref"] == str(fp) and f["output"] == "PcbLib/SOT-223-3.PcbLib"
        assert f["output_sha256"] == U.hashlib.sha256((Path(d) / "out" / f["output"]).read_bytes()).hexdigest()
        assert f["converter"].startswith("ecadlib ") and f["spec"]["height_mm"] == 1.8 and f["mpn"] == "AMS1117-3.3"
        (src,) = f["sources"]                                       # no http client: no STEP model was read
        assert src["file"] == str(fp) and src["sha256"] == U.hashlib.sha256(fp.read_bytes()).hexdigest()
        assert [p[0] for p in src["summary"]["pads"]] == ["1", "2", "3", "2"] and src["summary"]["courtyard"] == [-4, -4.5, 4, 4.5]
        assert [x["symbol"] for x in s["sources"]] == ["AMS1117-3.3", "AP1117-15"]
        assert s["sources"][1]["summary"]["pins"] == {"1": ["GND", "power_in"], "2": ["VO", "power_out"], "3": ["VI", "power_in"]}
        assert s["sources"][0]["summary"]["extends"] == "AP1117-15" and s["output"] == "SchLib/AMS1117-3.3.SchLib"
        rows, worst = U.check_kicad(man, None)                      # local sources: no GitLab needed
        assert set(worst.values()) == {"unchanged"}, rows
        fp.write_text(fp.read_text().replace("(at 2.3 3.15)", "(at 2.4 3.15)"))
        rows, worst = U.check_kicad(man, None)
        assert worst["footprint:SOT-223-3"] == "changed" and "moved 3 (2.3,3.15)->(2.4,3.15)" in rows[0]["summary"], rows
        fp.unlink()
        assert U.check_kicad(man, None)[1]["footprint:SOT-223-3"] == "deleted"


def test_manifest_records_gitlab_sources():
    with tempfile.TemporaryDirectory() as d:
        man = convert(d)
        f = man["entries"]["footprint:USB_C_16P"]
        roles = [(s["role"], s["repo"], s["path"]) for s in f["sources"]]
        assert roles == [("footprint", "kicad-footprints", FP_PATH), ("step", "kicad-packages3D", STEP_PATH)], roles
        assert f["sources"][0]["url"] == K.GITLAB.format(repo="kicad-footprints", path=FP_PATH)
        assert f["sources"][0]["blob_id"] == U.digests(USB)[1] and f["sources"][0]["last_commit"]["title"] == "old"
        assert f["sources"][1]["last_commit"] is None and f["sources"][1]["summary"]["extent"] == [-4.5, -3.6, 0, 4.5, 3.6, 3.26]
        assert len(f["sources"][0]["summary"]["pads"]) == 22
        s = man["entries"]["symbol:TYPE-C-31-M-12"]
        assert [(x["path"], x["symbol"]) for x in s["sources"]] == [("Regulator_Linear.kicad_sym", "AMS1117-3.3"),
                                                                     ("Regulator_Linear.kicad_sym", "AP1117-15")]


# ---------------------------------------------------------------- check-kicad
def test_footprint_and_symbol_diff_summaries():
    with tempfile.TemporaryDirectory() as d:
        man = convert(d)
        gl = U.GitLab(session=FakeGitLab(upstream_now(USB2, REG2), {("kicad-footprints", FP_PATH): {"id": "new1", "created_at": "2026-10-01", "title": "x"}}))
        rows, worst = U.check_kicad(man, gl)
        assert worst == {"footprint:USB_C_16P": "changed", "symbol:TYPE-C-31-M-12": "changed"}, worst
        fp = next(r for r in rows if r["role"] == "footprint")
        assert fp["summary"].split("; ") == ["pads 22 -> 21", "removed B12", "moved A4 (-2.45,-4.045)->(-2.5,-4.045)",
                                             "resized A4 0.6x1.45->0.7x1.45", "courtyard [-5.32, -5.27, 5.32, 4.15] -> [-5.32, -5.5, 5.32, 4.15]"], fp
        assert fp["upstream_commit"] == "new1"
        sym = [r for r in rows if r["kind"] == "symbol"]
        assert sym[1]["summary"] == "pins renamed 1 GND->ADJ" and sym[0]["summary"].startswith("content changed"), sym
        assert next(r for r in rows if r["role"] == "step")["status"] == "unchanged"
        heads = [c for c in gl.s.calls if c[0] == "HEAD"]
        assert len(heads) == 3, heads                                # the shared symbol file is asked about once


def test_deleted_and_renamed_upstream():
    with tempfile.TemporaryDirectory() as d:
        man = convert(d)
        files = upstream_now(step=None)
        del files[("kicad-footprints", FP_PATH)]
        files[("kicad-footprints", "Connector_USB.pretty/USB_C_HRO_TYPE-C-31-M-12.kicad_mod")] = USB
        hist = {("kicad-packages3D", STEP_PATH): {"id": "d1", "created_at": "2026-09-01", "title": "Remove old models"},
                ("kicad-footprints", FP_PATH): {"id": "r1", "created_at": "2026-09-02", "title": "Rename HRO"}}
        diffs = {"d1": [{"old_path": "Other.step", "new_path": "Other.step", "deleted_file": True, "renamed_file": False},
                        {"old_path": STEP_PATH, "new_path": STEP_PATH, "deleted_file": True, "renamed_file": False}],
                 "r1": [{"old_path": FP_PATH, "new_path": "Connector_USB.pretty/USB_C_HRO_TYPE-C-31-M-12.kicad_mod",
                         "deleted_file": False, "renamed_file": True}]}
        rows, worst = U.check_kicad(man, U.GitLab(session=FakeGitLab(files, hist, diffs)))
        by = {r["role"]: r for r in rows if r["kind"] == "footprint"}
        assert worst["footprint:USB_C_16P"] == "deleted" and by["step"]["status"] == "deleted", rows
        assert by["step"]["summary"] == 'deleted upstream in "Remove old models"' and by["step"]["upstream_commit"] == "d1"
        assert by["footprint"]["status"] == "renamed" and by["footprint"]["summary"].endswith("now Connector_USB.pretty/USB_C_HRO_TYPE-C-31-M-12.kicad_mod")
        assert worst["symbol:TYPE-C-31-M-12"] == "unchanged"


def test_update_reconverts_changed_parts():
    with tempfile.TemporaryDirectory() as d:
        man = convert(d)
        mpath, out = Path(d) / "out" / "manifest.json", Path(d) / "out"
        old_sha = man["entries"]["footprint:USB_C_16P"]["output_sha256"]
        fake = FakeGitLab(upstream_now(USB2, REG))
        code = run_main(["check-kicad", str(mpath), "--update", "--offline", "--cache", str(Path(d) / "cache"), "-o", str(Path(d) / "r")], fake)
        assert code == 1
        rows = list(csv.DictReader(open(Path(d) / "r.csv")))
        assert [(r["entry"], r["status"], r["update"]) for r in rows] == [("footprint:USB_C_16P", "changed", "reconverted")], rows
        new = json.loads(mpath.read_text())["entries"]["footprint:USB_C_16P"]
        assert new["sources"][0]["blob_id"] == U.digests(USB2)[1] and new["output_sha256"] != old_sha
        assert len(new["sources"][0]["summary"]["pads"]) == 21
        pads = [r for r in K.AL.read_pcblib(out / "PcbLib" / "USB_C_16P.PcbLib")[0]["records"] if r["type"] == "pad"]
        assert len(pads) == 21 and "B12" not in {p["designator"] for p in pads}
        assert "B12" in "".join(open(Path(d) / "r.md").read().split("\n")[6:])          # the md table names what changed
        code = run_main(["check-kicad", str(mpath), "-o", str(Path(d) / "r2")], fake)
        assert code == 0 and "No changes." in (Path(d) / "r2.md").read_text()


def test_check_kicad_exit_codes():
    with tempfile.TemporaryDirectory() as d:
        convert(d)
        mpath, o = str(Path(d) / "out" / "manifest.json"), str(Path(d) / "r")
        assert run_main(["check-kicad", mpath, "-o", o], FakeGitLab(upstream_now())) == 0
        assert run_main(["check-kicad", mpath, "-o", o], FakeGitLab(upstream_now(sym=REG2))) == 1
        assert run_main(["check-kicad", mpath, "-o", o], FakeGitLab(upstream_now(step=None))) == 1          # deleted
        assert run_main(["check-kicad", mpath, "-o", o], FakeGitLab({}, fail=True)) == 2                    # HTTP 503
        assert "error" in (Path(d) / "r.csv").read_text()
        assert run_main(["check-kicad", str(Path(d) / "nope.json"), "-o", o], FakeGitLab({})) == 2


# ---------------------------------------------------------------- check-a365
def _schema():
    try:
        from graphql import build_schema
    except ImportError:
        return None
    return build_schema(Path("tests/nexar/schema.graphql").read_text(), assume_valid_sdl=True)


def test_upstream_queries_match_nexar_schema():
    schema = _schema()
    if not schema:
        print("skip test_upstream_queries_match_nexar_schema (pip install graphql-core)"); return
    from graphql import parse, validate
    from graphql.execution.values import get_variable_values
    doc = parse(U.Q_COMPONENTS)
    assert not validate(schema, doc)
    assert not isinstance(get_variable_values(schema, doc.definitions[0].variable_definitions, {"ws": "https://ws", "after": None}), list)


class FakeWorkspace:
    """Nexar Design API mock: desLibrary.components in pages of two; every query is validated against the schema."""
    def __init__(self, nodes):
        self.nodes, self.queries, self.schema = nodes, [], _schema()

    def gql(self, query, variables=None):
        self.queries.append(query)
        if self.schema:
            from graphql import parse, validate
            assert not validate(self.schema, parse(query)), query
        i = int(variables.get("after") or 0)
        return {"desLibrary": {"components": {"pageInfo": {"hasNextPage": i + 2 < len(self.nodes), "endCursor": str(i + 2)},
                                              "nodes": self.nodes[i:i + 2]}}}


def node(name, folder, params, sym="CH340C", fp="SOIC-16", rev="r1", guid="g1", comment="*", description=""):
    return {"id": "id-" + name, "name": name, "comment": comment, "description": description, "isManaged": True,
            "folder": {"path": folder}, "revision": {"id": rev, "details": {"lifeCycleState": {"name": "Released"}, "createdAt": "2026-01-01"}},
            "details": {"parameters": [{"name": k, "value": v} for k, v in params.items()],
                        "symbols": [{"name": sym, "guid": "s" + guid}], "footprints": [{"name": fp, "guid": "f" + guid}]}}


def plan_items():
    items = A.plan([{"designator": "U1", "mpn": "CH340C", "manufacturer": "WCH", "footprint": "SOIC-16", "description": "USB UART"},
                    {"designator": "U2", "mpn": "AMS1117-3.3", "manufacturer": "AMS", "footprint": "SOT-223-3_TabPin2", "description": "LDO"},
                    {"designator": "U3", "mpn": "TPS62133RGTR", "manufacturer": "TI", "footprint": "VQFN-16", "description": "Buck"}])
    return items


def ws_nodes(items):
    ok = {p["name"]: p["value"] for p in items[0]["parameters"]}
    ch = node("CH340C", "Components/ICs", {**ok, "Added in A365": "kept"}, comment=items[0]["comment"], description=items[0]["description"])
    ams = items[1]
    p = {x["name"]: x["value"] for x in ams["parameters"]}
    drift = node("AMS1117-3.3", "Components\\Power", {**p, "Manufacturer": "Advanced Monolithic", "Value": "stale"},
                 sym="LDO_OLD", fp=ams["footprint"]["ref"], comment=ams["comment"], description=ams["description"])
    return ch, drift


def test_a365_drift_against_mock_workspace():
    items = plan_items()
    ch, drift = ws_nodes(items)
    extra = node("OLD-PART", "Components/ICs/Legacy", {}, rev="r9")
    elsewhere = node("SOMEONE-ELSES", "Projects/X", {})
    ws = FakeWorkspace([ch, drift, extra, elsewhere])
    nodes = U.workspace_components(ws, "https://ws")
    assert len(ws.queries) == 2 and [n["name"] for n in nodes] == ["CH340C", "AMS1117-3.3", "OLD-PART", "SOMEONE-ELSES"]
    items[1]["cleared"] = ["Value"]
    items[1]["parameters"] = [p for p in items[1]["parameters"] if p["name"] != "Value"]
    rows, state = U.check_a365(items, nodes, ["Components/ICs", "Components/Power"])
    got = {(r["name"], r["status"], r["field"]): (r["plan"], r["workspace"]) for r in rows}
    assert ("CH340C", "parameter", "Added in A365") not in got                    # hand-added parameters are not drift
    assert not [k for k in got if k[0] == "CH340C"], got
    assert got[("AMS1117-3.3", "parameter", "Manufacturer")] == ("AMS", "Advanced Monolithic")
    assert got[("AMS1117-3.3", "parameter", "Value")] == ("(cleared)", "stale")
    assert got[("AMS1117-3.3", "symbol", "symbol")] == (items[1]["symbol"]["ref"], "LDO_OLD")
    assert got[("AMS1117-3.3", "folder", "folder")] == (items[1]["folder"], "Components\\Power")
    assert got[("TPS62133RGTR", "missing", "")] == ("", "")
    assert got[("OLD-PART", "not_in_plan", "folder")] == ("", "Components/ICs/Legacy")
    assert not [k for k in got if k[0] == "SOMEONE-ELSES"]
    assert state["CH340C"] == {"revision": "r1", "lifecycle": "Released", "symbols": ["sg1"], "footprints": ["fg1"]}
    ch2 = node("CH340C", "Components/ICs", {p["name"]: p["value"] for p in ch["details"]["parameters"]}, rev="r2", guid="g2",
               comment=ch["comment"], description=ch["description"])
    rows2, _ = U.check_a365(items[:1], [ch2], None, state)
    assert {(r["status"], r["field"]) for r in rows2} == {("revision", "revision"), ("revision", "symbols"), ("revision", "footprints")}, rows2


def test_check_a365_exit_codes():
    import os
    items = plan_items()[:1]
    ch, _ = ws_nodes(plan_items())
    with tempfile.TemporaryDirectory() as d:
        plan = Path(d) / "plan.json"
        plan.write_text(json.dumps(items))
        argv = ["check-a365", str(plan), "--workspace", "https://ws", "--state", str(Path(d) / "s.json"), "-o", str(Path(d) / "a")]
        old_tok, old_nexar = os.environ.pop("NEXAR_TOKEN", None), A.Nexar
        try:
            assert U.main(argv) == 2                                                  # no token
            os.environ["NEXAR_TOKEN"] = "t"
            A.Nexar = lambda token: FakeWorkspace([ch])
            assert U.main(argv) == 0 and "No changes." in (Path(d) / "a.md").read_text()
            assert json.loads((Path(d) / "s.json").read_text())["CH340C"]["revision"] == "r1"
            A.Nexar = lambda token: FakeWorkspace([])
            assert U.main(argv) == 1
            rows = list(csv.DictReader(open(Path(d) / "a.csv")))
            assert [(r["name"], r["status"]) for r in rows] == [("CH340C", "missing")], rows
            def boom(token):
                raise A.NexarError("401 Unauthorized")
            A.Nexar = boom
            assert U.main(argv) == 2
        finally:
            A.Nexar = old_nexar
            os.environ.pop("NEXAR_TOKEN", None)
            if old_tok is not None:
                os.environ["NEXAR_TOKEN"] = old_tok


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_upstream"]))
