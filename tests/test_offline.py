"""Offline self-tests (no network). Run:  python3 -m tests.test_offline

Exercises header mapping, value parsing, the IPC math, and the EasyEDA/JLC parsers via a
seeded HTTP cache. The seeded payloads are SYNTHETIC, shaped after the field paths that
easyeda2kicad 1.0.1 reads (result.dataStr.head.c_para, result.packageDetail.dataStr.shape,
'SVGNODE~{json}' with attrs.c_etype == 'outline3D'); they are not captured live responses."""
import io, json, logging, random, struct, tempfile, uuid
from pathlib import Path

from ecadlib import altium365 as A, altium_lib as AL, bom_triage as T, cfb, footprint_gen as F, providers as P
from ecadlib.httpcache import CachedHTTP
from ecadlib.kicad_index import KiCadIndex, parse_value


def test_headers():
    m = T.map_headers(["Ref Des", "QTY.", "Manufacturer Part Number", "Mfg", "LCSC Part #", "Package / Case", "comment"])
    assert m == {"designator": "Ref Des", "qty": "QTY.", "mpn": "Manufacturer Part Number", "manufacturer": "Mfg",
                 "lcsc": "LCSC Part #", "footprint": "Package / Case", "value": "comment"}, m
    m = T.map_headers(["Reference", "Quantity", "MPN", "Manufacturer", "JLCPCB Part"])
    assert m["mpn"] == "MPN" and m["lcsc"] == "JLCPCB Part" and m["manufacturer"] == "Manufacturer", m


def test_values():
    assert parse_value("10k") == 1e4 and parse_value("4k7") == 4700 and parse_value("10kΩ") == 1e4
    assert abs(parse_value("0.1uF") - parse_value("100nF")) < 1e-15 and parse_value("AMS1117") is None


def test_ipc_soic8():
    spec = json.loads(Path("ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    _, rep = F.build(spec)
    p1 = rep["pads"][0]
    assert abs(p1["w"] - 1.95) < 0.05 and abs(p1["h"] - 0.6) < 0.05 and abs(p1["x"] + 2.475) < 0.02, p1


def test_providers_seeded_cache():
    with tempfile.TemporaryDirectory() as d:
        http = CachedHTTP(d, offline=True)
        svg = json.dumps({"attrs": {"c_etype": "outline3D", "uuid": "abc123"}})
        ee = {"success": True, "result": {"title": "FAKE-PART", "dataStr": {"head": {"c_para": {
            "Manufacturer Part": "FAKE-PART", "Manufacturer": "ACME", "package": "SOT-23"}}, "shape": ["R~..."]},
            "packageDetail": {"title": "SOT-23-3_L2.9-W1.3", "dataStr": {"shape": ["PAD~...", "SVGNODE~" + svg]}}}}
        jlc = {"data": {"componentPageInfo": {"list": [{"componentCode": "C999999", "componentModelEn": "FAKE-PART",
                                                        "componentBrandEn": "ACME", "componentSpecificationEn": "SOT-23"}]}}}
        seed(http, "GET", P.EasyEDA.URL.format("C999999"), None, ee)
        seed(http, "POST", P.JLCPCBSearch.URL, json.dumps({"keyword": "FAKE-PART", "currentPage": 1, "pageSize": 10}), jlc)
        provs = [P.DigiKey(http), P.JLCPCBSearch(http), P.EasyEDA(http)]
        rows = T.triage([{"mpn": "FAKE-PART", "designator": "Q9"}], KiCadIndex([]), provs, http)
        r = rows[0]
        assert r["status"] == "available_easyeda" and r["src_3d"] == "yes" and r["lcsc"] == "C999999 (via MPN)", r
        assert "jlcpcb: MPN -> C999999" in r["notes"] and "not configured: digikey" in r["notes"], r


class FakeNexar:
    """Records calls instead of hitting api.nexar.com / files.nexar.com."""
    def __init__(self, existing):
        self.existing, self.calls = existing, []

    def release_defs(self, ws):
        return {"componentScheme": {"id": "cs"}, "symbolLifeCycle": {"id": "sl"}}

    def find(self, ws, name):                 # existing: name -> id, or name -> {"id", "details": {"parameters"}}
        e = self.existing.get(name)
        return {"id": e, "details": {"parameters": []}} if isinstance(e, str) else e

    def upload(self, path):
        self.calls.append(("upload", Path(path).name)); return f"id-{Path(path).name}"

    def find_model(self, ws, kind, name, folder):
        return None

    def folder_id(self, ws, path):
        return "fld-" + path

    def release(self, inp):
        self.calls.append(("release", inp))

    def update_params(self, cid, params, note, replace=False):
        self.calls.append(("update", cid, params, replace))
        for e in self.existing.values():
            if isinstance(e, dict) and e["id"] == cid:
                e["details"]["parameters"] = list(params) if replace else e["details"]["parameters"] + list(params)
        return cid + "-r2"


def test_altium365_plan_and_push():
    lines = [{"designator": "U1", "mpn": "CH340C", "manufacturer": "WCH", "lcsc": "C84681", "footprint": "SOIC-16",
              "description": "USB UART"},
             {"designator": "R1", "mpn": "0603WAF1002T5E", "value": "10k", "footprint": "R_0603_1608Metric",
              "description": "Resistor 10k 0603"}]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "SchLib").mkdir(); (Path(d) / "PcbLib").mkdir()
        (Path(d) / "SchLib" / "CH340C.SchLib").write_bytes(b"x"); (Path(d) / "PcbLib" / "SOIC-16.PcbLib").write_bytes(b"x")
        items = A.plan(lines, models=d)
        by = {i["name"]: i for i in items}
        assert by["CH340C"]["status"] == "ready" and by["CH340C"]["folder"] == "Components/ICs", by["CH340C"]
        res = next(i for i in items if i["folder"] == "Components/Resistors")
        assert res["status"] == "needs_altium_models" and res["symbol"]["ref"] == "Resistor", res
        assert {"name": "Supplier Part Number 1", "value": "C84681"} in by["CH340C"]["parameters"]
        fake = FakeNexar({res["name"]: "cmp-1"})
        out = {r["name"]: r for r in A.push(items, fake, "https://ws")}
        assert out["CH340C"]["action"] == "release" and out[res["name"]]["action"] == "update_parameters", out
        rel = next(c[1] for c in fake.calls if c[0] == "release")
        assert rel["symbolFiles"] == [{"fileId": "id-CH340C.SchLib", "relativePath": "Released\\CH340C.SchLib"}], rel
        assert rel["componentRevisionNamingSchemeNodeId"] == "cs" and rel["symbolLifeCycleDefinitionNodeId"] == "sl"
        dry = A.push(items, None, "https://ws", dry_run=True)
        assert {r["action"] for r in dry} == {"release", "skip"}, dry
        assert rel["symbolReleaseFolder"] == "Components/Models/Symbols", rel
        # a test root ([folders] root) holds the symbols and footprints too, not only the components
        from ecadlib import rules as RU
        import copy
        rules = copy.deepcopy(RU.DEFAULTS); rules["folders"]["root"] = "ecadlib-test-47"
        inp = next(r for r in A.push(A.plan(lines, models=d, root="ecadlib-test-47"), None, "https://ws", dry_run=True,
                                     rules=rules) if r["action"] == "release")["detail"]["input"]
        # componentReleaseFolder is one folder name: "ecadlib-test-47/ICs" failed live with AddALU_FoldersInvalid
        # folder HRID ("/" not permitted), so the parent goes by id
        assert (inp["componentParentFolderId"], inp["componentReleaseFolder"]) == ("<id of ecadlib-test-47>", "ICs"), inp
        assert rel["componentParentFolderId"] == "fld-Components" and rel["componentReleaseFolder"] == "ICs", rel
        assert (inp["symbolReleaseFolder"], inp["footprintReleaseFolder"]) == (
            "ecadlib-test-47/Models/Symbols", "ecadlib-test-47/Models/Footprints"), inp


def test_altium365_folder_id_finds_or_creates_each_level():
    """A nested component folder resolves level by level: existing folders by name under their parent (any case),
    missing ones created under the right parent (by its GUID, live schema 2026-10) and read back for their node id;
    the folder list is read once per run plus once per create."""
    class N(A.Nexar):
        def __init__(self):
            self.token, self.calls = "t", []
            self.lib = [{"id": "f-c", "folderId": "G-c", "name": "Components", "parent": None},
                        {"id": "f-ic", "folderId": "G-ic", "name": "ICs", "parent": {"id": "f-c"}},
                        {"id": "f-x", "folderId": "G-x", "name": "ecadlib-test-47", "parent": {"id": "f-c"}}]  # other parent
        def gql(self, query, variables=None):
            self.calls.append(query)
            if query == A.Q_FOLDERS:
                return {"desLibrary": {"folders": [dict(f) for f in self.lib]}}
            inp = variables["input"]
            assert query == A.M_CREATE_FOLDER and inp["folderType"] == "GENERIC_FOLDER" and "parentId" not in inp, inp
            par = next((f["id"] for f in self.lib if f["folderId"] == inp["parentFolderId"]), None)
            assert (par is None) == (inp["parentFolderId"] is None), inp
            n = len(self.lib)
            self.lib.append({"id": f"f-{n}", "folderId": f"G-{n}", "name": inp["name"], "parent": {"id": par} if par else None})
            return {"desCreateFolder": {"folderId": f"G-{n}", "errors": []}}
    n = N()
    assert n.folder_id("https://ws", "components/ICs") == "f-ic"
    assert n.folder_id("https://ws", "ecadlib-test-47") == "f-3"      # a new root folder, not Components/ecadlib-test-47
    assert n.folder_id("https://ws", "ecadlib-test-47/Sub") == "f-4" and n.lib[-1]["parent"] == {"id": "f-3"}
    assert n.folder_id("https://ws", "ecadlib-test-47/Sub") == "f-4"  # found, not created again
    assert n.calls.count(A.Q_FOLDERS) == 3 and n.calls.count(A.M_CREATE_FOLDER) == 2, n.calls
    assert A.split_folder("ecadlib-test-47/ICs") == ("ecadlib-test-47", "ICs") and A.split_folder("ICs") == ("", "ICs")
    item = {**A.plan([{"designator": "U1", "mpn": "CH340C", "footprint": "SOIC-16"}])[0], "folder": "ICs"}
    inp = A.release_input(item, "https://ws", {}, ([], []))
    assert "componentParentFolderId" not in inp and inp["componentReleaseFolder"] == "ICs", inp


def test_altium365_release_retries_the_parent_folder_by_guid():
    """Nexar does not document whether componentParentFolderId is the folder's node id or its GUID: a folder error with
    the node id is retried once with the GUID; any other error is reported as is."""
    class C:
        def __init__(self, fail):
            self.fail, self.sent = fail, []
        def release_defs(self, ws): return {}
        def find(self, ws, name): return None
        def upload(self, p): return "f"
        def find_model(self, ws, kind, name, folder): return None
        def folder(self, ws, path): return {"id": "node-1", "guid": "GUID-1"}
        def folder_id(self, ws, path): return "node-1"
        def release(self, inp):
            self.sent.append(inp["componentParentFolderId"])
            if inp["componentParentFolderId"] == "node-1" and self.fail:
                raise A.NexarError(self.fail)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "SchLib").mkdir(); (Path(d) / "PcbLib").mkdir()
        (Path(d) / "SchLib" / "CH340C.SchLib").write_bytes(b"x"); (Path(d) / "PcbLib" / "SOIC-16.PcbLib").write_bytes(b"x")
        items = [i for i in A.plan([{"designator": "U1", "mpn": "CH340C", "footprint": "SOIC-16"}], models=d) if i["status"] == "ready"]
        for fail, sent, action in ((None, ["node-1"], "release"), ("Parent folder not found", ["node-1", "GUID-1"], "release"),
                                   ("Invalid revision naming scheme", ["node-1"], "error")):
            c = C(fail)
            assert A.push(items, c, "https://ws")[0]["action"] == action and c.sent == sent, (fail, c.sent)


def test_altium365_symbol_named_like_its_component_gets_a_suffix():
    """Live 2026-10-18: SS34's symbol item "SS34" failed with "Duplicate ID is not allowed" (item names share one
    namespace); a model item named like its component gets -SYM / -FP, other names are kept."""
    it = {"name": "SS34", "symbol": {"ref": "SS34"}, "footprint": {"ref": "ss34"}}
    assert A.model_item_names(it) == ("SS34-SYM", "ss34-FP")
    assert A.model_item_names({"name": "CAP-0402-100nF", "symbol": {"ref": "Capacitor"}, "footprint": {"ref": "C_0402"}}) == (
        "Capacitor", "C_0402")
    item = {**A.plan([{"designator": "U1", "mpn": "CH340C", "footprint": "SOIC-16"}])[0]}
    inp = A.release_input(item, "https://ws", {}, ([], []))
    assert inp["symbolItemName"] == "CH340C-SYM" and inp["footprintItemName"] == "SOIC-16", inp


def test_altium365_shared_symbol_is_released_once_then_linked():
    """Live 2026-10-18: the second capacitor failed with AddALU_ItemsDuplicate item HRID: Capacitor. A symbol or
    footprint already in the workspace folder (released earlier, or by an earlier part of this run) is linked by
    its node id and not uploaded again; the dry run shows the same."""
    lines = [{"designator": f"C{i}", "mpn": mpn, "value": v, "footprint": fp, "description": f"Capacitor {v}"}
             for i, (mpn, v, fp) in enumerate([("CL05B104KO5NNNC", "100nF", "C_0402"), ("CL05A105KA5NQNC", "1uF", "C_0402"),
                                               ("CL10A106KP8NNNC", "10uF", "C_0603")], 1)]
    class W:                                   # a workspace: what was released is found by revision name
        def __init__(self): self.rel, self.up = [], []
        def release_defs(self, ws): return {}
        def find(self, ws, name): return None
        def upload(self, p): self.up.append(Path(p).name); return "id-" + Path(p).name
        def folder(self, ws, path): return {"id": "node-" + path, "guid": "G"}
        def folder_id(self, ws, path): return "node-" + path
        def find_model(self, ws, kind, name, folder):
            k = kind[:-1]
            hit = [r for r in self.rel if r.get(k + "ItemName") == name and r[k + "ReleaseFolder"] == folder]
            return f"{k}-rev:{name}" if hit else None
        def release(self, inp): self.rel.append(inp)
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "SchLib").mkdir(); (Path(d) / "PcbLib").mkdir()
        (Path(d) / "SchLib" / "Capacitor.SchLib").write_bytes(b"x")
        for fp in ("C_0402", "C_0603"):
            (Path(d) / "PcbLib" / f"{fp}.PcbLib").write_bytes(b"x")
        items = A.plan(lines, models=d, root="ecadlib-test-47")
        assert [i["status"] for i in items] == ["ready"] * 3 and {i["symbol"]["ref"] for i in items} == {"Capacitor"}, items
        w = W()
        out = A.push(items, w, "https://ws")
        assert [r["action"] for r in out] == ["release"] * 3, out
        first, second, third = w.rel
        assert first["symbolFiles"] and "symbol" not in first and first["symbolItemName"] == "Capacitor", first
        assert second["symbol"] == {"id": "symbol-rev:Capacitor"} and second["symbolFiles"] == [] and second["symbolItemName"] is None
        assert second["footprints"] == [{"id": "footprint-rev:C_0402"}] and second["footprintFiles"] == [], second
        assert third["symbol"] == {"id": "symbol-rev:Capacitor"} and third["footprintItemName"] == "C_0603" and third["footprintFiles"]
        assert sorted(w.up) == ["C_0402.PcbLib", "C_0603.PcbLib", "Capacitor.SchLib"], w.up      # each file once
        dry = [r["detail"]["input"] for r in A.push(items, None, "https://ws", dry_run=True)]
        assert "symbol" not in dry[0] and dry[1]["symbol"] == {"id": "<id of Capacitor>"} and dry[2]["footprintFiles"], dry


def test_altium365_find_model_matches_the_item_by_revision_name_in_its_folder():
    """Symbols and footprints are listed by revision name (Capacitor-1): the newest revision of exactly that item in
    the release folder is linked; a longer name (Capacitor-SYM-1) is another item; the same item in another folder
    is reported, never linked."""
    class N(A.Nexar):
        def __init__(self, nodes): self.token, self.nodes, self.q = "t", nodes, []
        def gql(self, query, variables=None):
            self.q.append((query, variables))
            return {"desLibrary": {"symbols": {"nodes": [n for n in self.nodes if n["name"].startswith(variables["prefix"])]}}}
    f = {"path": "ecadlib-test-47/Models/Symbols"}
    n = N([{"id": "a", "name": "Capacitor-1", "releasedAt": "2026-10-18T00:49:53Z", "folder": f},
           {"id": "b", "name": "Capacitor-2", "releasedAt": "2026-10-19T00:00:00Z", "folder": f},
           {"id": "c", "name": "Capacitor-SYM-1", "releasedAt": "2026-10-20T00:00:00Z", "folder": f}])
    assert n.find_model("https://ws", "symbols", "Capacitor", "ecadlib-test-47/Models/Symbols") == "b"
    assert n.q[0][0] == A.Q_MODELS["symbols"] and n.q[0][1]["prefix"] == "Capacitor-", n.q
    assert n.find_model("https://ws", "symbols", "Capacitor-SYM", "/ecadlib-test-47/models/symbols/") == "c"
    assert n.find_model("https://ws", "symbols", "Resistor", "ecadlib-test-47/Models/Symbols") is None
    other = N([{"id": "x", "name": "Capacitor-1", "releasedAt": "", "folder": {"path": "Components/Models/Symbols"}}])
    try:
        other.find_model("https://ws", "symbols", "Capacitor", "ecadlib-test-47/Models/Symbols")
        raise AssertionError("linked a model from another folder")
    except A.NexarError as e:
        assert "Components/Models/Symbols" in str(e), e


def test_altium365_find_ignores_other_items_the_name_filter_returns():
    """Live 2026-10-18: components(where: {name: {eq: "AMS1117-3.3"}}) returned Altium's sample part
    CMP-017-000002-1, so a push would have updated it as ours. Only a node named as the item (or its revision) counts."""
    sample = {"id": "s", "name": "CMP-017-000002-1"}
    assert A.pick_named([sample], "AMS1117-3.3") is None
    assert A.pick_named([sample, {"id": "o", "name": "AMS1117-3.3-1"}], "AMS1117-3.3")["id"] == "o"
    assert A.pick_named([{"id": "p", "name": "SS34"}], "ss34")["id"] == "p"
    assert A.pick_named([{"id": "q", "name": "SS34-SYM-1"}], "SS34") is None and A.pick_named([], "x") is None
    class N(A.Nexar):
        def __init__(self): self.token = "t"
        def gql(self, query, variables=None): return {"desLibrary": {"components": {"nodes": [sample]}}}
    assert N().find("https://ws", "AMS1117-3.3") is None


def test_altium365_ops_match_nexar_schema():
    """Every query/mutation, and a release input built by plan(), validates against Nexar's schema."""
    try:
        from graphql import build_schema, parse, validate
        from graphql.execution.values import get_variable_values
    except ImportError:
        print("skip test_altium365_ops_match_nexar_schema (pip install graphql-core)"); return
    schema = build_schema(Path("tests/nexar/schema.graphql").read_text(), assume_valid_sdl=True)
    for op in (A.Q_WORKSPACES, A.Q_DEFS, A.Q_FIND, A.M_RELEASE, A.M_UPDATE_PARAMS, A.Q_FOLDERS, A.M_CREATE_FOLDER,
               *A.Q_MODELS.values()):
        assert not validate(schema, parse(op)), op
    item = A.plan([{"designator": "U1", "mpn": "CH340C", "footprint": "SOIC-16"}])[0]
    inp = A.release_input(item, "https://ws", {}, ([("a.SchLib", "f1")], [("b.PcbLib", "f2")]), parent_id="fld-1")
    var_defs = parse(A.M_RELEASE).definitions[0].variable_definitions
    res = get_variable_values(schema, var_defs, {"input": inp})
    assert not isinstance(res, list), [e.message for e in res]
    linked = A.release_input(item, "https://ws", {}, ([], []), parent_id="fld-1", existing=("sym-1", "fp-1"))
    res = get_variable_values(schema, var_defs, {"input": linked})
    assert not isinstance(res, list), [e.message for e in res]


def test_cfb_writer_reads_back_cleanly():
    """Random storage trees (mini + regular streams, empty streams, >109 FAT sectors -> DIFAT) read back
    byte-exact with olefile in strict mode, with no logged warnings, and every directory is a valid BST."""
    try:
        import olefile
    except ImportError:
        print("skip test_cfb_writer_reads_back_cleanly (pip install olefile)"); return
    rnd = random.Random(7)
    log = io.StringIO(); h = logging.StreamHandler(log)
    lg = logging.getLogger("olefile"); old_level = lg.level
    lg.addHandler(h); lg.setLevel(logging.WARNING)    # olefile mutes its logger (level 51) by default

    def mk(depth):
        t = {}
        for i in range(rnd.randint(1, 9)):
            k = rnd.choice(["Data", "Header", "Parameters", f"S{i}", f"long_name_{i}_" + "x" * rnd.randint(0, 14)])
            if k.upper() not in {x.upper() for x in t}:
                t[k] = mk(depth + 1) if depth < 2 and rnd.random() < 0.3 else bytes(rnd.getrandbits(8) for _ in range(
                    rnd.choice([0, 1, 64, 65, 4095, 4096, 5000, rnd.randint(0, 3000)])))
        return t

    def walk(t, pre=""):
        for k, v in t.items():
            yield from walk(v, pre + k + "/") if isinstance(v, dict) else [(pre + k, v)]
    trees = [mk(0) for _ in range(40)] + [{"big": b"\x5a" * 7_500_000, "small": b"abc"},
                                          {"\U0001F600" * 15: b"x", "\uffff": b"y", "\U00010000": b"z"}]
    try:
        _check_trees(olefile, trees, walk)
    finally:
        lg.removeHandler(h); lg.setLevel(old_level)
    assert log.getvalue() == "", log.getvalue()
    for bad in ({"": b""}, {"\U0001F600" * 16: b""}):
        try:
            cfb.to_bytes(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted illegal name {list(bad)}")


def _check_trees(olefile, trees, walk):
    for t in trees:
        o = olefile.OleFileIO(io.BytesIO(cfb.to_bytes(t)), raise_defects=olefile.DEFECT_INCORRECT)
        assert all(o.openstream(p).read() == v for p, v in walk(t))
        for d in o.direntries:
            if d is not None and d.entry_type in (1, 5):     # in-order walk of each child tree must be sorted
                seq, stack, i = [], [], d.sid_child
                while stack or i != cfb.NOSTREAM:
                    while i != cfb.NOSTREAM:
                        stack.append(i); i = o.direntries[i].sid_left
                    i = stack.pop(); seq.append(cfb._key(o.direntries[i].name)); i = o.direntries[i].sid_right
                assert seq == sorted(seq) and len(seq) == len(d.kids), d.name


# Bytes Altium Designer wrote (AltiumSharp TestData, Apache-2.0: QFN-24 pad "2" and a Top Overlay track)
ALTIUM_PAD = bytes.fromhex(
    "020000000132010000000005000000047c267c300100000000ca000000010c00ffffffffffffffffffff0196f4ff6b8104005ecd0100"
    "bc9a03005ecd0100bc9a03005ecd0100bc9a03000000000001010100000000008056400100000000000000a08601000400a0860100"
    "400d0300400d030000000000409c0000000000000000000101010000000000000000000001000001000000409c0000008785b3db7b"
    "57c2449ae515f1d8db0355ec3be13f0a809446bead96d0f4dbfa5200000000ffffff7fffffff7f00011a000000000000000000000001"
    "030000000000000000000000000000000000000000")
ALTIUM_TRACK = bytes.fromhex("31000000210c00ffffffffffffffffffff95c9f5ff6b360a0095c9f5ffae500c00943301000000000000"
                             "0000000600030100000000")


def test_altium_units_and_records_match_altium_bytes():
    assert AL.mm(0.0254) == 10000 and AL.mm(1) == 393701 and AL.mm(-2.54) == -1000000
    x1, y1, x2, y2, w = struct.unpack_from("<5i", ALTIUM_TRACK, 17)
    assert AL.track_rec(x1, y1, x2, y2, w, AL.TOP_OVERLAY) == b"\x04" + ALTIUM_TRACK
    main = ALTIUM_PAD[29:29 + 202]
    x, y, w, h = struct.unpack_from("<4i", main, 13)
    p = {"designator": "2", "x": x, "y": y, "w": w, "h": h, "shape": "round", "rot": 90.0, "paste": None}
    mine = AL.pad_rec(p, uuid.UUID(bytes_le=main[126:142]), uuid.UUID(bytes_le=main[142:158]))[1:]
    diff = [i - 29 for i in range(len(mine)) if mine[i] != ALTIUM_PAD[i]]
    assert len(mine) == len(ALTIUM_PAD) and diff == [103], diff     # 103 = paste-mask cache-valid flag (Altium revalidates)


def _missing(mod, test):
    """True (after reporting a skip) when an optional dev dependency isn't installed; under pytest, skips."""
    import importlib.util
    if importlib.util.find_spec(mod):
        return False
    try:
        import pytest
    except ImportError:
        print(f"skip {test} (pip install {mod})"); return True
    pytest.skip(f"{mod} not installed")


def test_pcblib_writer_roundtrip():
    if _missing("olefile", "test_pcblib_writer_roundtrip"):
        return
    specs = ["SOIC-8_3.9x4.9mm_P1.27mm", "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm"]
    fps = [AL.footprint_from_spec(json.loads(Path(f"ecadlib/package_specs/{n}.json").read_text()))[0] for n in specs]
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "two.PcbLib", fps)
        got = {f["name"]: f for f in AL.read_pcblib(Path(d) / "two.PcbLib")}
    q = got[specs[1]]
    assert q["storage"] == specs[1][:31] and q["params"]["PATTERN"] == specs[1] and q["params"]["HEIGHT"] == "31.4961mil", q["params"]
    pads = [r for r in q["records"] if r["type"] == "pad"]
    _, rep = F.build(json.loads(Path(f"ecadlib/package_specs/{specs[1]}.json").read_text()))
    p1, r1 = pads[0], rep["pads"][0]
    assert len(pads) == 33 and (p1["x"], p1["y"], p1["w"], p1["h"]) == (AL.mm(r1["x"]), AL.mm(-r1["y"]), AL.mm(r1["w"]), AL.mm(r1["h"]))
    assert p1["shape"] == 9 and p1["corner"] == 50 and p1["paste_mode"] == 1
    ep = pads[-1]
    assert ep["designator"] == "33" and ep["shape"] == 2 and ep["paste_mode"] == 2 and ep["paste"] < -ep["w"] // 2
    layers = {(r["type"], r["layer"]) for r in q["records"]}
    assert {("fill", AL.TOP_PASTE), ("track", AL.TOP_OVERLAY), ("track", AL.MECH13), ("track", AL.MECH15), ("region", AL.TOP_OVERLAY)} <= layers
    assert q["header_count"] == len(q["records"])


def test_schlib_writer_roundtrip():
    if _missing("olefile", "test_schlib_writer_roundtrip"):
        return
    sym = AL.symbol_from_json(json.loads(Path("examples/symbols/CH340C.json").read_text()))
    with tempfile.TemporaryDirectory() as d:
        AL.write_schlib(Path(d) / "x.SchLib", [sym, AL.resistor_symbol(), AL.capacitor_symbol()])
        hdr, syms = AL.read_schlib(Path(d) / "x.SchLib")
    assert hdr["COMPCOUNT"] == "3" and [s["name"] for s in syms] == ["CH340C", "Resistor", "Capacitor"]
    pins = [r for r in syms[0]["records"] if r["RECORD"] == "2"]
    assert len(pins) == 16 and {p["num"]: p["name"] for p in pins}["5"] == "UD+"
    assert all(p["x"] % 10 == 0 and p["y"] % 10 == 0 for p in pins)              # on the 100 mil grid
    impl = next(r for r in syms[0]["records"] if r["RECORD"] == "45")
    assert impl["MODELNAME"] == "SOIC-16_3.9x9.9mm_P1.27mm" and impl["MODELTYPE"] == "PCBLIB"
    # a pin record Altium wrote (AltiumSharp TestData PE42442 pin 1) re-encodes byte for byte
    real = bytes.fromhex("26000001" "020000000001000000000000000104" "3a1e00ceffd8ff00000000" "03474e44" "0131" "00" "037c267c" "00")
    assert AL.pin_rec("1", "GND", "passive", 2, -50, -40, 30) == real
    for bad in (lambda: AL.schlib_bytes([AL.ic_symbol("Storage", [])]),
                lambda: AL.ic_symbol("X", [{"num": "1", "name": "A", "side": "Left"}]),
                lambda: AL.pcblib_bytes([dict(name="Library", pads=[], tracks=[], fills=[], regions=[])])):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("reserved name / unknown pin side accepted")


def test_plan_ready_with_generated_models():
    lines = [{"designator": "U1", "mpn": "CH340C", "footprint": "SOIC-16_3.9x9.9mm_P1.27mm"}]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "SchLib").mkdir(); (Path(d) / "PcbLib").mkdir()
        AL.main(["schlib", "examples/symbols/CH340C.json", "-o", f"{d}/SchLib"])
        AL.main(["pcblib", "ecadlib/package_specs/SOIC-16_3.9x9.9mm_P1.27mm.json", "-o", f"{d}/PcbLib"])
        assert A.plan(lines, models=d)[0]["status"] == "ready"
        try:
            import pyaltiumlib                        # independent reader, if installed
        except ImportError:
            return
        pcb = pyaltiumlib.read(f"{d}/PcbLib/SOIC-16_3.9x9.9mm_P1.27mm.PcbLib")
        sch = pyaltiumlib.read(f"{d}/SchLib/CH340C.SchLib")
        assert len([r for r in pcb.Parts[0].Records if type(r).__name__ == "PcbPad"]) == 16
        assert len([r for r in sch.Parts[0].Records if type(r).__name__ == "SchPin"]) == 16


def seed(http, method, url, body, payload):
    f = http.dir / f"{http._key(method, url, body)}.json"
    f.write_text(json.dumps({"url": url, "status": 200, "text": json.dumps(payload)}))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)


def test_nexar_401_says_when_the_token_expired():
    # 2026-10-19: a token that worked for a push at 21:48 was refused hours later with a hint about scopes only
    import base64
    from ecadlib import altium365 as A
    jwt = lambda claims: "h." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".s"
    old = A.token_hint(jwt({"exp": 1_760_000_000}), now=1_760_000_100)
    assert "expired at 2025-10-09 08:53 UTC" in old and "ecadlib keys set NEXAR_TOKEN" in old, old
    for tok in (jwt({"exp": 1_760_000_200}), "not-a-jwt", None, jwt({})):
        assert "design.domain" in A.token_hint(tok, now=1_760_000_100), tok
