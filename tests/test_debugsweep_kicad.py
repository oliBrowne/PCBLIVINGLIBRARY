"""Regression tests for the debug sweep's KiCad group (DS2-*): kicad_pcm, kicad_export, easyeda_convert, kicad_index.
Plain functions (tests.run_all, no pytest)."""
import io, json, zipfile
from contextlib import redirect_stderr, redirect_stdout

from ecadlib import kicad_export as X, kicad_pcm as KP
from tests import test_tool_packages as TP
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def _two_libs(base):
    """kicad-export of one plan as library 'alpha', then as 'beta', into the same folder (EXP-27)."""
    plan, http = TP._plan(base)
    out = plan.parent.parent / "k"
    for lib in ("alpha", "beta"):
        assert not X.export(plan, out, lib, http=http, log=QUIET)["errors"]
    return out


@lite
def test_ds2_40_kicad_pcm_packages_the_named_library_of_a_two_library_folder(tmp_path):
    out = _two_libs(tmp_path)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()) as so, redirect_stderr(err):
        rc = KP.main([str(out), "--out", str(out.parent / "pcm"), "--name", "alpha", "--json"])
    assert rc == 0, err.getvalue()
    rep = json.loads(so.getvalue())
    assert (rep["library"], rep["identifier"], rep["nickname"]) == ("alpha", "local.ecadlib.alpha", "PCM_alpha"), rep
    names = zipfile.ZipFile(rep["zip"]).namelist()
    assert "symbols/alpha.kicad_sym" in names and not any("beta" in n for n in names), names
    # no --name: the folder holds two libraries, so it is refused and both are named (not the last one, silently)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = KP.main([str(out), "--out", str(out.parent / "pcm2")])
    assert rc == 2 and "alpha" in err.getvalue() and "beta" in err.getvalue() and "--name" in err.getvalue(), (rc, err.getvalue())
    assert not (out.parent / "pcm2").exists()


@lite
def test_ds2_40_kicad_pcm_licence_note_is_about_the_packaged_library(tmp_path):
    """alpha holds KiCad library content (CC-BY-SA), beta, exported after it, none: packaging alpha under MIT warns."""
    plan, http = TP._plan(tmp_path)
    out = plan.parent.parent / "k"
    assert X.export(plan, out, "alpha", http=http, log=QUIET)["kicad_content"]
    ee = plan.with_name("plan_easyeda.json")
    ee.write_text(json.dumps([i for i in json.loads(plan.read_text(encoding="utf-8")) if i["name"] == "EEPART1"]))
    assert not X.export(ee, out, "beta", http=http, log=QUIET)["kicad_content"]
    for lib, warned in (("alpha", True), ("beta", False)):
        logs = []
        KP.build(out, out.parent / f"pcm_{lib}", name=lib, license_="MIT", log=lambda *a: logs.append(" ".join(map(str, a))))
        assert any("may not be allowed" in ln for ln in logs) == warned, (lib, logs)


@lite
def test_ds2_40_kicad_pcm_unchecked_folder_of_several_libraries_needs_a_name_that_is_there(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for lib in ("ecadlib", "my_lib", "two"):                       # ecadlib: the name a plan is exported as by default
        (src / f"{lib}.kicad_sym").write_text('(kicad_symbol_lib (version 20231120) (generator "kicad")\n  (symbol "A")\n)\n')
    (src / "fp_only.pretty").mkdir()                              # a footprint library with no symbol library
    for name, exc in ((None, ValueError), ("nope", FileNotFoundError)):
        try:
            KP.build(src, tmp_path / "pcm", name=name, unchecked=True, log=QUIET)
        except exc as e:
            assert all(lib in str(e) for lib in ("ecadlib", "fp_only", "my_lib", "two")) and "--name" in str(e), (name, e)
        else:
            raise AssertionError(f"--name {name}: a library was picked from several")
    assert not (tmp_path / "pcm").exists()
    assert KP.build(src, tmp_path / "pcm", name="two", unchecked=True, log=QUIET)["library"] == "two"
    for f in ("ecadlib.kicad_sym", "my_lib.kicad_sym", "fp_only.pretty"):    # one library left: no name needed
        (src / f).rename(tmp_path / f)
    assert KP.build(src, tmp_path / "pcm1", unchecked=True, log=QUIET)["library"] == "two"


def test_ds2_41_easyeda_smd_footprint_with_npth_pegs_stays_smd():
    from ecadlib import easyeda_convert as EC, footprint_rules as FR
    smd = ["PAD~RECT~-10~0~4~6~1~~1~0~~0~g1~0~~Y~0", "PAD~RECT~10~0~4~6~1~~2~0~~0~g2~0~~Y~0"]
    for peg, want in (("HOLE~0~8~1.5~g3~0", "smd"),                                  # a locating peg (NPTH)
                      ("PAD~ELLIPSE~0~8~3~3~11~~3~1.5~~0~g3~0~~N~0", "smd"),          # a pad with an unplated hole
                      ("PAD~ELLIPSE~0~8~3~3~11~~3~1.5~~0~g3~0~~Y~0", "through_hole")):  # a plated pin: really THT
        text = EC.footprint_to_kicad({"head": {"x": 0, "y": 0}, "shape": smd + [peg]}, name="SW_Pegs")
        attrs = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("(attr")]
        assert attrs == [f"(attr {want})"], (peg, attrs)
        assert not [f for f in FR.check(text, "SW_Pegs.kicad_mod") if f[1] == "attr"], peg   # nothing to hold it on


@lite
def test_ds2_41_pcblib_smd_footprint_with_npth_pegs_stays_smd(tmp_path):
    """The same footprints through a .PcbLib (kicad-export reads library footprints back from the PcbLib)."""
    from ecadlib import altium_lib as AL, easyeda_convert as EC, footprint_rules as FR
    smd = ["PAD~RECT~-10~0~4~6~1~~1~0~~0~g1~0~~Y~0", "PAD~RECT~10~0~4~6~1~~2~0~~0~g2~0~~Y~0"]
    for i, (peg, want) in enumerate((("HOLE~0~8~1.5~g3~0", "smd"),
                                     ("PAD~ELLIPSE~0~8~3~3~11~~3~1.5~~0~g3~0~~N~0", "smd"),
                                     ("PAD~ELLIPSE~0~8~3~3~11~~3~1.5~~0~g3~0~~Y~0", "through_hole"))):
        text = EC.footprint_to_kicad({"head": {"x": 0, "y": 0}, "shape": smd + [peg]}, name="SW_Pegs")
        AL.write_pcblib(tmp_path / f"p{i}.PcbLib", [AL.footprint_from_kicad(text)])
        back = X.footprint_from_pcblib(tmp_path / f"p{i}.PcbLib", "SW_Pegs", [])
        attrs = [ln.strip() for ln in back.splitlines() if ln.strip().startswith("(attr")]
        assert attrs == [f"(attr {want})"], (peg, attrs)
        assert not [f for f in FR.check(back, "SW_Pegs.kicad_mod") if f[1] == "attr"], peg
    # a footprint of unplated holes alone (a mounting-hole footprint) keeps the through-hole type it always had
    text = EC.footprint_to_kicad({"head": {"x": 0, "y": 0}, "shape": ["HOLE~0~0~3~g1~0"]}, name="MH")
    AL.write_pcblib(tmp_path / "mh.PcbLib", [AL.footprint_from_kicad(text)])
    back = X.footprint_from_pcblib(tmp_path / "mh.PcbLib", "MH", [])
    assert [ln.strip() for ln in back.splitlines() if ln.strip().startswith("(attr")] == ["(attr through_hole)"], back


@lite
def test_ds2_42_kicad_pcm_a_second_change_the_same_day_gets_a_newer_version(tmp_path):
    import datetime
    _, exp, _ = TP._export(False, tmp_path)
    out, day = exp.parent / "pcm", datetime.date(2026, 10, 20)
    build = lambda d: KP.build(exp, out, today=d, base_url="https://x.example/k", log=QUIET)
    r1 = build(day)
    f = exp / "lib.pretty" / "R_0603_1608Metric.kicad_mod"
    f.write_text(f.read_text().replace("(attr smd)", "(attr smd)\n\t(tags \"changed\")", 1))
    TP._as_written(exp, f)                     # a corrected library, exported again the same day
    r2 = build(day)
    assert r2["content_sha256"] != r1["content_sha256"]
    assert (r1["version"], r2["version"]) == ("2026.10.20", "2026.10.21"), (r1["version"], r2["version"])
    assert sorted(p.name for p in out.glob("*.zip")) == ["local.ecadlib.lib-2026.10.20.zip", "local.ecadlib.lib-2026.10.21.zip"]
    assert r2["update_timestamp"] > r1["update_timestamp"], (r1["update_timestamp"], r2["update_timestamp"])
    assert build(day)["version"] == "2026.10.21"                    # nothing changed since: same version
    f.write_text(f.read_text().replace('(tags "changed")', '(tags "changed again")', 1))
    TP._as_written(exp, f)                     # and the next day: newer than the version already out
    r4 = build(day + datetime.timedelta(days=1))
    assert r4["version"] == "2026.10.22" and r4["update_timestamp"] > r2["update_timestamp"], r4
    f.write_text(f.read_text().replace('(tags "changed again")', '(tags "later")', 1))
    TP._as_written(exp, f)                     # a week on, the date is newer again: today's date
    assert build(day + datetime.timedelta(days=7))["version"] == "2026.10.27"


@lite
def test_ds2_42_kicad_pcm_update_timestamp_passes_the_published_one_after_a_build_without_base_url(tmp_path):
    """A build without --base-url rewrites kicad_pcm.json with no update_timestamp; repository.json still says what
    KiCad has, so the next --base-url build moves past that (KiCad refetches packages.json only when it moves)."""
    import datetime
    _, exp, _ = TP._export(False, tmp_path)
    out, day, url = exp.parent / "pcm", datetime.date(2026, 10, 20), "https://x.example/k"
    published = lambda: json.loads((out / "repository.json").read_text())["packages"]["update_timestamp"]
    r1 = KP.build(exp, out, today=day, base_url=url, log=QUIET)
    f = exp / "lib.pretty" / "R_0603_1608Metric.kicad_mod"
    f.write_text(f.read_text().replace("(attr smd)", "(attr smd)\n\t(tags \"changed\")", 1))
    TP._as_written(exp, f)
    KP.build(exp, out, today=day, log=QUIET)                      # a zip to try out first: no --base-url
    assert published() == r1["update_timestamp"]
    r3 = KP.build(exp, out, today=day, base_url=url, log=QUIET)
    assert r3["version"] == "2026.10.21" and r3["update_timestamp"] > r1["update_timestamp"], \
        (r3["version"], r1["update_timestamp"], r3["update_timestamp"])
    # nothing changed since: the next day too, the same timestamp and repository.json is not rewritten
    later = day + datetime.timedelta(days=1)
    KP.build(exp, out, today=later, log=QUIET)
    snap = (out / "repository.json").stat().st_mtime_ns
    assert KP.build(exp, out, today=later, base_url=url, log=QUIET)["update_timestamp"] == r3["update_timestamp"]
    assert (out / "repository.json").stat().st_mtime_ns == snap


@lite
def test_ds2_42_kicad_pcm_keeps_no_version_from_kicad_pcm_json_that_is_not_one(tmp_path):
    """kicad_pcm.json edited by hand: a version that is not major[.minor[.patch]] is not kept (and no internal error);
    the library is unchanged, so the zip already there is the package."""
    import datetime
    _, exp, _ = TP._export(False, tmp_path)
    out, day = exp.parent / "pcm", datetime.date(2026, 10, 20)
    KP.build(exp, out, today=day, log=QUIET)
    z, p = out / "local.ecadlib.lib-2026.10.20.zip", out / "kicad_pcm.json"
    snap = z.stat().st_mtime_ns
    for bad in (2026, ["2026.10.20"], "2026.10.20.1"):
        p.write_text(json.dumps(dict(json.loads(p.read_text()), version=bad)))
        assert KP.build(exp, out, today=day, log=QUIET)["version"] == "2026.10.20", bad
    assert [f.name for f in out.glob("*.zip")] == [z.name] and z.stat().st_mtime_ns == snap


def test_ds2_43_a_value_with_a_thousands_or_decimal_comma_is_not_bound_renamed_or_merged_by_value():
    """'4,700pF' is 4.7 pF with a decimal comma and 4.7 nF with a thousands separator (change_diff._readings): no
    library part is picked by its value, no value style rewrites it, no other line's part takes it."""
    from ecadlib import altium_export as AE, bom_triage as T, kicad_index as KI, rules as RU
    idx = KI.KiCadIndex([])
    idx.symbols.append(KI.Symbol("lib", "C_4p7_0603", {"Value": "4.7pF", "Reference": "C",
                                                       "Footprint": "Capacitor_SMD:C_0603_1608Metric"}))
    assert idx.match("", "", "4,700pF", "0603", ref_class="C") == (None, None)
    assert idx.match("", "", "4,7pF", "0603", ref_class="C")[1] == "value+package"      # a decimal comma alone: one reading
    assert idx.match("", "", "4.7pF", "0603", ref_class="C")[1] == "value+package"
    lib = KI.KiCadIndex([])                    # nor is a library part whose own Value reads two ways picked by value
    lib.symbols.append(KI.Symbol("lib", "C_4n7_0603", {"Value": "4,700pF", "Reference": "C",
                                                       "Footprint": "Capacitor_SMD:C_0603_1608Metric"}))
    assert lib.match("", "", "4.7pF", "0603", ref_class="C") == (None, None)
    assert lib.match("", "", "4,700pF", "0603", ref_class="C") == (None, None)
    [row] = T.triage([{"designator": "C2", "value": "4,700pF", "footprint": "C_0603_1608Metric", "qty": "1"}], idx, [], None)
    assert row["local_symbol"] == "" and "4,700pF reads two ways: 4.7pF or 4700pF" in row["notes"], row
    assert RU.fmt_value("4,700pF", "Capacitors", "si") == "4,700pF"
    assert RU.fmt_value("4,7pF", "Capacitors", "si") == "4.7p" and RU.fmt_value("0,100uF", "Capacitors", "si") == "100n"
    rows = AE.to_rows([{"designator": "C1", "value": "4.7pF", "footprint": "C_0603_1608Metric", "qty": "1"},
                       {"designator": "C2", "value": "4,700pF", "footprint": "C_0603_1608Metric", "qty": "1"}])
    assert sorted(rows["Capacitors"]["Value"]) == ["4,700pF", "4.7pF"], rows["Capacitors"].to_dict("records")


@lite
def test_ds2_44_kicad_pcm_packages_a_crlf_checkout_of_a_kicad_export_folder(tmp_path):
    _, out, _ = TP._export(False, tmp_path)
    lf = KP.build(out, out.parent / "pcm_lf", log=QUIET)
    for f in [out / "lib.kicad_sym", *(out / "lib.pretty").glob("*.kicad_mod")]:   # git core.autocrlf on Windows
        f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    crlf = KP.build(out, out.parent / "pcm_crlf", log=QUIET)     # not an edit: kicad-export does not call it one (EXP-65)
    assert crlf["content_sha256"] == lf["content_sha256"] and crlf["footprints"] == lf["footprints"] == 4, (crlf, lf)


@lite
def test_ds2_45_kicad_export_warns_of_removed_parts_after_another_library_was_exported_there(tmp_path):
    plan, http = TP._plan(tmp_path / "a")
    other, _ = TP._plan(tmp_path / "b")                          # another BOM's plan, its own library in the same folder
    out = plan.parent.parent / "k"
    X.export(plan, out, "alpha", http=http, log=QUIET)
    X.export(other, out, "beta", http=http, log=QUIET)
    items = json.loads(plan.read_text(encoding="utf-8"))
    plan.write_text(json.dumps(items[1:]), encoding="utf-8")    # a part leaves alpha's plan
    rep = X.export(plan, out, "alpha", http=http, log=QUIET)
    assert [w for w in rep["warnings"] if "removed from" in w] == [
        f"1 part exported last time and not in this plan removed from alpha: {items[0]['name']}; a KiCad project "
        "using them loses those links"], rep["warnings"]


# ---- DS2-45 with main's kept parts: a part kept as exported before (held now) that then leaves the plan is reported
# as removed, from the per-library record and from a legacy single-library record alike
@lite
def test_ds2_45_kept_part_that_leaves_the_plan_is_reported_removed(tmp_path):
    import json
    from ecadlib import kicad_export as KX, release_check as RC
    from tests.test_release_check import make
    quiet = lambda *a, **k: None
    for form in ("libraries", "legacy"):
        d = tmp_path / form
        d.mkdir()
        ok, two = make(d), make(d, name="PART2", fp_name="SOIC-8_other")
        RC.apply([ok, two], d)
        (d / "plan.json").write_text(json.dumps([ok, two]))
        KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        two["description"] = ""                           # now held: kept as exported before
        (d / "plan.json").write_text(json.dumps([ok, two]))
        rep = KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        assert [k["part"] for k in rep["kept"]] == ["PART2"], rep["kept"]
        if form == "legacy":                              # a kicad_export.json from before per-library records
            f = d / "kicad" / "kicad_export.json"
            rec = json.loads(f.read_text())
            rec.pop("libraries", None)
            f.write_text(json.dumps(rec))
        (d / "plan.json").write_text(json.dumps([ok]))    # PART2 leaves the plan
        rep = KX.export(d / "plan.json", d / "kicad", "mylib", log=quiet)
        assert [w for w in rep["warnings"] if "removed from" in w] == [
            "1 part exported last time and not in this plan removed from mylib: PART2; a KiCad project using them "
            "loses those links"], (form, rep["warnings"])
