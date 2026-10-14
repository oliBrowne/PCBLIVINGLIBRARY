"""Offline tests for ecadlib.a365_sync (two-way Altium 365 sync). Run:  python3 -m tests.test_a365_sync

FakeA365 extends test_offline's FakeNexar into a stateful workspace: desLibrary.components is served in pages,
parameter updates make a new revision (and a new component id, as Nexar documents), and every GraphQL operation
is validated against tests/nexar/schema.graphql. api.nexar.com is blocked from this sandbox: nothing here is live."""
import copy, io, json, os, tempfile, zipfile
from pathlib import Path

from ecadlib import a365_sync as S, altium365 as A, altium_lib as AL, dedupe as DD, release_check as RC
from tests.test_offline import FakeNexar

ROOT = Path(__file__).parent.parent
WS = "https://acme.365.altium.com"


def _schema():
    try:
        from graphql import build_schema
    except ImportError:
        return None
    return build_schema((ROOT / "tests/nexar/schema.graphql").read_text(), assume_valid_sdl=True)


SCHEMA = _schema()


def check_op(query, variables):
    if not SCHEMA:
        return
    from graphql import parse, validate
    from graphql.execution.values import get_variable_values
    doc = parse(query)
    errs = validate(SCHEMA, doc)
    assert not errs, (errs, query)
    op = next(d for d in doc.definitions if d.kind == "operation_definition")
    got = get_variable_values(SCHEMA, op.variable_definitions, variables)
    assert not isinstance(got, list), [e.message for e in got]


def node(name, params, folder="Components/ICs", sym="CH340C", fp="SOIC-16", rev="r1", guid="1", comment="=Value",
         description="", cid=None):
    return {"id": cid or f"id-{name}-{rev}", "name": name, "comment": comment, "description": description, "isManaged": True,
            "createdAt": "2026-01-01T00:00:00Z", "modifiedAt": "2026-01-02T00:00:00Z", "folder": {"path": folder},
            "revision": {"id": rev, "details": {"lifeCycleState": {"name": "Released"}, "createdAt": "2026-01-01T00:00:00Z",
                                                "modifiedAt": "2026-01-02T00:00:00Z"}},
            "details": {"parameters": [{"name": k, "value": v} for k, v in params.items()],
                        "symbols": [{"name": sym, "guid": "S" + guid, "dataDownloadUrl": f"https://dl/{sym}.SchLib", "folder": {"path": "Symbols"}}],
                        "footprints": [{"name": fp, "guid": "F" + guid, "dataDownloadUrl": f"https://dl/{fp}.PcbLib", "folder": {"path": "Footprints"}}]}}


class FakeA365(FakeNexar):
    """A workspace: list of component nodes, downloadable files by URL. Pages of at most two components."""
    def __init__(self, nodes, files=None):
        super().__init__({})
        self.nodes, self.files, self.queries, self.downloads, self.n = nodes, files or {}, [], [], 0

    def _by(self, key, v):
        return next((n for n in self.nodes if n[key] == v), None)

    def gql(self, query, variables=None):
        check_op(query, variables or {})
        self.queries.append(query)
        if "desComponentById" in query:
            return {"desComponentById": copy.deepcopy(self._by("id", variables["id"]))}
        if "where: {name: {eq: $name}}" in query:
            n = self._by("name", variables["name"])
            return {"desLibrary": {"components": {"nodes": [copy.deepcopy(n)] if n else []}}}
        i, k = int(variables.get("after") or 0), min(variables["first"], 2)
        return {"desLibrary": {"components": {"totalCount": len(self.nodes), "nodes": copy.deepcopy(self.nodes[i:i + k]),
                                              "pageInfo": {"hasNextPage": i + k < len(self.nodes), "endCursor": str(i + k)}}}}

    def find(self, ws, name):                  # what Nexar.find returns, through the validated query
        return (self.gql(A.Q_FIND, {"ws": ws, "name": name})["desLibrary"]["components"]["nodes"] or [None])[0]

    def component(self, cid):
        return self.gql(A.Q_BY_ID, {"id": cid})["desComponentById"]

    def update_params(self, cid, params, note, replace=False):
        check_op(A.M_UPDATE_PARAMS, {"input": {"componentId": cid, "releaseNote": note, "replaceExisting": replace, "parameters": params}})
        self.calls.append(("update", cid, params, replace))
        n = self._by("id", cid)
        assert n, f"no component {cid}"
        self.edit(n["name"], params=dict((p["name"], p["value"]) for p in params), replace=True)
        return self._by("name", n["name"])["id"]

    def edit(self, name, params=None, replace=False, **fields):
        """Someone changes a component in Altium 365: new revision and component id."""
        n = self._by("name", name)
        cur = {p["name"]: p["value"] for p in n["details"]["parameters"]}
        cur = dict(params) if replace else {**cur, **(params or {})}
        n["details"]["parameters"] = [{"name": k, "value": v} for k, v in cur.items() if v is not None]
        for k, v in fields.items():
            if k == "folder":
                n["folder"] = {"path": v}
            elif k in ("symbols", "footprints"):
                n["details"][k] = v
            else:
                n[k] = v
        self.n += 1
        n["revision"]["id"] = f"{n['revision']['id']}.{self.n}"
        n["id"] = f"id-{name}-{n['revision']['id']}"

    def download(self, url):
        self.downloads.append(url)
        return self.files[url]


def model_files():
    sym = AL.symbol_from_json(json.loads((ROOT / "examples/symbols/CH340C.json").read_text()))
    fp = AL.footprint_from_spec(json.loads((ROOT / "ecadlib/package_specs/SOIC-16_3.9x9.9mm_P1.27mm.json").read_text()))[0]
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d) / "s.SchLib", [sym]); AL.write_pcblib(Path(d) / "f.PcbLib", [fp])
        sch, pcb = (Path(d) / "s.SchLib").read_bytes(), (Path(d) / "f.PcbLib").read_bytes()
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:                       # one download comes zipped
        zf.writestr("Released/SOIC-16_3.9x9.9mm_P1.27mm.PcbLib", pcb)
    return sym["name"], fp["name"], {f"https://dl/{sym['name']}.SchLib": sch, f"https://dl/{fp['name']}.PcbLib": z.getvalue(),
                                     "https://dl/BROKEN.SchLib": b"<html>login</html>"}


def workspace():
    sym, fp, files = model_files()
    p = lambda mpn, **kw: {"Manufacturer": "ACME", "Manufacturer Part Number": mpn, **kw}
    nodes = [node(n, p(n, Value=n), sym=sym, fp=fp, guid=n) for n in ("PART-A", "PART-B", "PART-C", "PART-D", "PART-E")]
    nodes.append(node("RES-10K", p("RC0603FR-0710KL", Value="10k"), folder="Components\\Resistors", sym="BROKEN", fp=fp, guid="r"))
    return FakeA365(nodes, files)


def pull(d, ws, mode="pull", **kw):
    return S.run(mode, Path(d) / "plan.json", ws, WS, log=lambda *a: None, **kw)


def params(it):
    return {p["name"]: p["value"] for p in it["parameters"]}


def by(items):
    return {i["name"]: i for i in items}


# ---------------------------------------------------------------- tests
def test_queries_validate_against_nexar_schema():
    if not SCHEMA:
        print("skip test_queries_validate_against_nexar_schema (pip install graphql-core)"); return
    check_op(S.Q_COMPONENTS, {"ws": WS, "first": 50, "after": None})
    check_op(S.Q_COMPONENTS, {"ws": WS, "first": 50, "after": "abc"})
    check_op(A.Q_FIND, {"ws": WS, "name": "X"})
    check_op(A.Q_BY_ID, {"id": "X"})


def test_pull_imports_workspace_with_models_and_state():
    ws = workspace()
    with tempfile.TemporaryDirectory() as d:
        r = pull(d, ws)
        comps = [q for q in ws.queries if "A365Component" in q]
        assert len(comps) == 3, len(comps)                                       # 6 components, pages of 2
        items = by(json.loads((Path(d) / "plan.json").read_text()))
        assert set(items) == {"PART-A", "PART-B", "PART-C", "PART-D", "PART-E", "RES-10K"}
        a = items["PART-A"]
        assert a["status"] == "existing" and S.is_a365(a) and a["source"]["symbol"] == "a365:CH340C"
        assert a["a365"]["revision"] == "r1" and a["a365"]["symbols"] == ["SPART-A"] and a["a365"]["mirror"]
        assert params(a)["Manufacturer Part Number"] == "PART-A" and a["comment"] == "=Value"
        for k, ext in (("symbol", "SchLib"), ("footprint", "PcbLib")):
            f = Path(d) / a[k]["file"]
            assert f.exists() and f.parent.name == ext, a[k]
        assert AL.read_schlib(Path(d) / a["symbol"]["file"])[1][0]["name"] == "CH340C"          # read back
        assert AL.read_pcblib(Path(d) / a["footprint"]["file"])[0]["name"] == a["footprint"]["ref"]   # unzipped
        res = items["RES-10K"]
        assert res["folder"] == "Components/Resistors" and res["symbol"]["file"] is None
        assert any("not an Altium library file" in e for e in res["a365"]["model_errors"]), res["a365"]
        st = json.loads((Path(d) / "a365_state.json").read_text())
        assert st["PART-A"]["revision"] == "r1" and st["PART-A"]["symbols"] == ["SPART-A"] and st["PART-A"]["in_plan"]
        assert r["summary"] == {"new_in_workspace": 6} and r["unresolved"] == 0
        c = next(x for x in r["components"] if x["name"] == "RES-10K")
        assert {"name", "folder", "description", "comment", "parameters", "symbol", "footprint", "revision", "guid"} <= set(c)
        assert c["parameters"]["Value"] == "10k" and c["symbol"]["ref"] == "BROKEN" and c["revision"] == "r1" and c["mpn"] == "RC0603FR-0710KL"
        assert [x["name"] for x in S.components(list(items.values()))] == list(items)
        # pulled parts pass the release check untouched and push sees nothing to do
        rows = RC.apply(list(items.values()), d)
        assert {x["status"] for x in rows} == {"existing"} and all(i["status"] == "existing" for i in items.values())
        out = A.push(list(items.values()), ws, WS)
        assert {x["action"] for x in out} == {"up_to_date"} and not [c for c in ws.calls if c[0] in ("release", "update")], out
        again = pull(d, ws)
        assert again["summary"] == {"in_sync": 6} and len(ws.downloads) == 2 * 6 + 1          # only the broken one retried
        assert (Path(d) / "a365_sync.md").exists()


def scenario(d):
    """Pull, then edit both sides. Returns the fake workspace."""
    ws = workspace()
    pull(d, ws)
    plan = Path(d) / "plan.json"
    items = by(json.loads(plan.read_text()))
    ws.edit("PART-A", {"Value": "A-ws"})                                  # workspace only
    for p in items["PART-B"]["parameters"]:                               # local only
        if p["name"] == "Value":
            p["value"] = "B-local"
    ws.edit("PART-C", {"Value": "C-ws"})                                  # both, same field: conflict
    next(p for p in items["PART-C"]["parameters"] if p["name"] == "Value")["value"] = "C-local"
    ws.edit("PART-D", {"Manufacturer": "ACME Corp"})                      # both, different fields: merged
    next(p for p in items["PART-D"]["parameters"] if p["name"] == "Value")["value"] = "D-local"
    ws.nodes = [n for n in ws.nodes if n["name"] != "PART-E"]             # deleted in the workspace
    ws.nodes.append(node("NEW-F", {"Manufacturer Part Number": "NEW-F"}, guid="F"))
    ws.nodes.append(node("LINK-H", {"Manufacturer Part Number": "LINK-H", "Hand added": "keep", "Value": "old"}, guid="H"))
    del items["RES-10K"]                                                  # removed from the plan
    items["NEW-G"] = {"name": "NEW-G", "folder": "Components/ICs", "description": "", "comment": "*", "parameters": [],
                      "symbol": {"ref": "X", "file": None}, "footprint": {"ref": "Y", "file": None}, "status": "needs_altium_models"}
    items["LINK-H"] = {"name": "LINK-H", "folder": "Components/ICs", "description": "", "comment": "*",
                       "parameters": [{"name": "Value", "value": "new"}], "cleared": [],
                       "symbol": {"ref": "X", "file": None}, "footprint": {"ref": "Y", "file": None}, "status": "needs_altium_models"}
    plan.write_text(json.dumps(list(items.values())))
    return ws


def test_status_classifies_every_case_and_changes_nothing():
    with tempfile.TemporaryDirectory() as d:
        ws = scenario(d)
        before = (Path(d) / "plan.json").read_text(), (Path(d) / "a365_state.json").read_text()
        r = pull(d, ws, "status")
        assert ((Path(d) / "plan.json").read_text(), (Path(d) / "a365_state.json").read_text()) == before
        assert r["summary"] == {"changed_in_workspace": 1, "changed_locally": 1, "conflict": 1, "merged": 1,
                                "deleted_in_workspace": 1, "new_in_workspace": 1, "deleted_locally": 1, "new_local": 1,
                                "linked": 1}, r["summary"]
        rows = {(x["name"], x["status"], x["field"]): x for x in r["rows"]}
        c = rows[("PART-C", "conflict", "parameter Value")]
        assert (c["plan"], c["workspace"], c["base"]) == ("C-local", "C-ws", "PART-C"), c
        assert rows[("PART-B", "changed_locally", "parameter Value")]["detail"] == "push sends it"
        assert rows[("PART-D", "changed_in_workspace", "parameter Manufacturer")]["workspace"] == "ACME Corp"
        assert rows[("LINK-H", "linked", "parameter Value")]["workspace"] == "old"
        assert not any(k[0] == "LINK-H" and "Hand added" in k[2] for k in rows)      # a linked BOM item ignores extra params
        assert S.main(["status", str(Path(d) / "plan.json"), "--workspace", WS]) == 2  # no NEXAR_TOKEN


def test_pull_reports_conflicts_and_push_never_overwrites_them():
    with tempfile.TemporaryDirectory() as d:
        ws = scenario(d)
        r = pull(d, ws)
        assert r["unresolved"] == 1
        items = by(json.loads((Path(d) / "plan.json").read_text()))
        assert params(items["PART-A"])["Value"] == "A-ws"                                 # workspace edit came in
        assert params(items["PART-B"])["Value"] == "B-local"                              # local edit kept
        assert params(items["PART-C"])["Value"] == "C-local" and items["PART-C"]["a365"]["conflicts"]
        assert params(items["PART-D"]) == {**params(items["PART-D"]), "Value": "D-local", "Manufacturer": "ACME Corp"}
        assert items["PART-E"]["status"] == "deleted_in_workspace" and "NEW-F" in items and "RES-10K" not in items
        assert items["LINK-H"]["a365"]["mirror"] is False and items["LINK-H"]["status"] == "needs_altium_models"
        ws.edit("PART-B", {"Note": "added by a person"})                                  # after the pull: must survive
        out = {x["name"]: x for x in A.push(list(items.values()), ws, WS)}
        assert out["PART-C"]["action"] == "skip" and "reconcile" in out["PART-C"]["detail"]
        assert out["PART-B"]["action"] == "skip" and "edited in the workspace since the last pull" in out["PART-B"]["detail"]
        assert out["PART-D"]["action"] == "update_parameters" and out["PART-A"]["action"] == "up_to_date"
        assert out["PART-E"]["action"] == "skip" and "not in the workspace" in out["PART-E"]["detail"]
        assert out["LINK-H"]["action"] == "update_parameters" and out["NEW-G"]["action"] == "skip"
        assert not [c for c in ws.calls if c[0] == "release"]                             # nothing released twice
        h = {p["name"]: p["value"] for p in ws._by("name", "LINK-H")["details"]["parameters"]}
        assert h == {"Manufacturer Part Number": "LINK-H", "Hand added": "keep", "Value": "new"}, h
        b = {p["name"]: p["value"] for p in ws._by("name", "PART-B")["details"]["parameters"]}
        assert b["Value"] == "PART-B" and b["Note"] == "added by a person"
        # the push moved PART-D to its new revision: write that back, and the next push is a no-op, not a conflict
        assert S.save_meta(Path(d) / "plan.json", list(items.values()))
        again = {x["name"]: x for x in A.push(json.loads((Path(d) / "plan.json").read_text()), ws, WS)}
        assert again["PART-D"]["action"] == "up_to_date" and again["LINK-H"]["action"] == "up_to_date", again


def test_reconcile_workspace_wins_by_default_and_prefer_local():
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as d2:
        ws = scenario(d)
        pull(d, ws)
        for f in ("plan.json", "a365_state.json"):
            (Path(d2) / f).write_text((Path(d) / f).read_text())
        ws2 = copy.deepcopy(ws)
        r = pull(d, ws, "reconcile")
        items = by(json.loads((Path(d) / "plan.json").read_text()))
        c = items["PART-C"]
        assert params(c)["Value"] == "C-ws" and not c["a365"].get("conflicts") and r["unresolved"] == 0
        assert c["a365"]["overwritten"][0]["plan"] == "C-local"                       # the local value is not lost
        assert "PART-E" not in items and (Path(d) / "plan.json.pre-a365").exists()
        assert {x["name"]: x["action"] for x in A.push(list(items.values()), ws, WS)}["PART-C"] == "up_to_date"
        # --prefer local: the plan value stays and push sends it, after the workspace state was acknowledged
        assert pull(d2, ws2, "reconcile", prefer="local")["unresolved"] == 0
        items = by(json.loads((Path(d2) / "plan.json").read_text()))
        assert params(items["PART-C"])["Value"] == "C-local" and items["PART-E"]["status"] == "deleted_in_workspace"
        out = {x["name"]: x for x in A.push(list(items.values()), ws2, WS)}
        assert out["PART-C"]["action"] == "update_parameters", out["PART-C"]
        assert {p["name"]: p["value"] for p in ws2._by("name", "PART-C")["details"]["parameters"]}["Value"] == "C-local"


def test_models_changed_in_workspace_are_fetched_again_and_not_pushable_fields_reported():
    with tempfile.TemporaryDirectory() as d:
        ws = workspace()
        pull(d, ws)
        n0 = len(ws.downloads)
        sym = ws._by("name", "PART-A")["details"]["symbols"]
        ws.edit("PART-A", symbols=[{**sym[0], "guid": "S-new"}], description="new text")
        plan = Path(d) / "plan.json"
        items = by(json.loads(plan.read_text()))
        items["PART-B"]["folder"] = "Components/Elsewhere"
        plan.write_text(json.dumps(list(items.values())))
        r = pull(d, ws)
        rows = {(x["name"], x["field"]): x for x in r["rows"]}
        assert rows[("PART-A", "models")]["status"] == "changed_in_workspace"
        assert rows[("PART-A", "description")]["workspace"] == "new text"
        assert "not pushable" in rows[("PART-B", "folder")]["detail"]
        assert len(ws.downloads) == n0 + 2 + 1                                         # PART-A again, broken retried
        assert by(json.loads(plan.read_text()))["PART-A"]["a365"]["symbols"] == ["S-new"]


def test_push_finds_by_id_and_dry_run():
    ws = workspace()
    with tempfile.TemporaryDirectory() as d:
        pull(d, ws, models=False)
        items = by(json.loads((Path(d) / "plan.json").read_text()))
    it = items["PART-A"]
    it["name"] = "PART-A (renamed locally)"
    next(p for p in it["parameters"] if p["name"] == "Value")["value"] = "v2"
    it["parameters"] = [p for p in it["parameters"] if p["name"] != "Manufacturer"]   # removed locally: cleared
    dry = A.push([copy.deepcopy(it)], None, WS, dry_run=True)[0]
    assert dry["action"] == "update_parameters" and {"name": "Value", "value": "v2"} in dry["detail"]["parameters"]
    assert not any(p["name"] == "Manufacturer" for p in dry["detail"]["parameters"])
    res = A.push([it], ws, WS)[0]
    assert res["action"] == "update_parameters" and any("desComponentById" in q for q in ws.queries), res
    got = {p["name"]: p["value"] for p in ws._by("name", "PART-A")["details"]["parameters"]}
    assert got == {"Manufacturer Part Number": "PART-A", "Value": "v2"}, got
    assert it["a365"]["revision"] != "r1" and it["a365"]["id"] == ws._by("name", "PART-A")["id"]
    ws.nodes = [n for n in ws.nodes if n["name"] != "PART-A"]
    gone = A.push([it], ws, WS)[0]
    assert gone["action"] == "skip" and not [c for c in ws.calls if c[0] == "release"]


def test_dedupe_counts_workspace_components_as_library():
    ws = workspace()
    with tempfile.TemporaryDirectory() as d:
        pull(d, ws, models=False)
        plan = Path(d) / "plan.json"
        items = json.loads(plan.read_text())
        items.append({"name": "RES-0603-10k", "folder": "Components/Resistors", "description": "", "comment": "",
                      "parameters": [{"name": "Manufacturer Part Number", "value": "RC0603FR-0710KL-TR"}], "cleared": [],
                      "symbol": {"ref": "Resistor", "file": None}, "footprint": {"ref": "R_0603", "file": None}, "status": "ready"})
        plan.write_text(json.dumps(items))
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            r = DD.run(plan, do_merge=True)
        g = next(g for g in r["groups"] if g["kind"] == "mpn")
        assert g["keeper"] == "RES-10K" and g["members"][0]["origin"] == "a365", g
        got = by(json.loads(plan.read_text()))
        assert got["RES-0603-10k"]["status"] == "duplicate" and got["RES-0603-10k"]["duplicate_of"] == "RES-10K"
        assert got["RES-10K"]["status"] == "existing" and "duplicate_of" not in got["RES-10K"]
        col = DD.Collector(DD.load_config())
        col.add_a365(Path(d) / "a365_state.json")                                      # the state carries parameters
        assert {p["mpn"] for p in col.parts if p["name"] == "RES-10K"} == {"RC0603FR-0710KL"}


def test_librarian_reuses_workspace_parts():
    from tests import test_librarian as TL
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out"; out.mkdir()
        ws = FakeA365([node("ACME-TPS1234", {"Manufacturer": "ACME", "Manufacturer Part Number": "TPS1234DSGR"}, guid="t"),
                       node("UNUSED-1", {"Manufacturer Part Number": "UNUSED-1"}, guid="u")])
        S.run("pull", out / "plan.json", ws, WS, models=False, log=lambda *a: None)
        items, http = TL.run(d)
        assert not [u for u in http.calls if "TPS1234DSG" in u], [u for u in http.calls if "TPS1234" in u]
        assert "TPS1234DSGR" not in items and items["ACME-TPS1234"]["status"] == "existing"
        assert "reused for BOM part TPS1234DSGR" in items["ACME-TPS1234"]["librarian"]["notes"][0]
        assert items["UNUSED-1"]["status"] == "existing" and items["RES-0603-10k"]["status"] == "ready"
        plan = by(json.loads((out / "plan.json").read_text()))
        assert plan["ACME-TPS1234"]["a365"]["revision"] == "r1" and "UNUSED-1" in plan
        assert "ACME-TPS1234" in (out / "review.md").read_text()


def test_rules_param_names_and_folders():
    rules = {"parameters": {"names": {"Manufacturer Part Number": "MPN#"}},
             "folders": {"map": {"R": "Components/Resistors"}}}
    n = node("R1", {"MPN#": "ERJ-3EKF1002V"}, folder="Components\\Resistors")
    c = S.component(n, rules)
    assert c["mpn"] == "ERJ-3EKF1002V" and c["category"] == "R"
    assert S.component(n)["category"] == "Resistors" and S.component(n)["mpn"] == "R1"


def test_cli_mcp_and_workspace():
    import contextlib
    from ecadlib import app as APP, mcp_server as M
    with tempfile.TemporaryDirectory() as d:
        plan = Path(d) / "p" / "plan.json"
        old_tok, old_nexar = os.environ.pop("NEXAR_TOKEN", None), A.Nexar
        try:
            os.environ["NEXAR_TOKEN"] = "t"
            ws = workspace()
            A.Nexar = lambda token: ws
            with contextlib.redirect_stdout(io.StringIO()):
                assert S.main(["pull", str(plan), "--workspace", WS, "--no-models"]) == 0
                assert S.main(["status", str(plan), "--workspace", WS]) == 0
                ws.edit("PART-A", {"Value": "x"})
                assert S.main(["status", str(plan), "--workspace", WS]) == 1
                assert S.main(["reconcile", str(plan), "--workspace", WS, "--prefer", "local"]) == 0
                assert S.main(["status", str(plan), "--workspace", WS, "-o", str(Path(d) / "rep")]) == 0
                from ecadlib import cli
                assert cli.main(["a365", "status", str(plan), "--workspace", WS]) == 0
            assert (Path(d) / "rep.md").exists() and (plan.parent / "a365_state.json").exists()
            W = APP.Workspace(Path(d) / "w", providers=["digikey"])
            names = {t["name"] for t in M.TOOLS}
            assert {"a365_pull", "a365_status"} <= names
            call = lambda name, args: M.handle(W, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                   "params": {"name": name, "arguments": args}})["result"]
            ws.files = {}
            r = call("a365_pull", {"workspace": WS})
            assert not r["isError"], r
            body = json.loads(r["content"][0]["text"])
            assert body["summary"] == {"new_in_workspace": 6} and body["plan"] == "altium365/workspace_plan.json", body
            body = json.loads(call("a365_status", {"workspace": WS})["content"][0]["text"])
            assert body["summary"] == {"in_sync": 6} and body["state"] is None, body
            assert call("a365_status", {"workspace": ""})["isError"]
        finally:
            A.Nexar = old_nexar
            os.environ.pop("NEXAR_TOKEN", None)
            if old_tok is not None:
                os.environ["NEXAR_TOKEN"] = old_tok


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_a365_sync"]))
