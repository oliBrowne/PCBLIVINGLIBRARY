"""Full review 2026-11-10, change control: ecadlib writes the MPN, Value and Description into every symbol in the
.SchLib. The fingerprint takes identity from plan.json only, so an edit of those fields inside the released file moved
no digest, and released_bytes took the edited file for a re-save of the released one: dblib-sync, the Altium 365 push
and kicad-export shipped another MPN / value under revision 1. A file whose own fields differ from the released copy's
is not a re-save: the released copy goes out."""
import json, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, change_gate as CG, fingerprint as FP, ledger as LG
from ecadlib import cli


def _demo(d):
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert cli.main(["demo", "-o", str(d)]) in (0, None)
    plan = next(Path(d).rglob("plan.json"))
    items = json.loads(plan.read_text(encoding="utf-8"))
    lg = LG.Ledger(plan.parent)
    it = next(i for i in items if i.get("status") == "ready" and lg.head(i["name"])
              and str((i.get("symbol") or {}).get("file") or "").lower().endswith(".schlib"))
    return plan.parent, it, lg


def _fields(data, ref, d):
    f = Path(d) / "probe.SchLib"
    f.write_bytes(data)
    return FP.schlib(f, ref)[1]


def test_an_identity_edit_inside_a_released_schlib_is_not_shipped_as_the_released_revision():
    with tempfile.TemporaryDirectory() as d:
        lib, it, lg = _demo(Path(d) / "demo")
        f = lib / it["symbol"]["file"]
        released = f.read_bytes()
        was = _fields(released, it["symbol"]["ref"], d)
        key = next(k for k in ("MPN", "Manufacturer Part Number", "Value") if was.get(k))
        old = f"|Text={was[key]}|Name={key}".encode()
        assert released.count(old) == 1, (key, was)
        new = f"|Text={'X' * len(was[key])}|Name={key}".encode()         # same length: an editor's save of one field
        f.write_bytes(released.replace(old, new))
        AL.read_schlib(f)                                                 # still a readable library
        assert FP.of_item(it, lib)["hashes"]["digest"] == lg.head(it["name"])["digest"]   # the fingerprint cannot see it
        data, head = CG.released_bytes(lg, it, f, lib)
        assert head is not None and data == released, "the edited file must not go out as the released revision"
        assert _fields(data, it["symbol"]["ref"], d)[key] == was[key]


def test_a_plain_re_save_of_a_released_schlib_still_ships_as_it_is():
    with tempfile.TemporaryDirectory() as d:
        lib, it, lg = _demo(Path(d) / "demo")
        f = lib / it["symbol"]["file"]
        assert CG.same_content(lg, lg.head(it["name"]), it, f, f.read_bytes(), lib)
        assert CG.released_bytes(lg, it, f, lib) == (f.read_bytes(), None)
