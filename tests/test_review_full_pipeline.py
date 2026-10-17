"""Regression tests for the pipeline review: BOM header mapping, duplicate headers, designator counting, plan item to
BOM line matching, error bodies in the HTTP cache, duplicate MPNs in sourcing, placeholder MPNs in dedupe, and
SQLite paths with '#'.
Run:  python3 -m tests.test_review_full_pipeline"""
import json, os, sqlite3, tempfile
from pathlib import Path

from ecadlib import bom_triage as BT, dedupe as DD, librarian as L, lifecycle as LC, rules_infer as RI, sourcing as S
from ecadlib.httpcache import CachedHTTP


def test_map_headers_hash_and_pn_mean_part_number():
    m = BT.map_headers(["Mouser #", "Mfr. #", "Manufacturer", "Description", "Order Qty."])
    assert m["mpn"] == "Mfr. #" and m["manufacturer"] == "Manufacturer"
    assert BT.map_headers(["Ref", "Part #", "Qty"]).get("mpn") == "Part #"
    m = BT.map_headers(["Ref Des", "MFR P/N", "MFR", "QTY"])
    assert m["mpn"] == "MFR P/N" and m["manufacturer"] == "MFR"


def test_read_bom_duplicate_header_reads_one_cell():
    d = Path(tempfile.mkdtemp())
    f = d / "dup.csv"
    f.write_text("Designator,Value,Manufacturer,MPN,Manufacturer,MPN,Qty\n"
                 "R1,10k,Yageo,RC0603FR-0710KL,Vishay,CRCW060310K0FKEA,1\n")
    lines, _ = BT.read_bom(f)
    ln = lines[0]
    assert ln["mpn"] == "RC0603FR-0710KL", ln["mpn"]
    assert ln["manufacturer"] == "Yageo"


def test_count_refs_suffixed_designators():
    assert BT.count_refs("R1_CH1, R1_CH2") == 2
    assert BT.count_refs("R1_1") == 1
    assert BT.count_refs("R1-R4") == 4 and BT.count_refs("U1A, U1B") == 1


def _run(text):
    d = Path(tempfile.mkdtemp())
    bom = d / "bom.csv"
    bom.write_text(text)
    return L.run(bom, out=d / "out", sources=["standard"], offline=True, cache=d / "cache", log=lambda *a, **k: None)


def test_line_for_prefers_matching_footprint():
    items = _run("Designator,Value,Footprint,Qty\nR1,10k,0603,1\nR2,10k,0402,1\nC1,100n,0805,1\nC2,100n,0402,1\n")
    got = {it["name"]: it["source"]["footprint"] for it in items}
    assert got["CAP-0402-100n"].endswith("C_0402_1005Metric"), got
    assert got["CAP-0805-100n"].endswith("C_0805_2012Metric"), got
    assert got["RES-0402-10k"].endswith("R_0402_1005Metric"), got
    assert got["RES-0603-10k"].endswith("R_0603_1608Metric"), got


def test_line_for_prefers_fitted_line_over_dnp():
    items = _run("Designator,Value,Footprint,MPN,Manufacturer,DNP,Qty\n"
                 "R5,10k,0603,RC0603FR-0710KL,Yageo,DNP,1\nR1,10k,0603,RC0603FR-0710KL,Yageo,,1\n")
    assert len(items) == 1, items
    it = items[0]
    assert "DNP line: not fetched" not in it["librarian"]["tried"], it["librarian"]
    assert it["source"]["footprint"] and it["source"]["symbol"], it["source"]


class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text, self.ok = status, text, 200 <= status < 400


def _http(answers):
    http = CachedHTTP(tempfile.mkdtemp(), min_interval=0)
    http.s.request = lambda method, url, data=None, headers=None, timeout=None: answers.pop(0)
    return http


def test_cache_does_not_keep_mouser_errors_body():
    answers = []
    http = _http(answers)
    old = os.environ.get("MOUSER_API_KEY")
    try:
        os.environ["MOUSER_API_KEY"] = "WRONGKEY"
        m = S.MouserSourcing(http)
        answers.append(_Resp(200, json.dumps({"Errors": [{"Code": "Invalid", "Message": "Invalid unique identifier."}],
                                              "SearchResults": None})))
        assert m.part({"mpn": "LM317T", "manufacturer": ""}).error
        os.environ["MOUSER_API_KEY"] = "GOODKEY"
        answers.append(_Resp(200, json.dumps({"Errors": [], "SearchResults": {"Parts": [
            {"ManufacturerPartNumber": "LM317T", "Manufacturer": "TI", "AvailabilityInStock": "5"}]}})))
        r = m.part({"mpn": "LM317T", "manufacturer": ""})
        assert r.found and not r.error and not answers
    finally:
        if old is None:
            os.environ.pop("MOUSER_API_KEY", None)
        else:
            os.environ["MOUSER_API_KEY"] = old


def test_cache_does_not_keep_graphql_errors_body():
    answers = []
    http = _http(answers)
    old = os.environ.get("NEXAR_SUPPLY_TOKEN")
    try:
        os.environ["NEXAR_SUPPLY_TOKEN"] = "tok"
        n = LC.NexarSupply(http)
        answers.append(_Resp(200, json.dumps({"errors": [{"message": "Part limit exceeded"}]})))
        assert n.check({"mpn": "LM317T", "manufacturer": ""}).error
        answers.append(_Resp(200, json.dumps({"data": {"supSearchMpn": {"results": [{"part": {"mpn": "LM317T"}}]}}})))
        r = n.check({"mpn": "LM317T", "manufacturer": ""})
        assert not r.error and not answers
    finally:
        if old is None:
            os.environ.pop("NEXAR_SUPPLY_TOKEN", None)
        else:
            os.environ["NEXAR_SUPPLY_TOKEN"] = old


class _Fake:
    name, shapes, auth = "fake", "", ""

    def part(self, p, alternates=False):
        i = S.PartInfo("fake", True, p["mpn"], "Maker")
        i.offers = [S.Offer("fake", "Mouser", "SKU1", stock=8, moq=1, mult=1, breaks=[(1, 1.0), (10, 0.5)], currency="USD")]
        return i

    def search(self, q, limit=20):
        return [], ""

    def substitutes(self, p):
        return [], ""


def test_sourcing_buys_duplicate_mpn_lines_together():
    lines = [{"designator": "U1-U5", "qty": "5", "mpn": "LM358", "manufacturer": "TI"},
             {"designator": "U6-U10", "qty": "5", "mpn": "LM358", "manufacturer": "TI"}]
    rows, _ = S.source_bom(lines, [_Fake()], 1, "USD")
    assert rows[0]["need"] == 10 and rows[0]["chosen"]["covers"] is False
    assert rows[1]["kind"] == "merged" and rows[1]["need"] == 0
    roll = S.rollup(rows, 1, "USD")
    assert roll["complete"] is False


def test_sourcing_does_not_merge_placeholder_mpns():
    lines = [{"designator": "R1", "qty": "1", "mpn": "N/A", "manufacturer": "", "value": "10k"},
             {"designator": "R2", "qty": "1", "mpn": "N/A", "manufacturer": "", "value": "1k"}]
    rows, _ = S.source_bom(lines, [_Fake()], 1, "USD")
    assert not any(r.get("kind") == "merged" for r in rows)


def test_placeholder_mpn_has_no_dedupe_key():
    assert DD.mpn_key("N/A") == DD.mpn_key("TBD") == DD.mpn_key("-") == ""
    assert DD.mpn_key("LM358") == "LM358"
    col = DD.Collector(DD.load_config(), log=lambda *a: None)
    col.add_plan([{"name": "RES-0603-10k", "parameters": [{"name": "Manufacturer Part Number", "value": "N/A"}]},
                  {"name": "RES-0603-1k", "parameters": [{"name": "Manufacturer Part Number", "value": "n/a"}]}],
                 tempfile.mkdtemp())
    assert not [g for g in DD.find_groups(col) if g["kind"] == "mpn"]


def _hash_db():
    d = Path(tempfile.mkdtemp()) / "Lib #2"
    d.mkdir()
    f = d / "parts.sqlite"
    con = sqlite3.connect(f)
    con.execute('create table Resistors ("Part Number" text, "Manufacturer Part Number" text)')
    con.execute("insert into Resistors values ('R1', 'RC0603FR-0710KL')")
    con.commit()
    con.close()
    return f


def test_sqlite_path_with_hash_dedupe():
    c = DD.Collector(DD.load_config(), log=lambda *a: None)
    c.add_table_file(_hash_db())
    assert [p["name"] for p in c.parts] == ["R1"] and not c.errors


def test_sqlite_path_with_hash_rules_infer():
    got = RI.read_table_file(_hash_db())
    assert got and "RC0603FR-0710KL" in json.dumps(got, default=str)


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_review_full_pipeline"]))
