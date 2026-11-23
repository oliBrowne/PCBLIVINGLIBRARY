"""Round 2 of the 2026-11-23 review, the free pin checks (ecadlib.pin_text): C2, another package's pin order was still
given to this part when the datasheet spells that package another way ('TO 92', 'to-92', 'TO-92MOD', 'E-Line'), and
C4, a pin the read calls NC passed when the table's row for it was not recognised ('EN 3* Enable', '03', the number
first). Each has the inputs that must hold and the ordinary ones that must stay ready.
Run:  python3 -m tests.test_review1123_round2_pin_checks"""
from ecadlib import librarian as L
from ecadlib import pin_text as PT
from tests.test_review1123_pin_checks import ROW, SO8, SO8_ROW, Run, pdf, state

# ------------------------------------------------------------------ C2: the other package, however it is spelt
TABLE = "Pin Description\nPIN NAME\n1 E\n2 B\n3 C\n"
SPELT = ["TO 92", "to-92", "TO-\n92", "TO- 92", "TO-92MOD", "TO-92 MOD", "TO-92Package", "TO-92MOD/SOT-23", "E-Line",
         "E-LINE", "SPT", "TO–92", "TO-92L", "SOT54", "TO92"]


def family(other):
    """The reviewer's sheet: two devices, the other one's package spelt `other`, and one pin table (the TO-92's)."""
    return {1: f"XYZ3904 / MMXYZ3904 NPN General Purpose Amplifier\n{other} package (XYZ3904)\nSOT-23 (MMXYZ3904)\n" + TABLE,
            2: "Electrical characteristics"}


def test_another_package_is_seen_however_it_is_spelt():
    for other in SPELT:
        text, why = family(other), []
        assert PT.pin_orders(text, "BEC") == ["EBC"], other                 # the bare text: the TO-92's order
        got = PT.pin_orders(text, "BEC", part="MMXYZ3904", package=("SOT-23",), doubts=why)
        assert got == [] and why and "package" in why[0], (other, got, why)


def test_package_words_in_other_spellings():
    words = lambda t: {b for b, _ in PT.package_words(t)}
    assert words("TO 92 package") == {"TO92"} and words("to-92 and sot-23") == {"TO92", "SOT23"}
    assert words("TO-\n92") == {"TO92"} and words("TO- 92") == {"TO92"} and words("SOT 23") == {"SOT23"}
    assert words("TO-92MOD") == {"TO92MOD"} and words("TO-92MOD/SOT-23") == {"TO92MOD", "SOT23"}
    assert words("TO-92Package") == {"TO92"} and words("SOT-23package outline") == {"SOT23"}     # a word run on
    assert words("E-Line package") == {"ELINE"} and words("SPT") == {"SPT"} and words("soic") == {"SOIC"}
    # ranges, sentences and part numbers are no packages
    for plain in ("Storage from -55 TO 150", "TJ, Tstg -55 TO +150", "Tstg -65 TO 92", "up to 220 V", "equal to 92 %",
                  "DO3316P-103", "SC18IS602", "refer to 5 V", "SPTR", "the dip switch so 8 bits"):
        assert words(plain) == set(), (plain, words(plain))
    # a maker's own name for this part's package is this part's package
    assert "SST3" in PT.own_packages(["SOT-23"])[0] and "UMT3" in PT.own_packages(["SOT-323_SC-70"])[0]


def test_the_sot23_part_is_not_given_the_order_when_the_to92_is_spelt_another_way():
    for other in ("TO 92", "TO-92MOD", "E-Line"):
        data = pdf([[ln for ln in family(other)[1].split("\n") if ln], ["Electrical characteristics"]])
        st = state("Package_TO_SOT_SMD:SOT-23")
        assert Run(data).pin_order(st, {}, {}, ROW, "MMXYZ3904", "NPN") is None, (other, st["tried"])
        assert "does not settle the pin order" in st["tried"][-1] and "package" in st["tried"][-1], st["tried"]


def test_a_family_sheets_table_counts_only_when_its_own_heading_names_this_package():
    # the page names SOT-23 once, far above the table, and nothing by the table says which package it is (the other
    # package is on another page): the rule turned round, the lines just above the table must name this package
    far = {1: "MMXYZ3904 NPN amplifier in SOT-23\n" + "\n".join(f"Feature line {i} of the part" for i in range(12)) + "\n" + TABLE,
           2: "XYZ3904 in TO-92: see page 4"}
    why = []
    assert PT.pin_orders(far, "BEC", package=("SOT-23",), doubts=why) == [] and "above" in why[0], why
    near = {1: "MMXYZ3904 NPN amplifier\n" + "\n".join(f"Feature line {i} of the part" for i in range(12))
               + "\nSOT-23 (MMXYZ3904)\n" + TABLE, 2: "XYZ3904 in TO-92: see page 4"}
    assert PT.pin_orders(near, "BEC", package=("SOT-23",)) == ["EBC"]
    titled = {1: "MMXYZ3904 NPN amplifier\nPin Description (SOT-23)\nPIN NAME\n1 B\n2 E\n3 C\n", 2: "XYZ3904 in TO-92"}
    assert PT.pin_orders(titled, "BEC", package=("SOT-23",)) == ["BEC"]
    # the heading names this package and the page another one too: not this package's alone
    both = {1: "MMXYZ3904 NPN amplifier, also in TO-92\n" + "\n".join(f"Feature line {i}" for i in range(12))
               + "\nSOT-23 (MMXYZ3904)\n" + TABLE}
    assert PT.pin_orders(both, "BEC", package=("SOT-23",)) == []


def test_ordinary_one_package_sheets_stay_ready():
    for head in ("SOT-23 (TO-236AB)", "SOT-23Package", "sot-23", "SOT 23", "SOT‑23", "SST3 (SOT-23)", "SOT-23-3",
                 "3-SOT-23", "SOT-23 plastic package, Tstg -55 TO 150, up to 200 mA"):
        one = {1: f"MMXYZ3904 NPN General Purpose Amplifier\n{head}\nPin Description\nPIN NAME\n1 B\n2 E\n3 C\n",
               2: "Electrical characteristics\nJunction temperature -55 to 150\nStorage -65 TO +150"}
        why = []
        assert PT.pin_orders(one, "BEC", part="MMXYZ3904", package=("SOT-23",), doubts=why) == ["BEC"], (head, why)
    # the leaded part of a sheet that names its own package only, in the maker's spelling
    for head in ("TO-92", "TO 92", "to-92", "TO-92 (TO-226AA)"):
        one = {1: f"XYZ3904 NPN Amplifier\n{head} package\nPIN NAME\n1 E\n2 B\n3 C\n"}
        own = ("TO-92_Inline",) if "226" not in head else ("TO-92_Inline", "TO-226AA")
        assert PT.pin_orders(one, "BEC", package=own) == ["EBC"], head
    data = pdf([["MMXYZ3904  NPN General Purpose Amplifier", "SOT-23Package", "Pin Description", "PIN  NAME",
                 "1  B", "2  E", "3  C"], ["Electrical characteristics", "Tstg -55 TO 150"]])
    st = state("Package_TO_SOT_SMD:SOT-23")
    assert Run(data).pin_order(st, {}, {}, ROW, "MMXYZ3904", "NPN") == "BEC", st["tried"]


def test_an_so8_order_needs_the_tables_heading_too():
    st = state("Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    one = pdf([["XYF7400 N-channel MOSFET", "so-8 package"] + SO8])
    assert Run(one).so8_order(st, {}, {}, SO8_ROW, "XYF7400", "NMOS") == "SSSGDDDD", st["tried"]
    fam = pdf([["XYF7400 / XYF7400T N-channel MOSFET", "SO-8 (XYF7400)", "PowerPAK 1212 (XYF7400T): page 3"]
               + [f"Feature line {i} of the part" for i in range(12)] + SO8, ["XYF7400T in TSSOP-8"]])
    assert Run(fam).so8_order(st, {}, {}, SO8_ROW, "XYF7400", "NMOS") is None
    assert "above" in st["tried"][-1], st["tried"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
