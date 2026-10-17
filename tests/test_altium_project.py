"""Offline tests for ecadlib.altium_project: .SchDoc records, .PrjPcb documents/variants/parameters, ActiveBOM
overrides, the bom_triage.read_bom hook, the `project` / `where-used` commands, the Workspace + MCP tools.

Synthetic .SchDoc files are written with ecadlib.cfb and altium_lib's record blocks (the layout AltiumSharp's
SchDocReader reads). When AltiumSharp's TestData is present (../ref/AltiumSharp or $ALTIUMSHARP), real Altium-saved
.SchDoc files are read too. Run:  python3 -m tests.test_altium_project"""
import contextlib, csv, io, json, os, shutil, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_lib as AL, altium_project as AP, bom_triage as T, cfb, cli

SCH_HDR = "Protel for Windows - Schematic Capture Binary File Version 5.0"
REF_DIRS = [Path(os.environ.get("ALTIUMSHARP", "/nonexistent")), Path("/home/claude/ref/AltiumSharp"),
            Path(__file__).resolve().parents[2] / "ref" / "AltiumSharp"]


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
        r = fn(*a, **k)
    return r, out.getvalue()


def comp(des, libref, uid, params=(), footprints=(), current=0, did="", lib="Parts.DbLib", desc="", kind=None, extra=(),
         comment=None, part=1, partcount=2):
    return {"des": des, "libref": libref, "uid": uid, "params": list(params), "fps": list(footprints), "cur": current, "did": did,
            "lib": lib, "desc": desc, "kind": kind, "extra": list(extra), "comment": comment, "part": part, "pc": partcount}


def write_schdoc(path, comps, raw_tail=b"", symbols=()):
    """Altium-shaped FileHeader: header, sheet record 31, then per component RECORD=1 + its owned children, then per
    (sheet name, file name) a sheet symbol RECORD=15 with its RECORD=32 name and RECORD=33 file name."""
    recs = [AL.pblock("|RECORD=31|FontIdCount=1|Size1=10|FontName1=Times New Roman|SystemFont=1")]
    for c in comps:
        i = len(recs)
        kv = [("RECORD", 1), ("LibReference", c["libref"])] + ([("ComponentDescription", c["desc"])] if c["desc"] else [])
        kv += [("PartCount", c["pc"]), ("DisplayModeCount", 1), ("IndexInSheet", i), ("OwnerPartId", -1), ("CurrentPartId", c["part"]),
               ("LibraryPath", "*"), ("SourceLibraryName", c["lib"]), ("TargetFileName", "*"), ("UniqueID", c["uid"]),
               ("PartIDLocked", "T"), ("DesignItemId", c["did"] or c["libref"])] + c["extra"]
        if c["kind"] is not None:
            kv.append(("ComponentKindVersion2", c["kind"]))
        recs.append(AL.pblock(AL.params(kv)))
        recs.append(AL.pblock(f"|RECORD=2|OwnerIndex={i}|OwnerPartId=1|Name=A|Designator=1"))
        for n, v in c["params"]:
            recs.append(AL.pblock(AL.params([("RECORD", 41), ("OwnerIndex", i), ("IsHidden", "T"), ("Text", v), ("Name", n)])))
        recs.append(AL.pblock(AL.params([("RECORD", 34), ("OwnerIndex", i), ("Text", c["des"]), ("Name", "Designator")])))
        recs.append(AL.pblock(AL.params([("RECORD", 41), ("OwnerIndex", i), ("Text", c["comment"] or c["libref"]), ("Name", "Comment")])))
        il = len(recs)
        recs.append(AL.pblock(f"|RECORD=44|OwnerIndex={i}"))
        for k, fp in enumerate(c["fps"]):
            j = len(recs)
            kv = [("RECORD", 45), ("OwnerIndex", il), ("ModelName", fp), ("ModelType", "PCBLIB"), ("DatafileCount", 1),
                  ("ModelDatafileEntity0", fp), ("ModelDatafileKind0", "PCBLib")] + ([("IsCurrent", "T")] if k == c["cur"] else [])
            recs.append(AL.pblock(AL.params(kv)))
            recs += [AL.pblock(f"|RECORD=46|OwnerIndex={j}"), AL.pblock(f"|RECORD=48|OwnerIndex={j}")]
    for name, fname in symbols:
        i = len(recs)
        recs.append(AL.pblock("|RECORD=15|Location.X=100|Location.Y=100|XSize=40|YSize=30|UniqueID=SYMBOLAA"))
        recs.append(AL.pblock(AL.params([("RECORD", 32), ("OwnerIndex", i), ("Text", name)])))
        recs.append(AL.pblock(AL.params([("RECORD", 33), ("OwnerIndex", i), ("Text", fname)])))
    data = AL.pblock(f"|HEADER={SCH_HDR}|WEIGHT={len(recs)}|MINORVERSION=9|UNIQUEID=ABCDEFGH") + b"".join(recs) + raw_tail
    Path(path).write_bytes(cfb.to_bytes({"FileHeader": data, "Storage": AL.pblock("|HEADER=Icon storage"),
                                         "Additional": AL.pblock(f"|HEADER={SCH_HDR}")}))
    return Path(path)


def mpn(m, mfr="Murata"):
    return [("Manufacturer Part Number", m), ("Manufacturer", mfr)]


def board(d, name="Board", variants=True, extra_docs=(), a365_rev=("1", "4B23757F-C4E5-4A42-B51C-6019C34CDDAE")):
    """Two sheets + a .PrjPcb with two variants."""
    d = Path(d)
    (d / "Sheets").mkdir(parents=True, exist_ok=True)
    tp_extra = [("VaultGUID", "dd5c2b2b-8d4d-4ebe-ba18-cba45e8f7d1b"), ("ItemGUID", "9BE5D5AE-9614-40AA-9CE5-D79A88B3AAFE"),
                ("RevisionGUID", a365_rev[1])]
    write_schdoc(d / "Sheets" / "Power.SchDoc", [
        comp("C1", "Capacitor", "CAPAAAAA", mpn("GRM188R71C104KA01D") + [("Value", "100nF")], ["CAP 0402", "CAP 0603"], 1, comment="=Value"),
        comp("C2", "Capacitor", "CAPBBBBB", mpn("GRM188R71C104KA01D") + [("Value", "100nF")], ["CAP 0402", "CAP 0603"], 1, comment="=Value"),
        comp("U1", "LDO", "LDOAAAAA", mpn("TPS7A0233PDBVR", "TI") + [("Lifecycle Status", "Obsolete")], ["SOT-23-5"], desc="LDO 3.3V"),
        comp("U2", "Dual OpAmp", "OPAPARTA", mpn("LM358DR", "TI"), ["SOIC-8"], partcount=3, part=1),
        comp("TP1", "Test Point", "TPAAAAAA", [], ["TestPoint-1.5mm"], did=f"CMP-003-000000-{a365_rev[0]}", lib="Original Circuit",
             extra=tp_extra),
        comp("FID1", "Fiducial", "FIDAAAAA", [], ["Fiducial-1mm"], kind=5),
    ])
    write_schdoc(d / "Sheets" / "IO.SchDoc", [
        comp("U2", "Dual OpAmp", "OPAPARTB", mpn("LM358DR", "TI"), ["SOIC-8"], partcount=3, part=2),
        comp("R1", "Resistor", "RESAAAAA", mpn("RC0603FR-0710KL", "Yageo") + [("Value", "10k")], ["RES 0603"], comment="=Value"),
        comp("R2", "Resistor", "RESBBBBB", mpn("RC0603FR-0710KL", "Yageo") + [("Value", "10k")], ["RES 0603"], comment="=Value"),
        comp("J1", "USB-C", "USBCAAAA", [("Part Number", "USB4105-GF-A"), ("Manufacturer", "GCT"),
                                          ("ComponentLink1Description", "Datasheet"), ("ComponentLink1URL", "https://example.com/usb.pdf")],
             ["GCT USB4105"], did="USB4105-GF-A"),
    ])
    var = ""
    if variants:
        var = ("[ProjectVariant1]\r\nUniqueId=58ba1377-484a-4829-9ab9-df57b90b5cef\r\nDescription=Lite\r\nAllowFabrication=0\r\n"
               "ParameterCount=0\r\nVariationCount=2\r\n"
               "Variation1=Designator=R2|UniqueId=\\RESBBBBB|Kind=1|AlternatePart=\r\n"
               "Variation2=Designator=J1|UniqueId=\\USBCAAAA|Kind=1|AlternatePart=\r\n"
               "ParamVariationCount=1\r\nParamVariation1=ParameterName=Value|VariantValue=22k\r\nParamDesignator1=R1\r\n\r\n"
               "[ProjectVariant2]\r\nDescription=Alt LDO\r\nVariationCount=1\r\n"
               "Variation1=Designator=U1|UniqueId=\\LDOAAAAA|Kind=2|AlternatePart=|AltLibLink_DesignItemID=AP2112K-3.3TRG1|AltLibLink_LibReference=LDO\r\n"
               "ParamVariationCount=1\r\nParamVariation1=ParameterCount=1|ParameterName1=Manufacturer Part Number|VariantValue1=AP2112K-3.3TRG1\r\n"
               "ParamDesignator1=U1\r\n\r\n")
    docs = ["Sheets\\Power.SchDoc", "sheets\\io.schdoc", "Board.PcbDoc", "Missing.SchDoc"] + list(extra_docs)
    txt = "[Design]\r\nVersion=1.0\r\nHierarchyMode=0\r\n\r\n" + "".join(
        f"[Document{i}]\r\nDocumentPath={p}\r\nAnnotationEnabled=1\r\n\r\n" for i, p in enumerate(docs, 1))
    txt += var + "[Parameter1]\r\nName=Revision\r\nValue=A.1\r\n\r\n"
    (d / f"{name}.PrjPcb").write_bytes(b"\xef\xbb\xbf" + txt.encode("utf-8"))
    return d / f"{name}.PrjPcb"


def _g(groups, m):
    return next(g for g in groups if g["mpn"] == m)


# ---------------------------------------------------------------- .SchDoc
def test_schdoc_records():
    with tempfile.TemporaryDirectory() as td:
        f = write_schdoc(Path(td) / "s.SchDoc", [
            comp("C1", "Capacitor", "AAAAAAAA", mpn("GRM1") + [("Value", "1uF")], ["CAP 0402", "CAP 0603"], 1, comment="=Value"),
            comp("R?", "Resistor", "BBBBBBBB", [], ["RES 0603"]),
            comp("MH1", "Hole", "CCCCCCCC", [], [], kind=2),
        ])
        s = AP.read_schdoc(f)
        c = {x["unique_id"]: x for x in s["components"]}
        a = c["AAAAAAAA"]
        assert a["designator"] == "C1" and a["libref"] == "Capacitor" and a["source_library"] == "Parts.DbLib"
        assert a["footprint"] == "CAP 0603" and a["footprints"] == ["CAP 0402", "CAP 0603"]      # IsCurrent wins
        assert a["comment"] == "1uF" and a["value"] == "1uF" and a["mpn"] == "GRM1" and a["manufacturer"] == "Murata"
        assert c["CCCCCCCC"]["in_bom"] is False and c["BBBBBBBB"]["in_bom"] is True
        assert any("R?" in w and "not annotated" in w for w in s["warnings"])


def test_utf8_fields_and_binary_records():
    """'%UTF8%Text' overrides its cp1252 twin; a binary record (flag byte set) keeps OwnerIndex counting aligned."""
    with tempfile.TemporaryDirectory() as td:
        recs = [AL.pblock("|RECORD=31"), AL.block(b"\x02binarypin", 1),
                AL.pblock("|RECORD=1|LibReference=Res|UniqueID=UUUUUUUU|DesignItemId=Res|SourceLibraryName=x.SchLib")]
        tail = ("|RECORD=41|OwnerIndex=2|Name=Value|Text=4.7k?|%UTF8%Text=4.7kΩ").encode("utf-8") + b"\0"
        recs += [AL.block(tail), AL.pblock("|RECORD=34|OwnerIndex=2|Text=R9|Name=Designator")]
        data = AL.pblock(f"|HEADER={SCH_HDR}") + b"".join(recs)
        f = Path(td) / "u.SchDoc"
        f.write_bytes(cfb.to_bytes({"FileHeader": data}))
        (c,) = AP.read_schdoc(f)["components"]
        assert c["designator"] == "R9" and c["params"]["Value"] == "4.7kΩ" and c["value"] == "4.7kΩ"


def test_not_a_schematic():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.SchDoc"
        AL.write_schlib(f, [AL.resistor_symbol()])             # a SchLib renamed .SchDoc: refused with a reason
        try:
            AP.read_schdoc(f)
            assert False
        except ValueError as e:
            assert "not an Altium schematic header" in str(e)


# ---------------------------------------------------------------- .PrjPcb + variants
def test_prjpcb_documents_variants_parameters():
    with tempfile.TemporaryDirectory() as td:
        prj = AP.read_prjpcb(board(td))
        assert [p.name for p in prj["schdocs"]] == ["Power.SchDoc", "IO.SchDoc"]       # case-insensitive Windows paths
        assert prj["missing"] == ["Missing.SchDoc"] and prj["parameters"] == {"Revision": "A.1"}
        lite, alt = prj["variants"]
        assert lite["name"] == "Lite" and [v["Designator"] for v in lite["variations"]] == ["R2", "J1"]
        assert lite["param_variations"] == [{"designator": "R1", "unique_id": "", "name": "Value", "value": "22k"}]
        assert alt["param_variations"][0]["name"] == "Manufacturer Part Number"


def test_project_bom_groups_and_variants():
    with tempfile.TemporaryDirectory() as td:
        p = AP.load_project(board(td))
        assert any("Missing.SchDoc" in w for w in p["warnings"])
        assert sum(c["designator"] == "U2" for c in p["components"]) == 1                 # multi-part U2 counts once
        u2 = next(c for c in p["components"] if c["designator"] == "U2")
        assert u2["parts_placed"] == 2 and u2["sheets"] == ["IO.SchDoc", "Power.SchDoc"]
        g = AP.bom_groups(p)
        assert not any("FID1" in x["designator"] for x in g)                              # Standard (No BOM)
        cap = _g(g, "GRM188R71C104KA01D")
        assert cap["designator"] == "C1, C2" and cap["qty"] == "2" and cap["footprint"] == "CAP 0603" and cap["value"] == "100nF"
        assert cap["variants"] == {"Lite": 2, "Alt LDO": 2}
        res = _g(g, "RC0603FR-0710KL")
        assert res["variants"] == {"Lite": 1, "Alt LDO": 2} and res["qty"] == "2"
        j1 = _g(g, "USB4105-GF-A")
        assert j1["datasheet"] == "https://example.com/usb.pdf" and j1["variants"]["Lite"] == 0
        tp = next(x for x in g if x["designator"] == "TP1")
        assert tp["mpn"] == "" and tp["design_item_id"] == "CMP-003-000000-1" and tp["item_guid"].startswith("9BE5D5AE")
        # variant Lite: R2 and J1 not fitted, R1 gets 22k
        gl = AP.bom_groups(p, "lite")
        dnp = [x for x in gl if x["dnp"]]
        assert sorted(x["designator"] for x in dnp) == ["J1", "R2"] and all(x["qty"] == "0" for x in dnp)
        r1 = next(x for x in gl if x["designator"] == "R1" and not x["dnp"])
        assert r1["value"] == "22k" and "parameters changed" in r1["variant_notes"]
        # variant Alt LDO: U1 becomes the alternate part
        ga = AP.bom_groups(p, "Alt LDO")
        u1 = next(x for x in ga if x["designator"] == "U1")
        assert u1["mpn"] == "AP2112K-3.3TRG1" and u1["design_item_id"] == "AP2112K-3.3TRG1" and "alternate" in u1["variant_notes"]
        try:
            AP.bom_groups(p, "Nope")
            assert False
        except ValueError as e:
            assert "Lite" in str(e)


def test_read_bom_hook_and_csv_roundtrip():
    """bom_triage.read_bom reads the project directly; the written CSV reads back with the same fields."""
    with tempfile.TemporaryDirectory() as td:
        f = board(td)
        lines, mapping = _quiet(T.read_bom, f)[0]
        assert {"designator", "mpn", "value"} <= set(mapping)
        cap = next(l for l in lines if l.get("mpn") == "GRM188R71C104KA01D")
        assert cap["designator"] == "C1, C2" and cap["qty"] == "2" and cap["dnp"] is False and cap["footprint"] == "CAP 0603"
        os.environ["ECADLIB_VARIANT"] = "Lite"
        try:
            lines_v, _ = _quiet(T.read_bom, f)[0]
        finally:
            del os.environ["ECADLIB_VARIANT"]
        assert any(l["designator"] == "R2" and l["dnp"] for l in lines_v)
        p = AP.load_project(f)
        out = AP.write_bom_csv(AP.bom_groups(p), Path(td) / "bom.csv", [v["name"] for v in p["variants"]])
        hdr = next(csv.reader(open(out)))
        assert hdr[-2:] == ["Qty [Lite]", "Qty [Alt LDO]"]
        back, m = T.read_bom(out)
        assert m["mpn"] == "Manufacturer Part Number" and m["designator"] == "Designator" and m["qty"] == "Quantity"
        assert m["footprint"] == "Footprint" and m["value"] == "Comment"
        assert next(l for l in back if l["mpn"] == "GRM188R71C104KA01D")["designator"] == "C1, C2"


def test_schdoc_alone_is_a_project():
    with tempfile.TemporaryDirectory() as td:
        board(td)
        lines, _ = _quiet(T.read_bom, Path(td) / "Sheets" / "IO.SchDoc")[0]
        assert {l["designator"] for l in lines} == {"U2", "R1, R2", "J1"}


def test_bomdoc_overrides_chosen_mpn():
    """ActiveBOM: the chosen (first) solution replaces the schematic MPN, later ones become alternates."""
    with tempfile.TemporaryDirectory() as td:
        f = board(td, extra_docs=["Board.BomDoc"])
        (Path(td) / "Board.BomDoc").write_text(
            "[BomLine1]\nDesignator=C1,C2\nManufacturerPartNumber1=CL10B104KB8NNNC\nManufacturer1=Samsung\nSupplierName1=Digi-Key\n"
            "SupplierPartNumber1=1276-1000-1-ND\nManufacturerPartNumber2=GRM188R71C104KA01D\nManufacturer2=Murata\n")
        p = AP.load_project(f)
        cap = next(x for x in AP.bom_groups(p) if x["designator"] == "C1, C2")
        assert cap["mpn"] == "CL10B104KB8NNNC" and cap["manufacturer"] == "Samsung" and cap["supplier_pn"] == "1276-1000-1-ND"
        assert cap["alternates"] == "GRM188R71C104KA01D" and cap["mpn_source"] == "BomDoc"
        (Path(td) / "Board.BomDoc").write_text("[General]\nVersion=1\n")
        p = AP.load_project(f)
        assert any("no ActiveBOM line items" in w for w in p["warnings"])
        assert _g(AP.bom_groups(p), "GRM188R71C104KA01D")


def test_project_cli_and_triage_cli():
    with tempfile.TemporaryDirectory() as td:
        f = board(td)
        rc, out = _quiet(cli.main, ["project", str(f), "-o", str(Path(td) / "b.csv"), "--variant", "Lite"])
        assert rc == 0 and "variant Lite" in out and (Path(td) / "b.csv").exists()
        assert (Path(td) / "b.csv").read_bytes()[:3] == b"\xef\xbb\xbf"           # a BOM: Excel opens it as UTF-8 (±, µ, Ω)
        rows = list(csv.DictReader(open(Path(td) / "b.csv", encoding="utf-8-sig")))
        assert any(r["Designator"] == "R2" and r["DNP"] == "DNP" for r in rows)
        rc, _ = _quiet(cli.main, ["project", str(Path(td) / "nothing.PrjPcb")])
        assert rc == 2
        _quiet(T.main, [str(f), "--providers", "", "--offline", "--cache", str(Path(td) / "c"), "-o", str(Path(td) / "tri")])
        tri = list(csv.DictReader(open(Path(td) / "tri.csv")))
        assert any(r["mpn"] == "TPS7A0233PDBVR" for r in tri) and len(tri) == len(AP.bom_groups(AP.load_project(f)))


def test_librarian_reads_project():
    """`ecadlib librarian BOARD.PrjPcb` plans one item per grouped part (offline, no sources reachable: all held)."""
    with tempfile.TemporaryDirectory() as td:
        f = board(td)
        rc, _ = _quiet(cli.main, ["librarian", str(f), "-o", str(Path(td) / "lib"), "--offline", "--cache", str(Path(td) / "c"),
                                  "--sources", "easyeda"])
        names = [i["name"] for i in json.loads((Path(td) / "lib" / "plan.json").read_text())]
        assert {"TPS7A0233PDBVR", "LM358DR", "USB4105-GF-A"} <= set(names) and len(names) == 6, names


# ---------------------------------------------------------------- where-used
def _plan(d, names):
    items = [{"name": n, "status": "ready", "parameters": [{"name": "Manufacturer Part Number", "value": m}],
              "symbol": {"ref": "", "file": None}, "footprint": {"ref": "", "file": None}, "source": {}} for n, m in names]
    f = Path(d) / "plan.json"
    f.write_text(json.dumps(items))
    return f


def test_where_used_index_and_flags():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        board(td / "projA", "Alpha")
        board(td / "projB" / "deep", "Beta", variants=False, a365_rev=("2", "11111111-2222-3333-4444-555555555555"))
        lib = td / "lib"
        lib.mkdir()
        plan = _plan(lib, [("GRM188R71C104KA01D", "GRM188R71C104KA01D"), ("RC0603FR-0710KL", "RC0603FR-0710KL"),
                           ("RC0603FR-0710KL/TR", "RC0603FR-0710KL/TR"), ("OLD-PART", "XYZ123"), ("LM358DR", "LM358DR")])
        (lib / "lifecycle_state.json").write_text(json.dumps({"parts": {"LM358DR": {"mpn": "LM358DR", "status": "nrnd"}}}))
        res = AP.where_used([td / "projA", td / "projB"], plan=plan)
        assert [p["name"] for p in res["projects"]] == ["Alpha", "Beta"]
        cap = next(e for e in res["parts"].values() if e["mpn"] == "GRM188R71C104KA01D")
        assert [u["project"] for u in cap["uses"]] == ["Alpha", "Beta"] and cap["uses"][0]["designators"] == ["C1", "C2"]
        assert cap["library"] == ["GRM188R71C104KA01D"]
        res_e = next(e for e in res["parts"].values() if e["mpn"] == "RC0603FR-0710KL")
        assert res_e["uses"][0]["not_fitted_in"] == {"Lite": ["R2"]}
        fl = {(f["flag"], f["part"]) for f in res["flags"]}
        assert ("missing_from_library", "TPS7A0233PDBVR") in fl and ("missing_from_library", "USB4105-GF-A") in fl
        assert ("unused_library_part", "OLD-PART") in fl
        assert ("lifecycle", "TPS7A0233PDBVR") in fl                     # schematic 'Lifecycle Status' = Obsolete
        assert ("lifecycle", "LM358DR") in fl                            # lifecycle_state.json next to the plan
        old = [f for f in res["flags"] if f["flag"] == "old_revision"]
        assert len(old) == 1 and old[0]["projects"] == "Alpha" and "CMP-003-000000-2" in old[0]["detail"]
        dup = [f for f in res["flags"] if f["flag"] == "duplicate_in_use"]
        assert dup and "RC0603FR-0710KL" in dup[0]["detail"]            # /TR copy is the same part (dedupe group)
        one = AP.where_used([td], part="GRM188", plan=plan)
        assert list(one["parts"]) == ["MPN:GRM188R71C104KA01D"] and not any(f["flag"] == "missing_from_library" for f in one["flags"])
        files = AP.write_where_used(res, td / "out" / "wu")
        md = files["md"].read_text()
        assert "| missing_from_library | TPS7A0233PDBVR |" in md and "Alpha" in md
        rows = list(csv.DictReader(open(files["csv"])))
        assert any(r["project"] == "Beta" and r["designators"] == "C1, C2" for r in rows)
        assert json.loads(files["json"].read_text())["flags"]


def test_where_used_a365_guid_and_cli():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        board(td / "p", "Alpha", variants=False)
        st = td / "a365_state.json"
        st.write_text(json.dumps({"Test Point": {"revision": "x", "item": "9be5d5ae-9614-40aa-9ce5-d79a88b3aafe"}}))
        res = AP.where_used([td / "p"], a365=st)
        tp = next(e for e in res["parts"].values() if "CMP-003-000000-1" in e["design_item_ids"])
        assert tp["library"] == ["Test Point"] and "a365" in tp["library_origin"]
        rc, out = _quiet(cli.main, ["where-used", "--projects", str(td / "p"), "-o", str(td / "wu")])
        assert rc == 1 and "lifecycle" in out and (td / "wu.md").exists()           # flags -> exit 1 (CI)
        rc, _ = _quiet(cli.main, ["where-used", "--projects", str(td / "none")])
        assert rc == 2


def test_workspace_and_mcp_tools():
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    with tempfile.TemporaryDirectory() as td:
        f = board(Path(td) / "proj")
        ws = Workspace(Path(td) / "ws")
        r = _quiet(M.call_tool, ws, "project_bom", {"project": str(f), "variant": "Lite"})[0]
        assert r["variants"] == ["Lite", "Alt LDO"] and r["csv"].startswith("reports/project_")
        assert any(l["designator"] == "J1" and l["dnp"] for l in r["lines"])
        lines, _ = _quiet(ws._read_bom, f)[0]                     # binary-file guard lets .PrjPcb/.SchDoc through
        assert lines
        w = _quiet(M.call_tool, ws, "where_used", {"projects": [str(Path(td) / "proj")], "part": "LM358"})[0]
        assert w["rows"] and all(x["mpn"] == "LM358DR" for x in w["rows"]) and "reports/where_used.md" in w["reports"]
        assert {"project_bom", "where_used"} <= {t["name"] for t in M.TOOLS}


# ---------------------------------------------------------------- real Altium files (AltiumSharp TestData)
def _testdata():
    return next((d / "TestData" for d in REF_DIRS if (d / "TestData" / "DAC.SchDoc").exists()), None)


def test_real_altium_schdocs():
    data = _testdata()
    if not data:
        print("  (AltiumSharp TestData not found; skipped)")
        return
    with tempfile.TemporaryDirectory() as td:
        for n in ("DAC.SchDoc", "Level Shifter.SchDoc", "USB Power.SchDoc"):
            shutil.copy(data / n, Path(td) / n)
        (Path(td) / "Real.PrjPcb").write_text("[Design]\nVersion=1.0\n\n[Document1]\nDocumentPath=DAC.SchDoc\n\n"
                                              "[Document2]\nDocumentPath=Level Shifter.SchDoc\n\n[Document3]\nDocumentPath=USB Power.SchDoc\n\n")
        p = AP.load_project(Path(td) / "Real.PrjPcb")
        assert not p["warnings"], p["warnings"]
        g = AP.bom_groups(p)
        c104 = _g(g, "CL10B104KB8NNNC")                          # 'Part Number' parameter, DbLib DesignItemId agrees
        assert c104["designator"] == "C3, C5, C7, C8, C11, C18, C20" and c104["qty"] == "7" and c104["footprint"] == "CAP 0603_1608" and c104["value"] == "0.1 µF"
        assert _g(g, "1002-021-01000")["designator"] == "J1, J3" and _g(g, "1002-021-01000")["manufacturer"] == "CNC Tech"
        tps = [c for c in p["components"] if c["libref"] == "Test Point"]
        assert len(tps) == 8 and all(not c["in_bom"] for c in tps)               # ComponentKindVersion2=5 (Standard, No BOM)
        assert not any(x["designator"].startswith(("TP", "FID")) for x in g)
        tp = tps[0]
        assert tp["item_guid"] == "9BE5D5AE-9614-40AA-9CE5-D79A88B3AAFE" and tp["design_item_id"] == "CMP-003-000000-1"
        fid = next(c for c in AP.read_schdoc(data / "Overview.SchDoc")["components"] if c["designator"] == "FID1")
        assert fid["footprint"] == "Fiducial-1mm" and len(fid["footprints"]) == 3              # IsCurrent of three models
        usb = _g(g, "1002-021-01000")
        assert usb["datasheet"].endswith("1002-021-01000.pdf")
        res = AP.where_used([td])
        assert res["projects"][0]["name"] == "Real" and len(res["projects"]) == 1   # sheets belong to the project



def _channel_board(d, design=""):
    """Top sheet: a Content Vault MCU (Comment = MPN, no MPN parameter), a 100nF cap, a sheet symbol REPEAT(CH,1,3) on
    Chan.SchDoc and a device sheet symbol on Sensor.SchDoc; the project lists Sensor under [DeviceSheet1] as Altium's
    managed path, and a second device sheet that is nowhere."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    write_schdoc(d / "Top.SchDoc", [
        comp("U1", "STM32F101T6U6A", "MCUAAAAA", [], ["QFN36"], comment="STM32F101T6U6A", lib="Content Vault", did="CMP-0001-00042-1"),
        comp("C1", "Cap", "CAPCCCCC", [("Value", "100nF")], ["CAP 0402"], comment="=Value", lib="Content Vault"),
    ], symbols=[("REPEAT(CH,1,3)", "Chan.SchDoc"), ("Sense", "Sensor.SchDoc")])
    write_schdoc(d / "Chan.SchDoc", [
        comp("R1", "Res", "RCHANAAA", mpn("RC0603FR-0710KL", "Yageo") + [("Value", "10k")], ["RES 0603"], comment="=Value"),
        comp("R2", "Res", "RCHANBBB", mpn("RC0603FR-0710KL", "Yageo") + [("Value", "10k")], ["RES 0603"], comment="=Value"),
        comp("C1", "Cap", "CCHANAAA", mpn("GRM155R71C104KA88D") + [("Value", "100nF")], ["CAP 0402"], comment="=Value"),
    ])
    dev = d / "altium data" / "Managed" / "Sheets" / "{5A1B}"
    dev.mkdir(parents=True)
    write_schdoc(dev / "Sensor.SchDoc", [comp("U9", "BME280", "SENSORAA", mpn("BME280", "Bosch"), ["LGA-8"])])
    txt = ("[Design]\r\nVersion=1.0\r\n" + design + "\r\n[Document1]\r\nDocumentPath=Top.SchDoc\r\n\r\n"
           "[Document2]\r\nDocumentPath=Chan.SchDoc\r\n\r\n"
           "[DeviceSheet1]\r\nDocumentPath=Managed\\Sheets\\{5A1B}\\Sensor.SchDoc\r\n\r\n"
           "[DeviceSheet2]\r\nDocumentPath=C:\\Users\\Public\\Documents\\Altium\\AD26\\Library\\Device Sheets\\Gone.SchDoc\r\n\r\n")
    (d / "Ch.PrjPcb").write_text(txt, encoding="utf-8")
    return d / "Ch.PrjPcb", d / "altium data"


def test_device_sheets_channels_and_comment_mpn():
    with tempfile.TemporaryDirectory() as td:
        prj, data = _channel_board(td)
        env = {k: v for k, v in os.environ.items() if k not in ("PUBLIC", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA")}
        with mock.patch.dict(os.environ, dict(env, ECADLIB_DEVICE_SHEETS=str(data)), clear=True):
            p = AP.load_project(prj)
        assert [Path(s).name for s in p["sheets"]] == ["Top.SchDoc", "Chan.SchDoc", "Sensor.SchDoc"], p["sheets"]
        w = " | ".join(p["warnings"])
        assert "device sheet C:\\Users\\Public\\Documents\\Altium\\AD26\\Library\\Device Sheets\\Gone.SchDoc not found" in w, w
        assert "NOT in this BOM" in w and "ECADLIB_DEVICE_SHEETS" in w
        assert "Chan.SchDoc: multi-channel sheet, 3 channels (CH1, CH2, CH3): 3 components counted once per channel" in w, w
        assert "designators like R1_CH1 ($Component_$RoomName)" in w
        g = AP.bom_groups(p)
        r = _g(g, "RC0603FR-0710KL")
        assert r["qty"] == "6" and r["designator"] == "R1_CH1, R1_CH2, R1_CH3, R2_CH1, R2_CH2, R2_CH3", r
        assert _g(g, "GRM155R71C104KA88D")["designator"] == "C1_CH1, C1_CH2, C1_CH3"
        assert _g(g, "BME280")["designator"] == "U9"                               # the device sheet's part is in
        mcu = _g(g, "STM32F101T6U6A")
        assert mcu["mpn_source"] == "Comment" and mcu["designator"] == "U1", mcu
        cap = next(x for x in g if x["designator"] == "C1")                      # 100nF is a value, not a part number
        assert cap["mpn"] == "" and cap["value"] == "100nF"
        assert "U1): their Comment is used as the MPN" in w, w
        # missing device sheet, no folder known: still a warning, never a silent skip
        with mock.patch.dict(os.environ, env, clear=True):
            (data / "Managed" / "Sheets" / "{5A1B}" / "Sensor.SchDoc").rename(Path(td) / "elsewhere.bin")
            p2 = AP.load_project(prj)
        assert any("device sheet Managed\\Sheets\\{5A1B}\\Sensor.SchDoc not found" in x for x in p2["warnings"]), p2["warnings"]
        # the project's own channel format; lines the triage gets carry the Comment note
        prj2, _ = _channel_board(Path(td) / "two", "ChannelDesignatorFormatString=$ComponentPrefix$ComponentIndex$ChannelAlpha\r\n")
        with mock.patch.dict(os.environ, dict(env, ECADLIB_DEVICE_SHEETS=str(Path(td) / "two" / "altium data")), clear=True):
            lines, _ = _quiet(AP.bom_lines, prj2)[0]
        des = {ln["designator"] for ln in lines}
        assert "R1A, R1B, R1C, R2A, R2B, R2C" in des, des
        u1 = next(ln for ln in lines if ln["designator"] == "U1")
        assert u1["mpn"] == "STM32F101T6U6A" and u1.get("altium_mpn_source") == "Comment", u1


def test_channel_designators_and_mpn_like_comments():
    assert AP.channel_designator("$Component_$RoomName", "R1", "CH2", 2) == "R1_CH2"
    assert AP.channel_designator("$ComponentPrefix$ComponentIndex$ChannelAlpha", "R12", "CH2", 2) == "R12B"
    assert AP.channel_designator("$Component$ChannelIndex", "C3", "X", 27) == "C327"
    assert AP.channel_designator("$ComponentPrefix_$ChannelAlpha$ComponentIndex", "U1", "X", 28) == "U_AB1"
    for s in ("STM32F101T6U6A", "LM358DR", "RC0603FR-0710KL", "GRM188R71H104KA93D", "TPS62162DSGR", "BC847C"):
        assert AP.looks_like_mpn(s), s
    for s in ("10k", "100nF", "4k7", "1uF/16V", "0R", "2.2uH", "100nF_X7R", "Res1", "Cap Pol1", "=Value", "LED", "", "47R",
              "10uF/25V", "1M", "100", "TestPoint", "330Ω"):
        assert not AP.looks_like_mpn(s), s

def test_read_bom_hook_uses_house_mpn_name():
    """ecadlib.toml [parameters.names] next to the project: the house MPN parameter is read through read_bom."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "ecadlib.toml").write_text('[parameters.names]\n"Manufacturer Part Number" = "Mfr P/N"\n')
        s = write_schdoc(d / "A.SchDoc", [comp("U1", "LDO", "UAAAAAAA", [("Mfr P/N", "TPS7A0233PDBVR"), ("Manufacturer", "TI")],
                                               ["SOT-23-5"], lib="x.SchLib")])
        (ln,), _ = _quiet(T.read_bom, s)[0]
        assert ln["mpn"] == "TPS7A0233PDBVR", ln


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_altium_project"]))
