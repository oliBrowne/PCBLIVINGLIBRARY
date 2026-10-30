"""Property-based fuzz round (parsers, 2026-10-30): the counterexamples hypothesis found, as plain regression tests."""
import json, struct
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]


def _soic_pcblib(tmp_path):
    from ecadlib import altium_lib as AL
    spec = json.loads((ROOT / "ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    p = tmp_path / "a.PcbLib"
    AL.write_pcblib(p, [AL.footprint_from_spec(spec)[0]])
    return p


@lite
def test_ole_header_with_a_bad_sector_size_is_a_clean_error(tmp_path):
    """One damaged header byte (sector shift 34: 16 GB sectors) made olefile read a 16 GB sector: a raw MemoryError,
    not the 'damaged file' message; a bad mini sector shift (3) read the mini streams wrong without a word."""
    from ecadlib import altium_lib as AL
    good = _soic_pcblib(tmp_path).read_bytes()
    for off, val in ((0x1E, 34), (0x1E, 31), (0x20, 3)):
        b = bytearray(good)
        struct.pack_into("<H", b, off, val)
        p = tmp_path / "bad.PcbLib"
        p.write_bytes(bytes(b))
        try:
            AL.read_pcblib(p)
        except ValueError as e:
            assert "damaged" in str(e), e
        else:
            raise AssertionError(f"header {off:#x}={val} read without an error")
    assert AL.read_pcblib(tmp_path / "a.PcbLib")      # the good file still reads
