"""2026-10-26 bug hunt (easyeda): EasyEDA conversion, part sources and the HTTP cache. Run:
    python -m tests.test_hunt1026_easyeda"""
import math

from ecadlib import easyeda_convert as EC, sexpr

HEAD = {"x": 4000, "y": 3000}


def _fp(shapes, notes=None):
    return EC.footprint_to_kicad({"head": HEAD, "shape": shapes}, "T", [] if notes is None else notes)


def _pads(text):
    return {str(p[1]): p for p in sexpr.find(sexpr.parse(text), "pad")}


def _rot(pad):
    at = sexpr.first(pad, "at")
    return float(at[3]) % 360 if len(at) > 3 else 0.0


def _rect_pts(cx, cy, w, h, kdeg):
    """The four corners of a w x h pad at (cx, cy), turned kdeg counter-clockwise on screen (KiCad's sense; the
    canvas is y down), in EasyEDA's order (local -x-y, +x-y, +x+y, -x+y)."""
    a = math.radians(kdeg)
    out = []
    for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out += [cx + x * math.cos(a) + y * math.sin(a), cy - x * math.sin(a) + y * math.cos(a)]
    return " ".join(f"{v:.4f}" for v in out)


def _mod(a, p=180):
    d = a % p
    return min(d, p - d)


def test_rotated_rect_pad_follows_its_own_outline():
    # EasyEDA writes a RECT pad's corners beside its rotation; a 45 degree pad whose rotation field says the other way
    # round must not be built mirrored (the outline is what EasyEDA draws)
    for kdeg, field in ((45, 315), (45, 45), (30, -30), (120, 60), (0, 0), (90, 90), (90, 0)):
        pts = _rect_pts(4000, 3000, 4, 2, kdeg)
        notes = []
        pad = _pads(_fp([f"PAD~RECT~4000~3000~4~2~1~~1~0~{pts}~{field}~g~0~~Y~0"], notes))["1"]
        assert _mod(_rot(pad) - kdeg) < 0.01, (kdeg, field, _rot(pad), notes)
        assert any("does not match the pad's own outline" in n for n in notes) == (_mod(kdeg - field) > 0.5), notes


def test_rotated_oval_pad_follows_its_axis():
    # an OVAL's points are its long axis' two ends
    for kdeg, field in ((45, 315), (45, 45), (0, 90)):
        a = math.radians(kdeg)
        ln = 6 - 2                                              # w - h: the axis between the two end circles
        x0, y0 = 4000 - ln / 2 * math.cos(a), 3000 + ln / 2 * math.sin(a)
        x1, y1 = 4000 + ln / 2 * math.cos(a), 3000 - ln / 2 * math.sin(a)
        pad = _pads(_fp([f"PAD~OVAL~4000~3000~6~2~1~~1~0~{x0:.4f} {y0:.4f} {x1:.4f} {y1:.4f}~{field}~g~0~~Y~0"]))["1"]
        assert _mod(_rot(pad) - kdeg) < 0.01, (kdeg, field, _rot(pad))


def test_pad_outline_of_another_size_is_ignored():
    # points that are not this pad's outline (another size): the rotation field stands
    pts = _rect_pts(4000, 3000, 8, 2, 0)
    pad = _pads(_fp([f"PAD~RECT~4000~3000~4~2~1~~1~0~{pts}~45~g~0~~Y~0"]))["1"]
    assert _rot(pad) == 45


def _live(lcsc):
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / "fixtures" / "easyeda_live" / f"{lcsc}.json").read_text(encoding="utf-8"))


def _xy(pad):
    at = sexpr.first(pad, "at")
    return float(at[1]), float(at[2])


def test_footprint_without_head_origin_is_centred_on_its_pads():
    # a head with no x / y put the origin at the canvas' 0, 0: every pad a metre away (4000 x 0.254 mm), unnoticed
    # by footprint_check; the pads' centre is the origin instead, and the notes say so
    import copy
    from ecadlib import easyeda_3d as E3
    ds = copy.deepcopy(_live("C5423")["result"]["packageDetail"]["dataStr"])
    good = _pads(EC.footprint_to_kicad(ds, "T", []))
    ref = E3.model_ref(ds)
    del ds["head"]["x"], ds["head"]["y"]
    notes = []
    bad = _pads(EC.footprint_to_kicad(ds, "T", notes))
    assert any("no origin" in n for n in notes), notes
    for k, p in good.items():
        (x0, y0), (x1, y1) = _xy(p), _xy(bad[k])
        assert abs(x0 - x1) < 0.01 and abs(y0 - y1) < 0.01, (k, _xy(p), _xy(bad[k]))
    r2 = E3.model_ref(ds)                        # the 3D model is placed from the same origin
    assert all(abs(a - b) < 0.01 for a, b in zip(ref["origin"], r2["origin"])), (ref["origin"], r2["origin"])


def _all_pins(node):
    out = []
    for c in node:
        if isinstance(c, list) and c:
            out += [c] if c[0] == "pin" else _all_pins(c)
    return out


def _sym_pins(shapes):
    t = sexpr.parse(EC.symbol_to_kicad({"head": {"x": 400, "y": 300}, "shape": shapes}, "X", "", {}, []))
    return {str(sexpr.first(p, "number")[1]): p for p in _all_pins(t)}


def test_symbol_pin_inversion_and_clock_marks_are_kept():
    # EasyEDA's pin: segment 5 is the inversion circle, segment 6 the clock mark, each "1" when shown (easyeda2kicad
    # reads them the same way); they were all written as plain lines, so an active-low pin lost its circle
    base = "P~show~1~{n}~355~285~180~gge{n}~0^^355~285^^M355,285h10~#880000^^1~368~289~0~P{n}~start~~~#0000FF^^" \
           "1~364~284~0~{n}~end~~~#0000FF^^{d}~362~285^^{c}~M 365 288 L 368 285 L 365 282"
    pins = _sym_pins([base.format(n=1, d=0, c=0), base.format(n=2, d=1, c=0), base.format(n=3, d=0, c=1),
                      base.format(n=4, d=1, c=1)])
    assert {k: str(p[2]) for k, p in pins.items()} == {"1": "line", "2": "inverted", "3": "clock", "4": "inverted_clock"}
    from ecadlib import kicad_import as K       # and Altium's converter reads them
    sym = K.convert_symbol([K._find_symbol(sexpr.parse(EC.symbol_to_kicad(
        {"head": {"x": 400, "y": 300}, "shape": [base.format(n=2, d=1, c=0)]}, "X", "", {}, [])), "X")], "X", None, [], None, [])
    assert [pr[1].get("shape") for pr in sym["prims"] if pr[0] == "pin"] == ["inverted"]


def test_real_symbols_keep_plain_pins():
    for lcsc in ("C5423", "C7593"):
        r = _live(lcsc)["result"]
        t = sexpr.parse(EC.symbol_to_kicad(r["dataStr"], "X", "", {}, []))
        assert {str(p[2]) for p in _all_pins(t)} == {"line"}, lcsc


def test_html_page_answering_the_3d_model_url_is_not_cached():
    # EasyEDA's STEP host serves /<key>/<uuid> (no extension): a captive portal / proxy page answering it with 200 was
    # cached for the TTL, so every run that week made its own model although the network was back
    from ecadlib import easyeda_3d as E3, httpcache as HC
    url = E3.STEP_URL.format("0123456789abcdef0123456789abcdef")
    assert HC.page_not_data(url, "<!DOCTYPE html><html><body>Sign in to the Wi-Fi</body></html>")
    assert not HC.page_not_data(url, "ISO-10303-21;\nHEADER;")


class _R:
    def __init__(self, body, status=200):
        import json
        self.text, self.status, self.error = json.dumps(body) if not isinstance(body, str) else body, status, None

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        import json
        return json.loads(self.text)


def _jlc(items):
    return _R({"data": {"componentPageInfo": {"list": items}}})


def test_jlcpcb_search_takes_the_boms_maker_of_several_exact_matches():
    # clones sell under the same MPN (in other packages too): the first listed was taken whoever made it, and its LCSC
    # number then chose the EasyEDA footprint
    from ecadlib import providers as P
    items = [{"componentCode": "C111", "componentModelEn": "SS34", "componentBrandEn": "MSKSEMI(美森科)",
              "componentSpecificationEn": "SMB"},
             {"componentCode": "C222", "componentModelEn": "SS34", "componentBrandEn": "MDD(辰达半导体)",
              "componentSpecificationEn": "SMA"}]
    res = P.parse_jlcpcb(_jlc(items), "SS34", P.SourceResult("jlcpcb_search"), maker="MDD Microdiode")
    assert res.found and res.lcsc == "C222" and res.manufacturer == "MDD", res
    res = P.parse_jlcpcb(_jlc(items), "SS34", P.SourceResult("jlcpcb_search"))          # no maker: as before
    assert res.lcsc == "C111"
    seen = {}

    class H:
        def post(self, url, body, **kw):
            seen["body"] = body
            return _jlc(items)
    res = P.JLCPCBSearch(H()).lookup({"mpn": "SS34", "manufacturer": "MDD"})
    assert res.lcsc == "C222", res


def test_jlcpcb_search_survives_odd_list_items():
    from ecadlib import providers as P
    for items in (["x", None, 3], {"a": 1}, "list"):
        res = P.parse_jlcpcb(_jlc(items), "SS34", P.SourceResult("jlcpcb_search"))
        assert res.found is False or res.error, (items, res)


def test_same_maker():
    from ecadlib.providers import same_maker as S
    for a, b in (("TI", "Texas Instruments"), ("TI(德州仪器)", "Texas Instruments Inc."), ("ST", "STMicroelectronics"),
                 ("ADI", "Analog Devices Inc."), ("onsemi", "ON Semiconductor"), ("Lite-On", "LITEON(光宝)"),
                 ("Diodes Incorporated", "DIODES(美台)"), ("Würth Elektronik", "Wurth Elektronik"),
                 ("Alpha & Omega Semiconductor", "AOS"), ("muRata(村田)", "Murata Electronics"),
                 ("Vishay Dale", "Vishay Intertech")):
        assert S(a, b) and S(b, a), (a, b)
    for a, b in (("TI", "UMW(友台半导体)"), ("Nexperia", "NXP"), ("", "TI"), ("Texas Instruments", "Microchip")):
        assert not S(a, b) and not S(b, a), (a, b)


def test_official_source_pick_prefers_the_boms_maker_by_name_not_by_letters():
    # lifecycle._pick looked for the BOM maker's first six letters anywhere in the source's maker: "ST" is inside
    # "TexaSInSTruments", "ON" inside "STMicroelectrONics", so an ST line took TI's part (datasheet, LCSC offer)
    from ecadlib import lifecycle as L
    items = [{"mpn": "LM2903D", "m": "Texas Instruments"}, {"mpn": "LM2903D", "m": "STMicroelectronics"},
             {"mpn": "LM2903D", "m": "onsemi"}]
    pick = lambda maker: L._pick(items, {"mpn": "LM2903D", "manufacturer": maker}, lambda x: x["mpn"], lambda x: x["m"])["m"]
    assert pick("ST") == "STMicroelectronics"
    assert pick("ON Semiconductor") == "onsemi"
    assert pick("ON") == "onsemi"
    assert pick("TI") == "Texas Instruments"
    assert pick("Nobody") == "Texas Instruments"                 # no maker agrees: the first exact match, as before
    assert pick("") == "Texas Instruments"


def test_fingerprint_of_stacked_pads_does_not_depend_on_file_order():
    # two pads with one number at one centre (a pad shape made of stacked pads) were kept in file order: the same
    # footprint written with them swapped had another gid and change_diff saw "pad 1 shape rect -> round" (a false hold)
    from ecadlib import change_diff as CD, fingerprint as FP
    a = '(pad "1" smd rect (at 0 0) (size 1 2) (layers "F.Cu" "F.Paste" "F.Mask"))'
    b = '(pad "1" smd circle (at 0 0) (size 1.5 1.5) (layers "F.Cu" "F.Mask"))'
    c = '(pad "2" smd rect (at 3 0) (size 1 2) (layers "F.Cu" "F.Paste" "F.Mask"))'
    mod = lambda *p: '(footprint "X" (version 20221018) (generator x) (layer "F.Cu") (attr smd) ' + " ".join(p) + ")"
    f1, f2 = FP.of_kicad(None, mod(a, b, c)), FP.of_kicad(None, mod(c, b, a))
    assert FP.hashes(f1) == FP.hashes(f2)
    assert CD.diff(f1, f2) == []


def test_fingerprint_of_repeated_pin_number_does_not_depend_on_file_order():
    # a number drawn twice in one unit (two GND pins "4"): the pin table's "first copy" was the file's first, so the
    # same symbol with the copies swapped had another cid and digest
    import random
    from ecadlib import fingerprint as FP, kicad_import as K
    pin = lambda t, x, y, a, nm, n: (f'(pin {t} line (at {x} {y} {a}) (length 2.54) (name "{nm}" (effects (font (size 1.27 1.27)))) '
                                     f'(number "{n}" (effects (font (size 1.27 1.27)))))')
    pins = [pin("power_in", 0, 5.08, 270, "VCC", 8), pin("power_in", 0, -5.08, 90, "GND", 4),
            pin("input", -7.62, 2.54, 0, "IN", 1), pin("power_in", 2.54, -5.08, 90, "GND", 4)]

    def fp(ps):
        t = '(kicad_symbol_lib (symbol "X" (property "Reference" "U" (at 0 0 0)) (symbol "X_1_1" ' + " ".join(ps) + ")))"
        return FP.hashes(FP.of_kicad([K._find_symbol(sexpr.parse(t), "X")]))
    want, rnd = fp(pins), random.Random(7)
    for _ in range(12):
        ps = pins[:]
        rnd.shuffle(ps)
        assert fp(ps) == want


def test_step_in_centimetres_or_metres_is_placed_at_its_real_size():
    # only MILLI and $ (metres) were read; a centimetre file was taken as mm, its height one tenth of the real one
    from ecadlib import easyeda_3d as E3, step_gen as SG
    mm = SG.write_step([SG.box(-1, -1, 0, 1, 1, 0.8, "body")], "t")
    unit = "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )"
    assert unit in mm
    ref = {"origin": (0.0, 0.0), "z": 0.0, "rotation": (0, 0, 0), "through_hole": False}
    for prefix, k in ((".CENTI.", 10.0), ("$", 1000.0), (".MILLI.", 1.0)):
        scaled = SG.write_step([SG.box(-1 / k, -1 / k, 0, 1 / k, 1 / k, 0.8 / k, "body")], "t")
        text = scaled.replace(unit, f"( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT({prefix},.METRE.) )")
        _, info = E3.place(text, ref)
        assert abs(info["height"] - 0.8) < 1e-6 and abs(info["bbox"][3] - 1.0) < 1e-6, (prefix, info)


def _index(fps):
    from ecadlib.kicad_index import KiCadIndex
    idx = KiCadIndex([])
    idx.footprints = {k: {"path": "", "has_3d": False} for k in fps}
    return idx


def test_footprint_by_package_never_guesses_between_lands_of_one_body():
    # a BOM line saying only "SOIC-8" / "QFN-16 3x3mm" took the first indexed footprint of that body (the file system's
    # order), an exposed-pad land or another EP size included; the librarian builds it ("local:", no verify hold), and
    # two EP sizes have the same pad numbers, so nothing later catches it
    soic = ["lib:SOIC-8-1EP_3.9x4.9mm_P1.27mm_EP2.29x3mm", "lib:SOIC-8_3.9x4.9mm_P1.27mm"]
    for fps in (soic, soic[::-1]):
        idx = _index(fps)
        assert idx.find_footprint_for_package("SOIC8", "U", hint="SOIC-8 op amp") == "lib:SOIC-8_3.9x4.9mm_P1.27mm"
        assert idx.find_footprint_for_package("SOIC8", "U", hint="SOIC-8 with exposed pad") == soic[0]
    qfn = ["lib:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm", "lib:QFN-16-1EP_3x3mm_P0.5mm_EP1.45x1.45mm"]
    for fps in (qfn, qfn[::-1]):
        assert _index(fps).find_footprint_for_package("QFN16", "U", hint="QFN-16 3x3mm") is None
    assert _index(qfn[:1]).find_footprint_for_package("QFN16", "U", hint="QFN-16 3x3mm") == qfn[0]   # only one: as before


def test_a_price_written_as_text_does_not_lose_the_part():
    # prices are not used any more (only the LCSC offer's SKU is), yet a DigiKey UnitPrice "$0.52" or a Nexar price
    # "N/A" raised ValueError and the whole answer became "unparseable response": maker, datasheet and LCSC number lost
    from ecadlib import part_lookup as S, providers as P
    prod = {"ManufacturerProductNumber": "LM358DR", "Manufacturer": {"Name": "Texas Instruments"}, "ProductStatus": "Active",
            "DatasheetUrl": "https://www.ti.com/lit/ds/symlink/lm358.pdf",
            "ProductVariations": [{"DigiKeyProductNumber": "296-1", "PackageType": {"Name": "Cut Tape"},
                                   "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": "$0.52"}]}, "odd"]}
    info = S.digikey_info(prod, "digikey", "USD")
    assert info.manufacturer == "Texas Instruments" and info.datasheet.endswith("lm358.pdf")
    assert info.offers[0].breaks == [(1, 0.52)] and info.lifecycle == "active", info
    part = {"mpn": "LM358DR", "manufacturer": {"name": "Texas Instruments"},
            "sellers": [{"company": {"name": "LCSC"}, "offers": [{"sku": "C7950", "prices": [
                {"quantity": 1, "price": "N/A", "currency": "USD"}, {"quantity": 10, "price": "0.1", "currency": "USD"}]}]}]}
    res = P.from_part_info(S.nexar_info(part, "nexar", "USD"), P.SourceResult("nexar"))
    assert res.lcsc == "C7950" and res.manufacturer == "Texas Instruments", res


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
