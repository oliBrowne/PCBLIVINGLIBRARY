"""2026-10-26 fixes "gate2" (reviews/2026-10-26_review_196/gate2.md): an edit saved while a writer runs is not the
writer's own rewrite (R-G2-1); a pad-to-pad gap cut below its margin is a major change (R-G2-2); a cut or merged ledger
fails closed (R-G2-3); CRLF line ends alone change nothing (R-G2-4); a recipe's STEP body rotation / dz / height is
checked (R-G2-5); pad-level paste / clearance / thermal settings of a .kicad_mod are fingerprinted (R-G2-6); part revert
checks the policy's users (R-G2-7).
Offline.  python3 -m pytest -q tests/test_fix1026_gate2.py   (or python -m tests.run_all test_fix1026_gate2)"""
import contextlib, io, json, re
from pathlib import Path

from tests.fixtures_lite import lite
from tests.test_fix1026_changes2 import _demo, _ledger
from tests.test_fix1026_changes3 import CAP, _head_recipe, _moved_pad, _recipe

ANN = "Ann Lee <ann@example.com>"


def _pcblib(lib):
    return lib / "models/PcbLib/C_0402_1005Metric.PcbLib"


def _silk_resave(lib, into=None):
    """The C_0402 .PcbLib with its silkscreen 0.12 -> 0.15 mm (cosmetic) and its recipe, written by altium_lib into
    the library (into=None) or into another folder (a person's save, copied in later as plain bytes)."""
    from ecadlib import altium_lib as AL, stdparts as SP
    txt = SP.build("C_0402_1005Metric")[0]
    txt = re.sub(r"\(stroke \(width 0\.12\)", "(stroke (width 0.15)", re.sub(r"\(width 0\.12\)", "(width 0.15)", txt))
    dst = Path(into) / _pcblib(lib).name if into else _pcblib(lib)
    dst.parent.mkdir(parents=True, exist_ok=True)
    AL.write_pcblib(dst, [AL.footprint_from_kicad(txt)])
    return dst


# ------------------------------------------------------------------ R-G2-1
def _race(writer):
    """g7_rewrote_race.py: a writer begins, builds nothing for CAP; meanwhile a person saves a silkscreen edit of the
    .PcbLib and moves PAD 1 in its recipe; the writer settles. The moved pad must not be released."""
    from ecadlib import change_gate as CG, ledger as LG
    w, lib = _demo()
    items = json.loads((lib / "plan.json").read_text(encoding="utf-8"))
    released = _recipe(lib).read_bytes()
    saved = _silk_resave(lib, into=w / "altium")               # what the person's Altium will save
    g = CG.Guard(lib, writer)
    g.begin(items)
    _pcblib(lib).write_bytes(saved.read_bytes())               # an edit saved while the writer works (another program)
    _recipe(lib).write_bytes(Path(str(saved) + ".ecadbuild").read_bytes())
    edited, moved = _moved_pad(lib)                            # ... with PAD 1 moved in the recipe
    _recipe(lib).write_text(edited, encoding="utf-8")
    rows = g.settle(items)
    g.close()
    head = LG.Ledger(lib).head(CAP)
    assert moved not in _head_recipe(lib), ("the moved pad was released", rows, head)
    assert head["revision"] == 1, head
    assert _recipe(lib).read_bytes() == released               # the released recipe is back
    assert [r for r in rows if r["part"] == CAP and r["action"] == "hold"], rows


def test_gate2_rg21_an_edit_saved_while_the_librarian_runs_is_not_its_own_rewrite():
    _race("librarian")


def test_gate2_rg21_an_edit_saved_while_resolve_runs_is_not_its_own_rewrite():
    _race("resolve")


def test_gate2_rg21_the_writers_own_rebuild_still_releases():
    """No false hold: the writer itself rewrites the .PcbLib (through altium_lib, which writes the matching recipe):
    released at the policy's action, no `overwritten` line."""
    from ecadlib import change_gate as CG, ledger as LG
    w, lib = _demo()
    items = json.loads((lib / "plan.json").read_text(encoding="utf-8"))
    g = CG.Guard(lib, "librarian")
    g.begin(items)
    _silk_resave(lib)                                          # the writer's own write this time
    mine = _recipe(lib).read_bytes()
    rows = g.settle(items)
    g.close()
    assert LG.Ledger(lib).head(CAP)["revision"] == 2, rows
    assert _recipe(lib).read_bytes() == mine
    assert not [e for e in _ledger(lib) if e.get("event") == "overwritten"]


# ------------------------------------------------------------------ R-G2-2
def _kfp(pads):
    from ecadlib import fingerprint as F
    body = "".join(f'(pad "{n}" smd rect (at {x:.3f} {y:.3f}) (size {w} {h}) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
                   for n, x, y, w, h in pads)
    return F.build("T", None, *F.kicad_footprint(f'(footprint "T" (version 20240108) (generator x) (layer "F.Cu")\n{body})'),
                   tool="kicad")


def _row(w, pitch, h=1.0, n=4):
    return _kfp([(i + 1, pitch * i, 0, w, h) for i in range(n)])


def _qfn16(ep):
    pads, n = [], 1
    for side in range(4):
        for i in range(4):
            t = -0.75 + 0.5 * i
            x, y, w, h = {0: (-1.45, t, 0.8, 0.25), 1: (t, 1.45, 0.25, 0.8), 2: (1.45, -t, 0.8, 0.25),
                          3: (-t, -1.45, 0.25, 0.8)}[side]
            pads.append((n, x, y, w, h))
            n += 1
    return _kfp(pads + [(17, 0, 0, ep, ep)])


def test_gate2_rg22_a_pad_gap_cut_below_its_margin_is_a_major_change():
    """g1_gap_rel.py / g1b_ep_grow.py: within the 10 % size band, but the copper gap to the next pad is cut (1.0 ->
    1.09 mm pads on 1.1 mm pitch: gap 0.10 -> 0.01), closed (0.6 -> 0.66 on 0.65: overlap), or an exposed pad grows into
    the leads (QFN-16 EP 1.70 -> 1.86: lead gap 0.225 -> 0.145). Each is geometry-major (held), not minor (released)."""
    from ecadlib import change_diff as CD
    assert CD.worst(CD.diff(_row(1.0, 1.1), _row(1.09, 1.1))) == "geometry-major"
    assert CD.worst(CD.diff(_row(0.6, 0.65, 0.6), _row(0.66, 0.65, 0.6))) == "geometry-major"
    ch = CD.diff(_qfn16(1.70), _qfn16(1.86))
    assert CD.worst(ch) == "geometry-major", ch
    assert any("gap" in c["text"] for c in ch), [c["text"] for c in ch]


def test_gate2_rg22_a_small_size_change_that_keeps_the_gap_stays_minor():
    """No false hold: pads trimmed or grown a little with the gap to every neighbour kept within its margin."""
    from ecadlib import change_diff as CD
    assert CD.worst(CD.diff(_row(0.60, 1.27), _row(0.65, 1.27))) == "geometry-minor"     # gap 0.67 -> 0.62
    assert CD.worst(CD.diff(_row(0.20, 0.4), _row(0.21, 0.4))) == "geometry-minor"       # gap 0.20 -> 0.19
    assert CD.worst(CD.diff(_row(1.0, 1.1), _row(0.95, 1.1))) == "geometry-minor"        # smaller: the gap grows
    assert CD.worst(CD.diff(_qfn16(1.70), _qfn16(1.72))) == "geometry-minor"             # EP gap 0.225 -> 0.215
    assert CD.worst(CD.diff(_row(1.0, 1.1, h=1.0), _row(1.0, 1.1, h=1.08))) == "geometry-minor"  # along the row


# ------------------------------------------------------------------ R-G2-3
KT = "KT-0603R"


def _publish(lib):
    from ecadlib import change_gate as CG
    plan = lib / "plan.json"
    with contextlib.redirect_stderr(io.StringIO()):
        return CG.publish_guard(plan, json.loads(plan.read_text(encoding="utf-8")), "kicad", log=lambda *a, **k: None)


def _describe(lib, tag):
    """A cosmetic edit of KT-0603R's description, released (pass) by the next publisher's settle."""
    plan = lib / "plan.json"
    items = json.loads(plan.read_text(encoding="utf-8"))
    it = next(i for i in items if i["name"] == KT)
    it["description"] = (it.get("description") or "") + f" ({tag})"
    plan.write_text(json.dumps(items, indent=2), encoding="utf-8")
    _publish(lib)


def _refused(fn):
    from ecadlib import ledger as LG
    try:
        fn()
    except LG.LedgerError as e:
        return str(e)
    raise AssertionError("not refused")


def test_gate2_rg23_a_ledger_cut_back_at_a_line_boundary_fails_closed():
    """g2_trunc.py: r2 released, then the ledger is an older copy (the lines from the r2 release on are gone). The next
    publisher must not take the files on disk (r2) as a hand edit of r1 and put r1 back: it refuses, nothing changes."""
    from ecadlib import ledger as LG
    w, lib = _demo()
    _describe(lib, "r2")
    assert LG.Ledger(lib).head(KT)["revision"] == 2
    led = lib / "library.ledger.jsonl"
    rows = led.read_text(encoding="utf-8").splitlines(keepends=True)
    cut = next(i for i, l in enumerate(rows) if json.loads(l).get("part") == KT and json.loads(l)["revision"] == 2
               if json.loads(l).get("event") == "release")
    led.write_text("".join(rows[:cut]), encoding="utf-8")
    plan_before = (lib / "plan.json").read_bytes()
    why = _refused(lambda: _publish(lib))
    assert "r2" in why, why
    assert (lib / "plan.json").read_bytes() == plan_before          # r1 was not put back
    assert led.read_text(encoding="utf-8") == "".join(rows[:cut])   # nothing recorded


def _two_branches(lib, keep_both):
    """Branch A and branch B each release r2 of KT-0603R from the same base; git merges .ecadlib/ (new files from both
    sides) and the ledger (both appended blocks, or only A's)."""
    led, plan, marks = lib / "library.ledger.jsonl", lib / "plan.json", lib / ".ecadlib/marks"
    base, plan0 = led.read_text(encoding="utf-8"), plan.read_text(encoding="utf-8")
    m0 = set(marks.iterdir()) if marks.is_dir() else set()
    _describe(lib, "A")
    a_lines, a_plan = led.read_text(encoding="utf-8")[len(base):], plan.read_text(encoding="utf-8")
    a_marks = set(marks.iterdir()) - m0
    for f in a_marks:                                          # branch B does not have branch A's files
        f.rename(lib / f.name)
    led.write_text(base, encoding="utf-8")
    plan.write_text(plan0, encoding="utf-8")
    _describe(lib, "B")
    b_lines = led.read_text(encoding="utf-8")[len(base):]
    for f in a_marks:
        (lib / f.name).rename(f)
    led.write_text(base + a_lines + (b_lines if keep_both else ""), encoding="utf-8")   # the merge
    plan.write_text(a_plan, encoding="utf-8")


def test_gate2_rg23_a_ledger_merged_from_two_branches_fails_closed():
    """g2b_merge.py: two branches each released r2 of a part; the merged ledger holds both r2 lines (or only branch A's,
    while the blob store keeps branch B's release). The head is not taken from either: publish and revert refuse."""
    from ecadlib import change_cli as CC, ledger as LG
    for keep_both in (True, False):
        w, lib = _demo()
        _two_branches(lib, keep_both)
        why = _refused(lambda: LG.Ledger(lib).head(KT))
        assert "r2" in why, why
        n = len(_ledger(lib))
        _refused(lambda: _publish(lib))
        assert len(_ledger(lib)) == n
        try:
            CC.revert(lib / "plan.json", f"{KT}@1", who=ANN, comment="back")
            raise AssertionError("reverted on a merged ledger")
        except (LG.LedgerError, Exception) as e:
            assert "r2" in str(e) or isinstance(e, LG.LedgerError), e
        assert len(_ledger(lib)) == n


def test_gate2_rg23_a_normal_history_and_a_revert_still_work():
    """No false alarm: r1 -> r2 -> revert to r1 (r3) -> r4 reads and publishes as before."""
    from ecadlib import change_cli as CC, ledger as LG
    w, lib = _demo()
    _describe(lib, "r2")
    CC.revert(lib / "plan.json", f"{KT}@1", who=ANN, comment="back")
    assert LG.Ledger(lib).head(KT)["revision"] == 3
    _describe(lib, "r4")
    assert LG.Ledger(lib).head(KT)["revision"] == 4


# ------------------------------------------------------------------ R-G2-4
def _crlf(b):
    return b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


@lite
def test_gate2_rg24_crlf_line_ends_alone_keep_a_sign_off(tmp_path):
    """g3_crlf.py: Git for Windows (core.autocrlf) checks the library out with CRLF. The .kicad_mod's content is the
    same, so a sign-off made with LF line ends still matches, and one made on a CRLF checkout still matches on LF. A
    real edit still makes it stale."""
    from ecadlib import approvals as AP
    km = tmp_path / "R.kicad_mod"
    lf = b'(footprint "R"\n  (pad "1" smd rect (at -0.8 0) (size 0.8 0.95) (layers "F.Cu"))\n)\n'
    km.write_bytes(lf)
    it = {"name": "R1", "description": "r", "parameters": [], "symbol": {"ref": "R", "file": None},
          "footprint": {"ref": "R", "file": None, "kicad_mod": "R.kicad_mod"}}
    h = AP.content_hash(it, tmp_path)
    km.write_bytes(_crlf(lf))
    assert AP.hash_matches(h, it, tmp_path), "CRLF alone made the sign-off stale"
    h_crlf = AP.content_hash(it, tmp_path)
    km.write_bytes(lf)
    assert AP.hash_matches(h_crlf, it, tmp_path)
    old_crlf_hash = "v2:" + __import__("hashlib").sha256(b"x").hexdigest()      # (any other hash: no match)
    assert not AP.hash_matches(old_crlf_hash, it, tmp_path)
    km.write_bytes(lf.replace(b"0.95", b"0.99"))
    assert not AP.hash_matches(h, it, tmp_path) and not AP.hash_matches(h_crlf, it, tmp_path)


@lite
def test_gate2_rg24_crlf_line_ends_alone_are_no_recipe_or_step_edit(tmp_path):
    """ledger.side_drift: the recipe (and a STEP file, which is text too) checked out with CRLF, or recorded on a CRLF
    checkout and now LF, is the released one; a real edit is still drift."""
    from ecadlib import ledger as LG
    pl = tmp_path / "X.PcbLib"
    pl.write_bytes(b"\xd0\xcf\x11\xe0 binary")
    rp = tmp_path / "X.PcbLib.ecadbuild"
    st = tmp_path / "ecadlib_3d/X_0/X.step"
    st.parent.mkdir(parents=True)
    recipe = b"ECADBUILD\t1\nFOOTPRINT\tX\nPAD\t1\t0\t0\n"
    step = b"ISO-10303-21;\nHEADER;\nENDSEC;\n"
    for disk, stored in ((_crlf, lambda b: b), (lambda b: b, _crlf)):
        rp.write_bytes(disk(recipe))
        st.write_bytes(disk(step))
        rec = {"ecadbuild": LG.sha_bytes(stored(recipe)), "steps": {"ecadlib_3d/X_0/X.step": LG.sha_bytes(stored(step))}}
        assert LG.side_drift(rec, pl) == {}, LG.side_drift(rec, pl)
    rp.write_bytes(_crlf(recipe.replace(b"PAD\t1\t0", b"PAD\t1\t9")))
    assert str(rp) in LG.side_drift(rec, pl)


# ------------------------------------------------------------------ R-G2-5
def _body_lib(tmp_path, rot=(0.0, 0.0, 0.0), dz=0.0):
    from ecadlib import altium_lib as AL, stdparts as SP
    fp = AL.footprint_from_kicad(SP.build("C_0402_1005Metric")[0])
    step = b"ISO-10303-21;\nHEADER;\nFILE_NAME('C.step');\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
    fp["bodies"] = [{"pts": [(-0.5, -0.25), (0.5, -0.25), (0.5, 0.25), (-0.5, 0.25)], "height": 0.5,
                     "step": {"name": "C.step", "data": step, "rot": rot, "dz": dz}}]
    pl = tmp_path / "C.PcbLib"
    AL.write_pcblib(pl, [fp])
    return pl, Path(str(pl) + ".ecadbuild")


@lite
def test_gate2_rg25_a_recipe_body_turned_or_lifted_does_not_build_the_pcblib(tmp_path):
    """g6_recipe_track.py rot: the recipe's STEP body turned 90 degrees about x and lifted 1 mm (fields 5-8, which
    Altium's rebuild applies: M.SetState(rx, ry, rz, dz)) no longer builds the .PcbLib; unchanged it does, and a body
    the .PcbLib itself holds turned matches a recipe that says so."""
    from ecadlib import altium_native as AN
    pl, rp = _body_lib(tmp_path)
    assert AN.compare_one(pl, rp) == []
    t = rp.read_text(encoding="utf-8")
    b = next(l for l in t.splitlines() if l.startswith("BODY\t"))
    for i, v in ((5, "90000"), (8, "393701")):
        f = b.split("\t")
        f[i] = v
        rp.write_text(t.replace(b, "\t".join(f), 1), encoding="utf-8")
        assert AN.compare_one(pl, rp), f"recipe BODY field {i} -> {v} was taken as building the .PcbLib"
    d = tmp_path / "turned"
    d.mkdir()
    pl2, rp2 = _body_lib(d, rot=(90.0, 0.0, 0.0), dz=1.0)
    assert AN.compare_one(pl2, rp2) == []


# ------------------------------------------------------------------ R-G2-6
_KMOD = ('(footprint "T" (version 20240108) (generator x) (layer "F.Cu")\n'
         '(pad "1" smd roundrect (at -0.8 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25){0})\n'
         '(pad "2" smd roundrect (at 0.8 0) (size 0.8 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25){0})\n)')


def _kfp_with(extra=""):
    from ecadlib import fingerprint as F
    return F.build("T", None, *F.kicad_footprint(_KMOD.format(extra)), tool="kicad")


def test_gate2_rg26_pad_level_paste_ratio_clearance_and_thermal_are_fingerprinted():
    """g8_kmod_fp.py / g8b: a pad's own solder_paste_margin_ratio (paste cut to 10 %), clearance and thermal bridge
    width in a .kicad_mod were not in the fingerprint: no change found, kicad-export shipped the edit as r1. Now each
    is a change; cutting the paste or the clearance, or thinning the thermal bridges, is geometry-major."""
    from ecadlib import change_diff as CD
    base = _kfp_with()
    for extra in (" (solder_paste_margin_ratio -0.9)", " (clearance 0.01)", " (thermal_bridge_width 0.01)",
                  " (thermal_gap 0.01)"):
        new = _kfp_with(extra)
        assert new["hashes"]["digest"] != base["hashes"]["digest"], extra
        ch = CD.diff(base, new)
        assert CD.worst(ch) == "geometry-major", (extra, ch)
    assert CD.worst(CD.diff(base, _kfp_with(" (solder_paste_margin_ratio -0.05)"))) == "geometry-minor"
    assert CD.diff(_kfp_with(" (clearance 0.2)"), _kfp_with(" (clearance 0.2)")) == []


def test_gate2_rg26_paste_cut_on_a_released_kicad_mod_is_held_not_published():
    """g8b_kmod_e2e.py: KT-0603R's .kicad_mod gets (solder_paste_margin_ratio -0.9) on both pads; the publisher's settle
    holds it (the released .kicad_mod goes back) instead of publishing it as r1."""
    from ecadlib import ledger as LG
    w, lib = _demo()
    items = {i["name"]: i for i in json.loads((lib / "plan.json").read_text(encoding="utf-8"))}
    km = lib / items[KT]["footprint"]["kicad_mod"]
    released = km.read_bytes()
    t = km.read_text(encoding="utf-8")
    assert "(roundrect_rratio 0.25))" in t
    km.write_text(t.replace("(roundrect_rratio 0.25))", "(roundrect_rratio 0.25) (solder_paste_margin_ratio -0.9))"),
                  encoding="utf-8")
    _publish(lib)
    assert LG.Ledger(lib).head(KT)["revision"] == 1 and LG.Ledger(lib).proposal(KT), _ledger(lib)[-3:]
    assert km.read_bytes() == released
