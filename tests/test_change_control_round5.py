"""Change control (#105), round 5: pins that share a pin end are connected (stacking), pin ends that move, and a
placed symbol whose Footprint property is empty."""
import copy, json, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import altium_lib as AL, change_diff as CD, change_gate as CG, fingerprint as FP, ledger as LG, sexpr
from tests.test_change_control import demo, gate, item, items_of
from tests.fixtures_lite import clean_all, scratch


def texts(ch):
    return [c["text"] for c in ch]


# ---------------------------------------------------------------- symbols to compare
def _altium(pins, d=None):
    """An Altium symbol, pins (num, name, type, side, x, y, length) in 10 mil units -> its fingerprint."""
    s = {"name": "REG", "description": "reg", "designator": "U?", "comment": "*", "footprint": None,
         "prims": [("rect", 0, 0, 40, -50)] + [("pin", {"num": n, "name": nm, "type": t}, side, x, y, L)
                                               for n, nm, t, side, x, y, L in pins], "label_at": ((0, 2), (0, -58))}
    d = Path(d or scratch("ecadlib-r5-"))
    f = d / "REG.SchLib"
    AL.write_schlib(f, [s])
    return FP.of_item({"name": "REG", "symbol": {"ref": "REG", "file": str(f)}, "parameters": []}, d), f


REG = [("10", "VIN", "power", "left", 0, -10, 30), ("11", "VIN", "power", "left", 0, -10, 30),
       ("12", "VIN", "power", "left", 0, -10, 30), ("13", "EN", "input", "left", 0, -20, 30),
       ("1", "SW", "output", "right", 40, -10, 30), ("9", "FB", "input", "right", 40, -20, 30),
       ("6", "GND", "power", "bottom", 20, -50, 30), ("15", "GND", "power", "bottom", 20, -50, 30)]


def _moved(pins, num, **kw):
    out = []
    for p in pins:
        if p[0] == num:
            p = list(p)
            for k, v in kw.items():
                p[{"side": 3, "x": 4, "y": 5, "len": 6}[k]] = v
            p = tuple(p)
        out.append(p)
    return out


def _kicad(pins):
    """A KiCad symbol, pins (num, name, type, x, y, angle, length) -> its fingerprint."""
    ps = "".join(f'(pin {t} line (at {x} {y} {a}) (length {L}) (name "{n}" (effects (font (size 1.27 1.27)))) '
                 f'(number "{num}" (effects (font (size 1.27 1.27)))))' for num, n, t, x, y, a, L in pins)
    node = sexpr.parse(f'(symbol "U" (property "Reference" "U" (at 0 0 0)) (property "Value" "U" (at 0 0 0)) '
                       f'(symbol "U_1_1" {ps}))')
    return FP.of_kicad(node, part="U")


QN = [("1", "G", "input", -5.08, 0, 0, 2.54), ("2", "S", "passive", 2.54, -5.08, 90, 2.54),
      ("3", "D", "passive", 2.54, 5.08, 270, 2.54)]


# ---------------------------------------------------------------- N5-1: stacking is electrical
def test_pins_that_now_share_a_pin_end_connect_and_are_held():
    """N5-1: moving D onto S's end shipped a D-S short as a cosmetic pin move. Pins on one end are connected in KiCad
    and Altium: a new sharing is electrical, from either tool, and says which pins now connect."""
    short = [p if p[0] != "3" else ("3", "D", "passive", 2.54, -5.08, 90, 2.54) for p in QN]
    a, b = _kicad(QN), _kicad(short)
    assert a["hashes"]["eid"] != b["hashes"]["eid"]
    ch = CD.diff(a, b)
    assert CD.worst(ch) == "electrical" and CG.decide(ch, CG.POLICY)["action"] == "hold", texts(ch)
    assert "pin 3 (D) now shares a pin end with pin 2 (S): they connect" in texts(ch), texts(ch)
    base, _ = _altium(REG)
    for num, kw, says in (("13", {"y": -10}, "pin 13 (EN) now shares a pin end with pins 10, 11, 12 (VIN): they connect"),
                          ("9", {"y": -10}, "pin 9 (FB) now shares a pin end with pin 1 (SW): they connect"),
                          ("15", {"side": "left", "x": 0, "y": -10},
                           "pin 15 (GND) now shares a pin end with pins 10, 11, 12 (VIN): they connect")):
        new, _ = _altium(_moved(REG, num, **kw))
        ch = CD.diff(base, new)
        assert CD.worst(ch) == "electrical" and CG.decide(ch, CG.POLICY)["action"] == "hold", (num, texts(ch))
        assert says in texts(ch), texts(ch)
        loose = CG.normalize_policy({"electrical": "warn", "geometry_minor": "pass"})
        assert CG.decide(ch, loose)["action"] == "warn"           # never passes


def test_different_pins_pulled_apart_lose_a_connection_and_are_held():
    stacked = [p if p[0] != "3" else ("3", "D", "passive", 2.54, -5.08, 90, 2.54) for p in QN]
    ch = CD.diff(_kicad(stacked), _kicad(QN))
    assert CD.worst(ch) == "electrical", texts(ch)
    assert "pin 3 (D) no longer shares a pin end with pin 2 (S): they are no longer connected" in texts(ch), texts(ch)


def test_stacks_are_the_same_read_from_kicad_and_from_altium():
    """KiCad/Altium parity: an Altium symbol with stacked pins, written to KiCad by kicad-export, has the same stacks
    (and, the pins passive, the same eid: kicad-export writes a power pin stacked under another as a hidden passive
    one, KLC S4.3); a KiCad symbol with stacked pins converted to Altium the same."""
    from ecadlib import kicad_export as KX, kicad_import as K
    a, f = _altium(REG)
    assert a["symbol"]["stacks"] == [[1, 1, ["6", "15"]], [1, 1, ["10", "11", "12"]]], a["symbol"]["stacks"]
    node = KX.symbol_from_schlib(f, "REG", [])
    k = FP.build("REG", FP.kicad_symbol(node)[0], tool="kicad")
    assert k["symbol"]["stacks"] == a["symbol"]["stacks"]
    assert not [c for c in CD.diff(a, k) if c["what"] == "pin ends"], texts(CD.diff(a, k))
    a, f = _altium([(n, nm, "passive", *rest) for n, nm, _, *rest in REG])
    k = FP.build("REG", FP.kicad_symbol(KX.symbol_from_schlib(f, "REG", []))[0], tool="kicad")
    assert k["symbol"]["stacks"] == a["symbol"]["stacks"] != []
    assert k["hashes"]["eid"] == FP.build("REG", a["symbol"], tool="altium")["hashes"]["eid"], texts(CD.diff(a, k))
    stacked = QN + [("4", "D", "passive", 2.54, 5.08, 270, 2.54)]
    ka = _kicad(stacked)
    with tempfile.TemporaryDirectory() as d:
        sym = K.convert_symbol([sexpr.parse(
            '(symbol "U" (property "Reference" "U" (at 0 0 0)) (property "Value" "U" (at 0 0 0)) (symbol "U_1_1" ' +
            "".join(f'(pin {t} line (at {x} {y} {an}) (length {L}) (name "{n}") (number "{num}"))'
                    for num, n, t, x, y, an, L in stacked) + "))")], notes=[])
        _, entries = AL.read_schlib(AL.write_schlib(Path(d) / "U.SchLib", [sym]))
        kb = FP.build("U", FP.altium_symbol(entries[0])[0], tool="altium")
    assert ka["symbol"]["stacks"] == kb["symbol"]["stacks"] == [[1, 1, ["3", "4"]]]
    assert FP.build("U", ka["symbol"], tool="kicad")["hashes"]["eid"] == kb["hashes"]["eid"]


# ---------------------------------------------------------------- N5-2: a stack of one power pin split apart
def test_a_split_stack_of_one_power_pin_warns_and_names_the_pins_that_need_wires():
    """N5-2: the house rules now give each stacked power pin its own end (#123 / #130). Pins of one name and type
    split apart join no wrong net, but a placed schematic wired them once: a warn that names the pins, whatever the
    policy says about geometry-minor. Untouched, nothing."""
    base, _ = _altium(REG)
    split, _ = _altium(_moved(_moved(REG, "11", y=-30), "12", y=-40))
    ch = CD.diff(base, split)
    assert CD.worst(ch) == "geometry-minor" and CG.decide(ch, CG.POLICY)["action"] == "warn", texts(ch)
    assert any(t.startswith("pins 10, 11, 12 (VIN) no longer share one pin end: pins 11, 12 now need their own wires")
               for t in texts(ch)), texts(ch)
    quiet = CG.normalize_policy({"geometry_minor": "pass"})
    assert CG.decide(ch, quiet)["action"] == "warn"             # never silent
    again, _ = _altium(REG)
    assert CD.diff(base, again) == []                            # never held (or warned) when nothing changed
    one, _ = _altium(_moved(REG, "11", y=-30))
    assert any("pin 11 now needs its own wire" in t for t in texts(CD.diff(base, one))), texts(CD.diff(base, one))
    back = CD.diff(split, base)                                  # stacking them again is a new connection: held
    assert CD.worst(back) == "electrical" and CG.decide(back, CG.POLICY)["action"] == "hold", texts(back)


# ---------------------------------------------------------------- N5-3: pin ends
def test_a_moved_pin_end_warns_and_a_new_length_is_cosmetic():
    """N5-3: a pin end that moves on a released symbol leaves placed schematics' wires behind: warn. A new length or
    graphics that keep the end in place stay cosmetic, in KiCad ((at) is the end) and Altium (the location is the
    body end)."""
    a = _kicad(QN)
    ch = CD.diff(a, _kicad([p if p[0] != "1" else ("1", "G", "input", -7.62, 0, 0, 2.54) for p in QN]))
    assert CD.worst(ch) == "geometry-minor" and CG.decide(ch, CG.POLICY)["action"] == "warn", texts(ch)
    assert any("pin end moved: pin 1" in t and "wires in placed schematics may need moving" in t for t in texts(ch))
    ch = CD.diff(a, _kicad([p if p[0] != "1" else ("1", "G", "input", -5.08, 0, 0, 5.08) for p in QN]))
    assert CD.worst(ch) == "cosmetic", texts(ch)
    base, _ = _altium(REG)
    ch = CD.diff(base, _altium(_moved(REG, "13", x=-10, len=20))[0])       # body end out, shorter: same pin end
    assert CD.worst(ch) == "cosmetic", texts(ch)
    ch = CD.diff(base, _altium(_moved(REG, "13", len=40))[0])               # longer: the end moves
    assert CD.worst(ch) == "geometry-minor" and "pin end moved: pin 13" in texts(ch)[0], texts(ch)


# ---------------------------------------------------------------- the upgrade: schema 3 -> 4
def _age_to_3(lib):
    """Every stored fingerprint as the schema 3 ecadlib wrote it (no pin ends, no stacks)."""
    lg = LG.Ledger(lib)
    out = []
    for line in lg.path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("fingerprint"):
            fp = lg.get_json(e["fingerprint"])
            fp["schema"] = 3
            sym = fp.get("symbol") or {}
            sym.pop("stacks", None)
            for p in (sym.get("pins") or {}).values():
                for x in [p] + (p.get("inst") or []):
                    x.pop("end", None)
            fp["hashes"] = FP.hashes(fp)
            e["fingerprint"], e["digest"] = lg.put_json(fp), fp["hashes"]["digest"]
        out.append(json.dumps(e, sort_keys=True, ensure_ascii=False))
    lg.path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _draw_ao(pins):
    return {"name": "AO3401A", "description": "pmos", "designator": "Q?", "comment": "*", "footprint": None,
            "prims": [("rect", 0, 0, 40, -40)] + [("pin", {"num": n, "name": nm, "type": "passive"}, side, x, y, 30)
                                                  for n, nm, side, x, y in pins], "label_at": ((0, 2), (0, -48))}


def test_a_library_released_with_schema_3_shows_no_change_and_a_new_stack_still_holds():
    """SCHEMA 4 (pin ends, stacks): a library released by the previous ecadlib shows no change after the upgrade (old
    heads are fingerprinted again from their files); stacking D on S after it is held."""
    assert FP.SCHEMA >= 4
    plan, lib = demo()
    _age_to_3(lib)
    n = len(LG.Ledger(lib).lines())
    assert gate(lib).settle(items_of(plan)) == []
    assert len(LG.Ledger(lib).lines()) == n
    status, _ = CG.publish_guard(plan, items_of(plan), "kicad")
    assert all(v is None or v.startswith("not released") for v in status.values()), status
    items = items_of(plan)
    it = item(items, "AO3401A")
    head = FP.of_item(it, lib)["symbol"]["pins"]
    def tenmil(v):
        return round(v / 0.254)
    at = {n: (p["at"], p["orient"]) for n, p in head.items()}
    side = {0: "right", 90: "top", 180: "left", 270: "bottom"}
    pins = [(n, head[n]["raw"], side[o], tenmil(xy[0]), tenmil(xy[1])) for n, (xy, o) in at.items()]
    s_end = next(p for p in pins if p[0] == "2")
    short = [p if p[0] != "3" else ("3", p[1], s_end[2], s_end[3], s_end[4]) for p in pins]
    AL.write_schlib(lib / it["symbol"]["file"], [_draw_ao(short)])
    [row] = gate(lib).settle(items)
    assert row["part"] == "AO3401A" and row["action"] == "hold", row
    assert "now shares a pin end with pin 2 (S): they connect" in json.dumps(row), row


def test_a_fingerprint_stored_without_pin_ends_compares_as_before():
    """diff_stored (no files to read again): a schema 3 fingerprint has no pin ends, so none is reported moved."""
    a = _kicad(QN)
    old = copy.deepcopy(a)
    old["schema"] = 3
    old["symbol"].pop("stacks")
    for p in old["symbol"]["pins"].values():
        for x in [p] + p["inst"]:
            x.pop("end")
    old["hashes"] = FP.hashes(old)
    assert CD.diff_stored(old, a) == []


# ---------------------------------------------------------------- project check agrees with the classifier
def _qn(pins):
    rows = "\n".join(f'      (pin {t} line (at {x} {y} {a}) (length {L}) (name "{n}" (effects (font (size 1.27 1.27)))) '
                     f'(number "{num}" (effects (font (size 1.27 1.27)))))' for num, n, t, x, y, a, L in pins)
    from tests.test_project_check import LIB_SYM
    head, _, rest = LIB_SYM.partition('    (symbol "QN_1_1"\n')
    return head + '    (symbol "QN_1_1"\n' + rows + "))\n)\n"


def _project_row(d, placed, library):
    from ecadlib import project_check as PJ
    from tests import test_project_check as TP
    with mock.patch.object(TP, "LIB_SYM", _qn(placed)):
        pro = TP.make_project(d)
    (Path(d) / "lib" / "TSym.kicad_sym").write_text(_qn(library), encoding="utf-8")
    return next(r for r in PJ.check_kicad(pro, use_global=False)["rows"] if r["kind"] == "symbol")


def test_project_check_and_the_library_classifier_agree_on_stacks():
    """A placed symbol against the library today, by project check and by the classifier: the same action."""
    power = [("1", "G", "input", -5.08, 0, 0, 2.54), ("2", "S", "power_in", 2.54, -5.08, 90, 2.54),
             ("3", "S", "power_in", 2.54, -5.08, 90, 2.54)]
    split = [p if p[0] != "3" else ("3", "S", "power_in", 2.54, 5.08, 270, 2.54) for p in power]
    short = [p if p[0] != "3" else ("3", "D", "passive", 2.54, -5.08, 90, 2.54) for p in QN]
    for placed, library, want, says in ((power, split, "warn", "pins 2, 3 (S) no longer share one pin end"),
                                        (QN, short, "hold", "pin 3 (D) now shares a pin end with pin 2 (S): they connect")):
        with tempfile.TemporaryDirectory() as d:
            row = _project_row(d, placed, library)
            lib_says = CG.decide(CD.diff(_kicad(placed), _kicad(library)), CG.POLICY)["action"]
            assert row["action"] == lib_says == want, (row, lib_says)
            assert any(says in c["text"] for c in row["changes"]), row["changes"]


# ---------------------------------------------------------------- N5-4: an empty Footprint property
def test_an_empty_footprint_property_on_a_placed_symbol_is_held():
    from ecadlib import project_check as PJ
    from tests.test_change_control_round4 import _proj
    with tempfile.TemporaryDirectory() as d:
        pro = _proj(d, inst="")
        sym = next(r for r in PJ.check_kicad(pro, use_global=False)["rows"] if r["kind"] == "symbol")
        assert sym["action"] == "hold", sym
        assert any("Q1 places no footprint (its Footprint property is empty), but the library part's footprint is "
                   "TFp:SOT23" in c["text"] for c in sym["changes"]), sym["changes"]
    with tempfile.TemporaryDirectory() as d:
        pro = _proj(d, inst="~")
        sym = next(r for r in PJ.check_kicad(pro, use_global=False)["rows"] if r["kind"] == "symbol")
        assert sym["action"] == "hold", sym


def test_kicad_export_reuses_a_source_drawn_differently_but_not_one_that_joins_pins():
    """The house rules may redraw the released .SchLib (pins grouped, one end per power pin) while kicad-export keeps
    the KiCad source's drawing: pin ends that moved and power pins of one name stacked or not are how it is drawn
    (released_differs leaves them out). A stack that joins other pins is a difference."""
    base, _ = _altium(REG)
    split, _ = _altium(_moved(_moved(REG, "11", y=-30), "12", y=-40))
    for a, b in ((base, split), (split, base)):
        ch = CD.diff(a, b)
        assert ch and all(c.get("drawing") for c in ch if c["class"] != "cosmetic"), [(c["text"], c.get("drawing")) for c in ch]
    ch = CD.diff(base, _altium(_moved(REG, "13", y=-10))[0])
    assert any(c["class"] == "electrical" and not c.get("drawing") for c in ch), ch


clean_all(globals())        # R-G2-8: the scratch folders a test makes go when it ends
