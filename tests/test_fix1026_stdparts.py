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
