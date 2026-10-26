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
