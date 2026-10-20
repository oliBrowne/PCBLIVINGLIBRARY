"""Issue #54 (risk R3, licensing of third-party part data): only parts taken from the KiCad libraries are labelled
CC-BY-SA 4.0, a KiCad part bound for Altium carries the KiCad credit like an EasyEDA part carries EasyEDA's, and the
librarian's output holds a NOTICE.txt naming the third-party terms of what is in it.
Fixture: tests/fixtures/kicad (KiCad libraries, CC-BY-SA 4.0, see NOTICE)."""
from pathlib import Path

from ecadlib import easyeda_convert as EC, kicad_export as KX, kicad_import as K, librarian as L
from tests.fixtures_lite import lite

MOD = (Path(__file__).parent / "fixtures" / "kicad" / "USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod").read_text(encoding="utf-8")


def _par(item):
    return {p["name"]: p["value"] for p in item["parameters"]}


def test_only_kicad_footprints_are_labelled_cc_by_sa():
    """Before: every footprint the librarian converted said '(CC-BY-SA 4.0)', including EasyEDA parts, your own
    --lib files and footprints ecadlib generated."""
    kicad, _ = K.convert_footprint(MOD, "USB_C", "Connector_USB:USB_C_HRO", [], K.KICAD_LICENSE)
    assert kicad["description"].endswith("from Connector_USB:USB_C_HRO (CC-BY-SA 4.0)"), kicad["description"]
    for src in ("C165948", "my_lib:USB_C", "spec"):          # no license to attribute: the source is not named in the
        fp, _ = K.convert_footprint(MOD, "USB_C", src, [])   # description (the plan records it), 2026-10-20
        assert "CC-BY-SA" not in fp["description"] and src not in fp["description"], fp["description"]


@lite
def test_librarian_labels_by_source(tmp_path):
    r = L.Run(tmp_path, None, None, [], [])
    for source in ("kicad:Connector_USB:USB_C_HRO", "easyeda:C165948", "local:mine:USB_C", "standard:USB_C"):
        st = {"name": source.split(":")[0], "symbol": None, "footprint": None}
        r.put_fp(st, MOD.replace("HRO_TYPE-C-31-M-12", source.split(":")[0]), f"FP_{st['name']}", source)
        assert st["footprint"]["source"] == source
    for name, want in (("kicad", True), ("easyeda", False), ("local", False), ("standard", False)):
        data = (tmp_path / "models").rglob(f"FP_{name}.PcbLib").__next__().read_bytes()
        assert (b"CC-BY-SA" in data) == want, name


def test_kicad_part_gets_library_credit_for_altium():
    item = {"symbol": {}, "footprint": {}, "parameters": []}
    L.apply_state(item, {"found": {}, "symbol": {"source": "kicad:Device:R", "file": "s"},
                         "footprint": {"source": "standard:R_0603_1608Metric", "file": "f"}}, {})
    assert _par(item)["Library Credit"] == K.KICAD_CREDIT and item["field_sources"]["Library Credit"] == "KiCad library symbol"
    both = {"symbol": {}, "footprint": {}, "parameters": []}
    L.apply_state(both, {"found": {"credit": (EC.CREDIT, "EasyEDA C1")}, "symbol": {"source": "easyeda:C1", "file": "s"},
                         "footprint": {"source": "kicad:Package_TO_SOT_SMD:SOT-23", "file": "f"}}, {})
    assert _par(both)["Library Credit"] == f"{EC.CREDIT}; {K.KICAD_CREDIT}", _par(both)
    own = {"symbol": {}, "footprint": {}, "parameters": []}
    L.apply_state(own, {"found": {}, "symbol": {"source": "local:mine:R", "file": "s"},
                        "footprint": {"source": "datasheet:https://x/ds.pdf", "file": "f"}}, {})
    assert "Library Credit" not in _par(own)


@lite
def test_notice_follows_what_the_plan_holds(tmp_path):
    kicad = {"source": {"symbol": "kicad:Device:R", "footprint": "standard:R_0603"}}
    easy = {"source": {"symbol": "easyeda:C1", "footprint": "easyeda:C1"}}
    own = {"source": {"symbol": "local:mine:R", "footprint": None}}
    f = L.write_notice(tmp_path, [kicad, own])
    assert f.read_text(encoding="utf-8") == KX.KICAD_NOTICE
    text = L.write_notice(tmp_path, [kicad, easy]).read_text(encoding="utf-8")
    assert text.startswith(KX.KICAD_NOTICE) and EC.CREDIT in text
    assert L.write_notice(tmp_path, [own, {}]) is None and not (tmp_path / "NOTICE.txt").exists()
    (tmp_path / "NOTICE.txt").write_text("my own notice\n", encoding="utf-8")      # a notice you wrote is left alone
    L.write_notice(tmp_path, [own])
    assert (tmp_path / "NOTICE.txt").read_text(encoding="utf-8") == "my own notice\n"
