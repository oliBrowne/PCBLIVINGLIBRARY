"""Debug-sweep regressions (BOM reading, group bomread). Plain functions: python -m tests.run_all test_debugsweep_bomread"""
from ecadlib import bom_triage as T
from tests.fixtures_lite import lite


@lite
def test_ds2_03_trailing_unnamed_notes_column_keeps_mpn(tmp_path):
    # Excel writes every row to the sheet's width: a notes column with no header gives 'Footprint,' in the header
    f = tmp_path / "notes_col.csv"
    f.write_bytes(b"Designator,Qty,Value,MPN,Footprint,\r\nR1,1,10k,RC0603FR-0710KL,0603,check stock\r\n"
                  b"C1,1,100n,GRM188R71H104KA93D,0603,\r\nU1,1,LDO,AMS1117-3.3,SOT-223,alt: LM1117\r\n"
                  b"R2,1,4,7k,RC0603FR-074K7L,0603,\r\n")
    lines, _ = T.read_bom(f)
    got = [(l["designator"], l["value"], l.get("mpn"), l.get("footprint"), l.get("bom_note")) for l in lines]
    assert got[:3] == [("R1", "10k", "RC0603FR-0710KL", "0603", None),
                       ("C1", "100n", "GRM188R71H104KA93D", "0603", None),
                       ("U1", "LDO", "AMS1117-3.3", "SOT-223", None)], got
    # a row one cell wider than the header row is still an unquoted decimal comma
    assert got[3][:4] == ("R2", "4,7k", "RC0603FR-074K7L", "0603") and "decimal comma" in got[3][4], got
