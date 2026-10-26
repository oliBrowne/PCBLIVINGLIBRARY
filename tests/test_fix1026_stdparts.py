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


def test_family_fallback_takes_lead_count_suffixes_again():
    """R-FX5-7: R-SP-2's anchoring held SOT-223-3L, TO-220-3L, TO-220AB-3, SOT-23_3L, TO-261-4, TO-92A / TO-92AP, and
    any 'shunt' / 'filter' word in a transistor's description; they resolve as before R-SP-2."""
    from ecadlib import stdparts as S
    for text, kind, mpn, want in (("SOT-223-3L", "U", "AMS1117-3.3", "SOT-223-3_TabPin2"),
                                  ("TO-261-4", "U", "AMS1117-3.3", "SOT-223-3_TabPin2"),
                                  ("TO-220-3L", "U", "L7805CV", "TO-220-3_Vertical"),
                                  ("TO-220AB-3", "U", "L7805CV", "TO-220-3_Vertical"),
                                  ("SOT-23_3L", "Q", "MMBT3904", "SOT-23"), ("TO-92A", "Q", "2N3904", "TO-92_Inline"),
                                  ("TO-92AP", "Q", "2N3904", "TO-92_Inline"), ("SOT-89-3L", "U", "78L05", "SOT-89-3")):
        assert S.resolve([text], kind, mpn, "")["footprint"] == want, (text, S.resolve([text], kind, mpn, ""))
    assert S.resolve([""], "Q", "2N7002", "MOSFET N-CH 60V SOT-23 shunt")["footprint"] == "SOT-23"


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


def test_metric_word_dimensions_and_the_code_it_belongs_to():
    """R-FX5-6: body dimensions that fit the imperial reading decide; a metric word before another code is that
    code's; a loose 'mm' or a 'metric' past other values is not a marker for the code."""
    from ecadlib import stdparts as S
    for t in ("0603 [mm] 1.6x0.8", "0603 [Metric 1608]", "0603 size, metric 1608", "0603 SMD; L x W (mm): 1.6 x 0.8",
              "RES 10k 0603 dimensions (mm) 1.6x0.8", "10k 0603 1% 100mW metric thick film", "0603 Metric 1608",
              "R 0603 metric 1608"):
        assert S.chip_size(t) == "0603", (t, S.chip_size(t))
    for t in ("CAP 100nF 0603, mm", "mm 0603"):                 # unclear: refused, not a guess either way
        assert str(S.chip_size(t)).startswith("conflict"), (t, S.chip_size(t))


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


# --- R-CV-1: dedupe --merge keys footprints by custom copper, paste / mask and side too ---------------------------

def _dedupe_plan(d, texts):
    from ecadlib import altium_lib as AL, kicad_import as K
    (d / "models").mkdir()
    items = []
    for i, (n, t) in enumerate(texts):
        t = t.replace('"QFN-X"', f'"{n}"')
        (d / f"models/{n}.kicad_mod").write_text(t)
        fp, _ = K.convert_footprint(t, n, "", [])
        AL.write_pcblib(d / f"models/{n}.PcbLib", [fp])
        items.append({"name": f"PART{i}", "folder": "Components/ICs", "status": "needs_review",
                      "parameters": [{"name": "Manufacturer Part Number", "value": f"PART{i}"}],
                      "symbol": {"ref": f"PART{i}", "file": None},
                      "footprint": {"ref": n, "file": f"models/{n}.PcbLib", "kicad_mod": f"models/{n}.kicad_mod"},
                      "source": {"footprint": "easyeda:C1"}})
    (d / "plan.json").write_text(json.dumps(items))
    from ecadlib import dedupe as DD
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        DD.main([str(d / "plan.json"), "--merge", "--kinds", "footprint"])
    return {i["name"]: i["footprint"]["ref"] for i in json.loads((d / "plan.json").read_text())}


@lite
def test_dedupe_merge_keeps_other_custom_copper_or_paste_apart(tmp_path):
    """A 1.7 mm square custom EP and a 0.4 x 3 mm bar on the same anchor (or an EP with and without paste) hashed
    alike and --merge pointed one part at the other's land."""
    got = _dedupe_plan(tmp_path, [("FP_A", EP_CUST_SQUARE), ("FP_B", EP_CUST_BAR), ("FP_C", EP_FULL), ("FP_D", EP_NOPASTE)])
    assert got == {"PART0": "FP_A", "PART1": "FP_B", "PART2": "FP_C", "PART3": "FP_D"}, got


@lite
def test_dedupe_merge_still_merges_the_same_custom_footprint(tmp_path):
    got = _dedupe_plan(tmp_path, [("FP_A", EP_CUST_SQUARE), ("FP_A2", EP_CUST_SQUARE), ("FP_W", EP_WINDOWS),
                                  ("FP_W2", EP_WINDOWS)])
    assert len(set(got.values())) == 2, got


def test_dedupe_key_of_a_plain_footprint_is_unchanged():
    """Plain footprints keep dedupe's old key in both formats, so .kicad_mod and .PcbLib copies still group."""
    import tempfile
    from pathlib import Path
    from ecadlib import altium_lib as AL, dedupe as DD, kicad_import as K
    plain = _qfn('  (pad "5" smd rect (at 0 0) (size 1.7 1.7) (layers "F.Cu" "F.Paste" "F.Mask"))')
    old = DD.fp_hash(DD.fp_geometry_kicad(plain))
    assert DD.fp_key_kicad(plain) == old
    with tempfile.TemporaryDirectory() as d:
        AL.write_pcblib(Path(d) / "a.PcbLib", [K.convert_footprint(plain, "X", "", [])[0]])
        assert DD.fp_key_pcblib(AL.read_pcblib(Path(d) / "a.PcbLib")[0]) == old


# --- R-CV-2: a part with its own 3D body shares a footprint file only with one as high ----------------------------

@lite
def test_fp_share_pick_splits_another_body_height(tmp_path):
    from ecadlib import fp_share as FS
    written = {str(tmp_path / "models/PcbLib/QFN-16.PcbLib"): _rec(FS.digest(EP_FULL), hash_v=FS.HASH_V, height=0.6)}
    pick = lambda h: FS.pick(written, tmp_path / "models", "QFN-16", EP_FULL, "datasheet", "PARTB", tmp_path, {}, h)
    assert pick(None)[2] is not None                  # no own body: takes the footprint's
    assert pick(0.63)[2] is not None                  # within dedupe.HEIGHT_TOL
    got = pick(1.8)
    assert got[2] is None and got[0] != "QFN-16", got


@lite
def test_fp_share_old_record_height_is_read_from_its_pcblib(tmp_path):
    """A record written before heights were recorded: its .PcbLib's body says how high it is."""
    from ecadlib import altium_lib as AL, fp_share as FS, kicad_import as K
    f, _ = K.convert_footprint(EP_FULL, "QFN-16", notes=[])
    f["bodies"].append({"pts": [(-2, -2), (2, -2), (2, 2), (-2, 2)], "height": 0.9})
    (tmp_path / "models/PcbLib").mkdir(parents=True)
    AL.write_pcblib(tmp_path / "models/PcbLib/QFN-16.PcbLib", [f])
    written = {str(tmp_path / "models/PcbLib/QFN-16.PcbLib"): _rec(FS.digest(EP_FULL), hash_v=FS.HASH_V)}
    pick = lambda h: FS.pick(written, tmp_path / "models", "QFN-16", EP_FULL, "datasheet", "PARTB", tmp_path, {}, h)
    assert pick(0.9)[2] is not None
    assert pick(1.8)[2] is None


@lite
def test_librarian_gives_a_crystal_with_another_model_its_own_file(tmp_path):
    """Two EasyEDA crystals on one land with 0.6 and 1.8 mm models: the second got the first's file and body."""
    from ecadlib import altium_lib as AL, dedupe as DD, easyeda_3d as E3, easyeda_convert as EC, fp_tidy as FT
    from ecadlib import librarian as L, step_gen as SG
    r = json.loads((Path(__file__).parent / "fixtures/easyeda_live/C9002.json").read_text())["result"]
    ds = r["packageDetail"]["dataStr"]
    ref = E3.model_ref(ds)
    run = L.Run(tmp_path, None, None, [], [])
    got = {}
    for part, h in (("XTAL_A", 0.6), ("XTAL_B", 1.8), ("XTAL_C", 0.6)):
        text, _ = SG.generate({"name": "X", "package_type": "chip", "pin_count": 2, "body_length": {"nom": 3.2},
                               "body_width": {"nom": 2.5}, "terminal_length": {"nom": 0.5}}, height=h)
        placed, info = E3.place(text, ref)
        m = {"text": placed, "info": info, "ref": dict(ref, uuid=ref["uuid"][:-1] + part[-1])}
        st = {"name": part, "symbol": None, "footprint": None, "review": [], "tried": [], "verify": []}
        kmod = FT.tidy(EC.footprint_to_kicad(ds, "CRYSTAL-SMD_4P-L3.2-W2.5-BL", []), [])
        run.put_fp(st, kmod, "CRYSTAL-SMD_4P-L3.2-W2.5-BL", "easyeda:C9002", None, model=m)
        fp = st["footprint"]
        got[part] = (fp["file"], DD.body_height(AL.read_pcblib(tmp_path / fp["file"])[0]))
    assert got["XTAL_A"][0] != got["XTAL_B"][0], got
    assert abs(got["XTAL_B"][1] - 1.8) < 0.05 and abs(got["XTAL_A"][1] - 0.6) < 0.05, got
    assert got["XTAL_C"][0] == got["XTAL_A"][0], got      # as high: shared as before


# --- R-CV-3: untyped pins (EasyEDA) follow the house pin-side rule by their names ----------------------------------

def _sides(names, typ="unspecified"):
    from ecadlib import symbol_layout as SL
    pins = [{"num": str(i + 1), "name": n, "type": typ} for i, n in enumerate(names)]
    got = SL.arrange_pins(pins)
    assert sorted(p["num"] for p in got) == sorted(p["num"] for p in pins)            # every pin, numbers kept
    assert all(dict(p)["name"] == names[int(p["num"]) - 1] for p in got)
    by = {}
    for p in got:
        by.setdefault(p["side"], set()).add(p["name"])
    return by


def test_symbol_layout_shift_register_outputs_go_right():
    by = _sides(["QB", "QC", "QD", "QE", "QF", "QG", "QH", "GND", "QH'", "~{SRCLR}", "SRCLK", "RCLK", "~{OE}", "SER",
                 "QA", "VCC"])
    assert by["right"] == {"QA", "QB", "QC", "QD", "QE", "QF", "QG", "QH", "QH'"}, by
    assert {"SER", "SRCLK", "RCLK"} <= by["left"] and by["top"] == {"VCC"} and by["bottom"] == {"GND"}, by


def test_symbol_layout_signed_supplies_go_top_and_bottom():
    by = _sides(["-IN", "RG", "RG", "+IN", "-VS", "REF", "OUT", "+VS"])
    assert "+VS" in by["top"] and by["bottom"] == {"-VS"} and {"-IN", "+IN"} <= by["left"] and "OUT" in by["right"], by
    by = _sides(["-IN", "RG", "RG", "+IN", "-VS", "REF", "OUT", "+VS"], "power_in")   # typed power: -VS still at the bottom
    assert "-VS" in by["bottom"], by


def test_symbol_layout_name_classes():
    from ecadlib import symbol_layout as SL
    c = lambda n: SL.SIDES[SL.classify({"name": n, "type": "unspecified", "num": "1"})]
    for n in ("+VS", "VS+", "+V", "VCC+", "+5V", "+3V3", "V+", "3V3", "VCC"):
        assert c(n) == "top", n
    for n in ("-VS", "VS-", "-V", "-5V", "VNEG", "V-", "VEE", "GND"):
        assert c(n) == "bottom", n
    for n in ("1Y", "2Y", "Y0", "QA", "QB", "QH'", "1Q", "OUTA", "Q"):
        assert c(n) == "right", n
    for n in ("1IN-", "1IN+", "IN1-", "INA+", "INA-", "-INA", "IN+", "SER"):
        assert c(n) == "left", n
    for n in ("-IN", "+IN", "VREF-", "VIN"):          # unchanged neighbours
        assert c(n) == {"-IN": "left", "+IN": "left", "VREF-": "bottom", "VIN": "top"}[n], n


# --- R-FX9-1: lost copper is an error whatever else is approximated; every import route holds dropped copper ------

_TWO_PADS = ('(pad "1" smd rect (at -1 0) (size 0.8 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
             ' (pad "2" smd rect (at 1 0) (size 0.8 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))')
_CHAMFER = ' (pad "3" smd roundrect (at 0 3) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0) ' \
           '(chamfer_ratio 0.2) (chamfer top_left))'
_CURVED = (' (pad "3" smd custom (at 0 3) (size 0.4 0.4) (layers "F.Cu" "F.Paste" "F.Mask") (options (clearance outline) '
           '(anchor circle)) (primitives (gr_arc (start 0.4 -0.4) (mid 0.8 0) (end 0.4 0.4) (width 0.2))))')
_FREE = ' (fp_poly (pts (xy -0.5 -2) (xy 0.5 -2) (xy 0.5 -1.5) (xy -0.5 -1.5)) (stroke (width 0) (type solid)) ' \
        '(fill solid) (layer "F.Cu"))'


def _fp(*items):
    return ('(footprint "X" (version 20221018) (layer "F.Cu") (fp_rect (start -4 -4) (end 4 4) (stroke (width 0.05) '
            '(type solid)) (fill none) (layer "F.CrtYd")) ' + _TWO_PADS + " ".join(items) + ")")


@lite
def test_parity_lost_copper_next_to_a_chamfered_pad_is_an_error(tmp_path):
    from ecadlib import parity as P
    t = _fp(_CHAMFER, _FREE)
    assert _errors(P.footprint_pair(t, _pcblib(tmp_path, t))) == []
    got = _errors(P.footprint_pair(t, _pcblib(tmp_path, t, lambda f: f.update(regions=[]))))
    assert any("F.Cu copper" in m for m in got), got


@lite
def test_parity_curved_custom_pad_approximation_stays_a_warning(tmp_path):
    """Inside the approximated pad's own box the copper difference is the documented approximation."""
    from ecadlib import parity as P
    t = _fp(_CURVED)
    diffs = P.footprint_pair(t, _pcblib(tmp_path, t))
    assert _errors(diffs) == [], diffs
    t2 = _fp(_CURVED, _FREE)                               # ... but not a lost polygon elsewhere
    assert any("F.Cu copper" in m for m in _errors(P.footprint_pair(t2, _pcblib(tmp_path, t2, lambda f: f.update(regions=[])))))


_BCU = ' (fp_poly (pts (xy -0.6 -0.1) (xy 0.6 -0.1) (xy 0.6 0.1) (xy -0.6 0.1)) (stroke (width 0) (type solid)) ' \
       '(fill solid) (layer "B.Cu"))'


@lite
def test_kicad_import_holds_a_footprint_whose_copper_is_dropped(tmp_path):
    from ecadlib import kicad_import as K
    f = tmp_path / "NT.kicad_mod"
    f.write_text(_fp(_BCU))
    rep = K.import_part({"footprint": str(f), "height_mm": 1.0}, tmp_path / "out", None, write=False)
    assert not rep["ok"] and "copper not written" in rep.get("error", ""), rep
    f.write_text(_fp())
    assert K.import_part({"footprint": str(f), "height_mm": 1.0}, tmp_path / "out", None, write=False)["ok"]
    assert K.lost_copper_hold("NT", ["graphics on unmapped layers dropped: B.Cu x1, Dwgs.User x2"]).startswith("footprint NT")
    assert K.lost_copper_hold("NT", ["graphics on unmapped layers dropped: Dwgs.User x2"]) is None


# --- R-FX9-3: the copper map keeps tol / 3 resolution on large footprints --------------------------------------

def _far(d):
    return (f' (pad "8" smd rect (at -{d} {d / 2}) (size 0.8 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))'
            f' (pad "9" smd rect (at {d} -{d / 2}) (size 0.8 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))')


def test_copper_map_large_footprint_still_sees_small_shifts_and_bridges():
    from ecadlib import copper_map as CM
    sq = lambda x, y, w, h: [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    pads = [sq(-100, -50, 0.8, 0.8), sq(100, 50, 0.8, 0.8), sq(-1.4, -0.4, 0.8, 0.8), sq(0.6, -0.4, 0.8, 0.8)]
    bridge = sq(-0.6, -0.05, 1.2, 0.1)
    free = sq(-0.5, -2, 1, 0.5)
    assert CM.compare({"F": pads + [bridge, free]}, {"F": pads + [bridge, free]}) == []
    assert CM.compare({"F": pads + [bridge, free]}, {"F": pads + [free]})                  # lost 1.2 x 0.1 bridge
    moved = sq(-0.4, -2, 1, 0.5)                                                         # 0.1 mm shift
    assert CM.compare({"F": pads + [bridge, free]}, {"F": pads + [bridge, moved]})
    big = sq(-30, 5, 60, 40)                                                             # a large plane, same on both
    assert CM.compare({"F": pads + [big]}, {"F": pads + [list(reversed(big))]}) == []
    assert CM.compare({"F": pads + [big]}, {"F": pads + [sq(-30, 5, 60, 39.9)]})          # 0.1 mm short


@lite
def test_parity_large_footprint_lost_narrow_bridge_is_an_error(tmp_path):
    from ecadlib import parity as P
    bridge = ' (fp_poly (pts (xy -0.6 -0.025) (xy 0.6 -0.025) (xy 0.6 0.025) (xy -0.6 0.025)) (stroke (width 0) ' \
             '(type solid)) (fill solid) (layer "F.Cu"))'
    t = _fp(_far(60), bridge)
    assert _errors(P.footprint_pair(t, _pcblib(tmp_path, t))) == []
    assert any("F.Cu copper" in m for m in _errors(P.footprint_pair(t, _pcblib(tmp_path, t, lambda f: f.update(regions=[])))))


# --- R-FX9-4: a protection diode named before the LED words is not an LED ----------------------------------------

def test_says_led_named_other_diode_wins_unless_the_led_is_the_head_noun():
    from ecadlib import stdparts as S
    for d in ("Zener diode for LED protection, white LED string", "Zener 3.3V (LED shunt)", "Open LED protector",
              "LED shunt protector 0603", "TVS diode, LED protection, 0603", "Schottky diode, LED backlight driver, white"):
        assert S.part_kind("Diodes", "", d) == "D", d
    for d in ("Red Zener-protected LED", "Zener-protected white LED", "LED, white", "ESD protected LED, white",
              "LED backlight white 3528", "Status LED (green), protects nothing", "IR LED 940nm"):
        assert S.part_kind("Diodes", "", d) == "LED", d


# --- R-FX9-2: a stroked custom-pad polygon is written with KiCad's round corners -----------------------------------

_STROKED = '''(footprint "JSTLIKE" (version 20221018) (layer "F.Cu")
 (fp_rect (start -3 -3) (end 3 3) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))
 (pad "1" smd custom (at -1 0) (size 0.5 0.5) (layers "F.Cu" "F.Paste" "F.Mask")
  (options (clearance outline) (anchor rect))
  (primitives (gr_poly (pts (xy -0.25 -1.2) (xy 0.25 -1.2) (xy 0.25 1.2) (xy -0.25 1.2)) (width 0.5) (fill yes))))
 (pad "2" smd rect (at 1 0) (size 1 2.4) (layers "F.Cu" "F.Paste" "F.Mask")))'''


def _errors(diffs):
    return [d["msg"] for d in diffs if d["level"] == "error" and "3D" not in d["msg"]]


@lite
def test_parity_stroked_custom_pad_polygon_is_not_held(tmp_path):
    """JST ZE / Molex Nano-Fit: a gr_poly with a 0.5 mm stroke was written grown with square corners (+0.013 mm2
    of copper KiCad does not have) and held."""
    from ecadlib import parity as P
    assert _errors(P.footprint_pair(_STROKED, _pcblib(tmp_path, _STROKED))) == []


@lite
def test_parity_stroked_custom_pad_still_catches_a_lost_stroke(tmp_path):
    from ecadlib import parity as P
    drop = lambda f: f.update(tracks=[])
    assert any("copper" in m for m in _errors(P.footprint_pair(_STROKED, _pcblib(tmp_path, _STROKED, drop))))
