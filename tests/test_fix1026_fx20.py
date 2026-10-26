"""Fixes 2026-10-26 (fx20): review R-FX20-1..3. Each test failed on 5d9ab0a and passes with its fix."""
from tests.fixtures_lite import fresh_home, lite


# --- R-FX20-1: a KiCad x stands for nothing only where an ordering letter goes --------------------------------------

def _lib(**symbols):
    from ecadlib.librarian import KiCadLibrary
    lib = KiCadLibrary.__new__(KiCadLibrary)
    lib.symbols, lib.footprints = {n: [l] for n, l in symbols.items()}, {}
    lib._build_norm()
    return lib


def test_a_blank_x_in_the_middle_of_a_name_does_not_make_another_part_fit():
    lib = _lib(VTL5C="Isolator", VTL5Cx2="Isolator")
    ref, how = lib.find_symbol("VTL5C2")             # the single Vactrol, not the dual VTL5Cx/2
    assert ref == "Isolator:VTL5C" and "check" in how, (ref, how)
    assert lib.find_symbol("VTL5C42")[0] == "Isolator:VTL5Cx2"   # a filled x still fits
    lib = _lib(SMAJxxA="Diode")
    assert lib.find_symbol("SMAJ5A") == (None, None)  # one of two x slots left blank mid-name: no fit
    assert lib.find_symbol("SMAJ5.0A")[0] == "Diode:SMAJxxA"


def test_a_blank_x_next_to_a_dash_or_slash_or_at_the_end_still_fits():
    lib = _lib(**{"MCP6L01x-xOT": "Amplifier_Operational", "MCP6L01Rx-xOT": "Amplifier_Operational"})
    for mpn, want in (("MCP6L01R-E/OT", "MCP6L01Rx-xOT"), ("MCP6L01-E/OT", "MCP6L01x-xOT"),
                      ("MCP6L01RT-E/OT", "MCP6L01Rx-xOT")):
        ref, how = lib.find_symbol(mpn)
        assert ref == f"Amplifier_Operational:{want}" and "wildcard" in how, (mpn, ref, how)
    lib = _lib(TPS7A20xx="Regulator_Linear")
    assert lib.find_symbol("TPS7A201")[0] == "Regulator_Linear:TPS7A20xx"   # blank at the end


# --- R-FX20-2: a plan of another project that moved is held as "not there any more", not dropped ------------------

@lite
@fresh_home
def test_a365_a_moved_project_that_adopted_the_component_still_holds_the_change(tmp_path):
    """B (another project) adopted A's component, then project B was moved. The computer's record skipped B's plan and
    rewrote the file without it, so A's approval alone made a new revision. Now B is named as not found and A waits;
    --forget-missing-plans (forget_missing) drops it."""
    import shutil
    from tests.test_change_control import FakeA365
    from tests.test_fix1026_fx15 import _a365_push, _new_land
    from tests.test_fix1026_fx18 import _two_projects
    nx = FakeA365()
    a, b = _two_projects(tmp_path)
    assert _a365_push(a, nx)["RES-0603-10k"]["action"] == "release"
    assert _a365_push(b, nx)["RES-0603-10k"].get("adopted")
    shutil.move(str(tmp_path / "projB"), str(tmp_path / "projB_moved"))
    _new_land(tmp_path / "projA/A")
    for _ in range(2):                                 # a second push (the file was rewritten) still holds
        r = _a365_push(a, nx)["RES-0603-10k"]
        assert r["action"] == "skip" and "not found from here" in r["detail"] and "--forget-missing-plans" in r["detail"], r
        assert len(nx.comps["RES-0603-10k"]["revisions"]) == 1
    res = _a365_push(a, nx, forget_missing=True)
    assert res["RES-0603-10k"]["action"] == "new_revision", res
    assert any(v["action"] == "forgotten" for v in res.values()), res
