"""ecadlib.messages: every reason code has a plain sentence and a next step; every raw reason a module can produce
maps to a code. Run:
    python3 -m tests.run_all test_messages"""
import re
from pathlib import Path

from ecadlib import messages as M

ROOT = Path(__file__).resolve().parents[1] / "ecadlib"

# one raw reason per code, written the way the producing module writes it
EXAMPLES = {
    "files.no_symbol": "files: no symbol model (.SchLib)",
    "files.no_footprint": "files: no footprint model (.PcbLib) (cannot be overridden)",
    "files.datasheet_not_pdf": "files: datasheet download refused by assets.nexperia.com (answered HTTP 403 with a web page): "
                               "the maker's site likely blocks automated downloads; BAT160A.pdf in your [datasheet] folder "
                               "is not a PDF (a web page saved as .pdf?): replace it with the PDF: download https://assets."
                               "nexperia.com/documents/data-sheet/BAT160_SERIES.pdf in a browser, save it as BAT160A.pdf and "
                               "run again (or read it alone with `ecadlib datasheet FILE.pdf --part BAT160A`)",
    "files.datasheet_refused": "files: datasheet download refused by www.analog.com (reset the connection): the maker's site "
                               "likely blocks automated downloads; download https://www.analog.com/media/en/technical-"
                               "documentation/data-sheets/LTM4671.pdf in a browser, save it as LTM4671.pdf in your "
                               "[datasheet] folder (datasheets) and run again (or read it alone with `ecadlib datasheet "
                               "FILE.pdf --part LTM4671`)",
    "files.missing": "files: footprint file missing: /srv/ws/models/PcbLib/X.PcbLib",
    "files.unreadable": "files: symbol file does not read back (ValueError: library is empty)",
    "pins.no_pins": "pins: symbol has no pins",
    "pins.no_pads": "pins: footprint has no pads",
    "pins.pin_without_pad": "pins: symbol pins without a pad: 5-8",
    "pins.pad_without_pin": "pins: pads without a symbol pin: 9",
    "padtable.no_pin1": "files: datasheet pad table: no pad numbered 1 (or A1) - pads named G, R1, S1, T1",
    "padtable.overlap": "files: datasheet pad table: pads 27 (pads[30]) and 28 (pads[34]) overlap: check their x/y (list same-net pads in same_net)",
    "padtable.duplicate": "files: datasheet pad table: pad 17 is listed twice at the same place",
    "padtable.symbol_waits": "files: datasheet pad table: the symbol waits for the footprint",
    "padtable.other": "files: datasheet pad table: pin_count is 8 but the table has 6 numbered pins",
    "footprint.overlap": "footprint: copper: pads 1 and 2 overlap by 0.050 mm",
    "footprint.pad_gap": "footprint: copper: pad pairs 1-2, 3-4 are 0.090 mm apart (< 0.1 mm)",
    "footprint.hole_clearance": "footprint: copper: pad 3 is 0.000 mm from the hole at (1.000, 2.000) (< 0.1 mm)",
    "footprint.zero_pad": "footprint: copper: pad 1 has no copper: size 1.95 x 0 mm",
    "footprint.no_pin1": "footprint: pin1: no pad numbered 1",
    "footprint.clockwise": "footprint: pin1: pads are numbered clockwise; KiCad and IPC number counter-clockwise from pin 1 (seen from the top)",
    "footprint.ball_rows": "footprint: bga: row letters and rows disagree at balls A1, B2",
    "footprint.silk_on_pad": "footprint: silk: a filled silkscreen shape covers pad 4",
    "footprint.silk_close": "footprint: silk: silkscreen is 0.050 mm from pad 2 (KLC: >= 0.2 mm, 0.1 mm on tiny parts)",
    "footprint.no_courtyard": "footprint: courtyard: no courtyard on F.CrtYd",
    "footprint.courtyard_small": "footprint: courtyard: courtyard does not enclose every pad and the body outline",
    "footprint.pad_outside": "footprint: courtyard: pad 7 sticks out of the courtyard outline",
    "footprint.courtyard_open": "footprint: courtyard: courtyard is not a closed outline",
    "footprint.not_a_number": "footprint: geometry: pad 3 has a position or size that is not a number (nan / inf)",
    "footprint.other": "footprint: fab: something new",
    "height.missing": "height: no 3D height (no 3D body and no HEIGHT)",
    "height.out_of_range": "height: 3D height 2.10 mm outside 0.8-1.45 mm for ^SOT-23",
    "fields.no_datasheet": "fields: no datasheet link",
    "fields.no_description": "fields: Description is empty",
    "fields.empty": "fields: Manufacturer is empty",
    "parity.pad_count": "parity: footprint pad count: KiCad 8 / Altium 9",
    "parity.pad_missing": "parity: pad 3 missing: KiCad present / Altium missing (at (1, 2))",
    "parity.pin_pad": "parity: pin 4 pad: KiCad pad / Altium no pad (pin -> pad mapping differs)",
    "parity.symbol": "parity: pin 2 name: KiCad GND / Altium VCC",
    "parity.courtyard": "parity: courtyard extent: KiCad (-1, -1, 1, 1) / Altium (-1, -1, 1, 2) (x0, y0, x1, y1 mm)",
    "parity.model3d": "parity: 3D model presence: KiCad SOT-23.step / Altium none",
    "parity.pad": "parity: pad 1 size/rotation: KiCad (1, 0.8, 0) / Altium (1, 0.9, 0) (w, h mm, rotation deg)",
    "parity.other": "parity: exposed pad paste: KiCad 4 windows 50% / Altium 1 windows 80%",
    "verify.datasheet_read": "verify: datasheet reader output (footprint, symbol): compare with https://x.example/a.pdf; "
                             "see datasheet/*.review.md (3 items flagged)",
    "verify.sim_pin_map": "verify: sim model BSS138: pin map not verified (by position)",
    "verify.other": "verify: something a person must look at",
    "crosscheck.disagree": "cross-check disagrees, nothing written: second read: pitch read 0.5, second read 0.65",
    "crosscheck.unchecked": "cross-check unchecked: second read: not checked (refused)",
    "datasheet.no_link": "datasheet: no datasheet URL known",
    "datasheet.no_key": "needs datasheet reader (no API key): https://x.example/a.pdf (BOM)",
    "datasheet.awaiting_ok": "datasheet: waiting for your OK to read it with AI (https://x.example/a.pdf)",
    "datasheet.not_downloaded": "the PDF was not downloaded here (sent as a URL): its content was not validated locally",
    "crosscheck.tie_break": "cross-check agreed once a difference was set aside: package type read custom, other source connector: a word only, the pads are built as the read lists them; check it on the drawing",
    "datasheet.unnamed": "the PDF at https://x/a.pdf doesn't mention LTC4060EDHC (no other link gave a PDF): check it is this part's datasheet",
    "datasheet.some_pages": "only some pages were sent (pages 1-4 of 40): check nothing was missed",
    "datasheet.outline_doc": "the package drawing came from 98ASS23174W (https://www.nxp.com/docs/en/package-information/"
                             "98ASS23174W.pdf), appended from page 55: check it is this part's package",
    "datasheet.outline_missing": "the datasheet has no drawing of this package, only its document number: 98ASS23174W (named "
                                 "on page 2; save it as 98ASS23174W.pdf in your datasheet folder)",
    "datasheet.family_pdf": "datasheet from your folder: LTM4671.pdf in the datasheet folder (a family datasheet: the name is the "
                            "start of LTM4671IY#PBF); check it covers this part",
    "datasheet.dip_holes": "DIP holes: the datasheet prints no hole size, so KiCad's DIP hole and pad (0.8 mm / 1.6 mm) were "
                           "used in place of the read's; check the maker's recommended hole",
    "datasheet.standard_land": "standard land: the datasheet read identified SOIC-8_3.9x4.9mm_P1.27mm (8 pins, pitch 1.27, body "
                               "3.9 x 4.9 -> SOIC-8_3.9x4.9mm_P1.27mm); the read left terminal_length blank, so the checked "
                               "standard land was used. Check the package code on the drawing is this one",
    "datasheet.tab_joined": "tab: the datasheet numbers the tab 3, on pin 1's net; the land numbers it 1, so the symbol has "
                            "no pin 3 (check the pin table)",
    "datasheet.tab_cut_lead": "tab: the datasheet numbers the tab 4, and lead 2 is cut (N.C.); the land numbers the tab 2, "
                              "so it was numbered 4 (check the pin table)",
    "datasheet.tab_cut_pin": "symbol: pin 2 (N.C., the cut lead) left out: it has no lead and no pad",
    "symbol.unknown_pin_type": "symbol: pin type not known, drawn passive: pin 3 'analog'",
    "datasheet.failed": "datasheet reader: failed (HTTP 500)",
    "librarian.lcsc_mismatch": "LCSC C123 is BSS84, not BSS138: fix the BOM's LCSC number",
    "librarian.footprint_guess": "footprint SOT-23 chosen by name: compare with the datasheet land pattern",
    "librarian.symbol_match": "symbol Q_NMOS_GSD matched by the BOM's value",
    "librarian.easyeda": "EasyEDA C8734 conversion: check pads and pins against the datasheet (native converter, format assumed)",
    "librarian.family_pinout": "symbol pinout AMS1117 from the AMS1117-3.3 family table (ecadlib.stdparts): check pins 1-3 against the datasheet",
    "librarian.value_two_ways": "verify: value 4,700pF reads two ways: 4.7pF or 4700pF; write it without the comma",
    "approval.waiting": "awaiting approval: 1 approval from role librarian",
    "approval.changed": "changed since the release check: run release-check again",
    "change.held": "change control: new part with the MPN of released RES-0603-10k r1 (pad 1 moved 0.2 mm in x); a person "
                   "approves it: ecadlib part show",
    "dedupe.duplicate": "duplicate of R_10k_0603 (dedupe)",
    "a365.deleted": "deleted in the Altium 365 workspace",
    "proof.doubt": "proof doubt: the pin 1 mark is on the other side",
    "proof.skipped": "proof skipped: ANTHROPIC_API_KEY is not set",
    "proof.finding": "proof: pad 1 width",
    "other": "something nobody wrote a sentence for",
}


def test_every_code_has_text_and_fix():
    for code, (text, fix, _) in M.CODES.items():
        assert text and fix, code
        assert fix.rstrip().endswith("."), (code, fix)
        assert text.rstrip("]").endswith((".", "}")), (code, text)


def test_every_code_has_an_example_that_explains_to_it():
    assert set(EXAMPLES) == set(M.CODES), set(M.CODES) ^ set(EXAMPLES)
    for code, raw in EXAMPLES.items():
        e = M.explain(raw)
        assert e["code"] == code, (code, raw, e)
        assert "?" not in e["text"] and "{" not in e["text"] and "[" not in e["text"], e
        assert not M.leaks(e["text"]) and not M.leaks(e["fix"]), e


def test_no_template_writes_brackets_of_its_own():
    """fill() keeps or drops each [...] by the {values} in it, and never prints the brackets: "put the PDF in your
    [datasheet] folder" came out as "your datasheet folder". So a bracket group in a template always holds a value."""
    for code, (text, fix) in [(c, v[:2]) for c, v in M.CODES.items()] + list(M.ERRORS.items()):
        for t in (text, fix):
            bare = [g for g in re.findall(r"\[([^\[\]]*)\]", t) if not re.search(r"{\w+}", g)]
            assert not bare, (code, bare)
    fix = M.explain(EXAMPLES["datasheet.no_link"])["fix"]
    assert fix == ("Add the datasheet link to the BOM line, save the PDF as MPN.pdf in your datasheet folder (set "
                   "`folder` under datasheet in ecadlib.toml), or pick a footprint by hand."), fix
    fix = M.explain(EXAMPLES["files.datasheet_refused"])["fix"]
    assert "save it as LTM4671.pdf in your datasheet folder (datasheets) and run again; or read it alone with ecadlib " \
           "datasheet FILE.pdf --part LTM4671; or pick" in fix, fix


REFUSED = ("files: datasheet download refused by www.analog.com (reset the connection): the maker's site likely blocks "
           "automated downloads; ")
URL = "https://www.analog.com/ltm4671.pdf"


def test_every_refused_download_line_explains_in_full():
    """Each way datasheet.refused_line writes its advice (folder set, not set, missing; a file there that is no PDF;
    an MPN with spaces or a '/'), and the lines of earlier versions kept in plans, come out whole: the file name and
    the --part value as written, nothing unfilled."""
    alone = " (or read it alone with `ecadlib datasheet FILE.pdf --part {}`)"
    tail = "; or read it alone with ecadlib datasheet FILE.pdf --part {}; or pick a footprint by hand."
    cases = [
        (f"download {URL} in a browser, save it as LTM4671.pdf in your [datasheet] folder (ds) and run again" + alone.format("LTM4671"),
         "files.datasheet_refused", f"Download the PDF in a browser ({URL}), save it as LTM4671.pdf in your datasheet folder "
                                    "(ds) and run again" + tail.format("LTM4671")),
        (f"download {URL} in a browser, set folder under [datasheet] in ecadlib.toml to a folder, save it there as "
         "DSPART1 REV A.pdf and run again" + alone.format('"DSPART1 REV A"'),
         "files.datasheet_refused", f"Download the PDF in a browser ({URL}), set `folder` under datasheet in ecadlib.toml to "
                                    "a folder, save it there as DSPART1 REV A.pdf and run again" + tail.format('"DSPART1 REV A"')),
        ("your [datasheet] folder (datasheetz) does not exist: fix folder under [datasheet] in ecadlib.toml, then download "
         f"{URL} in a browser, save it there as LM1117MPX-3.3-NOPB.pdf and run again" + alone.format("LM1117MPX-3.3/NOPB"),
         "files.datasheet_refused", "Your datasheet folder (datasheetz) does not exist: fix `folder` under datasheet in "
                                    f"ecadlib.toml, then download the PDF in a browser ({URL}), save it there as "
                                    "LM1117MPX-3.3-NOPB.pdf and run again" + tail.format("LM1117MPX-3.3/NOPB")),
        ("your [datasheet] folder (ds) cannot be read (PermissionError): fix folder under [datasheet] in ecadlib.toml, then "
         f"download {URL} in a browser, save it there as LTM4671.pdf and run again" + alone.format("LTM4671"),
         "files.datasheet_refused", "Your datasheet folder (ds) cannot be read (PermissionError): fix `folder` under "
                                    f"datasheet in ecadlib.toml, then download the PDF in a browser ({URL}), save it there "
                                    "as LTM4671.pdf and run again" + tail.format("LTM4671")),
        ("DSPART1 REV A.pdf in your [datasheet] folder cannot be read (PermissionError): replace it with the PDF: download "
         f"{URL} in a browser, save it as DSPART1 REV A.pdf and run again" + alone.format('"DSPART1 REV A"'),
         "files.datasheet_not_pdf", f"Replace DSPART1 REV A.pdf with the PDF: download it in a browser ({URL}), save it as "
                                    "DSPART1 REV A.pdf and run again" + tail.format('"DSPART1 REV A"')),
        # written by earlier versions, still in plans: the folder's way with the part's own name
        (f"download {URL} in a browser, save it as LTM4671.pdf in your [datasheet] folder and run again" + alone.format("LTM4671"),
         "files.datasheet_refused", f"Download the PDF in a browser ({URL}), save it as LTM4671.pdf in your datasheet folder "
                                    "(set `folder` under datasheet in ecadlib.toml) and run again" + tail.format("LTM4671")),
        (f"download {URL} in a browser and read it with `ecadlib datasheet FILE.pdf --part LM1117MPX-3.3/NOPB`",
         "files.datasheet_refused", f"Download the PDF in a browser ({URL}), save it as LM1117MPX-3.3-NOPB.pdf in your "
                                    "datasheet folder (set `folder` under datasheet in ecadlib.toml) and run again"
                                    + tail.format("LM1117MPX-3.3/NOPB")),
    ]
    for raw, code, fix in cases:
        e = M.explain(REFUSED + raw)
        assert (e["code"], e["fix"]) == (code, fix), (raw, e)
        assert not re.search(r"[{}\[\]?]", e["text"] + e["fix"]) and not M.leaks(e["text"] + e["fix"]), e
    e = M.explain(REFUSED.rstrip("; "))                             # nothing after the cause: still a whole sentence
    assert e["code"] == "files.datasheet_refused" and "?" not in e["fix"] and "{" not in e["fix"], e


def test_a_line_with_no_link_keeps_what_is_wrong_with_the_folder():
    """A BOM line with no link, and a [datasheet] folder that gave nothing for it (not there, no access, the part's
    file no PDF): the shown sentence says so, by the folder's name."""
    no_link = "No datasheet link is known, so the datasheet could not be read"
    for raw, said in (("datasheet: no datasheet URL known; your [datasheet] folder (datasheetz) does not exist",
                       ", and your datasheet folder (datasheetz) does not exist."),
                      ("datasheet: no datasheet URL known; your [datasheet] folder (ds) cannot be read (PermissionError)",
                       ", and your datasheet folder (ds) cannot be read (PermissionError)."),
                      ("datasheet: failed (DatasheetError: no datasheet URL known and DSPART1.pdf in your [datasheet] folder "
                       "is not a PDF (a web page saved as .pdf?): replace it with the PDF)",
                       ", and DSPART1.pdf in your datasheet folder is not a PDF."),
                      ("datasheet: no datasheet URL known", ".")):
        e = M.explain(raw)
        assert e["code"] == "datasheet.no_link" and e["text"] == no_link + said, (raw, e)
        assert not re.search(r"[{}\[\]?]", e["text"] + e["fix"]), e


def test_a_folder_file_with_spaces_in_its_name_is_named_whole():
    for name in ("BAT54S (1).pdf", "BAT54S datasheet.pdf", "BAT54S.PDF"):
        e = M.explain(f"verify: datasheet from your folder: {name} in the datasheet folder (named for another part "
                      "number that starts with BAT54); check it covers this part")
        assert e["code"] == "datasheet.family_pdf" and e["text"].endswith(f"({name})."), (name, e)


def test_held_fills_values_and_drops_optional_parts():
    t, f = M.held("crosscheck.disagree", field="pin pitch", first=0.5, second=0.65, page=34)
    assert t == "The two datasheet reads disagree on pin pitch (0.5 mm vs 0.65 mm); nothing was written to the library.", t
    assert f == "Check the datasheet (page 34) and pick one, or release anyway.", f
    t, f = M.held("crosscheck.disagree", field="pin count", first=8, second=10, other="table")
    assert t.startswith("The datasheet read and the standard package table disagree on pin count (8 vs 10)"), t
    assert f == "Check the datasheet and pick one, or release anyway.", f
    assert M.held("footprint.overlap", a="1", b="2")[0] == "Pads 1 and 2 overlap."
    assert M.held("height.out_of_range", h=2.1, lo=0.8, hi=1.45)[0] == \
        "The 3D height is 2.1 mm, outside the usual 0.8-1.45 mm for this package."
    try:
        M.held("no.such.code")
        assert False, "unknown codes must raise"
    except KeyError:
        pass


def test_override_notes_and_cleaning():
    e = M.explain("files: no symbol model (.SchLib) (cannot be overridden)")
    assert e["code"] == "files.no_symbol" and e["note"] == "This one cannot be released anyway.", e
    e = M.explain("height: no 3D height (no 3D body and no HEIGHT) (not covered by --allow)")
    assert e["code"] == "height.missing" and "released anyway" in e["note"], e
    assert M.explain(EXAMPLES["files.missing"])["text"] == "The footprint file is missing."
    e = M.explain("weird thing at /home/someone/ws/x/plan.json (ecadlib.stdparts)")
    assert e["code"] == "other" and "/home" not in e["text"] and "ecadlib." not in e["text"], e
    assert M.clean("see https://x.example/a/b.pdf") == "see https://x.example/a/b.pdf"       # URLs stay
    assert M.clean(r"wrote C:\Users\me\lib\X.PcbLib") == "wrote X.PcbLib"
    assert len(M.explain_all(["pins: footprint has no pads", "pins: footprint has no pads (not covered by --allow)"])) == 1


def test_words_and_counts():
    assert M.status_word("awaiting_approval") == "waiting for sign-off"
    assert M.status_word("something_new") == "something new"
    assert M.counts(["ready"] * 6 + ["held"] * 2) == "6 ready, 2 held"
    assert M.counts({"held": 1, "ready": 0, "awaiting_approval": 3}) == "1 held, 3 waiting for sign-off"
    assert M.counts([]) == "nothing yet"
    assert M.plural(1, "pin") == "1 pin" and M.plural(3, "pin") == "3 pins"


def test_errors_are_two_plain_lines():
    for code in M.ERRORS:
        t, f = M.error(code, what="The BOM", name="bom.csv", host="gitlab.com", why="bad")
        assert t and f and "\n" not in t + f and "?" not in t, (code, t, f)
    t, f = M.friendly_error(FileNotFoundError(2, "No such file", "/srv/secret/ws/bom.csv"))
    assert t == "A file was not found: bom.csv." and "/srv" not in t + f, t
    t, f = M.friendly_error(RuntimeError("boom at /srv/secret/x.py"))
    assert "/srv" not in t and "--debug" in f, (t, f)
    assert "/x" not in M.friendly_error(PermissionError(13, "denied", "/x/y"))[0]


# ---------------------------------------------------------------- nothing new without a sentence
def _src(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_every_release_check_rule_has_codes():
    rules = set(re.findall(r'\badd\("(\w+)",', _src("release_check.py")))
    assert rules >= {"files", "pins", "footprint", "height", "fields", "verify"}, rules
    rules |= {"parity"}                                   # added through add("parity", d["msg"])
    prefixes = {c.split(".")[0] for c in M.CODES}
    assert rules <= prefixes, f"release-check rules without a messages.CODES entry: {rules - prefixes}"


def test_every_footprint_check_error_has_a_specific_code():
    """Every error-level message footprint_check can write maps to a specific code (not footprint.other)."""
    src = _src("footprint_check.py")
    errs = re.findall(r'add\("error"(?: if [^,]+ else "warning")?, "(\w+)", f?"([^"]+)"', src)
    assert len(errs) >= 8, errs
    for rule, msg in errs:
        sample = re.sub(r"{[^}]*}", "1", msg).replace("1 mm", "0.1 mm")
        code = M.explain(f"footprint: {rule}: {sample}")["code"]
        assert code.startswith("footprint.") and code != "footprint.other", (rule, msg, code)
    # the copper-gap message is assembled from pieces
    assert M.explain("footprint: copper: pads 2-4 are 0.120 mm from pad 1 (< 0.1 mm)")["code"] == "footprint.pad_gap"


def test_every_parity_field_has_a_specific_code():
    src = _src("parity.py")
    found = re.findall(r'_d\(out, [^,]+, (f?"[^"]*"|\w+), "([^"]+)"', src)
    assert len(found) >= 20, found
    for what, field in found:
        w = {"what": "pad 1"}.get(what, what.strip('f"').replace("{wk}", "paste").replace("{p['num'] or '(unnumbered)'}", "1")
                                                      .replace("{n}", "1"))
        raw = f"parity: {w} {field}: KiCad 1 / Altium 2"
        e = M.explain(raw)
        assert e["code"].startswith("parity."), (raw, e)
        assert "?" not in e["text"], (raw, e)


NOTE_SITES = {   # file -> number of places that append a review / verify note (each has an EXAMPLES entry above)
    "librarian.py": 15, "datasheet.py": 7,
}


def test_new_review_notes_need_a_sentence():
    """Adding a place that writes a review or verify note fails here until messages.CODES has a sentence for it
    (add a pattern + an EXAMPLES line, then bump the count)."""
    for name, n in NOTE_SITES.items():
        src = _src(name)
        got = len(re.findall(r'\["(?:review|verify|needs_review)"\]\.append\(', src))
        assert got == n, f"{name}: {got} review/verify note sites, expected {n}: give the new one a messages.CODES entry"
