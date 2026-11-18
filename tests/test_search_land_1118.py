"""Lands named by a part search's measurements (2026-11-18, hold audit of the 1000-part run): DigiKey files an SMD
aluminium can under 'Radial, Can - SMD', a crystal under '2-SMD, No Lead' and a pin header under no case at all, so
these parts were held with "no footprint" although its other parameters (diameter, base, height; pads and body; row,
positions, pitch) name one generic KiCad land. Plain functions: python3 -m tests.run_all test_search_land_1118"""
from ecadlib import librarian as L, search_land as SL
from tests.fixtures_lite import lite

NAMES = {"CP_Elec_6.3x5.4", "CP_Elec_6.3x5.4_Nichicon", "CP_Elec_6.3x7.7", "CP_Elec_6.3x5.8", "CP_Elec_8x10",
         "CP_Elec_8x10.5", "CP_Radial_D5.0mm_P2.00mm", "CP_Radial_D5.0mm_P2.50mm",
         "Crystal_SMD_3215-2Pin_3.2x1.5mm", "Crystal_SMD_3225-4Pin_3.2x2.5mm", "Crystal_SMD_3225-4Pin_3.2x2.5mm_HandSoldering",
         "Crystal_SMD_HC49-SD", "PinHeader_1x04_P2.54mm_Vertical", "PinHeader_1x04_P2.54mm_Horizontal",
         "PinHeader_2x05_P2.54mm_Vertical", "PinSocket_1x10_P2.54mm_Vertical", "PinHeader_1x02_P2.00mm_Vertical",
         "C_Rect_L7.2mm_W2.5mm_P5.00mm", "C_Rect_L7.2mm_W2.5mm_P5.00mm_FKS2_FKP2_MKS2_MKP2", "C_Rect_L7.0mm_W2.5mm_P5.00mm",
         "C_Rect_L7.2mm_W3.5mm_P5.00mm", "C_Rect_L7.2mm_W2.5mm_P2.50mm"}
CAN = {"size": '0.248" Dia (6.30mm)', "height": '0.303" (7.70mm)', "land": '0.260" L x 0.260" W (6.60mm x 6.60mm)',
       "mounting": "Surface Mount", "case": "Radial, Can - SMD"}
XTAL = {"type": "kHz Crystal (Tuning Fork)", "mounting": "Surface Mount", "case": "2-SMD, No Lead",
        "size": '0.126" L x 0.059" W (3.20mm x 1.50mm)', "height": '0.035" (0.90mm)'}
HDR = {"connector": "Header", "contact": "Male Pin", "pitch": '0.100" (2.54mm)', "positions": "4", "rows": "1",
       "loaded": "All", "shrouding": "Unshrouded", "mounting": "Through Hole", "termination": "Solder"}
FILM = {"mounting": "Through Hole", "case": "Radial", "size": '0.287" L x 0.098" W (7.30mm x 2.50mm)',
        "height": '0.256" (6.50mm)', "termination": "PC Pins", "lead_spacing": '0.197" (5.00mm)'}


def _land(d, kind, desc, **kw):
    return SL.land(SL.dims_text(d), kind, desc, NAMES, **kw)


def test_the_triage_cell_round_trips():
    assert SL.dims(SL.dims_text(CAN)) == CAN
    assert SL.dims(SL.dims_text({"mounting": "Surface Mount, Right Angle; Through Hole", "x": ""})) == \
        {"mounting": "Surface Mount, Right Angle; Through Hole"}
    assert SL.dims("") == {} and SL.dims(None) == {}


def test_an_smd_can_is_named_by_diameter_and_every_height_of_it_must_be_one_land():
    got, _ = _land(CAN, "CP", "CAP ALUM 100UF 20% 35V SMD")
    assert got["pick"] == "CP_Elec_6.3x7.7" and set(got["same"]) == {"CP_Elec_6.3x5.4", "CP_Elec_6.3x5.8"}, got
    got, _ = _land({**CAN, "height": '0.240" (6.10mm)'}, "CP", "CAP ALUM 47UF 20% 35V SMD")
    assert got["pick"] == "CP_Elec_6.3x5.8", got                  # no 6.1 in KiCad: the nearest, if all are one land
    assert "_Nichicon" not in " ".join([got["pick"]] + got["same"])   # a maker's own variant is never a candidate


def test_a_can_without_a_square_base_or_of_another_kind_names_nothing():
    assert _land({**CAN, "land": '0.492" L x 0.300" W (12.50mm x 7.60mm)'}, "CP", "CAP ALUM")[0] is None
    assert _land({k: v for k, v in CAN.items() if k != "land"}, "CP", "CAP ALUM")[0] is None
    assert _land({k: v for k, v in CAN.items() if k != "height"}, "CP", "CAP ALUM")[0] is None
    assert _land({**CAN, "size": '0.492" Dia (12.50mm)', "land": '0.5" L x 0.5" W (12.50mm x 12.50mm)'}, "CP", "CAP ALUM")[0] is None
    assert _land(CAN, "C", "CAP ALUM")[0] is None                 # not a polarized capacitor line
    assert _land(CAN, "CP", "CAP ALUM", several=True)[0] is None  # a clone's measurements are not this part's
    assert _land({**CAN, "case": "2917 (7343 Metric)"}, "CP", "CAP TANT")[0] is None


def test_a_radial_can_is_named_by_diameter_and_lead_spacing():
    d = {"size": '0.197" Dia (5.00mm)', "height": '0.492" (12.50mm)', "lead_spacing": '0.079" (2.00mm)',
         "mounting": "Through Hole", "case": "Radial, Can"}
    assert _land(d, "CP", "CAP ALUM 10UF 50V RADIAL")[0]["pick"] == "CP_Radial_D5.0mm_P2.00mm"
    assert _land({**d, "lead_spacing": '0.138" (3.50mm)'}, "CP", "CAP ALUM")[0] is None
    assert _land({k: v for k, v in d.items() if k != "lead_spacing"}, "CP", "CAP ALUM")[0] is None


def test_a_leadless_crystal_is_named_by_pads_and_body():
    assert _land(XTAL, None, "CRYSTAL 32.7680KHZ 12.5PF SMD")[0]["pick"] == "Crystal_SMD_3215-2Pin_3.2x1.5mm"
    four = {**XTAL, "type": "MHz Crystal", "case": "4-SMD, No Lead", "size": '0.126" L x 0.098" W (3.20mm x 2.50mm)'}
    assert _land(four, None, "CRYSTAL 16.0000MHZ 18PF SMD")[0]["pick"] == "Crystal_SMD_3225-4Pin_3.2x2.5mm"
    hc = {**XTAL, "type": "MHz Crystal", "case": "HC-49/US", "size": '0.449" L x 0.185" W (11.40mm x 4.70mm)'}
    assert _land(hc, None, "CRYSTAL 16.0000MHZ 18PF SMD")[0]["pick"] == "Crystal_SMD_HC49-SD"
    assert _land({**hc, "mounting": "Through Hole"}, None, "CRYSTAL 16.0000MHZ 18PF TH")[0] is None


def test_an_oscillator_a_resonator_or_an_unknown_body_is_not_a_crystal_land():
    assert _land({**XTAL, "type": "XO (Standard)"}, None, "XTAL OSC XO 25.0000MHZ CMOS SMD")[0] is None
    assert _land({**XTAL, "type": "MEMS (Silicon)"}, None, "MEMS OSC XO 32.7680KHZ LVCMOS")[0] is None
    assert _land(XTAL, None, "CERAMIC RES 16.0000MHZ 15PF SMD")[0] is None
    assert _land({**XTAL, "size": '0.079" L x 0.047" W (2.00mm x 1.20mm)'}, None, "CRYSTAL 32.7680KHZ SMD")[0] is None
    assert _land({**XTAL, "case": "4-SOJ, 5.50mm pitch"}, None, "CRYSTAL 32.7680KHZ 12.5PF SMD")[0] is None
    assert _land(XTAL, "C", "CRYSTAL 32.7680KHZ 12.5PF SMD")[0] is None


def test_a_plain_one_row_header_is_named_by_positions_and_pitch():
    assert _land(HDR, None, "CONN HEADER VERT 4POS 2.54MM")[0]["pick"] == "PinHeader_1x04_P2.54mm_Vertical"
    assert _land({**HDR, "connector": "Header, Breakaway"}, None, "")[0]["pick"] == "PinHeader_1x04_P2.54mm_Vertical"
    assert _land({**HDR, "positions": "2", "pitch": '0.079" (2.00mm)'}, None, "")[0]["pick"] == "PinHeader_1x02_P2.00mm_Vertical"
    sock = {"connector": "Receptacle", "contact": "Forked", "positions": "10", "loaded": "All", "rows": "1",
            "pitch": '0.100" (2.54mm)', "mounting": "Through Hole", "termination": "Solder"}
    assert _land(sock, None, "CONN RCPT 10POS 0.1 TIN PCB")[0]["pick"] == "PinSocket_1x10_P2.54mm_Vertical"


def test_any_header_that_is_not_plain_names_nothing():
    for change in ({"mounting": "Through Hole, Right Angle"}, {"mounting": "Surface Mount"}, {"shrouding": "Shrouded - 4 Wall"},
                   {"rows": "2", "positions": "10"}, {"loaded": "Some"}, {"features": "Keying Shroud"},
                   {"termination": "Press-Fit"}, {"contact": "Female Socket"}, {"positions": "50"},
                   {"pitch": '0.156" (3.96mm)'}, {"connector": "Header, Shrouded"}):
        assert _land({**HDR, **change}, None, "CONN HEADER")[0] is None, change
    assert _land({k: v for k, v in HDR.items() if k != "pitch"}, None, "CONN HEADER")[0] is None


def test_a_radial_box_film_capacitor_is_named_by_body_and_lead_spacing():
    got, _ = _land(FILM, "C", "CAP FILM 0.1UF 5% 100VDC RADIAL")
    assert got["pick"] == "C_Rect_L7.2mm_W2.5mm_P5.00mm", got
    assert set(got["same"]) == {"C_Rect_L7.2mm_W2.5mm_P5.00mm_FKS2_FKP2_MKS2_MKP2", "C_Rect_L7.0mm_W2.5mm_P5.00mm"}, got
    assert _land(FILM, "C", "CAP CER 0.1UF 50V RADIAL")[0] is None            # a ceramic disc is another body
    assert _land({**FILM, "lead_spacing": '0.295" (7.50mm)'}, "C", "CAP FILM")[0] is None
    assert _land({**FILM, "mounting": "Surface Mount"}, "C", "CAP FILM")[0] is None


def test_measurements_reach_the_triage_row():
    from ecadlib import part_lookup as PL, providers as P, bom_triage as T
    from ecadlib.kicad_index import KiCadIndex
    specs = PL.specs_from_pairs([("Capacitance", "100 µF"), ("Lead Spacing", "-"), ("Features", "General Purpose"),
                                 ("Size / Dimension", '0.248" Dia (6.30mm)'), ("Height - Seated (Max)", '0.303" (7.70mm)'),
                                 ("Surface Mount Land Size", '0.260" L x 0.260" W (6.60mm x 6.60mm)'),
                                 ("Mounting Type", "Surface Mount"), ("Package / Case", "Radial, Can - SMD")])
    assert specs["dims"] == CAN, specs                             # '-' and a capacitor's Features are not kept
    assert "dims" not in PL.specs_from_pairs([("Capacitance", "1 µF"), ("Features", "-")])
    assert PL.specs_from_pairs([("Connector Type", "Header"), ("Features", "Keying Shroud")])["dims"]["features"] == "Keying Shroud"

    class Src:
        name = "digikey"

        def lookup(self, work):
            r = P.SourceResult("digikey", found=True, mpn="UWT1E101MCL1GS")
            return P.from_part_info(type("I", (), {"error": "", "found": True, "mpn": "UWT1E101MCL1GS", "manufacturer": "Nichicon",
                                                    "url": "", "specs": specs, "offers": [], "description": "CAP ALUM 100UF 20% 25V SMD",
                                                    "datasheet": ""})(), r)
    row = T.triage([{"designator": "C1", "mpn": "UWT1E101MCL1GS"}], KiCadIndex([]), [Src()], None)[0]
    assert SL.dims(row["src_dims"]) == CAN and row["src_dims_from"] == "digikey", row


def _mod(pads):
    return "(footprint \"X\"\n" + "\n".join(
        f'  (pad "{n}" smd roundrect (at {x} 0) (size {w} {h}) (layers "F.Cu" "F.Paste" "F.Mask"))' for n, x, w, h in pads) + "\n)\n"


class _Lib:
    footprints = {n: ["Capacitor_SMD"] for n in NAMES}

    def footprint(self, ref):
        name = str(ref or "").split(":")[-1]
        return f"Capacitor_SMD:{name}" if name in self.footprints else None


@lite
def test_the_librarian_takes_a_measured_land_only_when_every_fitting_kicad_land_is_the_same_copper(monkeypatch):
    lands = {"CP_Elec_6.3x7.7": [("1", -2.7, 3.5, 1.6), ("2", 2.7, 3.5, 1.6)],
             "CP_Elec_6.3x5.4": [("1", -2.8, 3.5, 1.6), ("2", 2.8, 3.5, 1.6)],
             "CP_Elec_6.3x5.8": [("1", -2.7, 3.5, 1.6), ("2", 2.7, 3.5, 1.6)],
             "CP_Elec_8x10": [("1", -3.25, 3.5, 2.5), ("2", 3.25, 3.5, 2.5)],
             "CP_Elec_8x10.5": [("1", -3.7, 4.4, 2.5), ("2", 3.7, 4.4, 2.5)]}
    monkeypatch.setattr(L.K, "load_footprint", lambda ref, http: (_mod(lands[ref.split(":")[-1]]), ref))
    run = L.Run.__new__(L.Run)
    run.http = None
    row = {"src_dims": SL.dims_text(CAN), "src_dims_from": "digikey", "src_description": "CAP ALUM 100UF 20% 25V SMD"}
    st = {"tried": [], "hint_fp": ""}
    assert run.measured_land(st, _Lib(), {}, row, "CP") == "Capacitor_SMD:CP_Elec_6.3x7.7"
    assert "digikey measures" in st["tried"][-1] and "same copper" in st["tried"][-1], st
    big = {**CAN, "size": '0.315" Dia (8.00mm)', "land": '0.327" L x 0.327" W (8.30mm x 8.30mm)', "height": '0.413" (10.50mm)'}
    st = {"tried": [], "hint_fp": ""}                 # KiCad's 8 mm lands differ: even the exact height is not taken
    assert run.measured_land(st, _Lib(), {}, {**row, "src_dims": SL.dims_text(big)}, "CP") is None
    assert "not the same land" in st["tried"][-1], st
    for ln, hint in (({"footprint": "CP_Elec_6.3x5.4"}, ""), ({}, "Capacitor_SMD:CP_Elec_6.3x5.4")):
        st = {"tried": [], "hint_fp": hint}           # the BOM or a symbol names a land: the measurements are not asked
        assert run.measured_land(st, _Lib(), ln, row, "CP") is None and not st["tried"]
    assert run.measured_land({"tried": [], "hint_fp": ""}, _Lib(), {}, {**row, "src_several": "yes"}, "CP") is None
