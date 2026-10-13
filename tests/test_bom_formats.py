"""Real-world BOM shapes reported by the UI thread (offline). Run:  python3 -m tests.test_bom_formats"""
import tempfile
from pathlib import Path

from ecadlib import bom_triage as T
from ecadlib.kicad_index import KiCadIndex, Symbol, package_of


def _write(text, suffix=".csv", enc="utf-8"):
    f = Path(tempfile.mkdtemp()) / f"bom{suffix}"
    f.write_bytes(text.encode(enc))
    return f


def _index(*syms):
    idx = KiCadIndex([])
    idx.symbols = list(syms)
    return idx


ACTIVEBOM = """Bill of Materials;;;;;
Project: X;;;;;

Comment;Description;Designator;Footprint;LibRef;Quantity;Manufacturer Part Number
10k;Resistor;R1, R2;RESC1608X55N;RES;2;
100nF;Capacitor;C1;CAPC1005X55N;CAP;1;
STM32F103C8T6;MCU;U1;LQFP-48;STM32F103C8Tx;1;
AO3401A;P-MOSFET;Q1;SOT-23;PMOS;1;
;;;;MyLibPart-01;1;
"""


def test_activebom_title_rows():
    lines, mapping = T.read_bom(_write(ACTIVEBOM))
    assert mapping["designator"] == "Designator" and mapping["libref"] == "LibRef", mapping
    assert [l["designator"] for l in lines][:4] == ["R1, R2", "C1", "U1", "Q1"], lines
    assert len(lines) == 5


def test_ipc7351_chip_names_map_to_imperial():
    assert package_of("RESC1608X55N") == "0603" and package_of("CAPC1005X55N") == "0402"
    assert package_of("INDC2012X100N") == "0805" and package_of("RESC3216X60N") == "1206"
    assert package_of("R_0603_1608Metric") == "0603"           # KiCad names still win on their imperial code


def test_comment_and_libref_used_as_part_numbers():
    lines, _ = T.read_bom(_write(ACTIVEBOM))
    assert T.part_numbers(lines[2]) == ["STM32F103C8T6", "STM32F103C8Tx"]
    assert T.part_numbers(lines[0]) == []                       # '10k' is a value, 'RES' a generic symbol
    for generic in ("Res1", "Cap", "Cap Pol1", "Inductor", "LED0", "Diode", "PMOS", "R"):
        assert T.part_numbers({"libref": generic}) == [], generic
    idx = _index(Symbol("mcu", "STM32F103C8T6", {"Value": "STM32F103C8T6"}),
                 Symbol("fet", "AO3401A", {"Value": "AO3401A"}),
                 Symbol("my", "MyLibPart-01", {}))
    rows = {r["designator"]: r for r in T.triage(lines, idx, [], None)}
    assert rows["U1"]["local_symbol"].startswith("mcu:STM32F103C8T6 (mpn)"), rows["U1"]
    assert rows["Q1"]["local_symbol"].startswith("fet:AO3401A (mpn)"), rows["Q1"]
    assert rows["U1"]["mpn"] == "STM32F103C8T6"
    assert rows[""]["local_symbol"].startswith("my:MyLibPart-01"), rows[""]


def test_kicad8_dnp_column():
    bom = ('"Reference","Value","Footprint","Qty","DNP","MPN"\n'
           '"R1","10k","Resistor_SMD:R_0603_1608Metric","1","",""\n'
           '"J9","Conn","Connector:Weird_Thing","1","DNP","XYZ-123"\n')
    lines, mapping = T.read_bom(_write(bom))
    assert mapping["dnp"] == "DNP" and [l["dnp"] for l in lines] == [False, True]
    rows = T.triage(lines, _index(), [], None)
    assert rows[1]["status"] == "dnp (not fitted)" and rows[0]["status"] != "dnp (not fitted)"


def test_empty_file_is_clear_error():
    for text in ("", "\n\n  \n", ";;;\n"):
        try:
            T.read_bom(_write(text))
        except ValueError as e:
            assert "empty" in str(e), e
        else:
            raise AssertionError(f"no error for {text!r}")


def test_binary_or_headerless_file_is_clear_error():
    for data, why in ((b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)), "binary"),
                      (b"hello,world\nfoo,bar\n", "no BOM header")):
        f = _write("")
        f.write_bytes(data)
        try:
            T.read_bom(f)
        except ValueError as e:
            assert why in str(e), e
        else:
            raise AssertionError(why)


def test_plain_and_cp1252_csv_still_work():
    lines, _ = T.read_bom(_write("Designator\tQty\tValue\nR1\t1\t4.7µF\n", enc="cp1252"))
    assert lines == [{"designator": "R1", "qty": "1", "value": "4.7µF"}], lines
    lines, _ = T.read_bom(Path("examples/sample_bom.csv"))
    assert len(lines) == 14


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
