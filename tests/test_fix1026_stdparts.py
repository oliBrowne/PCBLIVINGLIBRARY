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
