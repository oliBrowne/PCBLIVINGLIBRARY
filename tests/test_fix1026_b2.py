"""Fixes 2026-10-26 (b2): review 196 bomflow2 (R-B2-1..5). Each test failed on d562266 and passes with its fix."""
import contextlib, io, re
from pathlib import Path

from tests.fixtures_lite import lite
from tests.test_fix1026_bomflow import HDR, _holds, _run


def _sym_lib(tmp, parts):
    """A local KiCad library: one symbol per (mpn, land) whose Footprint property is mylib:<land>, and each land
    built by stdparts."""
    from ecadlib import stdparts as SP
    lib = Path(tmp) / "mylib"
    (lib / "mylib.pretty").mkdir(parents=True, exist_ok=True)
    syms = []
    for mpn, land in parts:
        (lib / "mylib.pretty" / f"{land}.kicad_mod").write_text(SP.build(land)[0], encoding="utf-8")
        pins = int(re.search(r"-(\d+)", land).group(1))
        pin = "".join(f'\n\t\t\t(pin passive line (at -10.16 {2.54 * k} 0) (length 2.54) (name "P{k}" (effects (font '
                      f'(size 1.27 1.27)))) (number "{k}" (effects (font (size 1.27 1.27)))))' for k in range(1, pins + 1))
        syms.append(f'''\t(symbol "{mpn}"
\t\t(exclude_from_sim no) (in_bom yes) (on_board yes)
\t\t(property "Reference" "U" (at 0 7.62 0) (effects (font (size 1.27 1.27))))
\t\t(property "Value" "{mpn}" (at 0 -7.62 0) (effects (font (size 1.27 1.27))))
\t\t(property "Footprint" "mylib:{land}" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t\t(property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
\t\t(symbol "{mpn}_1_1"{pin}
\t\t)
\t)''')
    (lib / "mylib.kicad_sym").write_text('(kicad_symbol_lib (version 20231120) (generator "kicad_symbol_editor")\n'
                                         + "\n".join(syms) + "\n)\n", encoding="utf-8")
    return lib


# --- R-B2-1: a distributor's body width, a body-less name and a power package are read before calling one land ------

DK_WIDTHS = {3.9: ['8-SOIC (0.154", 3.90mm Width)', '14-SOIC (0.154", 3.90mm Width)', '16-SOIC (0.154", 3.90mm Width)'],
             5.3: ['8-SOIC (0.209", 5.30mm Width)', '16-SOIC (0.209", 5.30mm Width)', '20-SSOP (0.209", 5.30mm Width)'],
             7.5: ['16-SOIC (0.295", 7.50mm Width)', '20-SOIC (0.295", 7.50mm Width)', '24-SOIC (0.295", 7.50mm Width)'],
             4.4: ['8-TSSOP (0.173", 4.40mm Width)', '14-TSSOP (0.173", 4.40mm Width)', '20-TSSOP (0.173", 4.40mm Width)'],
             3.0: ['8-TSSOP, 8-MSOP (0.118", 3.00mm Width)', '10-TFSOP, 10-MSOP (0.118", 3.00mm Width)']}
KI_WIDTHS = {3.9: ["SOIC-8_3.9x4.9mm_P1.27mm", "SOIC-14_3.9x8.7mm_P1.27mm", "SOIC-16_3.9x9.9mm_P1.27mm",
                   "SSOP-20_3.9x8.7mm_P0.635mm"],
             5.3: ["SOIC-8_5.23x5.23mm_P1.27mm", "SOIC-16_5.3x10.2mm_P1.27mm", "SSOP-20_5.3x7.2mm_P0.65mm"],
             7.5: ["SOIC-16W_7.5x10.3mm_P1.27mm", "SOIC-20W_7.5x12.8mm_P1.27mm", "SOIC-24W_7.5x15.4mm_P1.27mm"],
             4.4: ["TSSOP-8_4.4x3mm_P0.65mm", "TSSOP-14_4.4x5mm_P0.65mm", "TSSOP-20_4.4x6.5mm_P0.65mm"],
             3.0: ["TSSOP-8_3x3mm_P0.65mm", "MSOP-8_3x3mm_P0.65mm", "MSOP-10_3x3mm_P0.5mm"]}


def _pins(s):
    return int(re.search(r"(?:^|-)(\d+)(?=W?[-_ ]|$)", s.split(",")[0].split(" ")[0]).group(1))


def test_a_distributors_body_width_tells_two_lands_apart():
    from ecadlib.altium_export import lands_differ
    for wa, dks in DK_WIDTHS.items():
        for a in dks:
            for wb, kis in KI_WIDTHS.items():
                for b in kis:
                    if _pins(a) != _pins(b):
                        continue
                    if wa != wb:
                        assert lands_differ(a, b) and lands_differ(b, a), (a, b)
    for a, b in (('8-SOIC (0.154", 3.90mm Width)', "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"),
                 ('8-SOIC (0.209", 5.30mm Width)', "Package_SO:SOIC-8_5.23x5.23mm_P1.27mm"),
                 ('16-SOIC (0.295", 7.50mm Width)', "Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm"),
                 ('8-TSSOP (0.173", 4.40mm Width)', "Package_SO:TSSOP-8_4.4x3mm_P0.65mm"),
                 ("SOIC-8 208mil", "SOIC-8_5.23x5.23mm_P1.27mm")):
        assert not lands_differ(a, b) and not lands_differ(b, a), (a, b)


def test_a_body_less_name_is_not_any_width_of_its_family():
    from ecadlib.altium_export import lands_differ
    for a, b in (("SOIC-8", "SOIC-8_5.23x5.23mm_P1.27mm"),       # a bare SOIC is JEDEC 3.9 mm, not 208 mil
                 ("8-SOIC", "SOIC-8_5.23x5.23mm_P1.27mm"),
                 ("SOIC-16W", "SOIC-16W_5.3x10.2mm_P1.27mm"),     # a bare wide SOIC is 7.5 mm
                 ("QFN-20", "Package_DFN_QFN:QFN-20-1EP_4x4mm_P0.5mm_EP2.5x2.5mm"),   # 3, 3.5, 4 and 5 mm QFN-20s
                 ("LQFP-64", "Package_QFP:LQFP-64_10x10mm_P0.5mm"),                  # 7, 10 and 14 mm LQFP-64s
                 ("TSSOP-8", "TSSOP-8_4.4x3mm_P0.65mm")):                           # 3 and 4.4 mm TSSOP-8s
        assert lands_differ(a, b) and lands_differ(b, a), (a, b)
    for a, b in (("SOIC-8", "SOIC-8_3.9x4.9mm_P1.27mm"), ("SO-8", "SOIC-8_3.9x4.9mm_P1.27mm"),
                 ("48-LQFP", "Package_QFP:LQFP-48_7x7mm_P0.5mm"), ("QFP-48", "LQFP-48_7x7mm_P0.5mm"),
                 ("HTSSOP-16", "HTSSOP-16-1EP_4.4x5mm_P0.65mm_EP3.4x5mm"),
                 ("SOIC-16W", "SOIC-16W_7.5x10.3mm_P1.27mm")):                      # one body of that name
        assert not lands_differ(a, b) and not lands_differ(b, a), (a, b)


def test_a_power_package_is_its_own_land():
    from ecadlib.altium_export import lands_differ
    for a in ("PowerPAK SO-8", "PowerPAK_SO-8", "LFPAK56", "PowerDI-5060-8", "8-PowerTDFN", "SO-8FL"):
        for b in ("SOIC-8", "SO-8", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "8-SOIC"):
            assert lands_differ(a, b) and lands_differ(b, a), (a, b)
    assert not lands_differ("PowerPAK SO-8", "PowerPAK_SO-8")


@lite
def test_librarian_holds_a_208_mil_flash_on_the_symbols_150_mil_land(tmp_path):
    lib = _sym_lib(tmp_path, [("W25Q32JVSSIQ", "SOIC-8_3.9x4.9mm_P1.27mm"),
                              ("ISO7741DWR", "SOIC-16_3.9x9.9mm_P1.27mm"),
                              ("SQJ422EP-T1_GE3", "SOIC-8_3.9x4.9mm_P1.27mm")])
    items = _run(tmp_path, HDR + 'U3,1,W25Q32JVSSIQ,"8-SOIC (0.209"", 5.30mm Width)",Winbond,W25Q32JVSSIQ,SPI flash,'
                 'https://example.com/w.pdf\n'
                 'U2,1,ISO7741,"16-SOIC (0.295"", 7.50mm Width)",Texas Instruments,ISO7741DWR,Digital isolator,'
                 'https://example.com/i.pdf\n'
                 "Q1,1,SQJ422EP,PowerPAK SO-8,Vishay,SQJ422EP-T1_GE3,N-MOSFET 40V,https://example.com/q.pdf\n",
                 ["standard"], [lib])
    assert len(items) == 3, list(items)
    for it in items.values():
        assert it["status"] != "ready", (it["name"], it["source"], _holds(it))


# --- R-B2-2: two names no rule reads are one land only when they are one name ---------------------------------------

def test_two_unread_package_names_differ_unless_they_are_one_name():
    from ecadlib.altium_export import lands_differ
    for a, b in (("TO-263", "Package_TO_SOT_SMD:TO-252-2"), ("D2PAK", "DPAK"), ("TO-247", "TO-220-3_Vertical"),
                 ("TO-220", "TO-220F-3_Vertical"), ("SOT-563", "Package_TO_SOT_SMD:SOT-363_SC-70-6"),
                 ("SC-89", "SOT-523"), ("Crystal_SMD_3225-4Pin_3.2x2.5mm", "Crystal_SMD_2520-4Pin_2.5x2.0mm"),
                 ("USB-C", "Micro-USB"), ("JST PH 2-pin", "JST XH 2-pin"), ("LED 3mm", "LED 5mm"),
                 ("TO-220-3_Vertical", "TO-220-3_Horizontal_TabDown")):
        assert lands_differ(a, b) and lands_differ(b, a), (a, b)
    for a, b in (("TO-263", "TO263"), ("SOT-563", "sot563"), ("Package_TO_SOT_SMD:SOT-23", "SOT23"),
                 ("Resistor_SMD:R_0603_1608Metric", "0603"), ("Diode_SMD:D_SMA", "DO-214AC")):
        assert not lands_differ(a, b) and not lands_differ(b, a), (a, b)


@lite
def test_librarian_holds_a_to263_line_on_the_symbols_to252_land(tmp_path):
    from ecadlib import bom_triage as T
    from ecadlib.kicad_index import KiCadIndex
    lib = Path(tmp_path) / "mylib"
    (lib / "mylib.pretty").mkdir(parents=True)
    (lib / "mylib.pretty" / "TO-252-2.kicad_mod").write_text(
        '(footprint "TO-252-2" (layer "F.Cu") (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))\n', encoding="utf-8")
    sym = (Path(__file__).parent / "fixtures" / "e2e" / "kicad" / "kicad-symbols" / "Amplifier_Operational.kicad_symdir"
           / "LM2904.kicad_sym").read_text(encoding="utf-8")
    sym = sym.replace("LM2904", "LM1117DT").replace('(property "Footprint" ""', '(property "Footprint" "mylib:TO-252-2"', 1)
    (lib / "LM1117DT.kicad_sym").write_text(sym, encoding="utf-8")
    rows = T.triage([{"designator": "U1", "qty": "1", "value": "LM1117", "footprint": "TO-263", "mpn": "LM1117DT"}],
                    KiCadIndex([lib]), [], None)
    assert "TO-252" not in str(rows[0].get("local_footprint") or ""), rows[0]


# --- R-B2-5: Altium's IPC-7351 land names are read (family, pitch, body, pins) before comparing --------------------

def test_ipc7351_names_compare_with_kicad_names():
    from ecadlib.altium_export import lands_differ
    for a, b in (("QFP50P900X900X160-48N", "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
                 ("QFP50P900X900X160-48N", "48-LQFP"),
                 ("SOIC127P600X175-8N", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"), ("SOIC127P600X175-8N", "SOIC-8"),
                 ("SOT95P280X145-5N", "Package_TO_SOT_SMD:SOT-23-5"), ("SOT95P237X112-3N", "Package_TO_SOT_SMD:SOT-23"),
                 ("QFN50P300X300X100-17N", "Package_DFN_QFN:VQFN-16-1EP_3x3mm_P0.5mm_EP1.68x1.68mm"),
                 ("QFP50P1200X1200X160-64N", "LQFP-64_10x10mm_P0.5mm")):
        assert not lands_differ(a, b) and not lands_differ(b, a), (a, b)
    for a, b in (("QFP80P900X900X120-32N", "LQFP-48_7x7mm_P0.5mm"),            # 32 pins at 0.8 mm
                 ("QFP50P900X900X160-48N", "LQFP-48_7x7mm_P0.5mm_ThermalVias"),
                 ("QFP50P900X900X120-49N", "LQFP-48_7x7mm_P0.5mm"),             # 49th pin: an exposed pad
                 ("SOIC127P600X175-8N", "SOIC-8_5.23x5.23mm_P1.27mm"),         # 6.0 mm span: the 3.9 mm body
                 ("SOIC127P1030X265-16N", "SOIC-16_3.9x9.9mm_P1.27mm"),        # 10.3 mm span: the 7.5 mm body
                 ("SOT95P280X145-5N", "SOT-23-6"), ("SOT95P237X112-3N", "SOT-23-5"),
                 ("QFN50P300X300X100-17N", "QFN-16-1EP_4x4mm_P0.65mm_EP2.1x2.1mm"),
                 ("QFN50P300X300X100-17N", "SOIC-16_3.9x9.9mm_P1.27mm")):
        assert lands_differ(a, b) and lands_differ(b, a), (a, b)


@lite
def test_stm32_from_an_altium_bom_with_an_ipc_name_is_not_held_for_its_land(tmp_path):
    for k, fp in enumerate(("QFP50P900X900X160-48N", "48-LQFP", "LQFP-48")):      # R-FX10-2 stays ready too
        (tmp_path / str(k)).mkdir()
        items = _run(tmp_path / str(k), HDR + f"U2,1,STM32F103C8T6,{fp},STMicroelectronics,STM32F103C8T6,MCU,"
                     "https://example.com/s.pdf\n", ["kicad", "standard"])
        it = next(iter(items.values()))
        assert it["source"]["footprint"].endswith("LQFP-48_7x7mm_P0.5mm"), (fp, it["source"])
        assert it["status"] == "ready", (fp, it["status"], _holds(it))


# --- R-B2-3: the MPN's chip size is checked against a local land however it is named --------------------------------

def _chip(mpn, src):
    return {"name": "X", "folder": "Components/Resistors", "description": "part",
            "parameters": [{"name": "Manufacturer Part Number", "value": mpn}],
            "symbol": {}, "footprint": {"ref": src.split(":")[-1]}, "source": {"footprint": src}}


def test_release_check_reads_the_chip_size_of_local_land_names():
    from ecadlib import release_check as RC
    for mpn, src in (("RC0603FR-0710KL", "local:mylib:R0402"), ("GRM188R71C104KA01D", "local:mylib:CAP_0402"),
                     ("ERJ-3EKF1001V", "local:mylib:RESC1005X40N"), ("RC0603FR-0710KL", "standard:R_0402"),
                     ("RC0603FR-074K7L", "kicad:Resistor_SMD:R_0402_1005Metric")):
        fails, _, _ = RC.check_item(_chip(mpn, src))
        assert [m for r, m in fails if r == "footprint" and "chip size" in m], (mpn, src, fails)
    for mpn, src in (("RC0603FR-0710KL", "local:mylib:R0603"), ("GRM188R71C104KA01D", "local:mylib:CAP_0603"),
                     ("ERJ-3EKF1001V", "local:mylib:RESC1608X55N"), ("RC0603FR-0710KL", "local:mylib:L_Bourns_SRR1210"),
                     ("RC0603FR-0710KL", "easyeda:C0402"), ("RC0603FR-0710KL", "datasheet:https://x.com/R0402.pdf")):
        fails, _, _ = RC.check_item(_chip(mpn, src))
        assert not [m for r, m in fails if r == "footprint" and "chip size" in m], (mpn, src, fails)


@lite
def test_librarian_holds_0603_mpns_on_local_0402_lands(tmp_path):
    from ecadlib import stdparts as SP
    lib = Path(tmp_path) / "lib3"
    (lib / "mylib.pretty").mkdir(parents=True)
    for n, k in (("R0402", "R_0402_1005Metric"), ("CAP_0402", "C_0402_1005Metric"), ("RESC1005X40N", "R_0402_1005Metric")):
        (lib / "mylib.pretty" / f"{n}.kicad_mod").write_text(SP.build(k)[0].replace(f'"{k}"', f'"{n}"', 1), encoding="utf-8")
    items = _run(tmp_path, HDR + "R1,1,10k,mylib:R0402,Yageo,RC0603FR-0710KL,RES 10k 1%,https://example.com/r.pdf\n"
                 "C1,1,100nF,mylib:CAP_0402,Murata,GRM188R71C104KA01D,CAP 100nF,https://example.com/c.pdf\n"
                 "R2,1,1k,mylib:RESC1005X40N,Panasonic,ERJ-3EKF1001V,RES 1k,https://example.com/e.pdf\n",
                 ["kicad", "standard"], [lib])
    assert len(items) == 3, list(items)
    for it in items.values():
        assert it["status"] == "held" and "chip size" in _holds(it), (it["name"], it["source"], _holds(it))
