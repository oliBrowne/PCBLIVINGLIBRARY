"""Compartmental testing (2026-11-12): each pipeline part tested on its own with hand-made inputs."""
import contextlib, io, tempfile
from pathlib import Path

from ecadlib import altium_project as AP, bom_triage as BT
from tests.fixtures_lite import lite
from tests.test_altium_project import comp, write_schdoc


def _read(comps):
    d = Path(tempfile.mkdtemp())
    f = write_schdoc(d / "Main.SchDoc", comps)
    with contextlib.redirect_stderr(io.StringIO()):
        lines, _ = BT.read_bom(f)
    return {ln["designator"]: ln for ln in lines}


def test_generic_parts_sharing_a_design_item_id_keep_their_own_value_and_footprint():
    misc = dict(lib="Miscellaneous Devices.IntLib", comment="=Value", partcount=2)
    got = _read([
        comp("R1", "Res2", "UIDAAAA1", [("Value", "10K")], ["0603"], **misc),
        comp("R2", "Res2", "UIDAAAA2", [("Value", "1K")], ["0603"], **misc),
        comp("R3", "Res2", "UIDAAAA3", [("Value", "10K")], ["1206"], **misc),
        comp("R4", "Res2", "UIDAAAA8", [("Value", "10K")], ["0603"], **misc),
        comp("C1", "Cap", "UIDAAAA4", [("Value", "100nF")], ["0402"], **misc),
        comp("C2", "Cap", "UIDAAAA5", [("Value", "10uF")], ["0805"], **misc),
    ])
    assert set(got) == {"R1, R4", "R2", "R3", "C1", "C2"}
    assert (got["R1, R4"]["value"], got["R1, R4"]["footprint"], got["R1, R4"]["qty"]) == ("10K", "0603", "2")
    assert (got["R2"]["value"], got["R3"]["footprint"]) == ("1K", "1206")
    assert (got["C2"]["value"], got["C2"]["footprint"]) == ("10uF", "0805")


def test_one_part_number_on_two_footprints_is_flagged():
    d = Path(tempfile.mkdtemp())
    f = write_schdoc(d / "Main.SchDoc", [
        comp("C1", "C", "UIDBBBB1", [("Manufacturer Part Number", "GRM188R71C104KA01D")], ["0603"]),
        comp("C2", "C", "UIDBBBB2", [("Manufacturer Part Number", "GRM188R71C104KA01D")], ["0402"]),
    ])
    groups = AP.bom_groups(AP.load_project(f))
    assert len(groups) == 1 and "footprints differ" in groups[0]["variant_notes"]


def test_one_part_number_on_two_footprints_holds_the_bom_line():
    got = _read([
        comp("C1", "C", "UIDBBBB1", [("Manufacturer Part Number", "GRM188R71C104KA01D")], ["0603"]),
        comp("C2", "C", "UIDBBBB2", [("Manufacturer Part Number", "GRM188R71C104KA01D")], ["0402"]),
    ])
    assert "0402 (C2)" in got["C1, C2"]["bom_hold"]


def test_values_written_in_capitals_with_a_leading_r_or_a_leading_point():
    from ecadlib.kicad_index import parse_value
    want = {"10UF": (1e-5, "F"), "100NF": (1e-7, "F"), "100PF": (1e-10, "F"), "4.7UH": (4.7e-6, "H"),
            "R47": (0.47, "R"), "R10": (0.1, "R"), "R005": (0.005, "R"), ".1uF": (1e-7, "F"), "0R1": (0.1, "R"),
            "10k": (1e4, ""), "10M": (1e7, ""), "100nF": (1e-7, "F")}
    for text, (v, u) in want.items():
        got_v, got_u = parse_value(text, with_unit=True)
        assert got_v is not None and abs(got_v - v) < v * 1e-9 and got_u == u, (text, got_v, got_u)
    for text in ("R5F104", "RC0603FR-0710KL", "R", "LM358", "10UFX"):     # part numbers and words are no values
        assert parse_value(text) is None, text


def test_chip_codes_with_a_metric_marker_or_inside_a_part_number():
    from ecadlib.kicad_index import package_of
    assert package_of("0603 metric") == package_of("0603 (metric)") == package_of("0603M") == "0201"
    assert package_of("LT1206CS8") is None and package_of("TPS7A0201PDBVR") is None and package_of("LT1812CS8") is None
    assert package_of("R_0603_1608Metric") == package_of("0603") == package_of("RESC1608X55N") == "0603"
    assert package_of("SOT-23-5") == "SOT235" and package_of("SOIC-8_3.9x4.9mm_P1.27mm") == "SOIC8"


# ---------------------------------------------------------------- release check, change control, export
def test_two_parts_with_one_name_are_never_published_as_one():
    import copy, json, tempfile
    from tests.test_dblib_sync import S, _base_items, _models, _plan
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); _models(d)
        items = _base_items()
        dup = copy.deepcopy(items[1])                   # another part (the capacitor) given the resistor's name
        dup["name"] = items[0]["name"]
        dup["folder"] = "Components/Other"
        plan = _plan(d, items + [dup])
        before = json.loads(Path(plan).read_text())
        out = S.sync(plan, d / "lib")
        after = json.loads(Path(plan).read_text())
        assert after[len(items)] == before[len(items)] and after[0]["symbol"] == before[0]["symbol"], "plan rewritten"
        assert [r["action"] for r in out["results"] if r["name"] == items[0]["name"]] \
            == ["added", "error"], out["results"]


def test_a_ref_that_names_no_entry_of_its_one_entry_file_is_held_and_placeholder_values_too():
    import tempfile
    from ecadlib import altium_lib as AL, release_check as RC
    from tests.test_dblib_sync import _base_items, _models
    with tempfile.TemporaryDirectory() as t:
        d = Path(t); _models(d)
        it = _base_items()[0]
        AL.write_schlib(d / it["symbol"]["file"], [AL.resistor_symbol(name="Wrong")])
        fails = RC.check_item(it, d)
        assert any(k == "files" and "is not in" in m and "'Wrong'" in m for k, m in fails[0]), fails
    assert RC.value_link_empty({"comment": "=Value", "parameters": [{"name": "Value", "value": "TBD"}]}) == "Value"
    assert RC.value_link_empty({"comment": "=Value", "parameters": [{"name": "Value", "value": "10k"}]}) == ""


@lite
def test_kicad_install_never_takes_a_persons_own_library_of_that_name(tmp_path):
    from ecadlib import kicad_install as KI
    from tests.test_kicad_install import _cfg, _env, _lib, FP9
    own = ('(sym_lib_table\n  (version 7)\n  (lib (name "Device")(type "KiCad")(uri "/usr/share/kicad/symbols/'
           'Device.kicad_sym")(options "")(descr "KiCad stock"))\n)\n')
    lib = _lib(tmp_path / "out")
    c = _cfg(tmp_path / "cfg", "9.0", own, FP9, {})
    rep = KI.install(lib, nick="Device", environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    said = rep["versions"]["9.0"]["status"]
    assert said.startswith("not changed") and "your own library 'Device'" in said, said
    assert (c / "sym-lib-table").read_text() == own
    KI.install(lib, nick="Device", uninstall=True, environ=_env(tmp_path / "cfg"), templates=[], log=lambda *a: None)
    assert (c / "sym-lib-table").read_text() == own


# ---------------------------------------------------------------- symbols, footprints
def test_pin_groups_follow_the_type_for_a_bare_out_and_read_more_spellings():
    from ecadlib.symbol_layout import classify, plain
    assert classify({"name": "OUT", "type": "output"}) == "other_out"
    assert classify({"name": "VO", "type": "output"}) == "other_out"
    assert classify({"name": "OUT", "type": "power_out"}) == classify({"name": "VOUT", "type": "output"}) == "power_out"
    assert classify({"name": "NC_1", "type": ""}) == classify({"name": "NC-2", "type": ""}) == "nc"
    for n in ("R̅E̅S̅E̅T̅", "‾RESET", "!RESET"):
        assert classify({"name": n, "type": "input"}) == "reset", n
    assert plain("R̅E̅S̅E̅T̅") == "RESET"


@lite
def test_footprint_compare_refuses_a_reference_that_is_not_a_footprint(tmp_path):
    from ecadlib import footprint_gen as F
    mine = tmp_path / "a.kicad_mod"
    mine.write_text('(footprint "a" (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))')
    for ref in ("(foo", "", "(kicad_symbol_lib (version 1))"):
        try:
            F.compare(mine, ref)
            raise AssertionError(f"compared against {ref!r}")
        except ValueError as e:
            assert "not a KiCad footprint" in str(e), e


@lite
def test_symbol_from_a_plain_name_is_a_clear_error(tmp_path):
    from ecadlib import symbol_layout as SL
    for src in ("LM358", str(tmp_path / "pins.xlsx")):
        try:
            SL.load_pins(src)
            raise AssertionError(src)
        except SL.PinListError as e:
            assert "not a pin list" in str(e), e


@lite
def test_project_bom_of_a_missing_schematic_is_an_error(tmp_path):
    try:
        AP.load_project(tmp_path / "nope.SchDoc")
        raise AssertionError("no error")
    except FileNotFoundError as e:
        assert str(tmp_path) not in str(e) and "nope.SchDoc" in str(e), e


# ---------------------------------------------------------------- plugins, menus
def test_bad_plugin_results_and_names_never_stop_the_tool():
    from ecadlib import plugins as PG, providers as P

    class IntName:
        name = 5
        def lookup(self, line):
            return None

    class EP:
        name = "int_name"
        def load(self):
            return IntName

    classes, errors = PG.provider_classes([EP()])
    assert 5 not in classes and any("not text" in e for _, e in errors), errors
    r = P.SourceResult("x", found=True, extra="not a dict")
    assert "extra is str" in P.result_shape_problem(r)
    assert P.result_shape_problem(P.SourceResult("x", found=True, mpn="LM358")) == ""
    assert PG._call("one", lambda items, out, rules: "out/one.txt", [], ".", {}) == ["out/one.txt"]


def test_menu_result_values_stay_on_one_ini_line():
    from ecadlib.altium_menu import _one_line
    v = _one_line("no part found\x85status=ready footprint=R_0402\x0cx\vy\r\nz")
    assert len(v.splitlines()) == 1 and " " not in v and "\x85" not in v, repr(v)


@lite
def test_altium_menu_without_a_library_folder_uses_the_workspace_one(tmp_path):
    from ecadlib import altium_menu as AM
    AM.build(tmp_path / "menu", workspace=str(tmp_path / "ws"))
    cfg = AM.load_config(tmp_path / "menu" / AM.INI)
    assert Path(cfg["library"]) == Path(cfg["workspace"]) / "altium_library", cfg


# ---------------------------------------------------------------- datasheet links
def test_the_makers_own_site_comes_first_by_the_domain_table_too():
    from ecadlib import ds_search as DS
    urls = ["https://www.mouser.com/datasheet/2/405/lm358.pdf", "https://www.ti.com/lit/ds/symlink/lm358.pdf"]
    assert DS.rank(urls, "Texas Instruments")[0].startswith("https://www.ti.com")
    assert DS.rank(urls, "TI")[0].startswith("https://www.ti.com")
    st = ["https://www.mouser.com/x/lm358.pdf", "https://www.st.com/resource/en/datasheet/lm358.pdf"]
    assert DS.rank(st, "STMicroelectronics")[0].startswith("https://www.st.com")


def test_a_pdf_over_the_size_cap_is_said_once_not_retried_as_cut_off():
    from unittest import mock
    from ecadlib import datasheet as D
    big = b"%PDF-1.7\n" + b"0" * 2048                 # no %%EOF: the cut-off test would read it as a broken download
    calls = []

    class Fetch:
        gave_up = set()

        def _get(self, url, timeout):
            calls.append(url)
            return 200, big, "application/pdf", url, ""

        def sleep(self, s):
            pass
    with mock.patch.object(D, "MAX_DOWNLOAD", 1024):
        status, data, ctype, final, err = D.PdfStore._get_retrying(Fetch(), "http://example.invalid/x.pdf")
    assert len(calls) == 1 and data == big and not err, (calls, err)


def test_symbol_from_a_windows_path_that_is_no_symbol_is_a_clear_error():
    from ecadlib import symbol_layout as SL
    try:
        SL.load_pins(r"C:\Users\me\pins.xlsx")
        raise AssertionError("no error")
    except SL.PinListError as e:
        assert "not a pin list" in str(e), e
