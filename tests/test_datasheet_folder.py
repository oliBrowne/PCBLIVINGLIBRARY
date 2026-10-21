"""Offline tests for the [datasheet] folder (PDFs a person downloaded, tried before any link) and [datasheet]
standard_land (a read that identifies a checked standard package gets the table's land).   python3 -m tests.test_datasheet_folder

Nothing live: the API is test_crosscheck's fake, PDFs are test_ds_route's SYNTHETIC text PDFs, extractions are
test_datasheet's SYNTHETIC fixtures."""
import copy, json, tempfile
from pathlib import Path
from types import SimpleNamespace

from ecadlib import datasheet as D, librarian as L, rules as RU, stdparts as SP
from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB, test_ds_route as TR
from tests import test_datasheet_downloads as TDL, test_librarian as TL

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
        p, why = D.local_datasheet(f, "LT8610")
        assert p is None and "LT8610" in why and str(d) not in why, why                  # the folder's path is never said
        assert D.local_datasheet("", "LT8610") == (None, "no [datasheet] folder")
        why = D.local_datasheet(Path(d) / "nope", "LT8610")[1]
        assert "does not exist" in why and "(nope)" in why and str(d) not in why, why


def test_local_datasheet_kinds():
    """kinds: only the matches asked for, so a reader can try the links between the MPN's own file and the others."""
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "LTM4671.pdf", "BAT54S.pdf", "TESTPART.pdf")
        assert D.local_datasheet(f, "TESTPART", kinds=("exact",))[0].name == "TESTPART.pdf"
        assert D.local_datasheet(f, "LTM4671IY#PBF", kinds=("exact",))[0] is None
        assert D.local_datasheet(f, "LTM4671IY#PBF", kinds=("exact", "other"))[0] is None
        assert D.local_datasheet(f, "LTM4671IY#PBF", kinds=("family",))[0].name == "LTM4671.pdf"
        assert D.local_datasheet(f, "BAT54", kinds=("exact", "family"))[0] is None
        assert D.local_datasheet(f, "BAT54", kinds=("other",))[0].name == "BAT54S.pdf"
        assert D.local_datasheet(f, "BAT54")[0].name == "BAT54S.pdf"                   # every kind by default


def test_a_longer_part_number_is_never_taken_for_this_part():
    """BAT54S (series dual) is not BAT54 (single), LM317HV is not LM317: a file named for a part number that goes on
    with more letters or digits is only used when nothing else fits, and then always as a review item."""
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "BAT54S.pdf", "LM317HV.pdf", "lm317-rev-e.pdf", "TPS62133_datasheet.pdf")
        p, how = D.local_datasheet(f, "BAT54")
        assert p.name == "BAT54S.pdf" and "named for another part number" in how, how
        assert D.local_datasheet(f, "LM317")[0].name == "lm317-rev-e.pdf"          # the MPN then a separator wins
        p, how = D.local_datasheet(f, "TPS62133")
        assert p.name == "TPS62133_datasheet.pdf" and "(" not in how
        p, how = D.local_datasheet(f, "TPS6213")                                     # TPS62133 is another part
        assert "named for another part number" in how, how
        api = TC.API(lambda k, m, b: TC.first())
        with TB.patched(api):
            rd = TC.reader(d, {"datasheet": {"folder": str(f)}}, http=TB.PdfHTTP({TC.URL: 403}))
            job = rd.job("BAT54", [(TC.URL, "BOM")], TC.QFN)
        assert job["base_prov"]["local"] == "BAT54S.pdf" and "another part number" in job["where"]
    with tempfile.TemporaryDirectory() as d:                   # through the librarian: a review item, every time
        f = folder(d, "DSPART1X.pdf")
        rules = {"datasheet": {"folder": str(f)}}
        http = TDL.Scripted({"https://example.com/ds.pdf": [403], "https://www.lcsc.com/datasheet/C9999.pdf": TB.PDF})
        items, _, _ = TB.run2(d, TC.API(lambda k, m, b: TR.on_page(TD.INCH)), http=http,
                              reader_kw={"batch": False, "rules": rules}, rules=rules)
        assert "https://example.com/ds.pdf" in http.fetched   # its own link first: the site refused it (a 403 web page)
        a = items["DSPART1"]                                   # held, and the hold names the file
        assert a["status"] == "held" and any(h.startswith("verify: datasheet from your folder: DSPART1X.pdf")
                                             for h in a["hold_reasons"]), a["hold_reasons"]
        review = (Path(d) / "out" / "review.md").read_text()
        assert "from a file not named exactly for this part (DSPART1X.pdf)" in review, review


def test_a_working_link_beats_a_file_named_for_another_part_number():
    """BAT54's own link gives its own PDF: BAT54S.pdf in the folder (another part) is never read for it, and the
    part is not held for a folder file."""
    api = TC.API(lambda k, m, b: TC.first())
    own = TR.pdf_lines([["BAT54 Schottky barrier diode", "page one"], ["Package outline", "page two"]])
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "BAT54S.pdf")
        http = TB.PdfHTTP({TC.URL: own})
        with TB.patched(api):
            rd = TC.reader(d, {"datasheet": {"folder": str(f)}}, http=http)
            job = rd.job("BAT54", [(TC.URL, "BOM")], TC.QFN)
            res = rd.read_now(job)
        prov = job["base_prov"]
        assert job["data"] == own and "local" not in prov and prov["where"] == "BOM" and http.fetched == [TC.URL], prov
        assert not [r for r in res["needs_review"] if r.startswith("datasheet from your folder")], res["needs_review"]
    with tempfile.TemporaryDirectory() as d:                   # through the librarian: DSPART1's link works, no hold
        f = folder(d, "DSPART1X.pdf")
        rules = {"datasheet": {"folder": str(f)}}
        items, _, _ = TB.run2(d, TC.API(lambda k, m, b: TR.on_page(TD.INCH)), reader_kw={"batch": False, "rules": rules},
                              rules=rules)
        a = items["DSPART1"]
        assert not [h for h in a["hold_reasons"] if "datasheet from your folder" in h], a["hold_reasons"]
        assert a["source"]["footprint"] == "datasheet:https://example.com/ds.pdf", a["source"]


def test_a_family_file_comes_after_the_links_and_another_parts_file_after_the_hand_off():
    """A family datasheet (LTM4671.pdf for LTM4671IY#PBF) is read once no link gave a PDF, before the person is asked
    for one in their browser; a file named for another part number (BAT54S.pdf for BAT54) only when the hand-off got
    nothing either."""
    ltm, bat = "https://www.analog.com/media/ltm4671.pdf", "https://assets.nexperia.com/documents/data-sheet/BAT54_SER.pdf"
    bat2 = bat.replace("BAT54_SER", "BAT54")
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "LTM4671.pdf", "BAT54S.pdf")
        http = TDL.Scripted({ltm: [403], bat: [403], bat2: [403]})
        rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=http, log=None, sleep=lambda s: None, poll=0,
                      handoff=True, rules={"datasheet": {"folder": str(f), "downloads_folder": d}})
        asked, hand = [], {"pdf": None}
        rd.store.fns = dict(rd.store.fns, handoff=lambda u, p: asked.append((u, p)) or (
            (200, hand["pdf"], "application/pdf", u, None, {"x-ecadlib-file": "bat54.pdf"}) if hand["pdf"] else
            (0, None, "", u, "no PDF for it appeared in your Downloads folder within 180 s")))
        prov = rd.job("LTM4671IY#PBF", [(ltm, "BOM")], "QFN")["base_prov"]
        assert http.fetched == [ltm] and asked == [], (http.fetched, asked)     # the link first; nobody asked
        assert prov["local"] == "LTM4671.pdf" and "family datasheet" in prov["where"] and prov["url"] == ltm, prov
        prov = rd.job("BAT54", [(bat, "BOM")], "SOT-23")["base_prov"]
        assert http.fetched[-1] == bat and asked == [(bat, "BAT54")], (http.fetched, asked)   # the link, the person, ...
        assert prov["local"] == "BAT54S.pdf" and "another part number" in prov["where"], prov  # ... then the other file
        assert [a["url"] for a in prov["attempts"]] == [bat, bat], prov["attempts"]
        hand["pdf"] = TC.pdf3()                                # the person saved BAT54's own PDF: BAT54S.pdf is not read
        prov = rd.job("BAT54", [(bat2, "BOM")], "SOT-23")["base_prov"]
        assert asked[-1] == (bat2, "BAT54") and prov["via"] == "handoff" and "local" not in prov, prov


def test_a_bom_line_with_no_link_is_read_from_the_folder():
    """A BOM line with no datasheet link at all (and none found) is read from the [datasheet] folder."""
    api = TC.API(lambda k, m, b: TR.on_page(TD.INCH))
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "DSPART1.pdf")
        rules = {"datasheet": {"folder": str(f)}}
        bom = TB.BOM2.replace("Datasheet-only part,https://example.com/ds.pdf", "Datasheet-only part,")
        assert bom != TB.BOM2
        http = TB.PdfHTTP({"https://www.lcsc.com/datasheet/C9999.pdf": TB.PDF})
        rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=http, batch=False, poll=0,
                      sleep=lambda s: None, log=lambda *a: None, rules=rules)
        with TB.patched(api):
            items, _ = TL.run(d, http=http, bom=bom, reader=rd, rules=rules)
        tried = items["DSPART1"]["librarian"]["tried"]
        assert "datasheet: no datasheet URL known" not in tried, tried
        assert any(t.startswith("datasheet reader (DSPART1.pdf in the datasheet folder): footprint, symbol")
                   for t in tried), tried
        assert items["DSPART1"]["footprint"].get("file") and not [u for u in http.fetched if "example.com" in u]
        assert any("compare with DSPART1.pdf in the datasheet folder" in h for h in items["DSPART1"]["hold_reasons"])
        lock = json.loads((Path(d) / "out" / "library.lock.json").read_text())
        assert "datasheet" not in lock["parts"]["DSPART1"], lock["parts"]["DSPART1"]   # no link: no other part's read
    with tempfile.TemporaryDirectory() as d:                   # `ecadlib datasheet batch PLAN` (fill_plan) too
        f = folder(d, "DSPART1.pdf")
        rules = {"datasheet": {"folder": str(f)}}
        bom = TB.BOM2.replace("Datasheet-only part,https://example.com/ds.pdf", "Datasheet-only part,")
        http = TB.PdfHTTP({"https://www.lcsc.com/datasheet/C9999.pdf": TB.PDF})
        TL.run(d, http=http, bom=bom)                          # no key and no folder: DSPART1 stays open
        rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=http, batch=False, poll=0,
                      sleep=lambda s: None, log=lambda *a: None, rules=rules)
        with TB.patched(api):
            D.fill_plan(Path(d) / "out" / "plan.json", rd, rules=rules, log=lambda *a: None)
        tried = json.loads((Path(d) / "out" / "librarian_state.json").read_text())["DSPART1"]["tried"]
        assert any(t.startswith("datasheet reader (DSPART1.pdf in the datasheet folder)") for t in tried), tried
    with tempfile.TemporaryDirectory() as d:                   # no file for it: as before, no reader at all
        f = folder(d, "OTHER.pdf")
        rules = {"datasheet": {"folder": str(f)}}
        items, _ = TL.run(d, bom=TL.BOM, rules=rules)
        assert "datasheet: no datasheet URL known" in items["NOWHERE1"]["librarian"]["tried"]


def test_a_folder_that_is_not_there_is_said():
    """A typo in [datasheet] folder: the error of a part whose links all failed says so (with the folder's name, never
    its path), and `ecadlib doctor` warns."""
    from ecadlib import doctor as DR
    with tempfile.TemporaryDirectory() as d:
        gone = Path(d) / "datasheetz"
        try:
            rd = TC.reader(d, {"datasheet": {"folder": str(gone)}}, http=TB.PdfHTTP({TC.URL: 404}))
            rd.job("TESTPART", [(TC.URL, "BOM")], TC.QFN)
            raise AssertionError("no PDF, yet a job")
        except D.DatasheetError as e:
            msg = str(e)
        assert msg.startswith("no datasheet link gave a PDF") and "(datasheetz) does not exist" in msg and d not in msg, msg
        c = DR.check_datasheet_folder({"datasheet": {"folder": str(gone)}})
        assert [x["level"] for x in c] == ["WARN"] and "datasheetz" in c[0]["detail"], c
        assert DR.check_datasheet_folder({"datasheet": {"folder": d}})[0]["level"] == "OK"
        assert DR.check_datasheet_folder({"datasheet": {"folder": ""}}) == [] and DR.check_datasheet_folder(None) == []
        toml = Path(d) / "ecadlib.toml"
        toml.write_text(f"[datasheet]\nfolder = '{gone}'\n", encoding="utf-8")
        rep = DR.run(str(Path(d) / "ws"), [], str(toml), {})
        assert [x["level"] for x in rep["checks"] if x["check"] == "datasheet folder"] == ["WARN"], rep["checks"]


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
        assert prov["local"] == "TESTPART.pdf" and prov["where"] == "TESTPART.pdf in the datasheet folder"   # no path
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


def test_eval_counts_parts_read_from_the_folder():
    """A benchmark run with --datasheets says how many parts were read from hand-downloaded PDFs, and the pass rate
    without them: the number for maker links stays its own."""
    from ecadlib.eval import runner as R
    row = lambda i, ok, local: {"id": i, "pass": ok, "usd": 0, "tokens_in": 0, "tokens_out": 0, "source": "datasheet",
                                "family": "SOIC", "fields": {}, "datasheet": {"url": "https://a", **({"local": "X.pdf"} if local else {})}}
    s = R.summarize([row("a", True, True), row("b", False, False), row("c", True, False)])
    assert s["hand_downloaded"]["cases"] == 1 and s["hand_downloaded"]["ids"] == ["a"]
    rep = {"meta": {"set": "t", "set_version": 1, "model": "m", "sources": ["datasheet"], "mode": "live", "cases": 3,
                    "wall_s": 1, "wall_s_per_part": 0.3}, "summary": s, "cases": []}
    assert "1 part was read from PDFs downloaded by hand (--datasheets), not from a maker's link: 1/1 of them pass. " \
           "Without them: **1/2 (50.0 %)**." in R.report_md(rep), R.report_md(rep)


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


def test_standard_land_keeps_the_read_height():
    ext = copy.deepcopy(TD.INCH)                                       # 1.52 mm, not the table's 1.75 mm
    ext["package"]["height_max"] = TD.dim("A", None, None, 0.060, "in", 12, "D0008A")
    api, got, put = TC.API(lambda k, m, b: TR.on_page(ext)), [], L.Run.put_fp

    def spy(self, st, text, name, source, http=None, height=None, height_src="", spec=None):
        got.append((source, height, height_src))
        return put(self, st, text, name, source, http, height, height_src, spec)
    L.Run.put_fp = spy
    try:
        with tempfile.TemporaryDirectory() as d:
            TB.run2(d, api, reader_kw={"batch": False}, rules={"datasheet": {"standard_land": "auto"}})
    finally:
        L.Run.put_fp = put
    std = [g for g in got if g[0].startswith("standard:Package_SO:SOIC-8")]
    assert std and abs(std[0][1] - 1.524) < 0.001 and std[0][2] == "datasheet height_max", got


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
