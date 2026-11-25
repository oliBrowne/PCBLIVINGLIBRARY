"""A KiCad symbol matched by name + ordering suffix whose datasheet has no pin table in its text layer (a drawn
pinout, a table without numbers): the pinout DRAWING check (ecadlib.pin_drawing) may confirm it, as it confirms a
datasheet read's symbol (2026-11-25, 1000-part replay on main cf41046: 50 parts held on such a match). Never after a
doubt or a disagreement of the text check, never without the datasheet source, and only on "agreed".
Plain functions: python3 -m tests.run_all test_kicad_drawn_pins_1125"""
from ecadlib import librarian as L, pin_text as PT, pin_drawing as PD
from tests.fixtures_lite import lite
from tests.test_name_match_autocheck import _chain

SO8 = [("1", "RO"), ("2", "~{RE}"), ("3", "DE"), ("4", "DI"), ("5", "GND"), ("6", "A"), ("7", "B"), ("8", "VCC")]
CASE = '8-SOIC (0.154", 3.90mm Width)'
LAND = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"


class _Run(L.Run):
    """Only what variant_agrees needs: a PDF that is there, and the drawing check's verdict given by the test."""
    def __init__(self, drawing):
        self.drawing, self.asked = drawing, []

    def datasheet_pdf(self, item, ln, row, st, mpn):
        return b"%PDF-1.4 stand-in", "https://maker.example/ds.pdf"

    def drawn_kicad_pins(self, st, item, row, mpn, pins, data, packages):
        self.asked.append((mpn, list(pins), tuple(packages)))
        return self.drawing

    def kicad(self):
        k = L.KiCadLibrary.__new__(L.KiCadLibrary)
        k.footprints = {}
        return k


def _variant(monkeypatch, text_verdict, drawing):
    monkeypatch.setattr(PT, "check_pdf", lambda *a, **k: dict(text_verdict))
    run = _Run(drawing)
    st = {"hint_fp": LAND, "tried": []}
    item = {"footprint": {"kicad": ""}, "description": "", "name": "MAX485ESA+"}
    ln = {"footprint": "", "description": "", "value": ""}
    row = {"src_package": CASE, "src_package_from": "digikey", "src_several": "", "src_mpn": "MAX485ESA+"}
    chain = _chain("MAX485E", SO8, LAND)
    ok = run.variant_agrees(st, item, ln, row, None, "MAX485ESA+", "Interface_UART:MAX485E", chain)
    return ok, st["tried"], run.asked


NO_TABLE = {"status": "unchecked", "why": "page 7 names 3 of 8 pins"}
AGREED = {"status": "agreed", "why": "8 pins agree with the pinout drawing (printed)", "pages": [1]}


@lite
def test_no_pin_table_in_the_text_and_the_drawing_agrees_releases(monkeypatch):
    ok, tried, asked = _variant(monkeypatch, NO_TABLE, AGREED)
    assert ok, tried
    assert asked and asked[0][0] == "MAX485ESA+" and ("2", "R\\E\\") in [tuple(p) for p in asked[0][1]]
    assert LAND.split(":")[-1] in asked[0][2] and CASE in asked[0][2]         # the packages the drawing must be titled for
    said = tried[-1]
    assert "confirmed" in said and "pinout drawing" in said and "page 1" in said and "maker.example" in said, said
    assert any("did not confirm it (page 7 names 3 of 8 pins)" in t for t in tried)   # the text check's gap is still said


@lite
def test_a_drawing_that_does_not_agree_keeps_the_hold_and_says_why(monkeypatch):
    for d in ({"status": "disagreed", "why": "the pinout drawing disagrees: pin 2: symbol ~{RE}, drawing DE"},
              {"status": "unchecked", "why": "the second read found no pinout drawing of this part"},
              {"status": "waiting", "why": "waiting for your assistant to read the pinout drawing: x"}):
        ok, tried, asked = _variant(monkeypatch, NO_TABLE, d)
        assert not ok and asked, (d, tried)
        assert tried[-1].endswith(f"pinout drawing: {d['status']}: {d['why']}"), tried
    ok, tried, _ = _variant(monkeypatch, NO_TABLE, None)                     # not asked (datasheet source off)
    assert not ok and "pinout drawing" not in tried[-1], tried
    ok, tried, _ = _variant(monkeypatch, NO_TABLE, {"status": "agreed-ish", "why": "x"})
    assert not ok                                                           # only the word "agreed" releases


@lite
def test_a_doubt_or_a_disagreement_of_the_text_check_never_asks_the_drawing(monkeypatch):
    doubt = {"status": "unchecked", "doubt": True, "why": "page 3: GND=5 not printed with that number"}
    for v in (doubt, {"status": "disagreed", "why": "page 3 prints GND=5 with other numbers"}):
        ok, tried, asked = _variant(monkeypatch, v, AGREED)
        assert not ok and not asked, (v, tried)


@lite
def test_the_text_table_agreeing_does_not_ask_the_drawing(monkeypatch):
    ok, tried, asked = _variant(monkeypatch, {"status": "agreed", "page": 3, "pins": 8, "column": 0}, None)
    assert ok and not asked, tried


class _Reader:
    cfg = {}
    estimate_only = False


@lite
def test_drawn_kicad_pins_asks_only_with_the_datasheet_source_and_passes_the_symbols_pins(monkeypatch):
    seen = {}

    def confirm(reader, job, res, kind, packages):
        seen.update(job=job, res=res, kind=kind, packages=packages)
        return dict(AGREED)
    monkeypatch.setattr(PD, "confirm", confirm)
    run = L.Run.__new__(L.Run)
    run.reader = _Reader()
    st = {"found": {"description": ("RS-485 transceiver", "digikey")}}
    item, row = {"name": "U7"}, {"src_description": "IC TRANSCEIVER HALF 1/1 8SOIC"}
    run.sources = ["kicad", "standard"]
    assert run.drawn_kicad_pins(st, item, row, "MAX485ESA+", SO8, b"pdf", (LAND, CASE, "")) is None and not seen
    run.sources = ["kicad", "standard", "datasheet"]
    v = run.drawn_kicad_pins(st, item, row, "MAX485ESA+", SO8, b"pdf", (LAND, CASE, ""))
    assert v["status"] == "agreed"
    assert seen["job"] == {"data": b"pdf", "part": "MAX485ESA+", "name": "U7"}
    assert seen["res"]["symbol"]["pins"][1] == {"num": "2", "name": "~{RE}"} and len(seen["res"]["symbol"]["pins"]) == 8
    assert seen["kind"] == "other" and seen["packages"] == [LAND, CASE, ""]
    run.reader.estimate_only = True                                        # an estimate asks nothing
    assert run.drawn_kicad_pins(st, item, row, "MAX485ESA+", SO8, b"pdf", (LAND, CASE, "")) is None


@lite
def test_a_drawing_check_that_breaks_or_waits_never_releases(monkeypatch):
    from ecadlib import datasheet as D

    def boom(*a, **k):
        raise RuntimeError("render failed")

    def pending(*a, **k):
        raise D.AssistantPending("waiting for your assistant to read the pinout drawing: f")
    run = L.Run.__new__(L.Run)
    run.reader, run.sources = _Reader(), ["datasheet"]
    run.reader.estimate_only = False
    for fn, status in ((boom, "unchecked"), (pending, "waiting")):
        monkeypatch.setattr(PD, "confirm", fn)
        v = run.drawn_kicad_pins({"found": {}}, {"name": "U7"}, {}, "MAX485ESA+", SO8, b"pdf", (LAND, CASE, ""))
        assert v["status"] == status, v


# ---------------------------------------------------------------- names two sources spell differently (pin_drawing)
BAR = "R\\E\\S\\E\\T\\"                       # Altium's bar over RESET, as a converted KiCad symbol carries it


def _same(a, b, others=("GND", "OUT")):
    return PD.same_pin("other", "1", a, b, ([a, *others], [b]))


def test_a_multi_function_pin_agrees_when_one_sides_functions_are_all_the_others():
    assert _same("XTAL1/PB3", "(PCINT3/XTAL1/CLKI/OC1B/ADC3) PB3")          # KiCad's ATtiny85 and Atmel's drawing
    assert _same("PB3", "TOSC1/PB3") and _same("DO/IO_{1}", "DO (IO1)")
    assert _same("P0.02/AIN0", "P0.02 AIN0")
    assert not _same("XTAL1/PB3", "(PCINT4/XTAL2/CLKO/OC1B/ADC2) PB4")      # the neighbouring pin
    assert not _same("PB3", "TOSC2/PB2")
    assert not _same("XTAL1/PB3", "XTAL1/PB4")                              # one function alike is not enough
    assert not _same("XTAL1/PB3", "")
    both = ["(PCINT3/XTAL1/CLKI/OC1B/ADC3) PB3", "(PCINT4/XTAL2/CLKO/OC1B/ADC2) PB4"]
    assert not PD.same_pin("other", "2", "OC1B", both[0], (["OC1B", "GND"], both))   # a function two pins list
    assert PD.same_pin("other", "2", "XTAL1", both[0], (["XTAL1", "GND"], both))


def test_the_active_low_mark_is_compared_function_by_function():
    assert _same(BAR + "/PB5", "(PCINT5//RESET/ADC0/dW) PB5")
    assert _same(BAR + "/PB5", "PB5 (/RESET)") and _same("~{RESET}/PB5", "PB5 (RESET#)")
    assert not _same(BAR + "/PB5", "(PCINT5/RESET/ADC0/dW) PB5")            # no bar reported: held
    assert not _same("RESET/PB5", "PB5 (/RESET)")
    assert _same("W\\P\\/IO_{2}", "/WP (IO2)") and not _same("W\\P\\/IO_{2}", "WP (IO2)")
    assert _same("H\\O\\L\\D\\/" + BAR + "/IO_{3}", "/HOLD or /RESET (IO3)")
    assert _same("R\\S\\T\\", "/RST") and not _same("R\\S\\T\\", "RST") and not _same("EN", "/EN")


def test_a_rail_or_single_input_spelt_another_way_is_the_same_pin_only_when_it_is_the_symbols_only_one():
    assert _same("VS+", "V+") and _same("VCC", "VDD") and _same("IN", "VIN") and _same("Vin-", "IN-")
    assert not _same("VCC", "VDD", others=("VDD", "GND"))                   # the part has both: exact names only
    assert not _same("VCC", "GND") and not _same("GND", "VIN") and not _same("IN", "OUT")
    assert not PD.same_pin("mosfet", "1", "VCC", "VDD", (["VCC"], ["VDD"]))  # discretes keep their standard roles


def test_every_swap_of_two_pins_is_still_caught_with_the_looser_names():
    sym = {"1": BAR + "/PB5", "2": "XTAL1/PB3", "3": "XTAL2/PB4", "4": "GND", "5": "AREF/PB0", "6": "PB1", "7": "PB2",
           "8": "VCC"}
    drawn = {"1": "(PCINT5//RESET/ADC0/dW) PB5", "2": "(PCINT3/XTAL1/CLKI/OC1B/ADC3) PB3",
             "3": "(PCINT4/XTAL2/CLKO/OC1B/ADC2) PB4", "4": "GND", "5": "PB0 (MOSI/DI/SDA/AIN0/OC0A/OC1A/AREF/PCINT0)",
             "6": "PB1 (MISO/DO/AIN1/OC0B/OC1A/PCINT1)", "7": "PB2 (SCK/USCK/SCL/ADC1/T0/INT0/PCINT2)", "8": "VCC"}
    pins = list(sym.items())
    assert PD.compare("other", pins, drawn) == []
    keys = sorted(drawn)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            d = dict(drawn)
            d[keys[i]], d[keys[j]] = drawn[keys[j]], drawn[keys[i]]
            assert PD.compare("other", pins, d), (keys[i], keys[j])


def test_so_is_soic_and_spdip_is_dip_in_a_drawings_title():
    assert PD.same_package("DIP/SO/SSOP/TSSOP", ["SOIC-16_3.9x9.9mm_P1.27mm", "16-SOIC"]) is None
    assert PD.same_package("28 SPDIP", ["DIP-28_W7.62mm", "28-PDIP"]) is None
    assert PD.same_package("TSSOP", ["SOIC-16_3.9x9.9mm_P1.27mm"])          # another family still holds
    assert PD.same_package("SOIC-8", ["SOIC-16_3.9x9.9mm_P1.27mm"])         # and another lead count


def test_the_prompt_asks_for_the_bar():
    assert "bar over it" in PD.SYSTEM and '"/RESET"' in PD.SYSTEM
