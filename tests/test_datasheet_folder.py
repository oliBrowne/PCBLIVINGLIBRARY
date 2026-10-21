"""Offline tests for the [datasheet] folder (PDFs a person downloaded, tried before any link) and [datasheet]
standard_land (a read that identifies a checked standard package gets the table's land).   python3 -m tests.test_datasheet_folder

Nothing live: the API is test_crosscheck's fake, PDFs are test_ds_route's SYNTHETIC text PDFs, extractions are
test_datasheet's SYNTHETIC fixtures."""
import copy, json, re, tempfile, unicodedata
from pathlib import Path
from types import SimpleNamespace

from ecadlib import datasheet as D, librarian as L, messages as M, rules as RU, stdparts as SP
from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB, test_ds_route as TR
from tests import test_datasheet_downloads as TDL, test_librarian as TL
from tests.fixtures_lite import lite

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


def test_a_part_number_in_any_script_matches_only_its_own_file():
    """Names are compared as part numbers are everywhere (kicad_index.norm_mpn): µ and μ are U, an accented, full-width
    or Turkish dotless letter is its plain letter, and a letter with no ASCII form (Cyrillic) is kept, so К561ТМ2 never
    takes К561ЛН2's file (with the letters dropped both would be 5612). A word or symbol after the part number is a
    separator (TP4056中文资料.pdf is TP4056's own file), never a reason for another part to match: HX-2.54-4P直针 never
    takes HX-2.54-4P弯针's file, and a part named in Chinese alone has its own file. A name saved decomposed (macOS)
    reads the same as composed."""
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "К561ЛН2.pdf", "К561ТМ2.pdf", "КР142ЕН5Б.pdf", "uPD720202K8-711-BAA-A.pdf", "ırf540npbf.pdf",
                   "BAT54_техпаспорт.pdf", "LM358Ж.pdf", "TP4056中文资料.pdf", "TPS62130データシート.pdf",
                   "LM2596데이터시트.pdf", "LTM4671中文资料.pdf", "NE555™.pdf", unicodedata.normalize("NFD", "LM318Á1.pdf"),
                   "HX-2.54-4P弯针.pdf", "KSD301-85℉.pdf", "蜂鸣器.pdf")
        assert D.local_datasheet(f, "К561ТМ2")[0].name == "К561ТМ2.pdf"            # not К561ЛН2.pdf, sorted first
        assert D.local_datasheet(f, "к561лн2", kinds=("exact",))[0].name == "К561ЛН2.pdf"
        assert D.local_datasheet(f, "КР142ЕН5А")[0] is None                        # the 5 V part: the 6 V one's file
        assert D.local_datasheet(f, "1554ЛА3")[0] is None
        assert D.local_datasheet(f, "µPD720202K8-711-BAA-A", kinds=("exact",))[0].name == "uPD720202K8-711-BAA-A.pdf"
        assert D.local_datasheet(f, "ＩＲＦ540NPBF", kinds=("exact",))[0].name == "ırf540npbf.pdf"
        assert D.local_datasheet(f, "BAT54", kinds=("exact",))[0].name == "BAT54_техпаспорт.pdf"
        assert D.local_datasheet(f, "LM358", kinds=("exact", "family"))[0] is None   # LM358Ж: another part number
        assert D.local_datasheet(f, "LM358")[0].name == "LM358Ж.pdf"
        for part in ("TP4056", "TPS62130", "LM2596", "NE555"):
            p, how = D.local_datasheet(f, part, kinds=("exact",))
            assert p and how == f"{p.name} in the datasheet folder", (part, how)
        assert "family datasheet" in D.local_datasheet(f, "LTM4671IY#PBF")[1]
        assert D.local_datasheet(f, "LM318A", kinds=("exact", "family"))[0] is None   # LM318Á1, decomposed: not LM318A
        assert D.local_datasheet(f, "HX-2.54-4P直针", kinds=("exact",))[0] is None     # the right-angle header's file
        assert D.local_datasheet(f, "KSD301-85℃", kinds=("exact",))[0] is None        # the 85 °F switch's file
        assert D.local_datasheet(f, "蜂鸣器", kinds=("exact",))[0].name == "蜂鸣器.pdf"
        assert D.local_datasheet(f, "蜂鸣", kinds=("exact",))[0] is None              # a longer name in Chinese


def test_a_word_or_symbol_before_the_part_number_is_a_separator_too():
    """数据手册-TP4056.pdf, 【原厂】NE555.pdf, 规格书_LM2596_V1.0.pdf: the part's own file, read before any link with no
    hold, as TP4056中文资料.pdf is. Only for a part number that starts with a letter or digit: 鸣器 never takes 蜂鸣器.pdf."""
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "数据手册-TP4056.pdf", "【原厂】NE555.pdf", "规格书_LM2596_V1.0.pdf", "™TPS62130.pdf", "蜂鸣器.pdf")
        for part in ("TP4056", "NE555", "LM2596", "TPS62130"):
            p, how = D.local_datasheet(f, part, kinds=("exact",))
            assert p and how == f"{p.name} in the datasheet folder", (part, how)
        assert D.local_datasheet(f, "鸣器")[0] is None


def test_the_part_s_own_file_beats_a_longer_name_that_starts_with_it():
    """ESP32-S3.pdf (the name the review asks for) is ESP32-S3's file, even beside ESP32-S3-WROOM-1.pdf (the module),
    'BAT54 (1).pdf', an errata or an older copy with words in front ('(中文)TP4056.pdf'), whose names sort first."""
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "ESP32-S3-WROOM-1.pdf", "ESP32-S3.pdf", "BAT54 (1).pdf", "BAT54.pdf", "STM32F103C8T6 errata.pdf",
                   "stm32f103c8t6.pdf", "(中文)TP4056.pdf", "TP4056.pdf")
        for part in ("ESP32-S3", "BAT54", "STM32F103C8T6", "TP4056"):
            p, how = D.local_datasheet(f, part)
            assert p.name.lower() == f"{part.lower()}.pdf" and how == f"{p.name} in the datasheet folder", (part, how)
    with tempfile.TemporaryDirectory() as d:                   # a full-width MPN's own name sorts after every CJK word
        own = D.folder_file_name("ＩＲＦ540ＮＰＢＦ")
        f = folder(d, "数据手册-IRF540NPBF.pdf", "【原厂】IRF540NPBF.pdf", own)
        assert D.local_datasheet(f, "ＩＲＦ540ＮＰＢＦ")[0].name == own
    for part, names in (("WS2812B", ("WS2812B-2020.pdf", "数据手册-WS2812B.pdf")),   # words in front, then the MPN alone:
                        ("WS2812B", ("数据手册-WS2812B-2020.pdf", "数据手册-WS2812B.pdf")),   # still the part's own file,
                        ("ESP32-S3", ("ESP32-S3-WROOM-1.pdf", "【原厂】ESP32-S3.pdf")),     # never the variant's, the
                        ("STM32F103C8T6", ("STM32F103C8T6 errata.pdf", "数据手册-STM32F103C8T6.pdf")),   # module's or
                        ("BAT54", ("BAT54 errata.pdf", "规格书_BAT54.pdf")),                # an errata that sorts first
                        ("TP4056", ("TP4056-rev1.pdf", "데이터시트_TP4056.pdf")),
                        ("LM1117", ("LM1117-ADJ.pdf", "データシート LM1117.pdf")),
                        ("AMS1117", ("规格书_AMS1117-3.3.pdf", "规格书_AMS1117.pdf")),
                        ("HX-2.54-4P", ("HX-2.54-4P弯针.pdf", "数据手册-HX-2.54-4P.pdf"))):
        with tempfile.TemporaryDirectory() as d:
            f = folder(d, *names)
            p, how = D.local_datasheet(f, part)
            assert p.name == names[1] and how == f"{p.name} in the datasheet folder", (part, how)


def test_the_family_file_is_the_longest_start_of_the_part_number_never_a_sibling_s():
    """A family datasheet is the longest name the MPN starts with, a word or symbol after it a separator: LTM4671IY.pdf
    beats 'LTM4671 中文资料.pdf' for LTM4671IY#PBF. A name whose own word or symbol stands where the part goes on with
    another is a sibling's (HX-2.54-4P弯针.pdf for HX-2.54-4P直针, KSD301-85℉.pdf for KSD301-85℃): no match at all, so
    the person is asked in their browser. The plain series name (HX-2.54-4P.pdf) is still the family's."""
    with tempfile.TemporaryDirectory() as d:
        f = folder(d, "LTM4671 中文资料.pdf", "LTM4671IY.pdf", "LT8610 中文资料.pdf", "LT8610AB.pdf",
                   "ADP5070 データシート.pdf", "ADP5070A.pdf")
        for part, name in (("LTM4671IY#PBF", "LTM4671IY.pdf"), ("LT8610ABEMSE", "LT8610AB.pdf"),
                           ("ADP5070ACPZ", "ADP5070A.pdf"), ("LTM4671EY", "LTM4671 中文资料.pdf")):
            p, how = D.local_datasheet(f, part)
            assert p.name == name and how.endswith(f"(a family datasheet: the name is the start of {part})"), (part, how)
    for part, name in (("HX-2.54-4P直针", "HX-2.54-4P弯针.pdf"), ("HX-2.54-4P直针", "弯针HX-2.54-4P.pdf"),
                       ("KSD301-85℃", "KSD301-85℉.pdf"), ("KSD301-85℃", "℉KSD301-85.pdf"), ("0603红灯", "0603绿灯.pdf"),
                       ("0603红灯", "绿灯0603.pdf"), ("KSD9700-70℃常开", "KSD9700-70℃常闭.pdf"),
                       ("KSD9700-70C常开", "KSD9700-70℃常闭.pdf"), ("KSD9700-70°C常开", "KSD9700-70°C常闭.pdf"),
                       ("PH2.0-2P立贴", "PH2.0-2P卧贴.pdf"),
                       # a symbol inside the part number (°, ×, ±, ~, +) is no separator: what follows it counts
                       ("KSD301-85°C-NO", "KSD301-85°C-NC.pdf"), ("KSD301-85°F", "KSD301-85°C.pdf"),
                       ("PH2.0-2×10P", "PH2.0-2×4P.pdf"), ("PH2.0-2x10P", "PH2.0-2×4P.pdf"),
                       ("NTC-10K±1%-3950", "NTC-10K±5%-3950.pdf"), ("AC220V~12V", "AC220V~5V.pdf"),
                       ("TYPE-C-16P+2", "TYPE-C-16P+6.pdf"), ("LTM4671IY#PBF", "LTM4671®EY.pdf"),
                       # a word in the name, then more part number: another variant's
                       ("HX-2.54-5P直针", "HX-2.54 直针 4P.pdf"), ("AMS1117-ADJ", "AMS1117 稳压 3.3V.pdf"),
                       ("ESP32-S3", "ESP32 模组 WROOM-32E.pdf"), ("NE555DR", "NE555™P.pdf"),
                       # a shorter series name, then a word, where the part number has a word or symbol of its own
                       ("HX-2.54-4P直针", "HX-2.54 弯针.pdf"), ("XH2.54-4P直针", "XH2.54 弯针 规格书.pdf"),
                       ("PH2.0-2P卧贴", "PH2.0 立贴.pdf"), ("KSD9700-70℃常开", "KSD9700常闭.pdf")):
        with tempfile.TemporaryDirectory() as d:
            f = folder(d, name)
            assert D.local_datasheet(f, part)[0] is None, (part, D.local_datasheet(f, part))
    for part, name in (("HX-2.54-4P直针", "HX-2.54-4P.pdf"), ("KSD9700-70℃常开", "KSD9700-70℃.pdf"),
                       ("KSD301-85°C-NO", "KSD301-85 中文资料.pdf"), ("LTM4671IY#PBF", "数据手册-LTM4671.pdf"),
                       ("LTM4671IY#PBF", "LTM4671®.pdf")):
        with tempfile.TemporaryDirectory() as d:
            f = folder(d, name)
            assert "family datasheet" in D.local_datasheet(f, part)[1], (part, D.local_datasheet(f, part))


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
    for part, name in (("HX-2.54-4P直针", "HX-2.54-4P弯针.pdf"), ("HX-2.54-4P直针", "弯针HX-2.54-4P.pdf"),
                       ("PH2.0-2×10P", "PH2.0-2×4P.pdf"), ("HX-2.54-4P直针", "HX-2.54 弯针.pdf")):
        with tempfile.TemporaryDirectory() as d:               # another variant's file is no family's: the person is asked
            link = "https://www.example.com/series.pdf"
            f = folder(d, name)
            rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=TDL.Scripted({link: [403]}), log=None,
                          sleep=lambda s: None, poll=0, handoff=True,
                          rules={"datasheet": {"folder": str(f), "downloads_folder": d}})
            asked = []
            rd.store.fns = dict(rd.store.fns, handoff=lambda u, p: asked.append((u, p)) or (
                200, TC.pdf3(), "application/pdf", u, None, {"x-ecadlib-file": "own.pdf"}))
            prov = rd.job(part, [(link, "BOM")], "THT")["base_prov"]
            assert asked == [(link, part)] and prov["via"] == "handoff" and "local" not in prov, (name, asked, prov)


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
        tilde = "~nosuchuser/datasheets"                       # a user that is not there: expanduser raises
        assert [x["level"] for x in DR.check_datasheet_folder({"datasheet": {"folder": tilde}})] == ["WARN"]
        toml.write_text(f"[datasheet]\nfolder = '{tilde}'\n", encoding="utf-8")
        rep = DR.run(str(Path(d) / "ws"), [], str(toml), {})  # (init and the web UI's setup check run this too)
        assert [x["level"] for x in rep["checks"] if x["check"] == "datasheet folder"] == ["WARN"], rep["checks"]
        p, why = D.local_datasheet(tilde, "TESTPART")
        assert p is None and why.startswith("your [datasheet] folder (datasheets) "), why


@lite
def test_a_folder_that_cannot_be_read_never_stops_a_read(tmp_path, monkeypatch):
    """No access to the [datasheet] folder (macOS privacy settings, chmod 000, a Windows ACL): a part with a working
    link is read from it, a line with no link says why the folder gave nothing, and nothing stored names its path."""
    f = folder(tmp_path, "DSPART1.pdf", "NOWHERE1.pdf")
    real = Path.iterdir

    def iterdir(p):
        if Path(p) == f:
            raise PermissionError(1, "Operation not permitted", str(p))
        return real(p)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    rules, note = {"datasheet": {"folder": str(f)}}, "your [datasheet] folder (pdfs) cannot be read (PermissionError)"
    assert D.local_datasheet(f, "DSPART1") == (None, note)
    job = TC.reader(tmp_path / "a", rules).job("TESTPART", [(TC.URL, "BOM")], TC.QFN)    # TC.reader serves TC.URL
    assert job["data"] == TC.pdf3() and job["base_prov"]["folder_note"] == note, job["base_prov"]
    b = tmp_path / "b"
    b.mkdir()
    http = TB.PdfHTTP({LINK: TB.PDF})
    rd = D.Reader(b / "out", b / "cache", key=TB.KEY, http=http, batch=False, poll=0, sleep=lambda s: None,
                  log=lambda *a: None, rules=rules)
    with TB.patched(TC.API(lambda k, m, b: TR.on_page(TD.INCH))):
        items, _ = TL.run(b, http=http, bom=TL.BOM, reader=rd, rules=rules)
    assert items["DSPART1"]["source"]["footprint"] == f"datasheet:{LINK}", items["DSPART1"]["librarian"]["tried"]
    assert f"datasheet: no datasheet URL known; {note}" in items["NOWHERE1"]["librarian"]["tried"]
    for p in (b / "out").rglob("*"):
        if p.suffix in (".json", ".csv", ".md", ".html", ".jsonl", ".txt"):
            assert str(f) not in p.read_text(encoding="utf-8", errors="replace"), p
    from ecadlib import doctor as DR
    c = DR.check_datasheet_folder(rules)
    assert [x["level"] for x in c] == ["WARN"] and "cannot be read" in c[0]["detail"], c


# ---------------------------------------------------------------- a refused link: what the review says, followed
LINK = "https://example.com/ds.pdf"


class NoURLFetch(TC.API):
    """The model API, whose own fetcher cannot download the refused link either (its words in the #42 run)."""

    def post(self, url, headers=None, json=None, timeout=None):
        if url.endswith("/v1/messages") and (json["messages"][0]["content"][0].get("source") or {}).get("type") == "url":
            self.calls.append(("POST", url, headers, json))
            return TB.R(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                         "message": "Unable to download the file. Please verify the URL"}})
        return super().post(url, headers, json, timeout)


def refused_run(d, rules, mpn="DSPART1"):
    """The librarian on TL.BOM with DSPART1's MPN set to `mpn`; its maker link answers 403 with a web page and the API
    cannot fetch it either. Run again in the same folder to follow the advice. -> (that part's plan item, all items)"""
    bom = TL.BOM.replace("U3,1,DSPART1,,ACME,DSPART1,,", f"U3,1,{mpn},,ACME,{mpn},,")
    http = TDL.Scripted({LINK: [403]})
    rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=http, batch=False, poll=0, sleep=lambda s: None,
                  log=lambda *a: None, rules=rules)
    with TB.patched(NoURLFetch(lambda k, m, b: TR.on_page(TD.INCH))):
        items, _ = TL.run(d, http=http, bom=bom, reader=rd, rules=rules)
    return next(v for k, v in items.items() if D._mpn_key(k) == D._mpn_key(mpn)), items


def refused(it):
    """The part's review line for the refused download, and what a person is shown for it."""
    line = next(r for r in it["librarian"]["review"] if r.startswith("datasheet download refused"))
    return line, next(e for e in M.explain_all(it["hold_reasons"]) if e["code"].startswith("files.datasheet"))


def test_following_the_refused_advice_reads_the_part_s_own_file():
    """The review says what to save where. A character no file name can hold ('/' in LM1117MPX-3.3/NOPB) becomes '-';
    an MPN with spaces keeps them, and its --part is quoted. Saved under the name the shown fix gives, the next run
    reads it as the part's own file (µPD720202 too: a letter outside A-Z is punctuation to the MPN, as in its key):
    no hold for a folder file."""
    for mpn, name, arg in (("LM1117MPX-3.3/NOPB", "LM1117MPX-3.3-NOPB.pdf", "LM1117MPX-3.3/NOPB"),
                           ("DSPART1 REV A", "DSPART1 REV A.pdf", '"DSPART1 REV A"'),
                           ("µPD720202K8-711-BAA-A", "µPD720202K8-711-BAA-A.pdf", "µPD720202K8-711-BAA-A")):
        with tempfile.TemporaryDirectory() as d:
            f = folder(d)
            rules = {"datasheet": {"folder": str(f)}}
            it, _ = refused_run(d, rules, mpn)
            line, e = refused(it)
            assert f"download {LINK} in a browser, save it as {name} in your [datasheet] folder (pdfs) and run again (or " \
                   f"read it alone with `ecadlib datasheet FILE.pdf --part {arg}`)" in line and str(d) not in line, line
            assert e["code"] == "files.datasheet_refused" and e["fix"] == (
                f"Download the PDF in a browser ({LINK}), save it as {name} in your datasheet folder (pdfs) and run again; "
                f"or read it alone with ecadlib datasheet FILE.pdf --part {arg}; or pick a footprint by hand."), e["fix"]
            (f / re.search(r"save it as (.+?\.pdf) in your", e["fix"]).group(1)).write_bytes(TC.pdf3())
            it, _ = refused_run(d, rules, mpn)                 # the advice followed: run again
            tried = [t for t in it["librarian"]["tried"] if t.startswith("datasheet reader")]
            assert tried and tried[0].startswith(f"datasheet reader ({name} in the datasheet folder): footprint"), \
                it["librarian"]["tried"]
            assert not [h for h in it["hold_reasons"] if "datasheet from your folder" in h], it["hold_reasons"]


def test_a_web_page_saved_as_the_part_s_pdf_is_said_first():
    """A bot-check page saved as DSPART1.pdf: the review and the tried lines say that file is no PDF and to replace it,
    instead of asking for DSPART1.pdf again (which would change nothing)."""
    with tempfile.TemporaryDirectory() as d:
        f = folder(d)
        (f / "DSPART1.pdf").write_bytes(b"<!doctype html><html><body>Access Denied</body></html>")
        it, _ = refused_run(d, {"datasheet": {"folder": str(f)}})
        bad = "DSPART1.pdf in your [datasheet] folder is not a PDF (a web page saved as .pdf?): replace it with the PDF"
        line, e = refused(it)
        assert line.split("; ", 1)[1].startswith(f"{bad}: download {LINK} in a browser, save it as DSPART1.pdf and run "
                                                 "again"), line
        tried = [t for t in it["librarian"]["tried"] if t.startswith("datasheet")]
        assert tried[0] == f"datasheet: {bad}" and tried[1].startswith("datasheet: failed (APIError"), tried
        assert e["code"] == "files.datasheet_not_pdf", e
        assert e["text"] == ("DSPART1.pdf in your datasheet folder is not a PDF (it may be a web page saved as .pdf), and "
                             "example.com would not let ecadlib download the datasheet (it answered HTTP 403 with a web "
                             "page)."), e["text"]
        assert e["fix"] == (f"Replace DSPART1.pdf with the PDF: download it in a browser ({LINK}), save it as DSPART1.pdf "
                            "and run again; or read it alone with ecadlib datasheet FILE.pdf --part DSPART1; or pick a "
                            "footprint by hand."), e["fix"]


def fill_again(d, rules, api=None, cap=None, offline=False, **kw):
    """`ecadlib datasheet batch PLAN` (fill_plan) on the plan of an earlier run in `d`, DSPART1's link refused as in
    refused_run. api: the model API (the same one again to collect a batch); cap: --max-cost-usd; offline: --offline;
    kw: Reader settings (batch=True, wait=False: sent, not waited for) -> the plan items by name."""
    rd = D.Reader(Path(d) / "out", Path(d) / "cache", key=TB.KEY, http=TDL.Scripted({LINK: [403]}), poll=0,
                  sleep=lambda s: None, log=lambda *a: None, rules=rules, **{"batch": False, **kw})
    if cap is not None:
        rd.set_cap(cap)
    rd.offline = offline
    with TB.patched(api or NoURLFetch(lambda k, m, b: TR.on_page(TD.INCH))):
        items = D.fill_plan(Path(d) / "out" / "plan.json", rd, rules=rules, log=lambda *a: None)
    return {i["name"]: i for i in items}


def test_a_run_again_shows_the_advice_for_the_folder_as_it_is_now():
    """`ecadlib datasheet batch PLAN` keeps each part's state between runs: the refused link's advice and what was
    wrong with the folder's file are said as they are now, never as an earlier run found them (the folder not there
    yet, a web page saved as the PDF since deleted, the PDF saved since: sent in a batch, then read)."""
    def run(d, rules, **kw):
        it = fill_again(d, rules, **kw)["DSPART1"]
        lines = [r for r in it["librarian"]["review"] if r.startswith("datasheet download refused")]
        shown = [e for e in M.explain_all(it["hold_reasons"]) if e["code"].startswith("files.datasheet")]
        bad = [t for t in it["librarian"]["tried"] if "your [datasheet] folder" in t]
        return it, lines, shown, bad
    with tempfile.TemporaryDirectory() as d:
        TL.run(d, http=TB.PdfHTTP({}), bom=TL.BOM)            # no key: DSPART1 stays open
        f = Path(d) / "pdfs"
        rules = {"datasheet": {"folder": str(f)}}
        it, lines, shown, bad = run(d, rules)                  # the folder is not there yet
        assert len(lines) == 1 and shown[0]["fix"].startswith("Your datasheet folder (pdfs) does not exist"), (lines, shown)
        f.mkdir()                                              # made, and what the browser gave saved in it
        (f / "DSPART1.pdf").write_bytes(b"<!doctype html><html><body>Access Denied</body></html>")
        it, lines, shown, bad = run(d, rules)
        assert len(lines) == 1 and shown[0]["code"] == "files.datasheet_not_pdf", (lines, shown)
        assert bad == ["datasheet: DSPART1.pdf in your [datasheet] folder is not a PDF (a web page saved as .pdf?): "
                       "replace it with the PDF"], bad
        (f / "DSPART1.pdf").unlink()                           # deleted, not yet saved again
        it, lines, shown, bad = run(d, rules)
        assert len(lines) == 1 and shown[0]["code"] == "files.datasheet_refused" and bad == [], (lines, bad)
        assert shown[0]["fix"].startswith(f"Download the PDF in a browser ({LINK}), save it as DSPART1.pdf in your "
                                          "datasheet folder (pdfs) and run again"), shown[0]["fix"]
        (f / "DSPART1.pdf").write_bytes(TC.pdf3())             # the advice followed; the read sent in a batch
        api = NoURLFetch(lambda k, m, b: TR.on_page(TD.INCH))
        it, lines, shown, bad = run(d, rules, api=api, batch=True, wait=False)
        assert lines == shown == bad == [] and any("queued for the batch" in t for t in it["librarian"]["tried"]), \
            (lines, shown, it["librarian"]["tried"])
        it, lines, shown, bad = run(d, rules, api=api, batch=True)   # and collected
        assert it["footprint"].get("file") and lines == shown == bad == [], (lines, shown, it["librarian"]["tried"])


class Overloaded(NoURLFetch):
    """The model API, overloaded when the link is sent: a passing failure, which says nothing about the link."""

    def post(self, url, headers=None, json=None, timeout=None):
        if url.endswith("/v1/messages") and (json["messages"][0]["content"][0].get("source") or {}).get("type") == "url":
            self.calls.append(("POST", url, headers, json))
            return TB.R(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
                        headers={"retry-after": "0"})
        return super().post(url, headers, json, timeout)


def test_a_run_again_that_learns_nothing_new_keeps_the_advice():
    """The link still refused here and nothing read this time (the API overloaded, over the cost cap, --offline, a batch
    not back yet): the advice stays, said as the folder is now. Once a read comes back (the batch), it goes."""
    answer = lambda k, m, b: TR.on_page(TD.INCH)
    with tempfile.TemporaryDirectory() as d:
        TL.run(d, http=TB.PdfHTTP({}), bom=TL.BOM)
        f = Path(d) / "pdfs"
        rules = {"datasheet": {"folder": str(f)}}

        def advice(**kw):
            it = fill_again(d, rules, **kw)["DSPART1"]
            return it, {e["fix"] for e in M.explain_all(it["hold_reasons"]) if e["code"] == "files.datasheet_refused"}
        it, fix = advice()
        assert len(fix) == 1 and fix.pop().startswith("Your datasheet folder (pdfs) does not exist"), fix
        f.mkdir()                                              # made since: said as it is now
        now = f"Download the PDF in a browser ({LINK}), save it as DSPART1.pdf in your datasheet folder (pdfs) and run again"
        api = NoURLFetch(answer)
        for kw in ({"api": Overloaded(answer)}, {"cap": 0.0001}, {"cap": 5, "model": "claude-unpriced-9"}, {"offline": True},
                   {"api": api, "batch": True, "wait": False}):
            it, fix = advice(**kw)
            assert len(fix) == 1 and fix.pop().startswith(now), (kw, fix, it["librarian"]["tried"][-1])
            assert len([r for r in it["librarian"]["review"] if r.startswith("datasheet download refused")]) == 1
        it, fix = advice(api=api, batch=True)                  # the batch back, read
        assert it["footprint"].get("file") and not fix, (fix, it["librarian"]["tried"])
        assert not [r for r in it["librarian"]["review"] if r.startswith("datasheet download refused")]


def test_a_batch_that_comes_back_with_no_answer_keeps_the_advice():
    """A batch request that expired or errored (the API overloaded), or a batch gone when collected (404), read
    nothing: the advice stays, as for a synchronous read that got no answer."""
    class Gone(TB.FakeAPI):
        gone = False

        def get(self, url, headers=None, timeout=None):
            if self.gone:
                return TB.R(404, {"type": "error", "error": {"type": "not_found_error", "message": "not found"}})
            return super().get(url, headers, timeout)
    for answer in ("expired", ("errored", "overloaded_error", "Overloaded"), ("errored", "api_error", "Internal error"),
                   "gone"):
        with tempfile.TemporaryDirectory() as d:
            TL.run(d, http=TB.PdfHTTP({}), bom=TL.BOM)
            f = Path(d) / "pdfs"
            f.mkdir()
            rules = {"datasheet": {"folder": str(f)}}
            fill_again(d, rules)                               # the API could not fetch it: advice
            api = Gone(answer=lambda cid: answer)
            fill_again(d, rules, api=api, batch=True, wait=False)
            api.gone = answer == "gone"
            it = fill_again(d, rules, api=api, batch=True)["DSPART1"]
            assert len([r for r in it["librarian"]["review"] if r.startswith("datasheet download refused")]) == 1, \
                (answer, it["librarian"]["tried"][-1:])
            assert [e["code"] for e in M.explain_all(it["hold_reasons"])].count("files.datasheet_refused") == 1


def test_an_endpoint_that_cannot_fetch_links_keeps_the_advice():
    """Run again on a model endpoint that cannot fetch a PDF by its link (OpenAI-compatible): nothing is sent, so the
    advice to save the PDF in the folder stays."""
    rules = lambda f: {"datasheet": {"folder": str(f)},
                       "ai": {"provider": "openai-compatible", "base_url": "https://llm.example.invalid/v1",
                              "api_key_env": "ECADLIB_TEST_REMOTE_KEY", "model": "priced-model", "detect": False,
                              "profiles": {"priced-model": {"tier": "frontier", "pdf": True, "json_schema": True,
                                                            "price_in": 2.0, "price_out": 8.0}}}}
    import os
    os.environ["ECADLIB_TEST_REMOTE_KEY"] = "sk-remote-test"
    try:
        with tempfile.TemporaryDirectory() as d:
            TL.run(d, http=TB.PdfHTTP({}), bom=TL.BOM)
            f = Path(d) / "pdfs"
            f.mkdir()
            fill_again(d, {"datasheet": {"folder": str(f)}})
            api = TB.FakeAPI()
            it = fill_again(d, rules(f), api=api, yes=True)["DSPART1"]
            assert not api.calls and "can't fetch a PDF by URL" in it["librarian"]["tried"][-1], it["librarian"]["tried"]
            assert len([r for r in it["librarian"]["review"] if r.startswith("datasheet download refused")]) == 1
    finally:
        os.environ.pop("ECADLIB_TEST_REMOTE_KEY", None)


class CheckOverloaded(TC.API):
    """The model API: the read is answered, the cross-check's second read gets 529 overloaded every time."""

    def post(self, url, headers=None, json=None, timeout=None):
        if url.endswith("/v1/messages") and json["system"][0]["text"].startswith("You check the package"):
            self.calls.append(("POST", url, headers, json))
            return TB.R(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
                        headers={"retry-after": "0"})
        return super().post(url, headers, json, timeout)


class FeedbackOverloaded(TC.API):
    """The model API: the read is answered, the feedback turn (the answer and its errors sent back) gets 529 every time."""

    def post(self, url, headers=None, json=None, timeout=None):
        if url.endswith("/v1/messages") and len(json["messages"]) > 1:
            self.calls.append(("POST", url, headers, json))
            return TB.R(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
                        headers={"retry-after": "0"})
        return super().post(url, headers, json, timeout)


def test_a_read_that_comes_back_takes_the_advice_out():
    """The API fetches the link this time and an answer comes back, though not one to build from (the cross-check
    disagrees or cannot be sent, the model declines, its feedback turn gets no answer, the same in a batch): the
    earlier run's advice goes, as on a fresh run."""
    held = lambda: TC.API(lambda k, m, b: TC.first() if k == "read" else TC.check_answer(pitch=0.65),
                          answer=lambda cid: TD.message(TC.first()))
    declined = lambda cid: dict(TD.message(TD.GOOD), stop_reason="refusal", content=[])
    bad = TC.first(body_length=TD.dim("D", 3.1, 3.0, 2.9))   # min > max: fails the checks, so a feedback turn is sent
    for api, kw in ((held, {}), (lambda: TC.API(lambda k, m, b: "refusal"), {}), (held, {"batch": True, "wait": False}),
                    (lambda: CheckOverloaded(lambda k, m, b: TC.first()), {}),
                    (lambda: FeedbackOverloaded(lambda k, m, b: bad), {}),
                    (lambda: TB.FakeAPI(answer=declined), {"batch": True, "wait": False})):
        with tempfile.TemporaryDirectory() as d:
            TL.run(d, http=TB.PdfHTTP({}), bom=TL.BOM)
            f = Path(d) / "pdfs"
            f.mkdir()
            rules = {"datasheet": {"folder": str(f)}}
            fill_again(d, rules)                               # the API could not fetch it: advice
            a = api()
            it = fill_again(d, rules, api=a, **kw)["DSPART1"]
            if kw:
                it = fill_again(d, rules, api=a, batch=True)["DSPART1"]   # collected
            assert not [r for r in it["librarian"]["review"] if r.startswith("datasheet download refused")], \
                (kw, it["librarian"]["review"], it["librarian"]["tried"][-2:])
            assert not [h for h in it["hold_reasons"] if "refused" in h], it["hold_reasons"]


class ByModel(TB.FakeAPI):
    """Message Batches: a request for `model` gets `first`, any other `later` (a message, or 'expired')."""

    def __init__(self, model, first, later):
        super().__init__(answer=lambda cid: None)
        self.model, self.first, self.later = model, first, later

    def _results(self, bid):
        if "lines" in self.batches[bid]:
            return self.batches[bid]["lines"]
        lines = []
        for rq in self.batches[bid]["requests"]:
            a = self.first if rq["params"]["model"] == self.model else self.later
            lines.append({"custom_id": rq["custom_id"],
                          "result": {"type": "expired"} if a == "expired" else {"type": "succeeded", "message": a}})
        if self.batches[bid]["polls"] > self.polls:
            self.batches[bid]["lines"] = lines
        return lines


def test_an_answer_on_an_earlier_step_of_the_route_takes_the_advice_out():
    """[datasheet] route = "ladder", `datasheet batch PLAN --no-wait`: the first model's answer comes back (declined) and
    the part goes on to the next model. The API fetched the link, so the advice goes then, and does not come back when
    the next model's request expires."""
    declined = dict(TD.message(TD.GOOD), stop_reason="refusal", content=[])
    lines = lambda it: [r for r in it["librarian"]["review"] if r.startswith("datasheet download refused")]
    with tempfile.TemporaryDirectory() as d:
        TL.run(d, http=TB.PdfHTTP({}), bom=TL.BOM)
        plan = Path(d) / "out" / "plan.json"                   # a medium package: the route starts on the middle model
        items = json.loads(plan.read_text())
        next(i for i in items if i["name"] == "DSPART1")["footprint"]["ref"] = "SOIC-8"
        plan.write_text(json.dumps(items))
        f = Path(d) / "pdfs"
        f.mkdir()
        rules = {"datasheet": {"folder": str(f), "route": "ladder",
                               "ladder": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]}}
        assert len(lines(fill_again(d, rules)["DSPART1"])) == 1   # the API could not fetch it: advice
        api = ByModel("claude-sonnet-5-5", declined, "expired")
        fill_again(d, rules, api=api, batch=True, wait=False)     # the first step sent
        it = fill_again(d, rules, api=api, batch=True, wait=False)["DSPART1"]
        assert "next step claude-opus-5-5" in it["librarian"]["tried"][-1] and lines(it) == [], lines(it)
        it = fill_again(d, rules, api=api, batch=True, wait=False)["DSPART1"]   # the next step sent
        assert lines(it) == [], lines(it)
        it = fill_again(d, rules, api=api, batch=True, wait=False)["DSPART1"]   # ... and expired
        assert "expired" in it["librarian"]["tried"][-1] and lines(it) == [], (it["librarian"]["tried"][-1], lines(it))


def test_a_line_with_no_link_says_the_folder_as_it_is_now():
    """The same for a BOM line with no link: the folder not there, then a web page saved as its PDF, then deleted."""
    with tempfile.TemporaryDirectory() as d:
        TL.run(d, http=TB.PdfHTTP({}), bom=TL.BOM)
        f = Path(d) / "pdfs"
        rules = {"datasheet": {"folder": str(f)}}
        said = lambda: [t for t in fill_again(d, rules)["NOWHERE1"]["librarian"]["tried"] if "your [datasheet] folder" in t]
        assert said() == ["datasheet: no datasheet URL known; your [datasheet] folder (pdfs) does not exist"], said()
        f.mkdir()
        (f / "NOWHERE1.pdf").write_bytes(b"<!doctype html><html><body>Access Denied</body></html>")
        now = said()
        assert len(now) == 1 and "NOWHERE1.pdf in your [datasheet] folder is not a PDF" in now[0], now
        (f / "NOWHERE1.pdf").unlink()
        assert said() == []


def test_a_missing_folder_is_named_for_a_refused_link_and_a_line_with_no_link():
    """A typo in [datasheet] folder, said where the person looks: the refused link's review line (and its shown fix),
    and the tried line of a BOM line with no link. Its name only, never its path."""
    with tempfile.TemporaryDirectory() as d:
        gone = Path(d) / "datasheetz"
        it, items = refused_run(d, {"datasheet": {"folder": str(gone)}})
        note = "your [datasheet] folder (datasheetz) does not exist"
        line, e = refused(it)
        assert f"; {note}: fix folder under [datasheet] in ecadlib.toml, then download {LINK} in a browser, save it there " \
               "as DSPART1.pdf and run again (or read it" in line and str(gone) not in line, line
        assert e["fix"] == ("Your datasheet folder (datasheetz) does not exist: fix `folder` under datasheet in ecadlib.toml, "
                            f"then download the PDF in a browser ({LINK}), save it there as DSPART1.pdf and run again; or "
                            "read it alone with ecadlib datasheet FILE.pdf --part DSPART1; or pick a footprint by hand."), e["fix"]
        assert f"datasheet: no datasheet URL known; {note}" in items["NOWHERE1"]["librarian"]["tried"], \
            items["NOWHERE1"]["librarian"]["tried"]


def test_with_no_folder_set_the_advice_says_how_to_set_one():
    with tempfile.TemporaryDirectory() as d:
        it, _ = refused_run(d, None)
        line, e = refused(it)
        assert f"download {LINK} in a browser, set folder under [datasheet] in ecadlib.toml to a folder, save it there as " \
               "DSPART1.pdf and run again (or read it alone with `ecadlib datasheet FILE.pdf --part DSPART1`)" in line, line
        assert e["fix"] == (f"Download the PDF in a browser ({LINK}), set `folder` under datasheet in ecadlib.toml to a "
                            "folder, save it there as DSPART1.pdf and run again; or read it alone with ecadlib datasheet "
                            "FILE.pdf --part DSPART1; or pick a footprint by hand."), e["fix"]


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
        for rules, want in ((None, "set [datasheet] folder"),
                            ({"datasheet": {"folder": str(f)}}, "TESTPART.txt.pdf in your [datasheet] folder is not a PDF")):
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
