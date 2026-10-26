"""Change control (#105), the classifier: what the part fingerprint reads and how change_diff classes it.

Stacked and per-unit pins (every copy of a pin number is compared), units, alternate pin functions, custom pad
copper, pad copper layers, paste, pad and footprint attributes, value parsing (3.3V is not 3.0V, 100nF is not 100nH),
secondary MPN / manufacturer fields, and a library released by an older ecadlib (another fingerprint schema) that
must not show every part as changed after an upgrade."""
import copy, json, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, change_cli as CC, change_diff as CD, change_gate as CG, fingerprint as FP
from ecadlib import ledger as LG, sexpr
from tests.test_change_control import demo, gate, item, items_of, texts, write_symbol


# ---------------------------------------------------------------- upgrade: older fingerprint schema
def _age_ledger(lib):
    """Make every head look fingerprinted by an older ecadlib: schema 1, a fingerprint missing what this one reads,
    and its digest (the ledger line keeps the stored digest, as an old release would)."""
    lg = LG.Ledger(lib)
    raw = lg.path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in raw:
        e = json.loads(line)
        if e.get("fingerprint"):
            fp = lg.get_json(e["fingerprint"])
            fp["schema"] = 1
            for p in ((fp.get("symbol") or {}).get("pins") or {}).values():
                p.pop("inst", None)
            fp["hashes"] = FP.hashes(fp)
            e["fingerprint"], e["digest"] = lg.put_json(fp), fp["hashes"]["digest"]
        out.append(json.dumps(e, sort_keys=True, ensure_ascii=False))
    lg.path.write_text("\n".join(out) + "\n", encoding="utf-8")
    __import__("shutil").rmtree(lg.store / "marks", ignore_errors=True)   # an older ecadlib kept no marks (R-G2-3)


def test_library_released_by_an_older_ecadlib_shows_no_change_and_a_real_change_still_holds():
    plan, lib = demo()
    _age_ledger(lib)
    n = len(LG.Ledger(lib).lines())
    items = items_of(plan)
    g = gate(lib)
    rows = g.settle(items)
    assert rows == [], rows
    assert len(LG.Ledger(lib).lines()) == n, "nothing new recorded for unchanged parts"
    old = LG.Ledger(lib).fingerprint(LG.Ledger(lib).head("AO3401A"))
    assert old["schema"] == FP.SCHEMA and old["hashes"]["digest"] == FP.of_item(item(items, "AO3401A"), lib)["hashes"]["digest"]
    it = item(items, "AO3401A")
    g = gate(lib)
    write_symbol(lib, it, [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")])
    [row] = g.settle(items)
    assert row["action"] == "hold" and any("pin 1 was G now S" in t for t in texts(row["changes"])), row


# ---------------------------------------------------------------- helpers
def _diff(a, b):
    ch = CD.diff(a, b)
    return CD.worst(ch), texts(ch)


def _ksym(units, alts=None):
    """A KiCad symbol: units = [(unit name suffix, [(number, name, type, hidden)])]; alts = {number: [(name, type)]}."""
    body = ""
    for suffix, pins in units:
        ps = ""
        for k, (n, nm, t, hide) in enumerate(pins):
            alt = "".join(f' (alternate "{a}" {at} line)' for a, at in (alts or {}).get(n, []))
            ps += (f'(pin {t} line (at -7.62 {k * 2.54} 0) (length 2.54){" hide" if hide else ""} '
                   f'(name "{nm}" (effects (font (size 1.27 1.27)))) (number "{n}" (effects (font (size 1.27 1.27)))){alt})')
        body += f'(symbol "U_{suffix}" {ps})'
    return FP.of_kicad(sexpr.parse(f'(symbol "U" (property "Reference" "U" (at 0 0 0)) (property "Value" "U" (at 0 0 0)) {body})'),
                       part="U")


A_PINS = [("1", "A", "input", False), ("2", "B", "output", False), ("7", "GND", "power_in", False)]
B_PINS = [("3", "VCC", "power_in", True), ("7", "GND", "power_in", True)]


def _P(num, name, typ="input", hidden=False, net=None):
    return {"num": num, "name": name, "type": typ, "hidden": hidden, **({"net": net} if net else {})}


def _apw():
    return [_P("8", "VCC", "power", True, "VCC"), _P("4", "GND", "power", True, "GND")]


A_BASE = [[_P("1", "A1"), _P("2", "B1"), _P("3", "Y1", "output")] + _apw(),
          [_P("5", "A2"), _P("6", "B2"), _P("7", "Y2", "output")] + _apw()]


def _write_multipart(f, parts, name="DUAL"):
    """An Altium multi-part symbol, parts = [[pin dict, ...] per part] (Altium's per-part hidden power pins)."""
    prims, owner = [("rect", 0, 0, 40, -50)], [1]
    for k, pins in enumerate(parts, 1):
        prims.append(("rect", 0, 0, 40, -50))
        owner.append(k)
        for i, p in enumerate(pins):
            prims.append(("pin", p, "left" if i % 2 == 0 else "right", 0 if i % 2 == 0 else 40, -10 * (i + 1), 30))
            owner.append(k)
    AL.write_schlib(f, [{"name": name, "description": "dual", "designator": "U?", "comment": "*", "footprint": None,
                         "prims": prims, "prim_parts": owner, "parts": len(parts), "label_at": ((0, 2), (0, -58))}])


def _asym(tmp, parts, tag):
    f = Path(tmp) / f"{tag}.SchLib"
    _write_multipart(f, parts)
    return FP.of_files(str(f))


def _kmod(pads, extra="", attr="smd"):
    return FP.of_kicad(None, f'(footprint "F" (layer "F.Cu") (attr {attr}) '
                             f'(fp_rect (start -3 -3) (end 3 3) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd")) '
                             + " ".join(pads) + f" {extra})", part="F")


def _smd(num, x, y, w=0.6, h=1.5, layers='"F.Cu" "F.Paste" "F.Mask"', extra=""):
    return f'(pad "{num}" smd rect (at {x} {y}) (size {w} {h}) (layers {layers}){extra})'


def _tht(num, x, y, layers='"*.Cu" "*.Mask"', kind="thru_hole"):
    return f'(pad "{num}" {kind} circle (at {x} {y}) (size 1.7 1.7) (drill 1) (layers {layers}))'


SMD = [_smd(1, -1, 0), _smd(2, 1, 0)]


# ---------------------------------------------------------------- C4: every copy of a pin number
def test_kicad_stacked_pin_copies_are_all_compared():
    base = _ksym([("1_1", A_PINS), ("2_1", B_PINS)])
    for label, b_pins, want in (
            ("renamed", [B_PINS[0], ("7", "VBAT", "power_in", True)], "pin 7 in unit 2 was GND now VBAT"),
            ("retyped", [B_PINS[0], ("7", "GND", "output", True)], "pin 7 in unit 2 (GND) type power_in -> output"),
            ("removed", [B_PINS[0]], "pin 7 in unit 2 (GND) removed"),
            ("unhidden", [B_PINS[0], ("7", "GND", "power_in", False)], "pin 7 in unit 2 (GND) is visible now")):
        cls, t = _diff(base, _ksym([("1_1", A_PINS), ("2_1", b_pins)]))
        assert cls == "electrical" and want in t, (label, t)
    drains = [("9", "D", "passive", False), ("9", "D", "passive", False)]
    same = _ksym([("1_1", A_PINS + drains)])
    assert _diff(same, _ksym([("1_1", A_PINS + drains)])) == (None, [])
    cls, t = _diff(same, _ksym([("1_1", A_PINS + drains[:1] + [("9", "S", "passive", False)])]))
    assert cls == "electrical" and "pin 9 in unit 1 was D now S" in t, t


def test_altium_multipart_per_part_hidden_power_pins_are_all_compared():
    tmp = tempfile.mkdtemp(prefix="ecadlib-mp-")
    base = _asym(tmp, A_BASE, "base")
    assert [x["unit"] for x in base["symbol"]["pins"]["4"]["inst"]] == [1, 2]
    cases = []
    m = copy.deepcopy(A_BASE)
    m[1][4] = _P("4", "VCC", "power", True, "VCC")
    cases.append(("net", m, "pin 4 in unit 2 hidden net GND -> VCC"))
    m = copy.deepcopy(A_BASE)
    m[1][3] = _P("8", "VCC", "passive", True, "VCC")
    cases.append(("type", m, "pin 8 in unit 2 (VCC) type power -> passive"))
    m = copy.deepcopy(A_BASE)
    m[1] = m[1][:3]
    cases.append(("removed", m, "pin 4 in unit 2 (GND) removed"))
    m = copy.deepcopy(A_BASE)
    m[1][3]["hidden"] = False
    cases.append(("unhidden", m, "pin 8 in unit 2 (VCC) is visible now"))
    for label, parts, want in cases:
        cls, t = _diff(base, _asym(tmp, parts, label))
        assert cls == "electrical" and want in t, (label, t)
    assert _diff(base, _asym(tmp, A_BASE, "same"))[0] is None


def test_multipart_hidden_pin_net_change_is_held_end_to_end():
    """critic n_multipart_e2e: part B's copy of hidden pin 3 joins GND instead of VBUS. It used to be released and
    exported as a power short."""
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "AO3401A")
    f = lib / it["symbol"]["file"]
    parts = [[_P("1", "G"), _P("3", "D", "power", True, "VBUS")], [_P("2", "S", "passive"), _P("3", "D", "power", True, "VBUS")]]
    g = gate(lib)
    _write_multipart(f, parts, it["symbol"]["ref"])
    [row] = g.settle(items)
    assert row["action"] == "hold", row
    CG.write_plan(plan, items)
    CC.approve(plan, ["AO3401A"], who="Ann Lee <ann@example.com>", comment="dual")
    items = items_of(plan)
    assert LG.Ledger(lib).head("AO3401A")["revision"] == 2
    m = copy.deepcopy(parts)
    m[1][1] = _P("3", "D", "power", True, "GND")
    g = gate(lib)
    _write_multipart(f, m, it["symbol"]["ref"])
    [row] = g.settle(items)
    assert row["action"] == "hold" and "pin 3 in unit 2 hidden net VBUS -> GND" in texts(row["changes"]), row


# ---------------------------------------------------------------- H2: units
def test_units_split_merge_and_moves_are_electrical():
    one = _ksym([("1_1", A_PINS + B_PINS[:1])])
    two = _ksym([("1_1", A_PINS), ("2_1", B_PINS[:1])])
    for a, b in ((one, two), (two, one)):
        cls, t = _diff(a, b)
        assert cls == "electrical" and any("moved from unit" in x for x in t) and any("symbol units" in x for x in t), t
    common = _ksym([("0_1", A_PINS)])               # one unit: unit 0 (common) and unit 1 are the same unit
    assert _diff(common, _ksym([("1_1", A_PINS)]))[0] in (None, "cosmetic")
    cls, t = _diff(two, _ksym([("1_1", A_PINS[1:]), ("2_1", B_PINS[:1] + A_PINS[:1])]))
    assert cls == "electrical" and "pin 1 (A) moved from unit 1 to unit 2" in t, t


# ---------------------------------------------------------------- H1: alternate pin functions
def test_alternate_pin_functions_are_electrical():
    base = _ksym([("1_1", A_PINS)], {"1": [("TX", "output"), ("RX", "input")]})
    assert _diff(base, _ksym([("1_1", A_PINS)], {"1": [("RX", "input"), ("TX", "output")]}))[0] is None
    for alts in ([("TX", "output")], [("TXD", "output"), ("RX", "input")], [("TX", "input"), ("RX", "input")], []):
        cls, t = _diff(base, _ksym([("1_1", A_PINS)], {"1": alts}))
        assert cls == "electrical" and any("alternate functions" in x for x in t), (alts, t)


# ---------------------------------------------------------------- H3, H4, M1, M5: footprints
def test_custom_pad_copper_shape_is_geometry_major():
    def cpad(prim, rot=0):
        r = f" {rot}" if rot else ""
        return (f'(pad "3" smd custom (at 3 0{r}) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask") '
                f'(options (clearance outline) (anchor rect)) (primitives {prim}))')
    L = '(gr_poly (pts (xy -0.25 -0.25) (xy 1.5 -0.25) (xy 1.5 0.25) (xy 0.25 0.25) (xy 0.25 1.5) (xy -0.25 1.5)) (width 0) (fill yes))'
    SQ = '(gr_poly (pts (xy -0.25 -0.25) (xy 1.5 -0.25) (xy 1.5 1.5) (xy -0.25 1.5)) (width 0) (fill yes))'
    MIR = L.replace("(xy 1.5 -0.25) (xy 1.5 0.25) (xy 0.25 0.25) (xy 0.25 1.5)", "(xy 1.5 -0.25) (xy 1.5 1.5) (xy 1.0 1.5) (xy 1.0 0.25)")
    base = _kmod(SMD + [cpad(L)])
    assert _diff(base, _kmod(SMD + [cpad(L)])) == (None, [])
    for label, p in (("square, same box", cpad(SQ)), ("mirrored", cpad(MIR)), ("turned 180", cpad(L, 180))):
        cls, t = _diff(base, _kmod(SMD + [p]))
        assert cls == "geometry-major" and any("custom copper" in x for x in t), (label, t)


def test_pad_copper_layers():
    base = _kmod(SMD + [_tht(3, 0, 3)])
    cls, t = _diff(base, _kmod(SMD + [_tht(3, 0, 3, '"F.Cu" "*.Mask"')]))
    assert cls == "geometry-major" and "pad 3 copper layers back + front -> front" in t, t
    cls, t = _diff(base, _kmod(SMD + [_tht(3, 0, 3, '"F&B.Cu" "*.Mask"')]))
    assert cls == "geometry-minor" and any("inner layers" in x for x in t), t
    npth = _kmod(SMD + [_tht("", 0, 3, '"F&B.Cu" "*.Mask"', "np_thru_hole")])     # 89 such edits in KiCad 9 -> 10
    assert _diff(npth, _kmod(SMD + [_tht("", 0, 3, '"*.Cu" "*.Mask"', "np_thru_hole")]))[0] is None


def test_exposed_pad_losing_all_its_paste_is_held():
    ep = _smd(3, 0, 3, 2, 2, '"F.Cu" "F.Mask"')
    win = [f'(pad "" smd rect (at {x} {y}) (size 0.8 0.8) (layers "F.Paste"))' for x in (-0.5, 0.5) for y in (2.5, 3.5)]
    base = _kmod(SMD + [ep] + win)
    cls, t = _diff(base, _kmod(SMD + [ep]))
    assert cls == "geometry-major" and "pad 3 has no solder paste now (its paste windows were removed)" in t, t
    cls, _ = _diff(base, _kmod(SMD + [ep] + ['(pad "" smd rect (at 0 3) (size 1.8 1.8) (layers "F.Paste"))']))
    assert cls == "geometry-minor"                     # paste split 4 -> 1: still pasted


def test_pad_and_footprint_details_are_fingerprinted():
    base = _kmod(SMD)
    cases = (
        ("zone_connect", _kmod([_smd(1, -1, 0, extra=" (zone_connect 0)"), SMD[1]]), "geometry-major", "pad 1 zone connection"),
        ("footprint zone_connect", _kmod(SMD, "(zone_connect 2)"), "geometry-major", "footprint zone connect"),
        ("exclude_from_bom", _kmod(SMD, attr="smd exclude_from_bom"), "geometry-major", "footprint now excluded from the BOM"),
        ("dnp", _kmod(SMD, attr="smd dnp"), "geometry-major", "footprint now do not populate"),
        ("smd -> through_hole", _kmod(SMD, attr="through_hole"), "geometry-minor", "footprint attributes smd -> through_hole"),
        ("keepout", _kmod(SMD, '(zone (net 0) (net_name "") (layer "F.Cu") (hatch edge 0.5) (keepout (tracks not_allowed) '
                               '(vias not_allowed) (pads allowed) (copperpour not_allowed) (footprints allowed)) '
                               '(polygon (pts (xy -1 -1) (xy 1 -1) (xy 1 1) (xy -1 1))))'), "geometry-minor", "keepout zones"),
        ("edge cuts", _kmod(SMD, '(fp_line (start -3 0) (end 3 0) (stroke (width 0.1)) (layer "Edge.Cuts"))'), "geometry-major",
         "board edge (Edge.Cuts)"),
        ("pad property", _kmod([_smd(1, -1, 0, extra=" (property pad_prop_heatsink)"), SMD[1]]), "geometry-minor",
         "fabrication property"),
        ("paste margin", _kmod(SMD, "(solder_paste_margin -0.05)"), "geometry-minor", "footprint solder paste margin"))
    for label, new, want_cls, want in cases:
        cls, t = _diff(base, new)
        assert cls == want_cls and any(want in x for x in t), (label, t)
    a = _kmod([_smd(1, -1, 0, extra=' (pinfunction "GND") (pintype "passive")'), SMD[1]])
    assert _diff(base, a)[0] is None, "a board fills in pin functions; a library footprint without them is the same"
    cls, t = _diff(a, _kmod([_smd(1, -1, 0, extra=' (pinfunction "VCC") (pintype "passive")'), SMD[1]]))
    assert cls == "geometry-minor" and "pad 1 pin function GND -> VCC" in t, t


# ---------------------------------------------------------------- C3: values
def test_value_parsing_units_and_decimals():
    from ecadlib.kicad_index import parse_value
    for s, want in (("3.3V", (3.3, "V")), ("3.9V", (3.9, "V")), ("3.0V", (3.0, "V")), ("1.8V", (1.8, "V")),
                    ("5.1V", (5.1, "V")), ("0.5W", (0.5, "W")), ("1.5A", (1.5, "A")), ("32.768kHz", (32768.0, "Hz")),
                    ("100nH", (1e-7, "H")), ("100nF", (1e-7, "F")), ("3V3", (3.3, "V")), ("10mA", (0.01, "A")),
                    ("100nF,50V", (1e-7, "F")), ("10kΩ", (1e4, "R")), ("4k7", (4700.0, ""))):
        v, u = parse_value(s, True)
        assert v is not None and abs(v - want[0]) <= 1e-9 * want[0] + 1e-15 and u == want[1], (s, v, u)
    assert parse_value("74HC595") is None and parse_value("1N4148") is None and parse_value("R1,R2") is None


def _vfp(v, mpn="PN"):
    return FP.build("x", params={"Value": v, "Manufacturer Part Number": mpn})


def test_value_changes_that_parse_alike_are_identity():
    for a, b in (("3.3V", "3.9V"), ("3.3V", "3.0V"), ("1.8V", "1.0V"), ("5.1V", "5.0V"), ("0.5W", "0W"),
                 ("32.768kHz", "32kHz"), ("100nF", "100nH"), ("100nF 50V", "100nF 6.3V"), ("10k 1%", "10k 5%")):
        assert _diff(_vfp(a), _vfp(b))[0] == "identity", (a, b)
    for b in ("0.1uF", "100n", "100 nF", "0.1µF", "100nF"):
        assert _diff(_vfp("100nF"), _vfp(b))[0] in (None, "cosmetic"), b
    assert _diff(_vfp("100nF 50V"), _vfp("0.1uF/50V"))[0] == "cosmetic"
    # a rating or tolerance added says more and contradicts nothing; dropped, it is another part unless the MPN pins it
    assert _diff(_vfp("10k", ""), _vfp("10k 1%", ""))[0] == "cosmetic"
    assert _diff(_vfp("10k 1%", ""), _vfp("10k", ""))[0] == "identity"
    assert _diff(_vfp("10k 1%"), _vfp("10k"))[0] == "cosmetic"


def test_value_hand_edit_100nf_to_100nh_is_held():
    """tester s_val: the value hand-edited to another unit (or 3.3V -> 3.9V) used to pass as 'the same value'."""
    plan, lib = demo()
    items = items_of(plan)
    it = item(items, "CAP-0402-100nF")
    g = gate(lib)
    for p in it["parameters"]:
        if p["name"] == "Value":
            p["value"] = "100nH"
    [row] = g.settle(items)
    assert row["action"] == "hold" and row["changes"][0]["class"] == "identity", row


# ---------------------------------------------------------------- H7: secondary identity fields
def test_secondary_mpn_and_manufacturer_fields():
    def fp(**kw):
        return FP.build("x", params=kw)
    cls, t = _diff(fp(**{"Manufacturer Part Number 1": "A1"}), fp(**{"Manufacturer Part Number 1": "B1"}))
    assert cls == "identity" and "MPN changed from A1 to B1" in t, t
    a = {"Manufacturer Part Number": "A1", "MPN": "A1", "Manufacturer": "TI", "Mfr": "TI"}
    assert "MPN" in FP.other_fields(a) and "Mfr" in FP.other_fields(a) and "Manufacturer" not in FP.other_fields(a)
    for k, v, want in (("MPN", "B2", "identity"), ("MPN", "A-1", "cosmetic"), ("Mfr", "ADI", "identity")):
        cls, t = _diff(fp(**a), fp(**dict(a, **{k: v})))
        assert cls == want, (k, v, t)
    cls, t = _diff(fp(**a), fp(**{k: v for k, v in a.items() if k != "MPN"}))
    assert cls == "cosmetic" and "field MPN removed (was A1)" in t, t
