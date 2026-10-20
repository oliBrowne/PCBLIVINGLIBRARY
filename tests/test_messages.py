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
    "files.datasheet_refused": "files: datasheet download refused by www.analog.com (reset the connection): the maker's site "
                               "likely blocks automated downloads; download https://www.analog.com/media/en/technical-"
                               "documentation/data-sheets/LTM4671.pdf in a browser and read it with `ecadlib datasheet "
                               "FILE.pdf --part LTM4671`",
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
    "datasheet.some_pages": "only some pages were sent (pages 1-4 of 40): check nothing was missed",
    "datasheet.failed": "datasheet reader: failed (HTTP 500)",
    "librarian.lcsc_mismatch": "LCSC C123 is BSS84, not BSS138: fix the BOM's LCSC number",
    "librarian.footprint_guess": "footprint SOT-23 chosen by name: compare with the datasheet land pattern",
    "librarian.symbol_match": "symbol Q_NMOS_GSD matched by the BOM's value",
    "librarian.easyeda": "EasyEDA C8734 conversion: check pads and pins against the datasheet (native converter, format assumed)",
    "librarian.family_pinout": "symbol pinout AMS1117 from the AMS1117-3.3 family table (ecadlib.stdparts): check pins 1-3 against the datasheet",
    "librarian.value_two_ways": "verify: value 4,700pF reads two ways: 4.7pF or 4700pF; write it without the comma",
    "approval.waiting": "awaiting approval: 1 approval from role librarian",
    "approval.changed": "changed since the release check: run release-check again",
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
    "librarian.py": 9, "datasheet.py": 3,
}


def test_new_review_notes_need_a_sentence():
    """Adding a place that writes a review or verify note fails here until messages.CODES has a sentence for it
    (add a pattern + an EXAMPLES line, then bump the count)."""
    for name, n in NOTE_SITES.items():
        src = _src(name)
        got = len(re.findall(r'\["(?:review|verify|needs_review)"\]\.append\(', src))
        assert got == n, f"{name}: {got} review/verify note sites, expected {n}: give the new one a messages.CODES entry"
