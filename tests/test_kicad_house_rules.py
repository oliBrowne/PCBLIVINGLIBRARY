"""The KiCad export applies the librarian's symbol rules to a symbol it fetches again from the source (EasyEDA C2975360,
a 16 MHz crystal, BOM line Y1), as the .SchLib has them: passive pins, no pin names or numbers on a two-pin passive,
the category's designator (Y, not EasyEDA's U), one credit field. released_differs sees the designator and the pin
name / number visibility. The crystal symbol is test_easyeda_crystal_symbol's SYNTHETIC one."""
import copy, tempfile
from pathlib import Path

from ecadlib import kicad_export as X, sexpr
from tests import test_easyeda_crystal_symbol as TC, test_librarian as TL


def _xtal_ee():
    ee = copy.deepcopy(TL.EE)
    ee["result"]["dataStr"] = copy.deepcopy(TC.XTAL)
    ee["result"]["dataStr"]["head"]["c_para"]["Manufacturer Part"] = ee["result"]["title"] = "XTAL16M"
    ee["result"]["lcsc"]["number"] = "C2975360"
    return ee


BOM = TL.BOM.split("\n")[0] + "\nY1,1,16MHz,,SOSET,XTAL16M,C2975360,Crystal 16MHz 20pF,\n"


def _hidden(node):
    return node is not None and ("hide" in node or any(isinstance(c, list) and c[:2] == ["hide", "yes"] for c in node))


def test_easyeda_crystal_kicad_symbol_follows_the_librarian_rules():
    with tempfile.TemporaryDirectory() as d:
        http = TL.FakeHTTP({"C2975360": _xtal_ee()})
        TL.run(d, http, bom=BOM)
        rep = X.export(Path(d) / "out" / "plan.json", Path(d) / "kicad", "lib", include_held=True, http=http,
                       log=lambda *a: None)
        assert not rep["errors"], rep["errors"]
        row = next(r for r in rep["parts"] if r["part"] == "XTAL16M")
        assert row["symbol"].startswith("EasyEDA C2975360"), row["symbol"]     # still the source's drawing
        t = sexpr.parse((Path(d) / "kicad" / "lib.kicad_sym").read_text(encoding="utf-8"))
        sym = next(s for s in sexpr.find(t, "symbol") if s[1] == "XTAL16M")
        props = {p[1]: p[2] for p in sexpr.find(sym, "property")}
        assert props["Reference"] == "Y", props["Reference"]
        assert "Credit" not in props and props["Library Credit"].startswith("From the JLCEDA/EasyEDA"), props
        pins = [p for u in sexpr.find(sym, "symbol") for p in sexpr.find(u, "pin")]
        assert [p[1] for p in pins] == ["passive", "passive"], [p[1] for p in pins]
        assert _hidden(sexpr.first(sym, "pin_names")) and _hidden(sexpr.first(sym, "pin_numbers")), sym[:4]


def test_released_differs_sees_designator_and_pin_text_visibility():
    with tempfile.TemporaryDirectory() as d:
        http = TL.FakeHTTP({"C2975360": _xtal_ee()})
        items, _ = TL.run(d, http, bom=BOM)
        it, plan_dir = items["XTAL16M"], Path(d) / "out"
        ks = X.symbol_from_schlib(plan_dir / it["symbol"]["file"], "XTAL16M", [])
        assert X.released_differs(it, plan_dir, ks) == []                      # the released symbol itself
        for p in sexpr.find(ks, "property"):
            if p[1] == "Reference":
                p[2] = sexpr.Q("U")
        got = X.released_differs(it, plan_dir, ks)
        assert got and any("designator" in c["text"] for c in got), got
        ks = X.symbol_from_schlib(plan_dir / it["symbol"]["file"], "XTAL16M", [])
        ks[:] = [c for c in ks if not (isinstance(c, list) and c[0] in ("pin_numbers", "pin_names"))]
        got = X.released_differs(it, plan_dir, ks)
        assert got and any("pin 1" in c["text"] and ("name" in c["text"] or "number" in c["text"]) for c in got), got
