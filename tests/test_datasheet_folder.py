"""Offline tests for the [datasheet] folder (PDFs a person downloaded, tried before any link) and [datasheet]
standard_land (a read that identifies a checked standard package gets the table's land).   python3 -m tests.test_datasheet_folder

Nothing live: the API is test_crosscheck's fake, PDFs are test_ds_route's SYNTHETIC text PDFs, extractions are
test_datasheet's SYNTHETIC fixtures."""
import copy, tempfile
from pathlib import Path
from types import SimpleNamespace

from ecadlib import datasheet as D, librarian as L, rules as RU, stdparts as SP
from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB, test_ds_route as TR

SOIC = "SOIC-8_3.9x4.9mm_P1.27mm"


def folder(d, *names):
    f = Path(d) / "pdfs"
    f.mkdir()
    for n in names:
        (f / n).write_bytes(b"not a pdf" if n.endswith(".txt.pdf") else TC.pdf3())
    return f


# ---------------------------------------------------------------- finding the file
def test_local_datasheet_names():
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "ltm4671.pdf", "LTM4671IY-PBF_datasheet.PDF", "AD7.pdf", "notes.txt")
        p, how = D.local_datasheet(f, "LTM4671IY#PBF")
        assert p.name == "LTM4671IY-PBF_datasheet.PDF" and "family" not in how           # the full MPN beats the family
        p, how = D.local_datasheet(f, "LTM4671")
        assert p.name == "ltm4671.pdf"                                                   # case and punctuation ignored
        p, how = D.local_datasheet(f, "LTM4671EY")
        assert p.name == "ltm4671.pdf" and "family datasheet" in how
        assert D.local_datasheet(f, "AD7606")[0] is None                                 # 3 characters name no family
        assert D.local_datasheet(f, "LT8610")[0] is None
        assert D.local_datasheet("", "LT8610") == (None, "no [datasheet] folder")
        assert "does not exist" in D.local_datasheet(Path(d) / "nope", "LT8610")[1]


def test_folder_pdf_is_used_before_a_refused_link_and_keeps_the_link():
    api = TC.API(lambda k, m, b: TC.first())
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "TESTPART.pdf")
        http = TB.PdfHTTP({TC.URL: 403})                       # the maker's site refuses scripts
        with TB.patched(api):
            rd = TC.reader(d, {"datasheet": {"folder": str(f)}}, http=http)
            job = rd.job("TESTPART", [(TC.URL, "BOM")], TC.QFN)
        assert http.fetched == []                              # no download tried at all
        prov = job["base_prov"]
        assert prov["local"] == str(f / "TESTPART.pdf") and prov["where"] == "TESTPART.pdf in the datasheet folder"
        assert job["url"] == TC.URL and job["data"] == TC.pdf3()   # the part keeps its public link, never a local path
        with TB.patched(api):                                  # no link at all: the folder is enough
            job = TC.reader(d, {"datasheet": {"folder": str(f)}}, http=http).job("TESTPART", [], TC.QFN)
        assert job["data"] == TC.pdf3() and job["url"] == ""


def test_no_pdf_says_where_to_put_one():
    with tempfile.TemporaryDirectory() as d, TB.patched(TC.API(lambda k, m, b: TC.first())):
        f = folder(d, "TESTPART.txt.pdf")                      # named .pdf, not a PDF
        for rules, want in ((None, "set [datasheet] folder"), ({"datasheet": {"folder": str(f)}}, "TESTPART.txt.pdf: not a PDF")):
            try:
                TC.reader(d, rules).job("TESTPART", [], TC.QFN)
                raise AssertionError("no PDF, yet a job")
            except D.DatasheetError as e:
                assert want in str(e), e
        try:
            TC.reader(d, None, http=TB.PdfHTTP({TC.URL: 404})).job("TESTPART", [(TC.URL, "BOM")], TC.QFN)
            raise AssertionError
        except D.DatasheetError as e:
            assert "download it by hand into a folder and set [datasheet] folder" in str(e)


def test_rules_validate_folder_and_standard_land():
    RU.validate({"datasheet": {"folder": "~/datasheets", "standard_land": "always"}}, "t")
    for bad in ({"standard_land": "yes"}, {"folder": 3}):
        try:
            RU.validate({"datasheet": bad}, "t")
            raise AssertionError(bad)
        except RU.RulesError as e:
            assert "datasheet." in str(e)


# ---------------------------------------------------------------- identifying the package
def read_spec(ext):
    return D.process(TD._complete(copy.deepcopy(ext)), "TESTPART", "https://a/ds.pdf")["spec"]


def test_identify_by_the_read_numbers_never_by_a_guess():
    assert SP.identify(read_spec(TD.INCH))[0] == SOIC                   # printed in inches: 150 mil SOIC-8
    short = copy.deepcopy(TD.INCH)                                     # the terminal numbers do not pick the package
    short["package"]["terminal_length"] = short["package"]["terminal_width"] = None
    assert SP.identify(read_spec(short))[0] == SOIC
    wide = copy.deepcopy(TD.INCH)                                      # a 5.3 mm body is not the 3.9 mm SOIC-8
    wide["package"]["body_width"] = TD.dim("E1", 0.205, 0.209, 0.212, "in", 1, "D0008A")
    wide["package"]["lead_span"] = TD.dim("E", 0.300, 0.307, 0.315, "in", 1, "D0008A")
    assert SP.identify(read_spec(wide))[0] != SOIC
    span = copy.deepcopy(TD.INCH)                                      # body fits, lead span does not: no package
    span["package"]["lead_span"] = TD.dim("E", 0.300, 0.307, 0.315, "in", 1, "D0008A")
    name, why = SP.identify(read_spec(span))
    assert name is None and "disagree" in why, why
    nobody = copy.deepcopy(TD.INCH)
    nobody["package"]["body_width"] = None
    assert SP.identify(read_spec(nobody)) == (None, "the read has no body_width")
    assert SP.identify(read_spec(TD.GOOD))[0] == "VQFN-16-1EP_3x3mm_P0.5mm_EP1.45x1.45mm"
    name, why = SP.identify(read_spec(TC.first()))                     # EP 1.68: KiCad has it, the checked table not
    assert name is None and "not in the checked table" in why, why
    assert SP.identify({"package_type": "connector"})[0] is None


def test_identify_never_picks_another_land_in_the_table():
    """Every table entry, read back as if a datasheet printed exactly its numbers, names itself, an entry with the same
    pads, or nothing (ambiguous). Never an entry with other pads."""
    hits = 0
    for n in SP.packages():
        got, _ = SP.identify(SP.spec(n))
        assert got is None or got == n or SP._same_land([n, got]), (n, got)
        hits += got is not None
    assert hits > 150, hits


def test_standard_land_policy():
    run = lambda mode: SimpleNamespace(rules={"datasheet": {"standard_land": mode}} if mode else None)
    spec = read_spec(TD.INCH)
    assert L.Run.standard_land(run(None), spec) is None                         # off until measured live
    name, how, why = L.Run.standard_land(run("auto"), spec)
    assert name == SOIC and why == "the datasheet gives no land of its own"
    assert L.Run.standard_land(run("off"), spec) is None
    own = dict(spec, land={"pad_center": 2.7, "pad_length": 1.55, "pad_width": 0.6})
    assert L.Run.standard_land(run("auto"), own) is None                       # the maker's land is kept
    assert L.Run.standard_land(run("always"), own)[2] == "[datasheet] standard_land = always"
    blank = dict(own, unresolved=["terminal_length"])
    assert L.Run.standard_land(run("auto"), blank)[2] == "the read left terminal_length blank"


def test_librarian_writes_the_standard_land_for_an_identified_read():
    api = TC.API(lambda k, m, b: TR.on_page(TD.INCH))
    with tempfile.TemporaryDirectory() as d:
        items, _, _ = TB.run2(d, api, reader_kw={"batch": False}, rules={"datasheet": {"standard_land": "auto"}})
        a = items["DSPART1"]
        tried = [t for t in a["librarian"]["tried"] if t.startswith("datasheet reader")]
        assert tried[0].startswith(f"datasheet reader (BOM): footprint Package_SO:{SOIC} (identified from the read), symbol;"), tried
        assert a["footprint"].get("file")
        assert any(r.startswith(f"standard land: the datasheet read identified {SOIC}") for r in a["librarian"]["review"])
        assert "The datasheet read named the standard package" in (Path(d) / "out" / "review.md").read_text()
    with tempfile.TemporaryDirectory() as d:
        items, _, _ = TB.run2(d, api, reader_kw={"batch": False})                            # the default
        tried = [t for t in items["DSPART1"]["librarian"]["tried"] if t.startswith("datasheet reader")]
        assert tried[0].startswith("datasheet reader (BOM): footprint, symbol;"), tried


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
