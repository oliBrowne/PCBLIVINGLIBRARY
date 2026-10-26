"""2026-10-26 bug hunt (gate): dedupe fingerprints, lifecycle maker match, release gate statuses.
Run:  python3 -m tests.test_hunt1026_gate"""
import math, random, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, dedupe as DD, kicad_import as K, lifecycle as L

FP = """(footprint "X"
  (layer "F.Cu")
  (fp_line (start -3 -3) (end 3 -3) (layer "F.CrtYd") (width 0.05))
  (fp_line (start 3 -3) (end 3 3) (layer "F.CrtYd") (width 0.05))
  (fp_line (start 3 3) (end -3 3) (layer "F.CrtYd") (width 0.05))
  (fp_line (start -3 3) (end -3 -3) (layer "F.CrtYd") (width 0.05))
  (pad "1" smd rect (at -1.5 0 {r1}) (size {s1}) (layers "F.Cu" "F.Mask" "F.Paste"))
  (pad "2" smd rect (at 1.5 0 {r2}) (size {s2}) (layers "F.Cu" "F.Mask" "F.Paste"))
)"""


def _fp(r, s="1 2"):
    return FP.format(r1=r, r2=r, s1=s, s2=s)


def _hk(text):
    return DD.fp_hash(DD.fp_geometry_kicad(text))


def _hp(text):
    with tempfile.TemporaryDirectory() as d:
        fp, _ = K.convert_footprint(text, "X")
        AL.write_pcblib(Path(d, "a.PcbLib"), [fp])
        return DD.fp_hash(DD.fp_geometry_pcblib(AL.read_pcblib(Path(d, "a.PcbLib"))[0]))


def test_gate01_diagonal_pads_45_and_135_are_different_footprints():
    # a 1x2 pad at 45 degrees and the same pad at 135 (or -45) have their long side on opposite diagonals
    assert _hk(_fp(45)) != _hk(_fp(135))
    assert _hk(_fp(45)) != _hk(_fp(-45))
    # ... while a 1x2 pad at 135 IS a 2x1 pad at 45, and a turn of 180 is no change
    assert _hk(_fp(135)) == _hk(_fp(45, "2 1"))
    assert _hk(_fp(30)) == _hk(_fp(210))
    for a, b in ((0, 360), (90, 270), (90, -90), (0, 180), (0, 359.99999), (90, 89.99999)):
        assert _hk(_fp(a)) == _hk(_fp(b)), (a, b)
    for r in (45, 135, 30):
        assert _hk(_fp(r)) == _hp(_fp(r)), r            # .PcbLib and .kicad_mod still hash alike


def _corners(x, y, w, h, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return sorted((round(x + c * u - s * v, 3), round(y + s * u + c * v, 3))
                  for u, v in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)))


def test_gate01_pad_tuple_equal_iff_same_copper_property():
    # seeded property: two rectangular pads get the same tuple exactly when their copper outlines coincide
    rnd = random.Random(1026)
    angles = [0, 15, 30, 45, 60, 90, 120, 135, 180, 225, 270, 315, -45, -90, 360]
    sizes = [(1.0, 2.0), (2.0, 1.0), (1.5, 1.5)]
    for _ in range(400):
        (w1, h1), (w2, h2) = rnd.choice(sizes), rnd.choice(sizes)
        a1, a2 = rnd.choice(angles), rnd.choice(angles)
        same = _corners(0, 0, w1, h1, a1) == _corners(0, 0, w2, h2, a2)
        t1, t2 = DD._pad_tuple("1", 0, 0, w1, h1, a1, False), DD._pad_tuple("1", 0, 0, w2, h2, a2, False)
        assert (t1 == t2) == same, ((w1, h1, a1), (w2, h2, a2), t1, t2)


def _mouser(part, listings):
    data = {"Errors": [], "SearchResults": {"Parts": [
        {"ManufacturerPartNumber": part["mpn"], "Manufacturer": m, "LifecycleStatus": st, "AvailabilityInStock": "5"}
        for m, st in listings]}}
    return L.parse_mouser(data, part, L.LifecycleResult("mouser", mpn=part["mpn"]))


def test_gate02_lifecycle_picks_the_parts_own_maker():
    # the same MPN from several makers: each has its own lifecycle; the part's maker must be the one read
    many = [("STMicroelectronics", "Obsolete"), ("onsemi", "Active"), ("Texas Instruments", "Not Recommended for New Designs")]
    for maker, want in (("ON", "active"), ("ON Semiconductor", "active"), ("TI", "nrnd"), ("Texas Instruments", "nrnd"),
                        ("ST", "obsolete"), ("STMicroelectronics", "obsolete"), ("TI(德州仪器)", "nrnd")):
        r = _mouser({"mpn": "LM358D", "manufacturer": maker}, many)
        assert r.found and r.status == want, (maker, r.status, r.manufacturer)
    # a maker none of the listings is by, among two others: another maker's status says nothing about this part
    r = _mouser({"mpn": "LM358D", "manufacturer": "Monolithic Power Systems"}, many)
    assert not r.found and r.status == "unknown"
    # no maker on the part, or one listing only: as before, the first exact MPN
    assert _mouser({"mpn": "LM358D", "manufacturer": ""}, many).status == "obsolete"
    assert _mouser({"mpn": "LM358D", "manufacturer": "TI"}, [("Texas Instruments Inc.", "Active")]).status == "active"


def test_gate02_digikey_other_makers_part_is_not_this_part():
    data = {"Product": {"ManufacturerProductNumber": "2N7002", "Manufacturer": {"Name": "Nexperia USA Inc."},
                        "ProductStatus": {"Status": "Obsolete"}, "QuantityAvailable": 0}}
    r = L.parse_digikey(data, {"mpn": "2N7002", "manufacturer": "onsemi"}, L.LifecycleResult("digikey", mpn="2N7002"))
    assert r.found is False and r.status == "unknown" and "Nexperia" in r.note
    r = L.parse_digikey(data, {"mpn": "2N7002", "manufacturer": "Nexperia"}, L.LifecycleResult("digikey", mpn="2N7002"))
    assert r.found and r.status == "obsolete"
    r = L.parse_digikey(data, {"mpn": "2N7002", "manufacturer": ""}, L.LifecycleResult("digikey", mpn="2N7002"))
    assert r.found and r.status == "obsolete"


def test_gate02_maker_names():
    same = [("TI", "Texas Instruments"), ("Burr-Brown", "Texas Instruments"), ("onsemi", "ON Semiconductor"),
            ("Vishay", "Vishay Siliconix"), ("Rohm", "ROHM Semiconductor"), ("Würth Elektronik", "Wurth Elektronik"),
            ("Diodes Inc.", "Diodes Incorporated"), ("ADI", "Analog Devices Inc."), ("ST", "STMicroelectronics")]
    differ = [("ON", "Monolithic Power Systems"), ("ON", "STMicroelectronics"), ("ON", "ON Shore Technology"),
              ("TI", "Tiger"), ("TI", "STMicroelectronics"), ("Nexperia", "NXP"), ("", ""), ("", "TI")]
    for a, b in same:
        assert L.same_maker(a, b) and L.same_maker(b, a), (a, b)
    for a, b in differ:
        assert not L.same_maker(a, b) and not L.same_maker(b, a), (a, b)


def test_gate03_placeholder_fields_are_empty():
    from ecadlib import release_check as RC
    from tests.test_release_check import make
    with tempfile.TemporaryDirectory() as d:
        for name, v, want in (("Datasheet", "~", "fields: no datasheet link"), ("Datasheet", "N/A", "fields: no datasheet link"),
                              ("Manufacturer", "N/A", "fields: Manufacturer is empty"),
                              ("Manufacturer Part Number", "TBD", "fields: Manufacturer Part Number is empty"),
                              ("Manufacturer", "-", "fields: Manufacturer is empty")):
            it = make(d)
            drop = "ComponentLink2URL" if name == "Datasheet" else name
            it["parameters"] = [p for p in it["parameters"] if p["name"] != drop] + [{"name": name, "value": v}]
            f = [f"{r}: {m}" for r, m in RC.check_item(it, d)[0]]
            assert f == [want], (name, v, f)
        it = make(d)                                       # real values still pass
        it["parameters"] = [p for p in it["parameters"] if p["name"] != "Manufacturer"] + [{"name": "Manufacturer", "value": "NA Corp"}]
        assert RC.check_item(it, d)[0] == []


def test_gate04_release_check_mutation_property():
    """Seeded mutations of a good part: each must hold under its own rule (pins, parity, fields), and a missing or
    implausible 3D height is a note unless [release] height = "hold"."""
    import re
    from ecadlib import release_check as RC
    from tests.test_release_check import SOIC, make, HOLD_HEIGHT
    extra = '  (pad "9" smd roundrect (at 0 0) (size 1.0 1.0) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))\n'
    muts = ["drop_pad", "extra_pad", "pin_no_pad", "renumber", "parity", "no3d", "tall3d", "no_ds", "no_mfr", "no_desc"]
    rnd = random.Random(26)
    for _ in range(30):
        ms = set(rnd.sample(muts, rnd.randint(1, 3)))
        if {"no3d", "tall3d"} <= ms:
            ms.discard("tall3d")
        hold_h = rnd.random() < 0.3
        kmod, pins, height, exp = SOIC, list(range(1, 9)), None, set()
        if "drop_pad" in ms:
            kmod = re.sub(r'  \(pad "%d" .*\n' % rnd.randint(1, 8), "", kmod); exp.add("pins")
        if "extra_pad" in ms:
            kmod = kmod[:kmod.rstrip().rfind(")")] + extra + ")\n"; exp.add("pins")
        if "pin_no_pad" in ms:
            pins.append(10); exp.add("pins")
        if "renumber" in ms:
            pins = [p for p in pins if p != 3] + [11]; exp.add("pins")
        if "no3d" in ms or "tall3d" in ms:
            height = False if "no3d" in ms else 9.0
            if hold_h:
                exp.add("height")
        with tempfile.TemporaryDirectory() as d:
            it = make(d, kmod=kmod, pins=pins, height=height)
            if "parity" in ms:                     # the .kicad_mod changed after the PcbLib was written from it
                p = Path(d, it["footprint"]["kicad_mod"])
                p.write_text(p.read_text().replace("(size 1.965 0.58)", "(size 1.765 0.58)", 1)); exp.add("parity")
            for m, drop in (("no_ds", "ComponentLink2URL"), ("no_mfr", "Manufacturer")):
                if m in ms:
                    it["parameters"] = [x for x in it["parameters"] if x["name"] != drop]; exp.add("fields")
            if "no_desc" in ms:
                it["description"] = ""; exp.add("fields")
            f, w, _ = RC.check_item(it, d, HOLD_HEIGHT if hold_h else None)
            got = {r for r, _ in f}
            if {"drop_pad", "extra_pad"} & ms:
                got.discard("footprint")           # the name check counts the pads too: an extra hold is right
            assert got == exp, (sorted(ms), hold_h, f)
            if ({"no3d", "tall3d"} & ms) and not hold_h:
                assert any(x.startswith("height: ") for x in w), (sorted(ms), w)


def test_gate05_publish_problems_by_status():
    from ecadlib import release_check as RC
    from tests.test_release_check import make
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        for st in ("ready", "existing"):
            assert RC.publish_problems(dict(it, status=st), d) == [], st
        no_status = dict(it); no_status.pop("status")
        assert RC.publish_problems(no_status, d) == []                     # no status reads as ready everywhere
        from ecadlib import altium365 as A                                  # push: was KeyError 'status' (an error row)
        assert A.push([no_status], None, "https://ws", dry_run=True, folder=Path(d))[0]["action"] == "release"
        for st in ("draft", "Ready", "READY", "Held", "", None, "released", "approved"):
            why = RC.publish_problems(dict(it, status=st), d)
            assert why and why[0].startswith("status: "), (st, why)
        assert RC.publish_problems(dict(it, status="duplicate", duplicate_of="K1"), d) == ["duplicate of K1 (dedupe)"]
        gone = dict(it, status="deleted_in_workspace", a365={"id": "x"})
        assert RC.publish_problems(gone, d)[0].startswith("status: deleted in the Altium 365 workspace")
        assert RC.publish_problems(dict(it, status="existing", a365={"id": "x"}), d) == []   # released there: as before


def test_gate05_every_route_ships_only_released_statuses():
    """kicad-export took every status but held / awaiting_approval (a 'draft', a 'Ready' typo, a duplicate and a part
    deleted in the workspace went out), and export-parts listed a broken 'existing' part without checking it."""
    import json
    from ecadlib import kicad_dblib as KD, kicad_export as KX, parts_export as PE
    from tests.test_release_check import make
    statuses = ["ready", "draft", "Ready", "duplicate", "deleted_in_workspace", "existing", "existing"]
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        items = []
        for i, st in enumerate(statuses):
            it = make(d, name=f"P{i}", fp_name=f"SOIC-8_v{i}")
            it["status"] = st
            if st == "duplicate":
                it["duplicate_of"] = "P0"
            if st == "deleted_in_workspace":
                it["a365"] = {"id": "x"}
            items.append(it)
        items[6]["description"] = ""                       # P6: 'existing' but fails the check
        (d / "plan.json").write_text(json.dumps(items))
        quiet = lambda *a, **k: None
        assert sorted(p["part"] for p in KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)["parts"]) == ["P0", "P5"]
        tables, _ = KD.part_rows(d / "plan.json", "mylib")
        assert sorted(r["Part ID"] for rs in tables.values() for r in rs) == ["P0", "P5"]
        assert sorted(r["Part ID"] for r in PE.rows(d / "plan.json")) == ["P0", "P5"]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
            print("ok", n)
