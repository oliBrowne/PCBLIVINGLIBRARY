"""Problems seen in real Altium AD26 (PR #27), each with its cause fixed:

* NE555DR (TI, SOIC-8, LCSC C7593) sourced from EasyEDA: .Designator / .Comment in the middle of the PcbLib footprint,
  the pin-1 dot inside the body outline. (The SchLib pin-name overlap is another PR's: the symbol layout rule.)
  tests/fixtures/easyeda/C7593.json is SYNTHETIC (easyeda.com is blocked here), shaped after those screenshots.
* Check-BOM on an Altium project: Altium vault ids (CMP-1035-02800-1) from LibRef / Comment used as the MPN.
* JLCPCB answering 403: reported as a refusal, nothing more is sent to JLCPCB in the run, other sources still tried.
* Repeat channels with identical room names, stale librarian state from an older ecadlib, passives held for a missing
  3D height, `ecadlib doctor --altium-menu X.ini` checking the wrong workspace."""
import copy, json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_lib as AL, altium_project as AP, bom_triage as T, doctor as DR, easyeda_convert as EC, \
    footprint_check as FC, kicad_import as K, lifecycle as LC, providers as P, release_check as RC, sexpr
from ecadlib.httpcache import Response
from tests import test_altium_project as TAP, test_librarian as TL

NE555 = json.loads((Path(__file__).parent / "fixtures" / "easyeda" / "C7593.json").read_text())


# ---------------------------------------------------------------- 1+2. NE555 through the librarian
def test_librarian_ne555_footprint_text_and_10pt_symbol_font():
    bom = TL.BOM.split("\n")[0] + "\nU1,1,NE555DR,,TI,NE555DR,C7593,timer,\n"
    with tempfile.TemporaryDirectory() as d:
        items, _ = TL.run(d, TL.FakeHTTP({"C7593": NE555}), bom=bom)
        it = items["NE555DR"]
        assert it["source"]["footprint"] == "easyeda:C7593", it["source"]
        hdr, _ = AL.read_schlib(Path(d) / "out" / it["symbol"]["file"])
        assert hdr["FONTNAME1"] == "Times New Roman" and hdr["SIZE1"] == "10", hdr               # Altium's default font
        # the footprint: designator on silk above the courtyard, comment on Mechanical 13 below, 1 mm / 0.15 mm
        lib = AL.read_pcblib(Path(d) / "out" / it["footprint"]["file"])[0]
        texts = {r["text"]: r for r in lib["records"] if r["type"] == "text"}
        pads = [r for r in lib["records"] if r["type"] == "pad"]
        top = max(p["y"] + p["h"] / 2 for p in pads)
        des, com = texts[".Designator"], texts[".Comment"]
        assert des["layer"] == AL.TOP_OVERLAY and com["layer"] == AL.MECH13, (des["layer"], com["layer"])
        assert abs(AL.to_mm(des["h"]) - 1.0) < 1e-3 and abs(AL.to_mm(des["w"]) - 0.15) < 1e-3
        assert des["y"] > top and com["y"] + com["h"] < min(p["y"] - p["h"] / 2 for p in pads), (des, top)
        from ecadlib import footprint_rules as FR
        assert not [c for c in FR.check_pcblib(lib) if c[1] in ("silktext", "text")], FR.check_pcblib(lib)


# ---------------------------------------------------------------- 2. PcbLib text and pin-1 dot
def test_ne555_footprint_text_and_pin1_dot():
    notes = []
    text = EC.footprint_to_kicad(NE555["result"]["packageDetail"]["dataStr"], "SOIC8", notes)
    t = sexpr.parse(text)
    ref = next(x for x in sexpr.find(t, "fp_text") if x[1] == "reference")
    val = next(x for x in sexpr.find(t, "fp_text") if x[1] == "value")
    assert sexpr.first(ref, "layer")[1] == "F.SilkS" and sexpr.first(val, "layer")[1] == "F.Fab"
    crt = sexpr.first(next(x for x in sexpr.find(t, "fp_rect") if sexpr.first(x, "layer")[1] == "F.CrtYd"), "start")
    assert float(sexpr.first(ref, "at")[2]) + 0.5 <= float(crt[2]) - 0.2 + 1e-6, (ref, crt)     # 0.2 mm above the courtyard
    font = sexpr.first(sexpr.first(ref, "effects"), "font")
    assert sexpr.first(font, "size")[1:] == ["1", "1"] and sexpr.first(font, "thickness")[1] == "0.15"
    # the dot was inside the body; now beside pad 1, outside the body and 0.2 mm clear of every pad
    assert any("pin-1 dot moved" in n for n in notes), notes
    dot = [x for x in sexpr.find(t, "fp_poly") if sexpr.first(x, "layer")[1] == "F.SilkS"]
    assert len(dot) == 1
    pts = [(float(a[1]), float(a[2])) for a in sexpr.find(sexpr.first(dot[0], "pts"), "xy")]
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    assert abs(cx) > 2.45 or abs(cy) > 1.95, (cx, cy)                                     # off the 4.9 x 3.9 body
    assert abs(cx + 1.905) < 1.0 and abs(cy - 2.7) < 1.5, (cx, cy)                          # near pad 1 (-1.905, 2.7)
    pads = [{"x": float(sexpr.first(p, "at")[1]), "y": float(sexpr.first(p, "at")[2]), "w": 0.6, "h": 1.55} for p in sexpr.find(t, "pad")]
    gap = min(max(abs(x - p["x"]) - p["w"] / 2, abs(y - p["y"]) - p["h"] / 2) for x, y in pts for p in pads)
    assert gap >= 0.2 - 0.05, gap                                                           # 0.2 mm less the stroke
    assert not [c for c in FC.check(text) if c[0] == "error"], FC.check(text)
    from ecadlib import footprint_rules as FR                               # the rule book: text sizes, nothing on pads
    assert not [c for c in FR.check(text) if c[1] in ("text", "silktext", "layers")], FR.check(text)
    assert '(fp_text user "${REFERENCE}" (at 0 0) (layer "F.Fab")' in text
    # Altium side: the reference was on silk, so .Designator is on Top Overlay; a fab-only reference goes to Mech 13
    fp, _ = K.convert_footprint(text, "SOIC8", "x", [])
    layers = {tx[0]: tx[6] for tx in fp["texts"]}
    assert layers == {".Designator": AL.TOP_OVERLAY, ".Comment": AL.MECH13}, layers
    fab_ref = text.replace('(fp_text reference "REF**" (at 0 -4.43) (layer "F.SilkS")', '(fp_text reference "REF**" (at 0 0) (layer "F.Fab")')
    assert fab_ref != text
    fp2, _ = K.convert_footprint(fab_ref, "SOIC8", "x", [])
    assert {tx[0]: tx[6] for tx in fp2["texts"]}[".Designator"] == AL.MECH13


# ---------------------------------------------------------------- 3. vault ids are not MPNs
def test_vault_ids_never_mpn():
    for s in ("CMP-1035-02800-1", "CMP-003-000000-1", "cmp-1035-02800", "PCC-011-0000-2"):
        assert AP.is_vault_id(s) and not AP.looks_like_mpn(s), s
    for s in ("RC0603FR-0710KL", "NE555DR", "GRM155R71C104KA88D", "TPS7A0233PDBVR"):
        assert not AP.is_vault_id(s), s
    assert T.part_numbers({"mpn": "", "value": "CMP-1035-02800-1", "libref": "CMP-1035-02800-1"}) == []
    assert T.part_numbers({"mpn": "", "value": "NE555DR", "libref": "CMP-1035-02800-1"}) == ["NE555DR"]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        TAP.write_schdoc(d / "A.SchDoc", [
            TAP.comp("U1", "CMP-1035-02800-1", "UAAAAAAA", [("Part Number", "CMP-1035-02800-1"), ("Manufacturer Part Number", "NE555DR")],
                     ["SOIC8"], did="CMP-1035-02800-1", lib="Content Vault", comment="CMP-1035-02800-1"),
            TAP.comp("U2", "CMP-1035-02801-1", "UBBBBBBB", [("Part Number", "CMP-1035-02801-1")], ["SOIC8"],
                     did="CMP-1035-02801-1", lib="Content Vault", comment="LM358DR"),
            TAP.comp("U3", "CMP-1035-02802-1", "UCCCCCCC", [], ["SOIC8"], did="CMP-1035-02802-1", lib="Content Vault"),
        ])
        lines, _ = TAP._quiet(AP.bom_lines, d / "A.SchDoc")[0]
        by = {ln["designator"]: ln for ln in lines}
        assert by["U1"]["mpn"] == "NE555DR", by["U1"]                                   # the real MPN parameter
        assert by["U2"]["mpn"] == "LM358DR" and by["U2"].get("altium_mpn_source") == "Comment", by["U2"]
        assert not by["U3"].get("mpn"), by["U3"]                                         # Comment = LibRef = vault id
        assert T.part_numbers(by["U3"]) == []


# ---------------------------------------------------------------- 4. JLCPCB 403
class _Http:
    def __init__(self, status):
        self.status, self.posts, self.gets = status, 0, 0

    def post(self, url, body, headers=None, **kw):
        self.posts += 1
        return Response(self.status, "<html>Access denied</html>", error=f"HTTP {self.status}")

    def get(self, url, headers=None, **kw):
        self.gets += 1
        return Response(200, json.dumps(NE555))


def test_jlcpcb_403_is_reported_and_not_repeated():
    http = _Http(403)
    r = P.JLCPCBSearch(http).lookup({"mpn": "NE555DR"})
    assert r.error.startswith("JLCPCB refused the request (403)") and "other sources are still tried" in r.error, r.error
    r2 = P.JLCPCBSearch(http).lookup({"mpn": "LM358DR"})
    assert "JLCPCB refused the request (403)" in r2.error and "not asked again" in r2.error and http.posts == 1
    lc = LC.JLCLifecycle(http).check({"mpn": "NE555DR"})                              # shares the client: no new request
    assert "refused" in lc.error and http.posts == 1
    other = _Http(500)
    assert P.JLCPCBSearch(other).lookup({"mpn": "X"}).error == "HTTP 500"              # other errors as before


def test_triage_goes_on_after_jlcpcb_403():
    http = _Http(403)
    lines = [{"designator": "U1", "qty": "1", "mpn": "NE555DR", "lcsc": "", "value": "NE555DR", "footprint": "SOIC-8"},
             {"designator": "U2", "qty": "1", "mpn": "NE555DR", "lcsc": "C7593", "value": "NE555DR", "footprint": "SOIC-8"}]
    provs = [P.JLCPCBSearch(http), P.EasyEDA(http)]
    index = mock.Mock()
    index.match.return_value = (None, "")
    index.footprint.return_value = (None, "")
    index.find_footprint_for_package.return_value = None
    rows = T.triage(lines, index, provs, http)
    assert "JLCPCB refused the request (403)" in rows[0]["notes"], rows[0]["notes"]
    assert rows[1]["status"] == "available_easyeda" and http.gets == 1, rows[1]          # EasyEDA still asked
    assert http.posts == 1


# ---------------------------------------------------------------- A. repeat channels with one room name
def test_identical_room_names_get_numbered():
    assert AP.unique_rooms(["Red_LED"] * 3) == ["Red_LED1", "Red_LED2", "Red_LED3"]
    assert AP.unique_rooms(["A", "B"]) == ["A", "B"]
    assert AP.unique_rooms(["X", "X", "X1"]) == ["X2", "X3", "X1"]
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        TAP.write_schdoc(d / "Top.SchDoc", [], symbols=[("Red_LED", "Led.SchDoc")] * 6)
        TAP.write_schdoc(d / "Led.SchDoc", [TAP.comp("LED1", "LED", "LEDAAAAA", TAP.mpn("150060RS75000", "Wurth"), ["LED 0603"])])
        (d / "S.PrjPcb").write_text("[Design]\r\nVersion=1.0\r\n\r\n[Document1]\r\nDocumentPath=Top.SchDoc\r\n\r\n"
                                    "[Document2]\r\nDocumentPath=Led.SchDoc\r\n", encoding="utf-8")
        p = TAP._quiet(AP.load_project, d / "S.PrjPcb")[0]
        g = AP.bom_groups(p)
        line = next(x for x in g if x["mpn"] == "150060RS75000")
        des = line["designator"].split(", ")
        assert line["qty"] == "6" and len(set(des)) == 6 and des[0] == "LED1_Red_LED1" and des[-1] == "LED1_Red_LED6", des
        assert not any("duplicate designator" in w for w in p["warnings"]), p["warnings"]
        assert any("same name numbered" in w for w in p["warnings"]), p["warnings"]


# ---------------------------------------------------------------- B. stale state from an older ecadlib
def test_part_built_by_older_version_is_built_again():
    with tempfile.TemporaryDirectory() as d:
        items, http = TL.run(d)
        TL.run(d, http)                                         # same version, same rules: skipped
        sf = Path(d) / "out" / "librarian_state.json"
        state = json.loads(sf.read_text())
        name = "RES-0603-10k"
        assert state[name].get("skipped"), state[name]
        state[name]["built_with"] = {"ecadlib": "0.0.1", "rules_sha256": "old"}
        sf.write_text(json.dumps(state))
        pf = Path(d) / "out" / "plan.json"
        plan = json.loads(pf.read_text())
        for it in plan:
            if it["name"] == name:
                it["status"] = "held"                           # what the old version decided
        pf.write_text(json.dumps(plan))
        log = []
        b = Path(d) / "bom.csv"
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}, clear=True):
            TL.L.run(b, [], Path(d) / "out", cache=Path(d) / "cache", http=http, log=log.append)
        assert any(f"{name}: held, built by ecadlib 0.0.1: built again" in m for m in log), log
        state = json.loads(sf.read_text())
        assert state[name]["built_with"]["ecadlib"] != "0.0.1" and not state[name].get("skipped")
        # a ready part from another version is kept (updates never rebuild library parts behind its back)
        other = "LED-RED-0603"
        state[other]["built_with"] = {"ecadlib": "0.0.1", "rules_sha256": "old"}
        sf.write_text(json.dumps(state))
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}, clear=True):
            TL.L.run(b, [], Path(d) / "out", cache=Path(d) / "cache", http=http, log=log.append)
        assert json.loads(sf.read_text())[other].get("skipped")


# ---------------------------------------------------------------- C. package default heights
def test_package_default_height():
    assert K.package_default_height(["RES 0603"], "R") == (0.45, "0603 resistor")
    assert K.package_default_height(["CAP 0603"], None) == (0.9, "0603 capacitor")
    assert K.package_default_height(["C_0402_1005Metric"]) == (0.55, "0402 capacitor")
    assert K.package_default_height(["SOT23"]) == (1.2, "SOT-23")
    assert K.package_default_height(["SOIC-8"]) is None and K.package_default_height(["0603"]) is None   # kind unknown
    t = sexpr.parse('(footprint "RES0603_X" (pad "1" smd rect (at -0.8 0) (size 0.8 0.9) (layers "F.Cu")) '
                    '(pad "2" smd rect (at 0.8 0) (size 0.8 0.9) (layers "F.Cu")))')
    fp = {"name": "RES0603_X", "pads": [{"x": -0.8, "y": 0, "w": 0.8, "h": 0.9, "rot": 0}, {"x": 0.8, "y": 0, "w": 0.8, "h": 0.9, "rot": 0}],
          "tracks": [], "regions": [], "arcs": [], "bodies": []}
    notes = []
    K.add_body(fp, t, notes=notes, kind="R")
    assert fp["height_mm"] == 0.45 and any("HEIGHT from package default (0603 resistor), verify" in n for n in notes), notes


def test_release_check_warns_for_package_default_height():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        fp = {"name": "RES 0603", "pads": [{"designator": str(i), "x": x, "y": 0, "w": 0.8, "h": 0.9, "shape": "rect"} for i, x in ((1, -0.8), (2, 0.8))],
              "tracks": [], "fills": [], "regions": [], "texts": []}
        AL.write_pcblib(d / "f.PcbLib", [fp])
        AL.write_schlib(d / "s.SchLib", [AL.resistor_symbol("R10K", footprint="RES 0603")])
        item = {"name": "R10K", "folder": "Passives/Resistors", "description": "10k 0603", "parameters": [],
                "symbol": {"ref": "R10K", "file": "s.SchLib"}, "footprint": {"ref": "RES 0603", "file": "f.PcbLib"}}
        fails, warns, info = RC.check_item(item, d)
        assert not [f for f in fails if f[0] == "height"], fails
        assert any("HEIGHT from package default 0.45 mm (0603 resistor), verify" in w for w in warns), warns
        assert info["height"] == 0.45
        AL.write_pcblib(d / "q.PcbLib", [dict(fp, name="QQQ")])
        item2 = dict(item, folder="ICs", description="", name="U1", footprint={"ref": "QQQ", "file": "q.PcbLib"})
        fails2, _, _ = RC.check_item(item2, d)
        assert ("height", "no 3D height (no 3D body and no HEIGHT)") in [tuple(f) for f in fails2], fails2


# ---------------------------------------------------------------- D. doctor uses the ini's workspace
def test_doctor_workspace_from_menu_ini():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "menu").mkdir()
        (d / "elsewhere" / "ws").mkdir(parents=True)
        (d / "menu" / "ecadlib_menu.ini").write_text(f"[ecadlib]\nworkspace={d / 'elsewhere' / 'ws'}\nlibrary=lib\ncommand=\n",
                                                    encoding="utf-8")
        rep = DR.run(altium_menu=str(d / "menu" / "ecadlib_menu.ini"), environ={})
        ws = next(c for c in rep["checks"] if c["check"] == "workspace")
        assert str((d / "elsewhere" / "ws").resolve()) in ws["detail"] and ws["level"] == "OK", ws
        (d / "menu" / "ecadlib_menu.ini").write_text("[ecadlib]\nworkspace=..\\rel_ws\ncommand=\n", encoding="utf-8")
        rep = DR.run(altium_menu=str(d / "menu"), environ={})
        ws = next(c for c in rep["checks"] if c["check"] == "workspace")
        assert str((d / "rel_ws").resolve()) in ws["detail"], ws                       # relative to the ini's folder
        rep = DR.run(str(d / "elsewhere" / "ws"), altium_menu=str(d / "menu"), environ={})
        ws = next(c for c in rep["checks"] if c["check"] == "workspace")
        assert str((d / "elsewhere" / "ws").resolve()) in ws["detail"], ws             # --workspace still wins


if __name__ == "__main__":
    import sys
    from tests import run_all
    sys.exit(run_all.main(["test_altium_ad26_fixes"]))
