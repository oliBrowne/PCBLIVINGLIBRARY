"""Fixes 2026-10-26 (stdparts): review round 4 on standard tables, resolve, parity and fp_share (R-SP-1..7)."""
import contextlib, io, json
from pathlib import Path

from tests.fixtures_lite import lite


# --- R-SP-1: `ecadlib review` never applies a guessed pin map without the person's OK -----------------------------

def _review_fake6(d, answers):
    from tests import test_resolve as TR, test_librarian as TL
    from ecadlib import kicad_import as K, librarian as L, release_check as RC, resolve as R, review_cli as RV
    syms, foots = dict(TR.SYMS), dict(TR.FOOTS)
    TR.SYMS.clear(); TR.FOOTS.clear()
    try:
        TR.SYMS["Test:FAKE6"] = TL.ksym("FAKE6", ["1", "2", "3", "4", "6"], "U")       # pin 5 N.C., not drawn
        TR.FOOTS["Package_TO_SOT_SMD:SOT-23-5_FAKE"] = TR.kmod("SOT-23-5_FAKE", ["1", "2", "3", "4", "5"])
        http = TR.FakeHTTP()
        r = L.Run(d, http, None, [], [])
        it = TR.item("FAKE6", "ICs", "FAKE6 regulator", "FAKE6", "SOT-23-5_FAKE")
        st = {"name": "FAKE6", "symbol": None, "footprint": None}
        r.put_kicad_sym(st, K.load_symbol("Test:FAKE6", http)[0], "FAKE6", "SOT-23-5_FAKE", "kicad:Test:FAKE6")
        it["symbol"]["file"], it["source"]["symbol"] = st["symbol"]["file"], "kicad:Test:FAKE6"
        RC.apply([it], d)
        plan = d / "plan.json"
        plan.write_text(json.dumps([it], indent=2))
        src = R.Sources(d / "cache", http=http)
        out, asked = io.StringIO(), []
        ans = iter(answers)

        def inp(p):
            asked.append(p)
            return next(ans)
        old = R.Sources
        R.Sources = lambda *a, **k: src
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                RV.main([str(plan)], inp=inp)
        finally:
            R.Sources = old
        return out.getvalue(), asked, json.loads(plan.read_text())[0]
    finally:
        TR.SYMS.clear(); TR.SYMS.update(syms); TR.FOOTS.clear(); TR.FOOTS.update(foots)


@lite
def test_review_shows_a_guessed_pin_map_and_does_not_apply_it_unasked(tmp_path):
    """Picking a candidate whose pins and pads differ used to apply resolve.compare's guess {6->5} silently and the
    part came out ready. The card now shows the guess; declining it leaves the part held, without a map."""
    text, asked, it = _review_fake6(tmp_path, ["1", "n", "q"])
    assert "6->5" in text.split("is now")[0], text
    assert it["status"] == "held", it["status"]
    assert not it["symbol"].get("pin_map"), it["symbol"]
    assert any("6->5" in p for p in asked), asked


@lite
def test_review_applies_a_guessed_pin_map_only_after_a_yes(tmp_path):
    text, asked, it = _review_fake6(tmp_path, ["1", "y", "q"])
    assert it["symbol"].get("pin_map") == {"6": "5"}, it["symbol"]


# --- R-SP-2: the MPN-family fallback never builds another land than the BOM names -------------------------------

def test_family_fallback_does_not_override_a_named_land():
    """A known MPN family (2N3904 TO-92, AMS1117 SOT-223, MMBT3904 SOT-23) settled any text that merely contained the
    family name: TO-92_Wide (2.54 mm) became TO-92_Inline (1.27 mm), TO-92Mini-2 (2 pads) a 3-pad land, SOT-23W
    SOT-23, SOT-223-5/-6/-8 SOT-223-3_TabPin2. Every KiCad name now builds its own land or nothing."""
    import re
    from ecadlib import stdparts as S
    mpn_for = {"SOT-23": ("Q", "MMBT3904"), "TO-92_Inline": ("Q", "2N3904"), "SOT-223-3_TabPin2": ("U", "AMS1117-3.3"),
               "TO-220-3_Vertical": ("U", "L7805CV"), "SOT-89-3": ("U", "78L05")}
    fam = {"SOT-23": r"SOT-?23(?!\d)", "TO-92_Inline": r"TO-?92", "SOT-223-3_TabPin2": r"SOT-?223|TO-?261",
           "TO-220-3_Vertical": r"TO-?220", "SOT-89-3": r"SOT-?89"}
    bad = []
    for name, lib in sorted(S.kicad_names().items()):
        for pk, rx in fam.items():
            if not re.search(rx, name, re.I):
                continue
            kind, mpn = mpn_for[pk]
            for t in (name, f"{lib}:{name}"):
                got = S.resolve([t], kind, mpn, "")["footprint"]
                # bare 'SOT-223' / 'TO-92' are the package names distributors use (the family settles the tab)
                if got and got != name and not (t in ("SOT-223", "TO-92") and got == pk):
                    bad.append((t, mpn, got))
    assert not bad, bad


def test_family_fallback_keeps_the_family_names_it_is_for():
    from ecadlib import stdparts as S
    for texts, kind, mpn, want in ((["SOT-223"], "U", "AMS1117-3.3", "SOT-223-3_TabPin2"),
                                   (["SOT-223-3"], "U", "AMS1117-3.3", "SOT-223-3_TabPin2"),
                                   (["TO-261AA"], "U", "LM1117MPX-3.3", "SOT-223-3_TabPin2"),
                                   (["TO-92"], "Q", "2N3904", "TO-92_Inline"),
                                   (["SOT-23"], "Q", "MMBT3904", "SOT-23")):
        assert S.resolve(texts, kind, mpn, "")["footprint"] == want, (texts, S.resolve(texts, kind, mpn, ""))
    for texts, kind, mpn in ((["TO-92 staggered"], "Q", "2N3904"), (["TO-92 wide leads"], "Q", "2N3904"),
                             (["SOT-23W"], "Q", "MMBT3904"), (["SOT-223-5"], "U", "AMS1117-3.3"),
                             (["SOT-223 array"], "U", "AMS1117-3.3")):
        assert S.resolve(texts, kind, mpn, "")["footprint"] is None, (texts, S.resolve(texts, kind, mpn, ""))


def test_library_prefixed_to92_is_kicads_own_land():
    """'Package_TO_SOT_THT:TO-92' names KiCad's TO-92 footprint (not the table's TO-92_Inline); bare 'TO-92' is the
    package name and keeps meaning the inline land."""
    from ecadlib import stdparts as S
    assert S.resolve(["Package_TO_SOT_THT:TO-92"], "Q", "2N3904", "")["footprint"] is None
    assert S.footprint_for(["Package_TO_SOT_THT:TO-92"], None)[0] is None
    assert S.footprint_for(["TO-92"], None)[0] == "TO-92_Inline"


# --- R-SP-3: maker MPN size rules apply to their own maker's series only -----------------------------------------

def test_samsung_rc_resistor_size_is_metric_not_yageo_imperial():
    """Samsung RC0603F103CS is the RC series 0201 (0603 metric); the Yageo rule read it as an imperial 0603."""
    from ecadlib import stdparts as S
    assert S.mpn_size("RC0603F103CS") == ("R", "0201")
    assert S.mpn_size("RC1005F103CS") == ("R", "0402")
    assert S.mpn_size("RC1608J102CS") == ("R", "0603")
    assert S.mpn_size("RC3216F1001CS") == ("R", "1206")
    assert S.resolve([""], "R", "RC0603F103CS")["footprint"] == "R_0201_0603Metric"
    # Yageo keeps its imperial codes, every series form
    for mpn, size in (("RC0603FR-0710KL", "0603"), ("RC0402JR-071KL", "0402"), ("RT0603BRD0710KL", "0603"),
                      ("AC0805FR-0710KL", "0805"), ("RL1206FR-070R1L", "1206"), ("AF0402FR-0710KL", "0402")):
        assert S.mpn_size(mpn) == ("R", size), mpn


# --- R-SP-4: fp_share compares custom-pad copper, paste / mask windows and the copper side ------------------------

def _qfn(ep_pad, extra=""):
    leads = "\n".join(f'  (pad "{i + 1}" smd rect (at -1.5 {-0.75 + 0.5 * i:g}) (size 0.8 0.3) (layers "F.Cu" "F.Paste" "F.Mask"))'
                      for i in range(4))
    return (f'(footprint "QFN-X" (layer "F.Cu")\n  (fp_rect (start -2 -2) (end 2 2) (stroke (width 0.05) (type solid)) '
            f'(fill none) (layer "F.CrtYd"))\n{leads}\n{ep_pad}\n{extra})')


EP_FULL = _qfn('  (pad "5" smd rect (at 0 0) (size 1.7 1.7) (layers "F.Cu" "F.Paste" "F.Mask"))')
EP_WINDOWS = _qfn('  (pad "5" smd rect (at 0 0) (size 1.7 1.7) (layers "F.Cu" "F.Mask"))',
                  "\n".join(f'  (pad "" smd rect (at {x:g} {y:g}) (size 0.6 0.6) (layers "F.Paste"))'
                            for x in (-0.45, 0.45) for y in (-0.45, 0.45)))
EP_NOPASTE = _qfn('  (pad "5" smd rect (at 0 0) (size 1.7 1.7) (layers "F.Cu" "F.Mask"))')
_CUST = ('  (pad "5" smd custom (at 0 0) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask") (options (clearance outline) '
         '(anchor rect)) (primitives (gr_poly (pts {pts}) (width 0) (fill yes))))')
EP_CUST_SQUARE = _qfn(_CUST.format(pts="(xy -0.85 -0.85) (xy 0.85 -0.85) (xy 0.85 0.85) (xy -0.85 0.85)"))
EP_CUST_BAR = _qfn(_CUST.format(pts="(xy -0.2 -1.5) (xy 0.2 -1.5) (xy 0.2 1.5) (xy -0.2 1.5)"))
EP_BOTTOM = EP_FULL.replace('(pad "5" smd rect (at 0 0) (size 1.7 1.7) (layers "F.Cu" "F.Paste" "F.Mask"))',
                            '(pad "5" smd rect (at 0 0) (size 1.7 1.7) (layers "B.Cu" "B.Paste" "B.Mask"))')


def test_fp_share_digest_sees_custom_copper_paste_and_side():
    from ecadlib import fp_share as FS
    d = {k: FS.digest(v) for k, v in dict(full=EP_FULL, windows=EP_WINDOWS, nopaste=EP_NOPASTE, square=EP_CUST_SQUARE,
                                          bar=EP_CUST_BAR, bottom=EP_BOTTOM).items()}
    assert len(set(d.values())) == len(d), d
    assert FS.digest(EP_CUST_BAR) == FS.digest(EP_CUST_BAR.replace("(xy -0.2 -1.5)", "(xy -0.200001 -1.5)"))


def test_fp_share_digest_of_a_plain_footprint_is_unchanged():
    """Footprints with nothing of the above keep the hash workspaces already recorded (so a re-run shares again)."""
    from ecadlib import dedupe as DD, fp_share as FS
    assert FS.digest(EP_FULL) == DD.fp_hash(DD.fp_geometry_kicad(EP_FULL))


@lite
def test_fp_share_pick_gives_a_part_with_other_paste_its_own_file(tmp_path):
    from ecadlib import fp_share as FS
    (tmp_path / "a.kicad_mod").write_text(EP_FULL)
    written = {str(tmp_path / "models/PcbLib/QFN-16.PcbLib"): {"file": "models/PcbLib/QFN-16.PcbLib", "kicad_mod": "a.kicad_mod",
               "name": "QFN-16", "hash": FS.digest(EP_FULL), "source": "datasheet", "by": "PARTA"}}
    got = FS.pick(written, tmp_path / "models", "QFN-16", EP_WINDOWS, "datasheet", "PARTB", tmp_path, {})
    assert got[0] != "QFN-16", got


# --- R-SP-5: parity compares copper per pad and footprint copper graphics with their layer ------------------------

def _pcblib(tmp_path, text, edit=None):
    from ecadlib import kicad_import as K, altium_lib as AL
    f, _ = K.convert_footprint(text, "X", notes=[])
    if edit:
        edit(f)
    p = tmp_path / "X.PcbLib"
    AL.write_pcblib(p, [f])
    return AL.read_pcblib(p)[0]


_CU_RECT = '  (fp_rect (start 1.2 -1) (end 1.6 1) (stroke (width 0) (type solid)) (fill solid) (layer "F.Cu"))'
_EP_CUSTOM = ('  (pad "5" smd custom (at 0 0) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask") (options (clearance outline) '
              '(anchor rect)) (primitives (gr_poly (pts (xy -0.9 -0.15) (xy 0.9 -0.15) (xy 0.9 0.15) (xy -0.9 0.15)) '
              '(width 0) (fill yes))))')
_EP_PLAIN = '  (pad "5" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask"))'


@lite
def test_parity_lost_custom_pad_shape_is_an_error_even_with_footprint_copper_elsewhere(tmp_path):
    """A footprint copper rect away from the pad used to make the whole-copper box 'the same' and the lost custom
    shape a warning (warnings do not hold)."""
    from ecadlib import parity as P
    diffs = P.footprint_pair(_qfn(_EP_CUSTOM, _CU_RECT), _pcblib(tmp_path, _qfn(_EP_PLAIN, _CU_RECT)))
    errs = [d["msg"] for d in diffs if d["level"] == "error"]
    assert any("pad 5" in m and "shape" in m for m in errs), diffs


@lite
def test_parity_compares_footprint_copper_graphics_with_their_layer(tmp_path):
    from ecadlib import parity as P
    tht = ('(footprint "X" (layer "F.Cu")\n'
           '  (fp_rect (start -2 -2) (end 2 2) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))\n'
           '  (pad "1" thru_hole circle (at -1 0) (size 1.6 1.6) (drill 0.8) (layers "*.Cu" "*.Mask"))\n'
           '  (pad "2" thru_hole circle (at 1 0) (size 1.6 1.6) (drill 0.8) (layers "*.Cu" "*.Mask"))\n'
           '  {cu})')
    front = '(fp_poly (pts (xy -1 1.0) (xy 1 1.0) (xy 1 1.6) (xy -1 1.6)) (stroke (width 0) (type solid)) (fill solid) (layer "F.Cu"))'
    back = '(fp_rect (start -0.5 -1.8) (end 0.5 -1.2) (stroke (width 0) (type solid)) (fill solid) (layer "B.Cu"))'
    same = P.footprint_pair(tht.format(cu=front), _pcblib(tmp_path, tht.format(cu=front)))
    assert not [d for d in same if d["level"] == "error"], same
    lost = P.footprint_pair(tht.format(cu=front), _pcblib(tmp_path, tht.format(cu=front), lambda f: f.update(regions=[])))
    assert [d for d in lost if d["level"] == "error"], lost
    bcu = P.footprint_pair(tht.format(cu=front + " " + back), _pcblib(tmp_path, tht.format(cu=front + " " + back)))
    assert [d for d in bcu if d["level"] == "error" and "copper" in d["what"]], bcu      # B.Cu dropped by the converter


@lite
def test_parity_still_softens_a_pad_whose_copper_is_drawn_as_pad_plus_graphic(tmp_path):
    """A plain pad plus a footprint copper polygon touching it is the same copper as one custom pad: a warning."""
    from ecadlib import parity as P
    cu = '  (fp_poly (pts (xy 0.25 -0.15) (xy 0.9 -0.15) (xy 0.9 0.15) (xy 0.25 0.15)) (stroke (width 0) (type solid)) (fill solid) (layer "F.Cu"))'
    cust = _EP_CUSTOM.replace("(xy -0.9 -0.15)", "(xy 0 -0.15)").replace("(xy -0.9 0.15)", "(xy 0 0.15)")
    diffs = P.footprint_pair(_qfn(_EP_PLAIN, cu), _pcblib(tmp_path, _qfn(cust)))
    assert not [d for d in diffs if d["level"] == "error"], diffs


# --- R-SP-6: a metric / mm word next to a size code makes it metric; unclear is no size ----------------------------

def test_metric_word_next_to_the_code_reads_metric():
    from ecadlib import stdparts as S
    for t in ("0603 (metric)", "Metric 0603", "metric: 0603", "0603 [mm]", "0603(mm)", "mm: 0603", "Metric size 0603",
              "SMD 0603 (metric code)"):
        assert S.chip_size(t) == "0201", (t, S.chip_size(t))
        assert S.footprint_for([t], "R")[0] == "R_0201_0603Metric", t
    assert S.chip_size("1005 (metric)") == "0402"
    assert S.chip_size("0805 (metric)").startswith("conflict")        # 0805 is no metric code: refuse
    assert S.chip_size("SMD 0603 resistor, metric").startswith("conflict")   # the marker names no code: refuse


def test_metric_word_keeps_the_readings_that_were_right():
    from ecadlib import stdparts as S
    for t, want in (("0603", "0603"), ("0603 (1608 Metric)", "0603"), ("0805 (2012 metric)", "0805"),
                    ("R_0603_1608Metric", "0603"), ("0603 Metric", "0201"), ("1608 Metric", "0603"),
                    ("Chip resistor 0603 (1.6 x 0.8 mm)", "0603"), ("RES 10K 0603 1.6mm x 0.8mm", "0603"),
                    ("0603 (inch)", "0603"), ("0402 [1005 Metric]", "0402")):
        assert S.chip_size(t) == want, (t, S.chip_size(t))


# --- R-SP-7: an LED word makes a part an LED only when the description says it is one ----------------------------

NOT_LEDS = ("Schottky diode 40V 1A, LED backlight boost", "Zener 5.1V for LED string protection",
            "Rectifier 1A for LED driver", "TVS, LED lighting surge", "ESD protection diode for LED strip")
LEDS = ("LED red 0603", "Green LED, 525nm", "LED SMD 0805 white", "Red LED diffused", "LED, blue, 470 nm",
        "LED white 3W, ESD protection 8 kV", "LED array, 4 red", "LED lamp 5 mm", "LED current 20 mA red")


def test_led_word_in_another_diodes_description_is_not_an_led():
    from ecadlib import stdparts as S
    for d in NOT_LEDS:
        assert S.part_kind("Components/Diodes", "SOD-123", d) == "D", d
    for d in LEDS:
        assert S.part_kind("Components/Diodes", "", d) == "LED", d
    assert S.part_kind("Components/Diodes", "LED_0603_1608Metric", "Schottky") == "LED"     # the footprint says so


def test_led_as_the_head_noun_or_with_its_colour_is_an_led():
    """R-FX5-5: R-SP-7 made real LEDs diodes ('Zener-protected white LED' got the D_Zener symbol)."""
    from ecadlib import stdparts as S
    for d in ("Zener-protected white LED", "ESD protected LED, white", "TVS protected LED", "LED backlight white 3528",
              "Side view LED backlight, white", "LED lighting 3W white", "LED dimming white 2835", "LED string white",
              "LED protect", "Status LED (green), protects nothing", "Infrared emitting diode (IR LED)"):
        assert S.part_kind("Components/Diodes", "", d) == "LED", d
    for d in ("TVS for 5050 LED strip", "LED driver diode", "Photodiode with LED", "Schottky diode for LED driver"):
        assert S.part_kind("Components/Diodes", "", d) == "D", d


def test_librarian_passive_kind_agrees():
    from ecadlib import librarian as L
    item = lambda d: {"folder": "Components/Diodes", "footprint": {"ref": "SOD-123"}, "description": d}
    for d in NOT_LEDS:
        assert L.passive_kind(item(d)) is None, d
    for d in LEDS:
        assert L.passive_kind(item(d)) == "LED", d


# --- R-FX5-1 / R-FX5-4: the copper the board gets is compared, touching a pad or not, whatever shapes drew it -------

_NETTIE = ('(footprint "NetTie-2_THT" (version 20221018) (layer "F.Cu")\n'
           '  (fp_rect (start -2 -1.5) (end 2 1.5) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))\n'
           '  (pad "1" thru_hole circle (at -1 0) (size 1.2 1.2) (drill 0.6) (layers "*.Cu" "*.Mask"))\n'
           '  (pad "2" thru_hole circle (at 1 0) (size 1.2 1.2) (drill 0.6) (layers "*.Cu" "*.Mask"))\n'
           '  {cu})')
_BRIDGE_F = '(fp_line (start -1 0) (end 1 0) (stroke (width 0.4) (type solid)) (layer "F.Cu"))'
_BRIDGE_B = '(fp_line (start -1 0) (end 1 0) (stroke (width 0.4) (type solid)) (layer "B.Cu"))'


def _errs(diffs):
    return [d["msg"] for d in diffs if d["level"] == "error"]


@lite
def test_parity_copper_touching_pads_is_compared(tmp_path):
    """A net tie's bridge touches both pads: before, it was merged into the pads' boxes and never compared, so a
    .PcbLib without it passed."""
    from ecadlib import altium_lib as AL, parity as P
    text = _NETTIE.format(cu=_BRIDGE_F)
    assert not _errs(P.footprint_pair(text, _pcblib(tmp_path, text))), "a faithful conversion passes"
    lost = P.footprint_pair(text, _pcblib(tmp_path, text, lambda f: f.update(tracks=[x for x in f["tracks"] if x[-1] != AL.TOP])))
    assert any("F.Cu copper" in m for m in _errs(lost)), lost


@lite
def test_parity_bottom_copper_bridge_lost_by_the_converter_is_an_error(tmp_path):
    from ecadlib import parity as P
    text = _NETTIE.format(cu=_BRIDGE_F + " " + _BRIDGE_B)
    assert any("B.Cu copper" in m for m in _errs(P.footprint_pair(text, _pcblib(tmp_path, text))))


@lite
def test_librarian_holds_a_footprint_whose_copper_was_dropped(tmp_path):
    from ecadlib import librarian as L
    r = L.Run(tmp_path, None, None, [], [])
    st = {"name": "TIE1", "symbol": None, "footprint": None}
    r.put_fp(st, _NETTIE.format(cu=_BRIDGE_F + " " + _BRIDGE_B), "NetTie-2_THT", "kicad:NetTie:NetTie-2_THT")
    assert any("B.Cu x1" in v for v in st.get("verify", [])), st.get("verify")
    st2 = {"name": "TIE2", "symbol": None, "footprint": None}            # a part sharing that file is held too
    r.put_fp(st2, _NETTIE.format(cu=_BRIDGE_F + " " + _BRIDGE_B), "NetTie-2_THT", "kicad:NetTie:NetTie-2_THT")
    assert any("B.Cu x1" in v for v in st2.get("verify", [])), st2
    st3 = {"name": "TIE3", "symbol": None, "footprint": None}
    r.put_fp(st3, _NETTIE.format(cu=_BRIDGE_F), "NetTie-2_THT_F", "kicad:NetTie:NetTie-2_THT_F")
    assert not st3.get("verify"), st3


@lite
def test_parity_correct_conversions_of_copper_graphics_pass(tmp_path):
    """Unfilled rects / polygons (written as tracks), circles (written as arcs), arcs and lines, modern and KiCad 5:
    the same copper, no error (the graphic-by-graphic pairing failed the first three)."""
    from ecadlib import parity as P
    modern = ('(footprint "X" (version 20221018) (layer "F.Cu")\n'
              '  (fp_rect (start -4 -4) (end 4 4) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))\n'
              '  (pad "1" smd rect (at -1 0) (size 0.8 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
              '  (pad "2" smd rect (at 1 0) (size 0.8 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))\n  {g})')
    k5 = ('(module X (layer F.Cu)\n  (fp_line (start -4 -4) (end 4 4) (layer F.CrtYd) (width 0.05))\n'
          '  (pad 1 smd rect (at -1 0) (size 0.8 0.8) (layers F.Cu F.Paste F.Mask))\n'
          '  (pad 2 smd rect (at 1 0) (size 0.8 0.8) (layers F.Cu F.Paste F.Mask))\n  {g})')
    for base, g in (
            (modern, '(fp_rect (start -1 2) (end 1 3) (stroke (width 0.2) (type solid)) (fill none) (layer "F.Cu"))'),
            (modern, '(fp_poly (pts (xy -1 2) (xy 1 2) (xy 0 3)) (stroke (width 0.2) (type solid)) (fill none) (layer "F.Cu"))'),
            (modern, '(fp_circle (center 0 2.5) (end 0.5 2.5) (stroke (width 0) (type solid)) (fill solid) (layer "F.Cu"))'),
            (modern, '(fp_circle (center 0 2.5) (end 0.5 2.5) (stroke (width 0.2) (type solid)) (fill none) (layer "F.Cu"))'),
            (modern, '(fp_arc (start -1 2) (mid 0 3) (end 1 2) (stroke (width 0.2) (type solid)) (layer "F.Cu"))'),
            (modern, '(fp_poly (pts (xy -1.2 -0.1) (xy -2 -0.1) (xy -2 0.1) (xy -1.2 0.1)) (stroke (width 0) (type solid)) (fill solid) (layer "F.Cu"))'),
            (k5, "(fp_poly (pts (xy -1 2) (xy 1 2) (xy 0 3)) (layer F.Cu) (width 0.15))"),
            (k5, "(fp_circle (center 0 2.5) (end 0.5 2.5) (layer F.Cu) (width 0.15))"),
            (k5, "(fp_arc (start 0 2) (end 1 2) (angle 90) (layer F.Cu) (width 0.15))")):
        text = base.format(g=g)
        assert not _errs(P.footprint_pair(text, _pcblib(tmp_path, text))), g


def test_kicad5_polygon_is_written_filled():
    """KiCad 5 polygons have no fill token and are filled; kicad_import wrote only their outline."""
    from ecadlib import kicad_import as K
    f, _ = K.convert_footprint('(module X (layer F.Cu) (pad 1 smd rect (at 0 0) (size 1 1) (layers F.Cu)) '
                               '(fp_poly (pts (xy -1 2) (xy 1 2) (xy 0 3)) (layer F.Cu) (width 0.15)))', "X", notes=[])
    assert len(f["regions"]) == 1, f["regions"]


# --- R-FX5-3: a hash recorded before R-SP-4 still matches the same footprint -------------------------------------

def _rec(h, kmod="models/KiCad/QFN-16.kicad_mod", **kw):
    return {"file": "models/PcbLib/QFN-16.PcbLib", "kicad_mod": kmod, "name": "QFN-16", "hash": h, "source": "datasheet",
            "by": "PARTA", **kw}


@lite
def test_fp_share_old_hash_without_its_file_still_shares(tmp_path):
    """A released part's record from before the upgrade (no hash_v, its .kicad_mod not here): the same footprint
    shares it again instead of getting a new name (a new ref = re-approval of a released part)."""
    from ecadlib import fp_share as FS
    old = FS.digest(EP_WINDOWS, 1)
    assert old != FS.digest(EP_WINDOWS)                        # the case: the digest changed for this footprint
    written = {str(tmp_path / "models/PcbLib/QFN-16.PcbLib"): _rec(old)}
    got = FS.pick(written, tmp_path / "models", "QFN-16", EP_WINDOWS, "datasheet", "PARTB", tmp_path, {})
    assert got[0] == "QFN-16" and got[2] is not None, got


@lite
def test_fp_share_old_hash_is_recomputed_from_its_file(tmp_path):
    """With its .kicad_mod there, an old record is compared by today's digest: paste differences still split."""
    from ecadlib import fp_share as FS
    (tmp_path / "models/KiCad").mkdir(parents=True)
    (tmp_path / "models/KiCad/QFN-16.kicad_mod").write_text(EP_FULL)
    written = {str(tmp_path / "models/PcbLib/QFN-16.PcbLib"): _rec(FS.digest(EP_FULL, 1))}
    assert FS.pick(written, tmp_path / "models", "QFN-16", EP_FULL, "datasheet", "PARTB", tmp_path, {})[2] is not None
    got = FS.pick(written, tmp_path / "models", "QFN-16", EP_WINDOWS, "datasheet", "PARTB", tmp_path, {})
    assert got[0] != "QFN-16", got


@lite
def test_librarian_records_the_hash_version(tmp_path):
    from ecadlib import fp_share as FS, librarian as L
    r = L.Run(tmp_path, None, None, [], [])
    st = {"name": "P1", "symbol": None, "footprint": None}
    r.put_fp(st, EP_WINDOWS, "QFN-16", "datasheet")
    assert st["footprint"]["hash_v"] == FS.HASH_V and st["footprint"]["hash"] == FS.digest(EP_WINDOWS)
