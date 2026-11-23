"""Standard parts without Claude: when the package is fully set by standard data, build the footprint (and, for the
part kinds whose pinout is standard, the symbol) offline and deterministically. The librarian's `standard` source.

    ecadlib stdparts resolve "SOIC-8 3.9x4.9mm" | "0603" --kind R | --mpn GRM188R71H104KA93D --kind C   what would be built, and why
    ecadlib stdparts list [PATTERN]                    the packages the table knows
    ecadlib stdparts bench [--kicad DIR] [--fixture F] every table entry against KiCad's official footprint
    ecadlib stdparts coverage BOM [BOM ...]            how many BOM lines this source resolves (no network)

Footprints come only from package_specs/standard.json.gz: 631 packages, each with its KiCad name and library. Their
dimensions are the KiCad footprint generator's size definitions (JEDEC / IPC-SM-782 / manufacturer package drawings,
built here with footprint_gen's IPC-7351B equations) or, for the packages KiCad draws by hand (SOD, SMA/SMB/SMC, MELF,
SOT-223, SOT-89, SC-82AB, SOD-882, PowerDI-123, DO-15, DO-34, DO-35, DO-41, DO-201AD, DPAK/D2PAK, TO-220, TO-92), the published recommended land as a pad table. Every entry was built and
compared with kicad-footprints (pads and courtyard within footprint_bench tolerances) before it went into the table;
`bench` repeats that check.

What resolves (first match wins; footprint text = the BOM footprint, the KiCad footprint name, a KiCad symbol's
Footprint property; then the description and value; then the MPN):
  exact KiCad name     "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm", "SOT-23-5", "R_0603_1608Metric" (library prefix optional)
  chip passives        R / C / L / LED / D / fuse by the part's kind and a size code: imperial 01005..2512 ("0603",
                       "C0805", "0603 (1608)"), metric with a marker ("1608Metric", "1608M"), IPC names (RESC1608X55N),
                       or the MPN (Yageo RC/CC, Murata GRM/GCM/GJM/LQG/LQM/LQW/BLM, Samsung CL, KEMET C....C, TDK C<metric>,
                       Panasonic ERJ-, Vishay CRCW/TNPW, Stackpole RMCF, Bourns CR, KOA RK73, Uni-Royal). Two different
                       sizes from two places (footprint 0603, MPN GRM21 = 0805) is a conflict: nothing is built.
  named packages       SOT-23/-5/-6, SOT-323/353/363, SOT-523, SOT-89, SOD-123(F)/323(F)/523/923/128, SMA/SMB/SMC/SMF
                       (DO-214), SOD-882, PowerDI-123, MicroMELF/MiniMELF/MELF, DO-15/DO-34/DO-35/DO-41/DO-201AD (axial), SC-82AB, DPAK/D2PAK (TO-252/263), TO-220 (vertical), TO-92 (inline), and
                       SOIC/SO/TSSOP/MSOP/SSOP/QSOP/QFN/DFN/SON/QFP families from the name (pins, body, pitch, exposed pad,
                       KiCad or LCSC style "QFN-32_L5.0-W5.0-P0.50-EP3.5"). The name must give the body: a bare
                       family name (SOIC-8, SO-14, TSSOP-20, SOIC-16W) never resolves, because several bodies share it;
                       write e.g. "SOIC-8 3.9x4.9mm" or "SO-8 150mil". MSOP-8 (3x3 mm, 0.65 mm, JEDEC MO-187-AA) is the
                       one exception. SOIC/SO without a pitch is 1.27 mm; QFP without "EP" has no exposed pad.
  another KiCad land  a KiCad footprint name the table does not hold (package_specs/kicad_footprint_names.txt.gz:
                       'R_Array_Convex_4x0603', 'TO-92_Wide', 'SO-8_3.9x4.9mm_P1.27mm') builds nothing, and so does a
                       footprint field whose size code sits inside a part number ('SRR1210A', '43045-1210') or that
                       names a multi-pad part ('LED RGB 1210'): its land is not the table's for that size or package.
  ambiguous -> nothing: a name that fits two different footprints (QFN-16 without a body size, LQFP-64 without body and
                       pitch, "SOT-23-3", which KiCad uses for the 1.6 mm MO-178 body and distributors for TO-236,
                       bare "SOT-223" / TSOT-23 / SC-70 / SC-75 / SOT-26 (tab numbering or body differ by vendor),
                       power packages (PowerPAK, LFPAK, SO-8FL ...), TO-220 marked horizontal, a diode in a TO/DPAK
                       package, "SMA" on a part that is not a diode); the next source tries. Uniqueness is judged
                       against every generic name in KiCad's library, not only this table. A description is weaker
                       evidence than a footprint field: there no defaults apply (not even MSOP-8 or the SOIC pitch),
                       and a bare family (SOT-23, TO-220 ...) only counts for transistors and diodes.

Symbols (only when the pinout is standard for the part): R, C, C_Polarized, L, LED and diodes (2-pin packages: pin 1
cathode, as KiCad's footprints), and these MPN families: SOT-23 BJTs (MMBT39xx/2222/2907/4401/4403/5401/5551/A42/A92,
BC807/817/846-860, S8050/S8550: 1=B 2=E 3=C), SOT-23 MOSFETs (2N7002, BSS138/123/84, AO34xx, SI230x, DMG230x,
IRLML: 1=G 2=S 3=D), TO-92 2N39xx/2N2222A/2N440x/2N5401/5551 (E B C), BC327/337/546-558 (C B E), 2N7000 (S G D),
BS170 (D G S), 1117-type LDOs (AMS1117, LM1117, LD1117S ...) in SOT-223-3_TabPin2 (1=GND/ADJ 2=VOUT 3=VIN,
tab = 2), 78xx/79xx and LM317 in TO-220, 78Lxx in TO-92/SOT-89. Other parts get the footprint only and the symbol comes from the next source.
The librarian records `source: standard:<Lib>:<Name>` (footprint) and `standard:<symbol kind>`; it tries this
source after kicad and easyeda and before datasheet, and never replaces a symbol or footprint an earlier source or
the user's own library already filled."""
import argparse, functools, gzip, json, math, re, sys
from pathlib import Path

from . import footprint_gen as F

TABLE = Path(__file__).with_name("package_specs") / "standard.json.gz"
KICAD_NAMES = Path(__file__).with_name("package_specs") / "kicad_footprint_names.txt.gz"   # every kicad-footprints name
IMPERIAL = ("01005", "0201", "0402", "0603", "0805", "1008", "1206", "1210", "1806", "1812", "1825", "2010", "2220",
            "2225", "2512", "2816", "2920", "3640", "4020")
METRIC = {"0402": "01005", "0603": "0201", "1005": "0402", "1608": "0603", "2012": "0805", "2520": "1008", "3216": "1206",
          "3225": "1210", "4516": "1806", "4532": "1812", "4564": "1825", "5025": "2010", "5750": "2220", "5763": "2225",
          "6332": "2512", "7142": "2816", "7451": "2920", "9116": "3640", "9110": "3640", "10251": "4020"}
# 3640: KiCad's C_3640_9110Metric (9.1 x 10 mm); '9116' kept as read before. IMP2MET names it 9110 (the later key)
IMP2MET = {v: k for k, v in METRIC.items()}
CHIP_PREFIX = {"R": "R", "C": "C", "L": "L", "LED": "LED", "D": "D", "F": "Fuse"}
# named packages: normalised text (upper case, no spaces / dashes / underscores) -> table name; diode-only ones need a diode
ALIASES = {
    "SOT23": "SOT-23", "TO236": "SOT-23", "TO236AB": "SOT-23",
    "SOT235": "SOT-23-5", "SOT25": "SOT-23-5", "SOT753": "SOT-23-5",
    "SOT236": "SOT-23-6", "SOT26": None,          # SOT-26 is also sold as SC-74 (1.5 mm body)
    "TSOT235": "TSOT-23-5", "TSOT236": "TSOT-23-6", "TSOT23": None,
    "SOT323": "SOT-323_SC-70", "SC70": None, "SC703": "SOT-323_SC-70",
    "SOT353": "SOT-353_SC-70-5", "SC705": "SOT-353_SC-70-5", "SC88A": "SOT-353_SC-70-5",
    "SOT363": "SOT-363_SC-70-6", "SC706": "SOT-363_SC-70-6", "SC88": "SOT-363_SC-70-6",
    "SOT523": "SOT-523", "SC75": None, "SOT883": "SOT-883",                # SC-75 is SOT-416 or SOT-523
    "SOT223": None, "SOT2233": None, "TO261": None, "TO261AA": None,      # tab = pin 2 or pin 4: the part decides
    "SOT89": "SOT-89-3", "SOT893": "SOT-89-3", "TO243": "SOT-89-3", "TO243AA": "SOT-89-3",
    "DPAK": "TO-252-2", "TO252": "TO-252-2", "TO252AA": "TO-252-2", "TO2522": "TO-252-2", "TO2525": None,
    "D2PAK": "TO-263-2", "TO263": "TO-263-2", "TO263AB": "TO-263-2", "TO2632": "TO-263-2", "TO2635": None,     # 5-lead: KiCad counts 4 or 5
    "TO220": "TO-220-3_Vertical", "TO2203": "TO-220-3_Vertical", "TO220AB": "TO-220-3_Vertical",
    "TO92": "TO-92_Inline", "TO923": "TO-92_Inline", "TO92A": None,
    "SC82AB": "SC-82AB",                          # 4 leads, 2 x 2.1 mm body (SOT-343 is a different land)
    "SC74": "SC-74-6_1.55x2.9mm_P0.95mm", "SC746": "SC-74-6_1.55x2.9mm_P0.95mm",   # JEITA SC-74 = Nexperia SOT457
    "SOT457": "SC-74-6_1.55x2.9mm_P0.95mm",
}
DIODE_ALIASES = {
    # JEITA SC-90 is the flat-lead SOD-323F (SC-76 is the gull-wing SOD-323)
    "SOD123": "D_SOD-123", "SOD123F": "D_SOD-123F", "SOD323": "D_SOD-323", "SC76": "D_SOD-323", "SC90": "D_SOD-323F",
    "SOD323F": "D_SOD-323F", "SOD523": "D_SOD-523", "SC79": "D_SOD-523", "SOD923": "D_SOD-923", "SOD128": "D_SOD-128",
    "SMA": "D_SMA", "DO214AC": "D_SMA", "SMB": "D_SMB", "DO214AA": "D_SMB", "SMC": "D_SMC", "DO214AB": "D_SMC",
    "SMF": "D_SMF", "DO219AB": "D_SMF", "MINIMELF": "D_MiniMELF", "SOD80": "D_MiniMELF", "LL34": "D_MiniMELF",
    "MELF": "D_MELF", "DO213AB": "D_MELF", "LL41": "D_MELF",
    "DO41": "D_DO-41_SOD81_P10.16mm_Horizontal", "DO204AL": "D_DO-41_SOD81_P10.16mm_Horizontal",
    "SOD81": "D_DO-41_SOD81_P10.16mm_Horizontal",       # axial, laid flat with KiCad's 10.16 mm lead bend
    "DO201AD": "D_DO-201AD_P15.24mm_Horizontal", "DO201": "D_DO-201AD_P15.24mm_Horizontal",   # 15.24 mm bend
    "DO34": "D_DO-34_SOD68_P7.62mm_Horizontal", "DO204AG": "D_DO-34_SOD68_P7.62mm_Horizontal",   # axial glass, laid
    "SOD68": "D_DO-34_SOD68_P7.62mm_Horizontal",        # flat with KiCad's 7.62 mm bend (KTY83 2026-10-25: NXP prints
    "DO35": "D_DO-35_SOD27_P7.62mm_Horizontal", "DO204AH": "D_DO-35_SOD27_P7.62mm_Horizontal",   # no hole or bend)
    "SOD27": "D_DO-35_SOD27_P7.62mm_Horizontal",
    # DO-15 (JEDEC DO-204AC, 7.6 x 3.6 mm body): laid flat at KiCad's 12.70 mm bend, the same ~2.5 mm of straight lead
    # each side of the body as DO-41 at 10.16 mm (P6KE6.8A, 2026-11-19)
    "DO15": "D_DO-15_P12.70mm_Horizontal", "DO204AC": "D_DO-15_P12.70mm_Horizontal",
    "SOD882": "D_SOD-882",                              # Nexperia's SOD882 (DFN1006-2): KiCad's land drawn from it
    "MICROMELF": "D_MicroMELF",
}
# Diodes Incorporated's PowerDI-123 (written 'PowerDI®123', 'POWERDI 123'): one outline, two pads, the big one the
# cathode (KiCad's D_PowerDI-123, pad 1, drawn from Diodes' suggested pad layout). Other PowerDI cases are other lands.
POWERDI123 = re.compile(r"(?i)(?<![A-Z0-9])Power[ -]?DI\W{0,2}123(?![0-9])")
AMBIGUOUS = {"SOT233": "KiCad's SOT-23-3 is the 1.6 mm MO-178 body; distributors use SOT-23-3 for TO-236 SOT-23",
             "SOT223": "SOT-223: tab numbered 2 (SOT-223-3_TabPin2, regulators) or 4 (KiCad's SOT-223)",
             "SOT2233": "SOT-223: tab numbered 2 or 4", "TO261": "SOT-223: tab numbered 2 or 4", "TO261AA": "SOT-223: tab numbered 2 or 4",
             "TSOT23": "TSOT-23 without a pin count", "TO92A": "TO-92 variant", "SOT23W": "wide SOT-23",
             **{k: "KiCad has TO-252-2 and TO-252-2_TabPin1 (tab numbered 2 or 1): the part decides"
                for k in ("TO2522", "TO252", "TO252AA", "DPAK")},
             **{k: "KiCad has TO-263-2 and TO-263-2_TabPin1 (tab numbered 2 or 1): the part decides"
                for k in ("TO2632", "TO263", "TO263AB", "D2PAK")}}
# IC families from the name: generic table entries are '<FAM>-<n>[W][-1EP]_<a>x<b>mm_P<p>mm[_EP<x>x<y>mm]'
NAME_RE = re.compile(r"^(?P<fam>[A-Z]+)-(?P<pins>\d+)(?P<w>W)?(?:-(?P<ep>\d)EP)?_(?P<a>[\d.]+)x(?P<b>[\d.]+)mm"
                     r"_P(?P<p>[\d.]+)mm(?:_EP(?P<ea>[\d.]*\d)x(?P<eb>[\d.]*\d)mm)?(?P<rest>_.+)?$")
FAMILIES = ("SOIC", "SO", "SOP", "TSSOP", "HTSSOP", "MSOP", "VSSOP", "SSOP", "QSOP", "TSOP", "QFN", "VQFN", "WQFN", "UQFN",
            "TQFN", "HVQFN", "LQFN", "DFN", "TDFN", "WDFN", "UDFN", "SON", "VSON", "WSON", "USON", "LQFP", "TQFP", "QFP",
            "PQFP", "LFCSP")
NO_LEAD = {"QFN", "VQFN", "WQFN", "UQFN", "TQFN", "HVQFN", "LQFN", "DFN", "TDFN", "WDFN", "UDFN", "SON", "VSON", "WSON", "USON", "LFCSP"}
QUAD_LEADED = {"LQFP", "TQFP", "QFP", "PQFP"}
FREE_RE = re.compile(r"(?<![A-Z0-9])(?P<fam>" + "|".join(sorted(FAMILIES, key=len, reverse=True)) +
                     r")[-_ ]?(?P<pins>\d{1,3})(?P<w>W)?(?:-(?P<ep>\d)EP)?(?![0-9A-Z])", re.I)
POWER_PKG = re.compile(r"Power ?PAK|PowerDI|PowerSO|PowerFLAT|LFPAK|DirectFET|PQFN|\bSO-?8FL|\bWDFN\d|TSDSON|SuperSO|TOLL|\bPDFN", re.I)
MIL_BODY = {"150": 3.9, "208": 5.3, "209": 5.3, "300": 7.5}
# words between underscores count too ('TO-220-3_Horizontal_TabDown': \b sees no edge at '_')
NO_GO = re.compile(r"(?<![A-Za-z])(horizontal|lying|right[- ]angle|bent)(?![a-z])", re.I)
NO_GO_TO92 = re.compile(r"(?<![A-Za-z])(wide|stagger\w*)(?![a-z])|(?<![A-Za-z0-9])W\d+(?:\.\d+)?mm", re.I)   # not inline 1.27 mm
STANDING = re.compile(r"(?<![A-Za-z])(vertical|standing|upright)(?![a-z])", re.I)   # an axial part on end: not laid flat
BEND = re.compile(r"(?<![\d.])P?(\d{1,2}(?:\.\d{1,2})?)\s?mm\b|(?<![A-Z0-9])P(\d{1,2}(?:\.\d{1,2})?)(?![\d.])", re.I)
EP_WORD = re.compile(r"exposed[ _-]?pad|thermal[ _-]?pad|(?<![A-Z0-9])\d?EP(?![A-Z])|PowerPAD|"
                     r"(?<![A-Za-z])E[ _-]?PAD(?![A-Za-z])|(?<![A-Za-z0-9])(?:PP|TP)(?![A-Za-z0-9])", re.I)
# words beside a package name that say it is another land than the plain package of that name (full review
# 2026-11-10: 'SMA FLAT' gave the standard SMA, 'TO-92 Formed Leads' the 1.27 mm inline holes): flat leads
# (SMAF, SOD-123FL), a smaller namesake (Mini SMA), leads formed or kinked to another hole pattern
OTHER_LAND = re.compile(r"(?<![A-Za-z])(flat(?:[ _-]?leads?)?|FL|mini|micro|sub|(?:pre[ -]?)?formed|kinked|"
                        r"lead[ _-]?form\w*)(?![A-Za-z])", re.I)
# a lead count given beside the name: 'SOT-23 (5)', 'SOT-323 5 pins', '6 leads', '5L', '5-pin'
LEAD_COUNT = re.compile(r"\(\s*(\d{1,2})\s*\)|(?<![\d.A-Za-z])(\d{1,2})[ -]?(?:pins?|leads?|ld|L)(?![A-Za-z])", re.I)
# words in a footprint field that name a part with more pads, or another land, on a chip size code
NOT_CHIP = re.compile(r"(?<![A-Za-z])(RGB|arrays?|reverse[ _-]?mount|mouse[ _-]?bite|shunt|kelvin|common[ _-]?mode|"
                      r"choke|crystal|balun|filter|transformer|speaker|buzzer|connector|jack|header|modified)(?![a-z])", re.I)
# words in a DESCRIPTION (free text, so narrower than NOT_CHIP: 'RF choke', 'current sense' and 'EMI filter' bead are
# two-pad chips) that say the part has more than two terminals, whatever chip size its footprint column gives
MULTI_WORD = re.compile(
    r"(?<![A-Za-z])(RGB[WA]?|arrays?|arr|networks?|res[ _-]?nets?|r[ _-]?(?:nets?|packs?)|common[ _-]?mode|CMC|kelvin|"
    r"balun|transformer|feed[ _-]?thr(?:ough|u)|coupled|bifilar|X2Y|quad|dual|triple|pairs?|bussed|isolated|convex|"
    r"concave|dividers?|trimmers?|trim[ _-]?pots?|potentiometers?|addressable|common[ _-]?(?:cathode|anode)|"
    r"(?:T|pi|LC|RC|line)[ _-]?filters?|"
    r"(?:bi|tri|dual|two|three|multi|full|[2-9])[ _-]?colou?rs?(?:ed)?)(?![a-z])"
    r"|(?<![\d.A-Za-z])(?:[3-9]|three|four|five|six|eight)[ _-]?"
    r"(?:terminals?|term|pins?|pads?|leads?|elements?|res(?:istors?)?|caps?|ch(?:annels?)?|wires?)(?![a-z0-9])"
    r"|(?<![\d.A-Za-z])4[ _-]?T(?![a-z0-9])"            # a 4-terminal sense resistor ('5T' is an air coil's turns)
    r"|(?<![\d.A-Za-z])[2-9][ _-]?(?:lines?|LN|circuits?|windings?|in[ _-]?1)(?![a-z0-9])"
    # 8P4R / 4R8P (pins and resistors of an array), 'x4', '4x10k', '4x100nF', '4x0603': a count of elements, where a
    # body size ('2x1.25mm') is two lengths
    r"|(?<![\dA-Za-z.])\d{1,2}[PR]\d{1,2}[RP](?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9.])x[2-9](?![\d.A-Za-z])"
    r"|(?<![\d.A-Za-z])[2-9][ ]?x[ ]?(?=\d+(?:\.\d+)?[ ]?(?:[kMR]|ohm|\u03a9|[pnu\u00b5\u03bc]F|[nu\u00b5\u03bcm]H)(?![a-z])|0[1-9]\d\d(?!\d))"
    # two colours on one LED: 'red/green', 'R+G', 'R/G/B'
    r"|(?<![A-Za-z0-9])(?:red|green|blue|yellow|amber|orange|white|[RGBYW])[ ]?[/+&][ ]?"
    r"(?:red|green|blue|yellow|amber|orange|white|[RGBYW])(?![A-Za-z0-9])", re.I)
# words in a description that name another land than the plain chip of the size code: an LED mounted through a hole
# in the board or shining sideways sits on its own land
DESC_LAND = re.compile(r"(?<![A-Za-z])(reverse[ _-]?(?:mount\w*|gull\w*)|side[ _-]?(?:view\w*|emit\w*|look\w*|fir\w*)|"
                       r"right[ _-]?angled?)(?![a-z])", re.I)
# words that say a capacitor has a + side (tantalum, polymer, aluminium, niobium, electrolytic), in any maker's or
# distributor's wording ('CAP TANT', 'CAP ALUM POLY'); 'non-polarized' / 'unpolarized' / 'bipolar' say the opposite
POLAR_WORD = re.compile(r"(?<![A-Za-z])(tant(?:al\w*|cap)?|Ta|electrolytic\w*|elektrolyt\w*|elect?|elko|lytic|e[ _-]?cap|"
                        r"polymer|poly|alum(?:inum|inium)?|alu|niobium|poscap|os[ _-]?con|sp[ _-]?cap|hybrid|mno2|"
                        r"super[ _-]?cap\w*|ultra[ _-]?cap\w*|EDLC|double[ _-]?layer|polari[sz]ed)(?![a-z])"
                        r"|\u94bd|\u7535\u89e3|\u805a\u5408\u7269", re.I)   # tantalum, electrolytic, polymer in Chinese
NOT_POLAR = re.compile(r"(?<![A-Za-z])(non|un|bi)[ _-]?polar", re.I)


def multi_terminal(*texts):
    """The words in a description that say the part has more than two terminals ('RES ARRAY 4 RES', 'common mode',
    '4-terminal', 'RGB'), or None. Such a part is never a two-pad chip land or a generic two-pin symbol."""
    for t in texts:
        m = MULTI_WORD.search(str(t or ""))
        if m:
            return m.group(0).strip()
    return None


def says_polarized(*texts):
    """A capacitor's description says it has a + side. The MPN prefix list (POLAR_CAP) is only extra evidence: a
    series it does not know is still caught by what the BOM says the part is."""
    said = " ".join(str(t or "") for t in texts)
    return bool(POLAR_WORD.search(said)) and not NOT_POLAR.search(said)


# a land named KiCad's way after a maker or series (L_Wuerth_WE-PD-Typ-LS_1210, R_Shunt_..): its own land, whatever
# chip code it ends in; 'R_0603', 'RES_0603', 'R_SMD_0603' name the size
MAKER_LAND = re.compile(r"^(?:R|C|CP|L|LED|D|FB|Fuse)_(?!(?:SMD|Chip|Small)_)[A-Za-z][^_]*_")
# a KiCad chip name with more after its metric code ('L_0603_1608Metric_Kelvin', '.._1608Metric_typo'): a land KiCad
# names otherwise (the hand-solder / castellated variants) or one it does not have, never the plain chip
CHIP_SUFFIX = re.compile(r"^(?:R|C|CP|L|LED|D|FB|Fuse)_\d{4,5}_\d{4,5}Metric_.", re.I)


def own_land_name(text):
    """A footprint field that names a land of its own rather than a bare chip size, whatever chip code it carries
    (R-FX10-1): a maker's / series land named KiCad's way (L_Bourns_SRR1210, R_Shnt_Vishay_WSK2512, even misspelt), a
    multi-pad or other part (NOT_CHIP), a land variant, or a KiCad chip name with a suffix. Not in the index, such a
    land is held, never built as the chip its code names."""
    n = _strip_lib(text).strip()
    return bool(n and (MAKER_LAND.match(n) or CHIP_SUFFIX.match(n) or NOT_CHIP.search(n) or LAND_VARIANT.search(n)))
# a diode package name (SMA, SMB ...) in an RF connector's or a variant's name: not the diode land
NOT_DIODE = re.compile(r"(?<![A-Za-z])(jack|plug|edge[ _-]?mount|coax\w*|connector|receptacle|vertical|horizontal|"
                       r"modified|bulkhead)(?![a-z])", re.I)
# KiCad land variants (bigger hand-solder pads, vias in the exposed pad, other mask / pull-back): never the plain land
LAND_VARIANT = re.compile(r"(?<![A-Za-z])(Hand[_ -]?Solder(?:ing)?|ThermalVias|PullBack|TopTented|(?:\d+x)?Mask\d[\d.]*x[\d.]+(?:mm)?)(?![A-Za-z])", re.I)


# ---------------------------------------------------------------- the table
@functools.lru_cache(maxsize=1)
def table():
    with gzip.open(TABLE, "rt", encoding="utf-8") as f:
        return json.load(f)


def packages():
    return table()["packages"]


@functools.lru_cache(maxsize=1)
def kicad_names():
    """Every footprint name in kicad-footprints (package_specs/kicad_footprint_names.txt.gz, names only) -> {name:
    library}. A text that is one of them names that footprint's own land (footprint_for). Empty when the file is
    missing."""
    try:
        with gzip.open(KICAD_NAMES, "rt", encoding="utf-8") as f:
            return dict(reversed(ln.strip().split(":", 1)) for ln in f if ":" in ln and not ln.startswith("#"))
    except OSError:
        return {}


def spec(name):
    """Table entry -> footprint_gen spec (a fresh copy with its name)."""
    e = packages()[name]
    s = {k: v for k, v in json.loads(json.dumps(e)).items() if k != "lib"}
    s["name"] = name
    return s


def kicad_ref(name):
    return f"{packages()[name]['lib']}:{name}"


@functools.lru_cache(maxsize=4096)
def build(name, density=None):
    """(kicad_mod text, report) for a table entry."""
    return F.build(spec(name), density)


@functools.lru_cache(maxsize=1)
def _generic():
    """Every generic IC footprint name in KiCad's library (the table's kicad_generic_names, built and checked or
    not), parsed: [(attrs, name)]. Ambiguity is judged against all of them, so a name that fits two KiCad packages
    is ambiguous even when only one of them is in the checked table."""
    out = []
    for n in sorted(set(table().get("kicad_generic_names") or []) | set(packages())):
        m = NAME_RE.match(n)
        if m and m.group("fam") in FAMILIES:
            g = m.groupdict()
            out.append(({"fam": g["fam"], "pins": int(g["pins"]), "w": bool(g["w"]), "ep": bool(g["ep"]),
                         "body": tuple(sorted((float(g["a"]), float(g["b"])))), "pitch": float(g["p"]),
                         "epsize": tuple(sorted((float(g["ea"]), float(g["eb"])))) if g["ea"] else None,
                         "variant": bool(g["rest"])}, n))
    return out


# ---------------------------------------------------------------- name parsing
def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _strip_lib(s):
    s = str(s or "").strip()
    return s.split(":", 1)[1] if re.match(r"^[A-Za-z0-9_.\-]+:[^:]", s) and not re.match(r"^[A-Z]:[\\/]", s) else s


_UNIT_LETTER = r"[VWR]\b|K(?:\b|Ω|ohm)"      # '1206 V', '1210 K', '1210 kΩ': a value; not '0603 Resistor', '0805 Vishay'


CHIP_WORD = {"R", "C", "L", "D", "F", "LED", "CP", "RES", "CAP", "IND", "FUSE", "SMD", "SMT", "RC", "CC", "CR", "MLCC", "CHIP", "EIA"}


# a dash then a value after the code ('RES-0603-10k', 'C0805-10uF', 'R0603-4R7', '0603-1%'): the part's value, not a
# part number's suffix ('FV1206-1', '0603-2Pin')
_VALUE_AFTER = re.compile(r"-\d+(?:[.,]\d+)?\s*(?:[kKMR]\d*|[pnuµ]F|[nuµm]H|%|V|W|ohm|Ω)(?![A-Za-z])")
_CHIP_GLUE = re.compile(r"(?:" + "|".join(sorted(CHIP_WORD, key=len, reverse=True)) + r")-?", re.I)


def _in_part_number(t, a, b):
    """A size code at t[a:b] that is part of a part number or an array, not a chip size: glued to letters other than
    a chip word ('SRR1210A', 'XxL4020', 'CMR-1206S', '0603USB', 'MAPI-2010', 'ASPIAIG-F4020'), to other digits by a
    dash ('43045-1210', 'FV1206-1', '0603-2Pin'), or a count of chips ('4x0603')."""
    before, after = t[:a], t[b:]
    glue = re.search(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*-?$", before)
    if glue and not _CHIP_GLUE.fullmatch(glue.group(0)):
        return True
    if re.match(r"-\d", after) and not _VALUE_AFTER.match(after) or \
            re.match(r"[A-Za-z]", after) and not re.match(r"M(?:etric)?(?![A-Za-z])", after, re.I):
        return True
    return False


# nominal chip body length x width (mm) per imperial size: explicit dimensions in a text decide a size code's reading
CHIP_DIMS = {"01005": (0.4, 0.2), "0201": (0.6, 0.3), "0402": (1.0, 0.5), "0603": (1.6, 0.8), "0805": (2.0, 1.25),
             "1206": (3.2, 1.6), "1210": (3.2, 2.5), "1812": (4.5, 3.2), "2010": (5.0, 2.5), "2512": (6.3, 3.2)}


def _chip_dims(t):
    """The imperial size whose body 'L x W' in mm the text gives ('1.6x0.8', '(1.60mm x 0.80mm)'), or None."""
    for m in re.finditer(r"(?<![\d.])(\d+\.\d+|\d)\s*(?:mm)?\s*[xX×*]\s*(\d+\.\d+|\d)(?![\d.])", t):
        a, b = sorted((float(m.group(1)), float(m.group(2))), reverse=True)
        hit = [s for s, (l, w) in CHIP_DIMS.items() if abs(a - l) <= 0.12 and abs(b - w) <= 0.12]
        if len(hit) == 1:
            return hit[0]
    return None


def chip_size(text, strict=False):
    """Imperial size code from a footprint / description text, or None. Bare 4-digit codes are imperial (the
    industry default); metric needs a marker (1608Metric, 1608M, '0603 (1608)' gives both and they must agree).
    A code inside a part number is no size (_in_part_number). strict (a footprint field): such a code anywhere in the
    text means the text names that part's own land, so no size at all ('L_Coilcraft_0805HQ_2012Metric',
    'R_Shunt_Vishay_WSK2512_6332Metric', 'Anaren_0805_2012Metric-6'); a description keeps its other codes."""
    t = str(text or "")
    found, glued = set(), False
    m = re.search(r"(?:RES|CAP|IND|LED|DIO|FUS)C(\d{4})X\d+", t, re.I)       # IPC-7351 names: RESC1608X55N
    if m and m.group(1) in METRIC:
        found.add(METRIC[m.group(1)])
    for m in re.finditer(r"(?<![0-9])(\d{4,5})\s*(?:_?Metric|M)(?![A-Za-z0-9])", t, re.I):
        if re.match(r"[\s:,(\[-]*\d{4,5}(?![0-9.])", t[m.end():]):
            continue                                                # '0603 Metric 1608': read below
        if m.group(1) in METRIC:
            found.add(METRIC[m.group(1)])
            if _chip_dims(t) and _chip_dims(t) != METRIC[m.group(1)]:     # '0603 Metric (1.6 x 0.8 mm)': the body
                found.add(f"conflict:{m.group(1)} metric, body {_chip_dims(t)}")   # disagrees: held
            glued |= bool(re.match(r"-\d", t[m.end():]))           # 'Metric-6': a 6-pad part on that size
    # a metric / mm word next to the code, not glued to it: '0603 (metric)', 'Metric 0603', 'mm: 0603', '0603 [mm]'.
    # 'mm' counts only in brackets or before a colon ('0603, mm' and 'mm 0603' are unclear); a word between two codes
    # belongs to the one after it ('0603 [Metric 1608]', '0603 size, metric 1608': 1608 is the metric code); body
    # dimensions in the text ('1.6x0.8') that fit the imperial reading decide for it
    dims = _chip_dims(t)
    # an imperial / inch word names its code imperial ('1608 Metric - 0603 Imperial', 'Imperial 0603'): the word right
    # after a code is that code's, else the code right after it takes it (R-FX9-5)
    imp = {}
    for w in re.finditer(r"(?<![A-Za-z])(imperial|inch(?:es)?|EIA)(?![A-Za-z])", t, re.I):
        b = re.search(r"(?<![0-9A-Za-z.])(\d{4,5})[\s(\[:,-]*$", t[:w.start()])
        a = re.match(r"[\s:)\],-]*(\d{4,5})(?![0-9.])", t[w.end():])
        if b and not (a and (re.match(r"\s*:", t[w.end():]) or b.group(1) not in IMPERIAL)):   # 'Imperial: 0603'
            imp[b.start(1)] = b.group(1)
        elif a:
            imp[w.end() + a.start(1)] = a.group(1)
    for at, code in imp.items():
        found.add(code if code in IMPERIAL else f"conflict:{code} imperial?")

    def owner_is_before(m):
        """A metric word between two codes ('1608 Metric, 0603'): it is the code before's when the code after is
        named imperial, or the code before is the only one that can only be metric; else the code after's."""
        b = re.search(r"(?<![0-9A-Za-z.])(\d{4,5})(?:\s*(?:size|code))?[\s(\[:,-]*$", t[:m.start(1)])
        if not b or b.start(1) in imp:
            return False
        if m.start(2) in imp:
            return True
        only = lambda c: c in METRIC and c not in IMPERIAL
        return only(b.group(1)) and not only(m.group(2))
    after = {m.start(1): m for m in re.finditer(
        r"(?<![A-Za-z])(metric|mm)(?:\s*(?:size|code))?[\s:)\],-]*(\d{4,5})(?![0-9.])", t, re.I)
        if (m.group(1).lower() == "metric" or re.match(r"mm\s*:", m.group(0), re.I)) and m.start(2) not in imp
        and not owner_is_before(m)}
    given = {m.start(1) for m in after.values()}                  # metric words a later code took
    before = [m for m in re.finditer(r"(?<![0-9A-Za-z.])(\d{4,5})[\s(\[:,]*(?:size|code)?[\s(\[:,]*(metric|mm)(?![A-Za-z])",
                                     t, re.I)
              if m.start(2) not in after and (m.group(2).lower() == "metric" or (
                  re.search(r"[(\[]\s*$", t[:m.start(2)]) and re.match(r"mm\s*[)\]]", t[m.start(2):], re.I)))]
    near, marks = set(), set()
    for code, at, span in [(m.group(1), m.start(1), m.span(2)) for m in before] + \
            [(m.group(2), m.start(2), m.span(1)) for m in after.values()]:
        marks.add(span)
        if code == dims:                                            # '0603 [mm] 1.6x0.8': the body says imperial
            continue
        if dims and code in METRIC and METRIC[code] != dims:        # '0402 [mm] 1.6x0.8': the body is neither
            found.add(f"conflict:{code} metric, body {dims}")       # reading of the code (R-FX14-5): held
            near.add(at)
            continue
        found.add(METRIC[code] if code in METRIC else f"conflict:{code} metric?")
        near.add(at)
    free = [m.span() for m in re.finditer(r"(?<![A-Za-z])(metric|mm)(?![A-Za-z])", t, re.I)     # a marker no code
            if m.span() not in marks and not re.search(r"(?:\d|_)\s*$", t[:m.start()])]   # takes, not a dimension
    for m in re.finditer(rf"(?<![0-9.])(\d{{4,5}})(?![0-9.])(?!\s*(?:mil|{_UNIT_LETTER}|nF|pF|uF|µF|Ω|ohm|%))", t, re.I):
        code = m.group(1)
        if m.start(1) in near or m.start(1) in imp:
            continue
        w = re.match(r"\s*(?:_?(Metric)|M\b|mm)", t[m.end():], re.I)
        if w and not (w.group(1) and m.end() + w.start(1) in given):
            continue                                                # its own metric marker (read above)
        # unclear: a free marker with no value between it and the code ('10k 0603 1% 100mW metric': not its marker)
        unclear = [w for w in free if not re.search(r"\d", t[min(m.end(), w[1]):max(m.start(), w[0])])]
        if code in IMPERIAL and unclear and code != dims and not _in_part_number(t, m.start(), m.end()):
            found.add(f"conflict:{code} imperial or metric?")
            continue
        if code in IMPERIAL and not re.search(r"(?:RES|CAP|IND|LED|DIO|FUS)C$", t[:m.start()], re.I):
            if _in_part_number(t, m.start(), m.end()):
                glued = True
            else:
                found.add(code)
    if strict and glued:
        return None
    # a bare metric-only code next to an imperial one ('0603 (1005)', '0402 / 1608') must agree with it; alone it
    # is not read (bare codes are imperial), so it can only turn a size into a conflict, never pick one
    bare_metric = {METRIC[m.group(1)] for m in re.finditer(
        rf"(?<![0-9A-Za-z.])(\d{{4,5}})(?![0-9A-Za-z.])(?!\s*(?:mm|mil|{_UNIT_LETTER}|nF|pF|uF|µF|Ω|ohm|%))", t, re.I)
        if m.group(1) in METRIC and m.group(1) not in IMPERIAL}
    if found and bare_metric - found:
        found |= bare_metric
    return found.pop() if len(found) == 1 else ("conflict:" + "/".join(sorted(found)) if found else None)


# MPN -> (kind, imperial size); kind "" = any passive kind (decided by the part)
def _mpn_rules():
    m3 = {"03": "0201", "15": "0402", "18": "0603", "21": "0805", "31": "1206", "32": "1210", "43": "1812", "55": "2220"}
    cl = {"03": "0201", "05": "0402", "10": "0603", "21": "0805", "31": "1206", "32": "1210", "43": "1812", "55": "2220"}
    erj = {"XG": "01005", "1G": "0201", "2": "0402", "3": "0603", "6": "0805", "8": "1206", "14": "1210", "12Z": "2010",
           "12": "1812", "1T": "2512", "P03": "0603", "P06": "0805", "P08": "1206", "PA2": "0402", "PA3": "0603", "PA6": "0805",
           "PA8": "1206"}
    koa = {"1H": "0201", "1E": "0402", "1J": "0603", "2A": "0805", "2B": "1206", "2E": "1210", "2H": "2010", "3A": "2512"}
    imp = "(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)"
    # Samsung RC series: RC<metric size><tolerance><resistance digits> (RC0603F103CS is a 0201)
    src = {"0402": "01005", "0603": "0201", "1005": "0402", "1608": "0603", "2012": "0805", "3216": "1206", "3225": "1210",
           "5025": "2010", "6432": "2512"}
    return [
        (re.compile(r"^RC(0402|0603|1005|1608|2012|3216|3225|5025|6432)[BDFGJK]\d"), "R", src),     # Samsung resistors
        # Yageo resistors: <series><imperial size><tolerance><packing letter>... (RC0603FR-07, RT0603BRD07)
        (re.compile(r"^(?:RC|RT|RL|AC|AF|AT|PE|PT|RV)" + imp + r"[A-Z][A-Z]"), "R", None),
        (re.compile(r"^CC" + imp + r"[A-Z]{2}"), "C", None),                                     # Yageo MLCC
        (re.compile(r"^(?:GRM|GCM|GJM|GCJ|GRT|GCQ|GQM|GMA)(\d\d)"), "C", m3),                   # Murata MLCC
        (re.compile(r"^(?:LQG|LQM|LQW|LQP)(\d\d)"), "L", m3), (re.compile(r"^BLM(\d\d)"), "L", m3),   # Murata L, beads
        (re.compile(r"^CL(\d\d)[A-Z]\d"), "C", cl),                                              # Samsung MLCC
        (re.compile(r"^C(0402|0603|1005|1608|2012|3216|3225|4532|5750)(?:C0G|NP0|X[5-8][A-Z]|JB|CH)"), "C", "metric"),   # TDK
        # KEMET MLCC: C<imperial><C standard | X flexible termination | F ...><capacitance>; C0603X563K1RACTU is an
        # imperial 0603, not TDK's metric 0603 (X followed by a digit is KEMET's capacitance code, not a dielectric)
        (re.compile(r"^C" + imp + r"[CXF]\d{3}[A-Z]"), "C", None),
        (re.compile(r"^ERJ-?(XG|1G|1T|12Z|12|14|PA[2368]|P0[368]|[2368])"), "R", erj),           # Panasonic
        (re.compile(r"^(?:CRCW|TNPW|RCG|RCS)" + imp), "R", None),                                # Vishay
        (re.compile(r"^RMCF" + imp), "R", None), (re.compile(r"^CR" + imp + r"-"), "R", None),   # Stackpole, Bourns
        (re.compile(r"^RK73[HB](1H|1E|1J|2A|2B|2E|2H|3A)"), "R", koa),                           # KOA
        (re.compile(r"^" + imp + r"W[A-Z]{2}[A-Z0-9]"), "R", None),                              # Uni-Royal 0603WAF1002T5E
    ]


MPN_RULES = _mpn_rules()


def mpn_size(mpn):
    """-> (kind, imperial size) or (None, None)."""
    s = re.sub(r"\s+", "", str(mpn or "").upper())
    for rx, kind, conv in MPN_RULES:
        m = rx.match(s)
        if m:
            code = m.group(1)
            size = METRIC.get(code) if conv == "metric" else conv.get(code) if isinstance(conv, dict) else code
            if size:
                return kind, size
    from . import mpn_decode as MD                    # the value decoder's makers too (KYOCERA AVX 0603YC104KAT2A):
    d = MD.decode(s)                                  # it reads the whole part number, size included
    if d and d["kind"] in ("R", "C") and d.get("size"):
        return d["kind"], d["size"]
    return None, None


def _free_attrs(text):
    """IC family attributes from free text (BOM footprint, LCSC package, description)."""
    t = str(text or "")
    dk = re.match(r"\s*(\d{1,3})-(" + "|".join(sorted(FAMILIES, key=len, reverse=True)) + r")\b(.*)$", t, re.I)
    if dk:                                            # a distributor's '8-SOIC (0.154", 3.90mm Width)': pins first,
        leads = re.search(r"(?i)\b(\d{1,3})\s*Leads?\b", dk.group(3))     # the body width in words; '..., 7 Leads':
        if leads and int(leads.group(1)) != int(dk.group(1)):            # a pin removed, not the full land
            return None
        t = f"{dk.group(2)}-{dk.group(1)}{dk.group(3)}"
    m = FREE_RE.search(t)
    if not m:
        return None
    a = {"fam": m.group("fam").upper(), "pins": int(m.group("pins")), "w": bool(m.group("w")), "ep": True if m.group("ep") else None,
         "body": None, "pitch": None, "epsize": None}
    rest = t[m.end():] + " " + t[:m.start()]
    v = re.match(r"-(N?\d+)(?![\d.]|\s*(?:[xX×*]|mm|mil|EP))", t[m.end():], re.I)   # SOIC-8-N7, SOIC-14-16: pins removed
    if v:
        a["variant"] = v.group(0)
    b = (re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)\s*(?:x\s*[\d.]+\s*)?(?:mm)?", rest)
         or re.search(r"(?<![A-Z])L(\d+(?:\.\d+)?)-W(\d+(?:\.\d+)?)", rest))                    # LCSC L5.0-W5.0
    if b:
        a["body"] = tuple(sorted((float(b.group(1)), float(b.group(2)))))
    p = re.search(r"(?<![A-Z])P\s?(\d+(?:\.\d+)?)\s*(?:mm)?(?![\d.])", rest) or re.search(r"(\d\.\d+)\s*mm\s*pitch|pitch\s*(\d\.\d+)", rest, re.I)
    if p:
        a["pitch"] = float(next(g for g in p.groups() if g))
    e = re.search(r"(?<![A-Z])EP\s?(\d+(?:\.\d+)?)(?:\s*[xX×]\s*(\d+(?:\.\d+)?))?", rest)
    if e:
        a["ep"] = True
        a["epsize"] = tuple(sorted((float(e.group(1)), float(e.group(2) or e.group(1)))))
    elif re.search(r"exposed pad|thermal pad|\bEP\b|-EP\b|_EP\b|PowerPAD", rest, re.I) or EP_WORD.search(rest):
        a["ep"] = True                                # ('MSOP-8 (EPAD)', 'MSOP8-PP', 'MSOP-8-TP' too)
    mil = re.search(r"(150|208|209|300)\s*mil", t, re.I)
    if mil and not a["body"]:
        a["body_w"] = MIL_BODY[mil.group(1)]
    wide = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*mm\s*Width\b", t, re.I)
    if wide and not a["body"] and "body_w" not in a:
        a["body_w"] = a["body_w_words"] = float(wide.group(1))   # the body's width: the first size in KiCad's name too
    return a


def _defaults(a):
    """JEDEC defaults where the name leaves one package (ic_package already refused names without a body size)."""
    fam, n = a["fam"], a["pins"]
    if fam == "SO" or (fam == "SOP" and n in (8, 14, 16) and (a.get("body_w") == 3.9 or (a["body"] or (0,))[0] == 3.9)):
        a["fam"] = fam = "SOIC"                           # SO-8, and SOP-8 150 mil (LCSC), are JEDEC SOIC
    if fam == "SOIC" and a["pitch"] is None:
        a["pitch"] = 1.27
    if fam == "MSOP" and not a["body"] and n == 8 and a["pitch"] in (None, 0.65):
        a["body"], a["pitch"] = (3.0, 3.0), 0.65
    if fam not in NO_LEAD and a["ep"] is None:            # leaded packages with a pad name it (HTSSOP, -1EP, PowerPAD)
        a["ep"] = False
    return a


def _close(x, y, tol=0.051):
    return abs(x - y) <= tol


def _candidates(a):
    out = []
    for g, n in _generic():
        if g["fam"] != a["fam"] or g["pins"] != a["pins"]:
            continue
        if a["fam"] == "SOIC" and g["w"] != a["w"] and not a["body"]:
            continue
        if a["w"] and not g["w"] and g["body"][0] < 4.0:   # 'SOIC-8W 3.9x4.9': W and a narrow body contradict
            continue
        if a["body"] and not all(_close(x, y) for x, y in zip(g["body"], a["body"])):
            continue
        if a.get("body_w") and not _close(g["body"][0], a["body_w"]):
            continue
        named = re.search(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm", n)       # '(3.00mm Width)' is the width, not the
        if a.get("body_w_words") and not (named and _close(float(named.group(1)), a["body_w_words"])):   # shorter
            continue                                                         # side: SSOP-8_5.3x3mm is 5.3 mm wide
        if a["pitch"] is not None and not _close(g["pitch"], a["pitch"], 0.011):
            continue
        if a["ep"] is not None and g["ep"] != a["ep"]:
            continue
        if a["epsize"] and not (g["epsize"] and all(_close(x, y) for x, y in zip(g["epsize"], a["epsize"]))):
            continue
        if g["variant"] and n not in packages():      # _Mask / _PullBack ... variants: count only when we can build them
            continue
        out.append(n)
    return out


def _same_land(names):
    """True when every candidate gives the same pads (then the name is not really ambiguous)."""
    from . import footprint_bench as B
    ref = None
    for n in names:
        pads = sorted((p[0], p[1], p[2], p[3], p[4]) for p in B.numbers(build(n)[0])["pads"])
        if ref is None:
            ref = pads
        elif len(pads) != len(ref) or any(p[0] != q[0] or max(abs(x - y) for x, y in zip(p[1:], q[1:])) > 0.01 for p, q in zip(pads, ref)):
            return False
    return True


def ic_package(text, defaults=True):
    """IC package name from free text -> (table name, how) / (None, why) / (None, None) when no family is named.
    defaults=False (descriptions): no JEDEC default body / pitch; the text alone must pick one package."""
    if POWER_PKG.search(str(text or "")):
        return None, f"power package ({POWER_PKG.search(str(text)).group(0)}): not a standard JEDEC outline"
    a = _free_attrs(text)
    if not a:
        return None, None
    if a.get("variant"):
        return None, f"{a['fam']}-{a['pins']}{'W' if a['w'] else ''}{a['variant']}: a variant with pins removed, not the standard package"
    if not defaults and not a["body"]:
        return None, f"{a['fam']}-{a['pins']} in the description without a body size"
    label = f"{a['fam']}-{a['pins']}" + ("W" if a["w"] else "")
    # Never a guess: a wrong footprint skips the datasheet reader. The name must give the body (SO-14 is 3.9 or 5.3 mm,
    # SOIC-16W 10.3 or 12.8 mm long, TDFN-12 3x3 at 0.4 or 0.5 mm) and, with an exposed pad, its size. MSOP-8 is the
    # one family name JEDEC (MO-187-AA) gives a single body.
    if not a["body"] and not a.get("body_w") and not (a["fam"] == "MSOP" and a["pins"] == 8 and not a["ep"]):
        return None, f"{label}: no body size in the name (several bodies share it)"
    if (a["ep"] or a["fam"] in NO_LEAD) and not a["epsize"]:
        return None, f"{label}: no exposed-pad size in the name; give e.g. _EP2.1x2.1mm"
    a = _defaults(a) if defaults else dict(a, fam="SOIC" if a["fam"] == "SO" else a["fam"])
    c = _candidates(a)
    if not c and a["fam"] in NO_LEAD:                     # VQFN/WQFN/...: same land as QFN, height differs
        for fam in ("QFN", "DFN") if a["fam"] not in ("QFN", "DFN") else ():
            c = _candidates(dict(a, fam=fam))
            if c:
                break
    if not c:
        return None, f"{label}: no standard package with those dimensions in the table"
    if len(c) > 1 and not (all(n in packages() for n in c) and _same_land(c)):
        rx = {"body": r"_(\d[\d.]*x\d[\d.]*(?:x\d[\d.]*)?)mm", "pitch": r"_P(\d[\d.]*)mm", "epsize": r"_EP(\d[\d.]*x\d[\d.]*)mm"}
        miss = [k for k, r_ in rx.items()                  # what tells the fitting packages apart, by their names
                if len({(re.search(r_, n) or [None, ""])[1] for n in c}) > 1]
        miss = miss or [k for k in ("body", "pitch", "epsize") if not a.get(k) and not (k == "body" and a.get("body_w"))]
        return None, f"{label}: ambiguous ({len(c)} KiCad packages fit, e.g. {', '.join(sorted(c)[:3])}; give the {'/'.join(miss) or 'full name'})"
    best = sorted(c, key=lambda n: (n not in packages(), len(n)))[0]
    if best not in packages():
        return None, f"{label} -> {best}: not in the checked table (ecadlib's IPC build differs from KiCad's)"
    return best, f"{label} -> {best}"


READ_FAMILIES = {"gullwing": set(FAMILIES) - NO_LEAD - QUAD_LEADED, "qfp": QUAD_LEADED, "qfn": NO_LEAD}


# ds_route's package families (an ordering key's or ordering table's "QFP144") -> the KiCad family names they cover
ROUTE_FAMILIES = {"QFP": QUAD_LEADED, "QFN": {"QFN", "VQFN", "WQFN", "UQFN", "TQFN", "HVQFN", "LQFN"},
                  "SON": {"DFN", "TDFN", "WDFN", "UDFN", "SON", "VSON", "WSON", "USON"}, "SO": {"SOIC", "SO", "SOP"},
                  "SSOP": {"SSOP", "QSOP"}, "TSSOP": {"TSSOP", "HTSSOP"}, "MSOP": {"MSOP", "VSSOP"}, "TSOP": {"TSOP"}}


def _identify_known(read, c, known, miss):
    """identify() for a read that left pitch or body blank, when the PDF's own ordering key or ordering table named
    the package (ds_route: "QFP144"): the KiCad generic names of that family and pin count that fit everything the
    read does give (pitch, body, exposed pad, the family its drawing is titled with) -> (name, how) when exactly one
    land is left and it is in the checked table, else (None, why). The known package only narrows; it never stands
    in for a number the read gives."""
    from . import crosscheck as CC
    m = re.fullmatch(r"([A-Z]+?)(\d+)(?: ([\d.]+)x([\d.]+)mm)?", str(known or ""))
    if not m or m.group(1) not in ROUTE_FAMILIES:
        return None, f"the read has no {', '.join(miss)}"
    fams, pins = ROUTE_FAMILIES[m.group(1)] & READ_FAMILIES.get(read.get("package_type"), set()), int(m.group(2))
    if not fams or (c["pin_count"] is not None and c["pin_count"] != pins):
        return None, f"the read has no {', '.join(miss)}; the PDF's ordering information names {known}, which does not fit the read"
    fam = next((a["fam"] for a in map(_free_attrs, (read.get("name"), read.get("description"))) if a), None)
    if fam in fams:
        fams = {fam}
    fits = lambda k, a, x: CC._close(k, a, (x, x, x), True)
    body = sorted((c["body_length"], c["body_width"]), key=lambda v: v[0]) \
        if c["body_length"] is not None and c["body_width"] is not None else None
    ep = sorted((c["ep_length"], c["ep_width"]), key=lambda v: v[0]) if c["has_ep"] else None
    printed = tuple(sorted((float(m.group(3)), float(m.group(4))))) if m.group(3) else None   # "LK = 80 LQFP (12 mm x 12 mm)"
    if printed and body and not all(fits("body_width", a, x) for a, x in zip(body, printed)):
        return None, f"the read has no {', '.join(miss)}; the PDF's ordering information names {known}, not the read's body"
    names = []
    for g, n in _generic():
        if g["fam"] not in fams or g["pins"] != pins or g["ep"] != c["has_ep"] or g["variant"] and n not in packages():
            continue
        if c["pitch"] is not None and not fits("pitch", c["pitch"], g["pitch"]):
            continue
        if body and not all(fits("body_width", a, x) for a, x in zip(body, g["body"])):
            continue
        if printed and not all(abs(a - x) <= 0.05 for a, x in zip(printed, g["body"])):
            continue
        if ep and not (g["epsize"] and all(fits("ep_width", a, x) for a, x in zip(ep, g["epsize"]))):
            continue
        names.append(n)
    label = f"the read has no {', '.join(miss)}, the PDF's ordering information names {known}"
    if not names:
        return None, f"{label}: no standard package fits"
    if any(n not in packages() for n in names) or len(names) > 1 and not _same_land(names):
        return None, f"{label}: ambiguous ({', '.join(sorted(names)[:3])})"
    c2 = dict(c, pin_count=pins)
    if any(CC.compare(c2, CC.critical(spec(n)), "table")["disagreed"] for n in names):
        return None, f"{label}: fits {', '.join(sorted(names)[:3])}, but the read's other numbers disagree"
    best = sorted(names, key=len)[0]
    return best, f"{label} -> {best}"


def _maker_land(names, maker, c):
    """The table's maker outline of the one generic package the read fits (Analog_LFCSP-16-1EP_4x4mm_P0.65mm_EP2.1x2.1mm
    for LFCSP-16-1EP_4x4mm_P0.65mm_EP2.1x2.1mm), when the part's maker is that outline's (its first word: Analog for
    Analog Devices) and the outline agrees with the whole read; else None. Held-out test 2026-11-13: AD8231's datasheet
    prints no land, KiCad's footprint is ADI's land, and IPC pads from the body missed it by 0.1-0.2 mm."""
    from . import crosscheck as CC
    word = re.split(r"[\s,.]+", str(maker or "").strip())[0].lower()
    if len(names) != 1 or len(word) < 2:
        return None
    hits = [m for m in packages() if m.lower() == f"{word}_{names[0].lower()}"
            and CC.compare(c, CC.critical(spec(m)), "table")["status"] == "agreed"]
    return hits[0] if len(hits) == 1 else None


UNCHECKED_FIT = re.compile(r"but the read's other numbers disagree")   # identify(): a standard
# package fits the read, but not one the table vouches for: KiCad's land for it may be a maker's (librarian holds a
# read with no land of its own then)


def identify(read, known=None, maker=None):
    """A datasheet read's package (a footprint_gen spec) -> (table name, how) when its own numbers name exactly one
    checked package, else (None, why). Pin count, pitch, body and exposed pad pick the KiCad generic names that fit, in
    the family the drawing is titled with when it names one; a fit that is not in the checked table makes it ambiguous
    (as in ic_package). Each table entry left must agree with the whole read, lead span included (crosscheck.compare
    against the table), and so must no other entry with other pads: a maker's own outline the read names (a slightly
    other exposed pad) makes it ambiguous. The read's terminal and land numbers never pick: a misread there cannot
    pick wrong. known: the package the PDF's own ordering key or ordering table names for this part (ds_route,
    "QFP144" or "QFP80 12x12mm"), for a read that left the pitch or body blank (_identify_known)."""
    from . import crosscheck as CC
    fams = READ_FAMILIES.get(read.get("package_type"))
    if not fams:
        return None, f"package type {read.get('package_type')}: no JEDEC family in the table"
    c = CC.critical(read)
    miss = [k for k in ("pin_count", "pitch", "body_length", "body_width") if c.get(k) is None]
    if c["has_ep"] and (c["ep_length"] is None or c["ep_width"] is None):
        miss.append("exposed pad size")
    if miss:
        return _identify_known(read, c, known, miss) if known and set(miss) <= {"pin_count", "pitch", "body_length",
                                                                               "body_width"} \
            else (None, f"the read has no {', '.join(miss)}")
    fits = lambda k, a, x: CC._close(k, a, (x, x, x), True)
    body = sorted((c["body_length"], c["body_width"]), key=lambda v: v[0])
    ep = sorted((c["ep_length"], c["ep_width"]), key=lambda v: v[0]) if c["has_ep"] else None

    def fitting(fs):
        out = []
        for g, n in _generic():
            if g["fam"] not in fs or g["pins"] != c["pin_count"] or g["ep"] != c["has_ep"]:
                continue
            if g["variant"] and n not in packages():
                continue
            if not fits("pitch", c["pitch"], g["pitch"]) or not all(fits("body_width", a, x) for a, x in zip(body, g["body"])):
                continue
            if ep and not (g["epsize"] and all(fits("ep_width", a, x) for a, x in zip(ep, g["epsize"]))):
                continue
            out.append(n)
        return out
    # the family the drawing is titled with (SOIC, TSSOP, VQFN ...) narrows the rivals, as a BOM name does in
    # ic_package; its size never comes from the name. SO and 150 mil SOP may be JEDEC SOIC or KiCad's own SO / SOP
    # land (both stay rivals); a no-lead family the table lacks (VQFN, WSON) has the QFN / DFN land.
    fam = next((a["fam"] for a in map(_free_attrs, (read.get("name"), read.get("description"))) if a), None)
    first = {fam, "SOIC"} if fam == "SO" or (fam == "SOP" and c["pin_count"] in (8, 14, 16) and _close(body[0][0], 3.9, 0.1)) \
        else {fam}
    tries = ([first] + ([{"QFN"}, {"DFN"}] if fam in NO_LEAD else [])) if fam in fams else [fams]
    names = next((r for r in map(fitting, tries) if r), [])
    label = f"{c['pin_count']} pins, pitch {c['pitch'][0]:g}, body {body[0][0]:g} x {body[1][0]:g}" + \
        (f", exposed pad {ep[0][0]:g} x {ep[1][0]:g}" if ep else "")
    if not names:
        return None, f"{label}: no standard package fits"
    if any(n not in packages() for n in names):
        own = _maker_land(names, maker, c)
        if own:                                       # KiCad lands this maker's package its own way, and the table
            return own, f"{label} -> {own} (the maker's own outline of {names[0]})"   # carries it (AD8231: ADI's
        return None, f"{label}: fits a KiCad package that is not in the checked table ({', '.join(sorted(names)[:3])})"
    agree = [n for n in names if CC.compare(c, CC.critical(spec(n)), "table")["status"] == "agreed"]
    if not agree:
        return None, f"{label}: fits {', '.join(sorted(names)[:3])}, but the read's other numbers disagree"
    said = " ".join(str(read.get(k) or "") for k in ("name", "description", "drawing")).upper()
    generic = {n for _, n in _generic()}
    for n, e in packages().items():                   # a maker's or JEITA's own outline (Texas_RHA0040B_VQFN-40,
        tags = [t for t in n.split("_")[:2] if not FREE_RE.fullmatch(t)]                    # JEITA_SOIC-8) is a rival
        if n in names or n in generic or not any(re.search(rf"(?<![A-Z0-9]){re.escape(t.upper())}(?![A-Z0-9])", said)
                                                 for t in tags):                         # when the read names it
            continue
        if e.get("package_type") == read.get("package_type") and e.get("pin_count") == c["pin_count"] \
                and CC.compare(c, CC.critical(spec(n)), "table")["status"] == "agreed":
            agree.append(n)
    if len(agree) > 1 and not _same_land(agree):
        return None, f"{label}: ambiguous ({', '.join(sorted(agree)[:3])})"
    best = sorted(agree, key=len)[0]
    return best, f"{label} -> {best}"


# power packages with a tab that KiCad draws by hand (the table's pad tables): the drawing's family name (or Nexperia's
# SOT outline code) -> table prefix
TAB_FAMILY = re.compile(r"(?<![A-Z0-9])(TO-?252|D-?PAK|SOT-?428|TO-?263|D[2\u00b2]-?PAK|DDPAK|SOT-?404|SOT-?223|TO-?261|"
                        r"LFPAK-?56|LFPAK-?33|SOT-?669|SOT-?1210|SOT-?89|TO-?243(?:AA)?|SC-?62)"
                        r"(?:[-_ ]?(\d)(?!\d))?")
TAB_PREFIX = {"TO252": "TO-252", "DPAK": "TO-252", "SOT428": "TO-252", "TO263": "TO-263", "D2PAK": "TO-263",
              "DDPAK": "TO-263", "SOT404": "TO-263", "SOT223": "SOT-223",
              "TO261": "SOT-223", "LFPAK56": "LFPAK56", "SOT669": "LFPAK56", "LFPAK33": "LFPAK33", "SOT1210": "LFPAK33",
              "SOT89": "SOT-89", "TO243": "SOT-89", "TO243AA": "SOT-89", "SC62": "SOT-89"}
TAB_POS_TOL = 0.06          # a lead's place along the row: TO-252's 2.28 mm is printed 2.3 (e1) on many drawings
TAB_BODY_TOL = 0.3          # the table's body length (KiCad's fab outline) against the read's min..max


def _tab_entry(name):
    """A pad-table entry with one tab -> (tab number, {lead number: y}, lead side sign, body length) or None. The tab
    is the largest pad; the leads are every other pad, all on the other side."""
    e = packages()[name]
    pads = []
    for p in (e.get("pad_table") or {}).get("pads") or []:
        if p.get("number") and p.get("polygon"):        # LFPAK's mounting base: its box
            xs, ys = [q["x"] for q in p["polygon"]], [q["y"] for q in p["polygon"]]
            p = dict(p, x=(min(xs) + max(xs)) / 2, y=(min(ys) + max(ys)) / 2, w=max(xs) - min(xs), h=max(ys) - min(ys))
        if p.get("number") and "w" in p:
            pads.append(p)
    body = (e.get("pad_table") or {}).get("body")
    if len(pads) < 3 or not body:
        return None
    tab = max(pads, key=lambda p: p["w"] * p["h"])
    leads = [p for p in pads if p is not tab]
    xs = [q["x"] for q in tab.get("polygon") or []]
    if xs and leads and all((p["x"] < 0) == (leads[0]["x"] < 0) for p in leads) and \
            (min(xs) <= leads[0]["x"] if leads[0]["x"] < 0 else max(xs) >= leads[0]["x"]):
        # one pad that is a lead and the tab (KiCad's SOT-89-3: the middle lead runs on into the tab under the body):
        # the lead at its place along the row, the tab on the far side
        far = max(xs) if leads[0]["x"] < 0 else min(xs)
        leads.append(dict(tab, x=leads[0]["x"]))
        tab = dict(tab, x=far)
    if not all((p["x"] < 0) == (leads[0]["x"] < 0) for p in leads) or (tab["x"] < 0) == (leads[0]["x"] < 0):
        return None
    return str(tab["number"]), {str(p["number"]): p["y"] for p in leads}, body["y2"] - body["y1"]


def tab_joins(read, pins):
    """The lead number a read's tab takes when the tab has a number of its own but the pin table puts it on exactly one
    lead's net (C3D08060G: 1 CATHODE, 2 ANODE, 3 CATHODE the tab; KiCad's TO-263-2_TabPin1 numbers that tab 1, as
    KiCad's _TabPinN lands do), else None. pins: [(number, name)] from the same read."""
    from .eval.score import norm_name
    tab = read.get("tab") if isinstance(read.get("tab"), dict) else {}
    leads = {str(p.get("number")) for p in read.get("pin_positions") or [] if isinstance(p, dict)}
    names = {str(n): norm_name(s) for n, s in pins or []}
    t = str(tab.get("number"))
    if not tab or t in leads or not names.get(t):
        return None
    same = [n for n in leads if names.get(n) == names[t]]
    return same[0] if len(same) == 1 else None


CUT_NC = re.compile(r"^(?:n\.?\s*c\.?|n/c|no\s*connect(?:ion)?)$", re.I)


def _cut_lead(read, pins, t, ys):
    """The table land's tab number t is a lead the read's part has cut: the read places no lead t, its pin table lists
    t as N.C. and the tab under a number of its own with a net, which is no pad of the land (BD33FC0FP: ROHM TO252-3
    "2 N.C.", FIN GND numbered 4; KiCad's TO-252-2 numbers the tab 2). The land is the same copper, the tab under the
    read's number."""
    tab = read.get("tab") if isinstance(read.get("tab"), dict) else {}
    leads = {str(p.get("number")) for p in read.get("pin_positions") or [] if isinstance(p, dict)}
    names = {str(n): str(s or "").strip() for n, s in pins or []}
    no = str(tab.get("number"))
    return bool(tab) and t != no and t not in ys and t not in leads and no not in ys and no not in leads \
        and bool(CUT_NC.match(names.get(t, "-"))) and bool(names.get(no)) and not CUT_NC.match(names[no])


def tab_renumber(read, name, pins=None):
    """{the land's tab number: the read's} when the checked tab land `name` numbers the tab as a lead the read's part
    has cut (_cut_lead), else {}."""
    e = _tab_entry(name) if name in packages() else None
    if not e or not isinstance(read.get("tab"), dict):
        return {}
    return {e[0]: str(read["tab"]["number"])} if _cut_lead(read, pins, e[0], e[1]) else {}


def sides_fit(read, name):
    """True unless the read lists its pins per side (pin_sides) and the table entry lays its pads out otherwise: a
    QFN-16 read 5/3/5/3 and a same-size 4/4/4/4 land have the same pin numbers, so only the sides tell the pads
    apart (identify compares counts and sizes, not sides)."""
    if not F.listed_sides(read):
        return True
    t = spec(name)
    if t.get("package_type") != "qfn":
        return False
    try:
        return F.side_lists(read) == F.side_lists(t)
    except (KeyError, TypeError, ValueError):
        return False


def spec_renumbered(name, renumber):
    """spec(name) with its pads renumbered ({old: new}, tab_renumber)."""
    s = spec(name)
    for p in (s.get("pad_table") or {}).get("pads") or []:
        if str(p.get("number")) in renumber:
            p["number"] = renumber[str(p["number"])]
    return s


def build_renumbered(name, renumber, density=None):
    """build(name) with its pads renumbered ({old: new}, tab_renumber)."""
    return F.build(spec_renumbered(name, renumber), density) if renumber else build(name, density)


def identify_tab(read, pins=None):
    """A datasheet read of a power package with a tab (DPAK / D2PAK / SOT-223 / LFPAK: gullwing with a tab and pin_positions)
    -> (table name, how) when exactly one of the table's hand-drawn tab lands fits it, else (None, why). It fits when
    the drawing's family name (TO-252, D2PAK, SOT-223 ...) names the entry's family, the tab carries the entry's tab
    number, every lead the read places is one of the entry's leads at its place along the row (TAB_POS_TOL), on the
    side away from the tab, and the body length (along the leads) fits the entry's. A lead the read leaves out is
    only allowed when it carries the tab's number and the name counts it (TO-252-5_TabPin3: five leads by its name,
    the read gives 1 2 4 5 and tab 3). The builder's tab is the exposed tab only (IPC from the lead span); a tab land
    runs under the body, so the checked land of the package the read names is the footprint. pins (the read's pin
    table): a tab numbered on its own but on one lead's net may take that lead's number (tab_joins); a tab numbered
    past a cut N.C. lead fits the land that numbers the tab as that lead, built with the read's number (_cut_lead,
    tab_renumber)."""
    if read.get("package_type") != "gullwing" or not isinstance(read.get("tab"), dict):
        return None, "not a gullwing read with a tab"
    said = " ".join(str(read.get(k) or "") for k in ("name", "description", "drawing")).upper()
    m = TAB_FAMILY.search(said)
    if not m:
        return None, "the read's package name is no DPAK / D2PAK / SOT-223 family"
    prefix, counted = TAB_PREFIX[_norm(m.group(1).replace("\u00b2", "2"))], int(m.group(2)) if m.group(2) else None
    pos = read.get("pin_positions") or []
    try:
        tab_no = str(read["tab"]["number"])
        sides = {p["side"] for p in pos}
        leads = {str(p["number"]): float(p["offset"]) for p in pos}
        body = F.dim(read["body_length"])
    except (KeyError, TypeError, ValueError):
        return None, "the read gives no tab number, lead places or body length"
    if not leads or len(sides) != 1 or sides == {read["tab"].get("side", "R")}:
        return None, "the read's leads are not all on the side away from the tab"
    join = tab_joins(read, pins)
    fits = []
    for name in packages():
        if not name.startswith(prefix):
            continue
        e = _tab_entry(name)
        if not e:
            continue
        t, ys, length = e
        n_leads = counted if counted is not None else len(leads)
        cut = t not in (tab_no, join) and _cut_lead(read, pins, t, ys)
        counts = {n_leads, n_leads - 1} if cut and counted is not None else {n_leads}   # TO252-3: ROHM counts the cut lead
        if counted is not None and tab_no not in leads and len(leads) == counted - 1:
            counts.add(len(leads))                    # the lead the name counts and the read leaves out may be cut: the
        #                                               land without it fits too (TO-263-4, below)
        if t not in (tab_no, join) and not cut or len(ys) not in counts or not set(leads) <= set(ys) or set(ys) - set(leads) - {t}:
            continue
        if any(abs(leads[n] - ys[n]) > TAB_POS_TOL for n in leads):
            continue
        if not body[0] - TAB_BODY_TOL <= length <= body[2] + TAB_BODY_TOL:
            continue
        fits.append(name)
    label = f"{m.group(0)}: tab {tab_no}, leads {' '.join(sorted(leads, key=lambda n: (len(n), n)))}"
    exact = [f for f in fits if set(_tab_entry(f)[1]) == set(leads)]
    if len(fits) > 1 and len(exact) == 1:           # a land with exactly the leads drawn: no pad where no lead is
        fits = exact                                  # (AUIPS7081S, held-out test 2026-11-13: D2PAK-5L drawn with lead 3
        #                                               cut, the tab numbered 3; KiCad's TO-263-4, not TO-263-5_TabPin3's
        #                                               middle-lead pad. A family with no such land keeps the counted one)
    if len(fits) != 1:
        return None, f"{label}: {'no' if not fits else 'more than one'} checked tab land fits" + \
            (f" ({', '.join(sorted(fits))})" if fits else "")
    t = _tab_entry(fits[0])[0]
    if t == join and t != tab_no:
        label += f" (on lead {join}'s net, so numbered {join})"
    elif t != tab_no:
        label += f" (the land's tab {t} is the cut lead {t}, N.C. in the pin table: the tab is numbered {tab_no})"
    return fits[0], f"{label} -> {fits[0]}"


def _other_bend(text, name):
    """Why an axial table land (laid flat at KiCad's bend, 'D_DO-41_SOD81_P10.16mm_Horizontal') is not the one the
    text names: it says the part stands up, or names another lead bend pitch. None when it fits."""
    if STANDING.search(text):
        return f"mounted {STANDING.search(text).group(0).lower()}: the table land is laid flat at one lead bend"
    pitch = float(re.search(r"_P([\d.]+)mm_", name).group(1))
    for m in BEND.finditer(text):
        v = float(m.group(1) or m.group(2))
        if abs(v - pitch) > 0.01:
            return f"a {v:g} mm lead bend: the table land is laid flat at the {pitch:g} mm bend"
    return None


BARE_FAMILY = {"SOT23", "TO220", "TO220AB", "TO263", "TO252", "DPAK", "D2PAK", "TO92"}   # in a description: often the family


def named_package(text, diode=False, weak=False, kind=None):
    """SOT/SOD/DO-214/TO names -> (table name, how) / (None, why) / (None, None). weak (a description): a bare family
    name (SOT-23, TO-220 ...) counts only for a transistor or diode, where it means the 3-lead package."""
    pdi = POWERDI123.search(str(text or ""))
    if pdi and diode and not POWER_PKG.search(POWERDI123.sub(" ", str(text))) and not EP_WORD.search(str(text)) \
            and not OTHER_LAND.search(POWERDI123.sub(" ", str(text))):
        return "D_PowerDI-123", f"{pdi.group(0).strip()} -> D_PowerDI-123 (Diodes Inc. outline, pad 1 = cathode tab)"
    if POWER_PKG.search(str(text or "")):
        return None, f"power package ({POWER_PKG.search(str(text)).group(0)}): not a standard JEDEC outline"
    t = str(text or "").upper()
    for m in re.finditer(r"(?<![A-Z0-9])(SOT|TSOT|SOD|SC|DO|TO|SM[ABCF]|D2PAK|DPAK|MICROMELF|MINIMELF|MELF|LL)[-_ ]?(\d{0,3}[A-Z]{0,2})(?:[-_ ]?(\d)L?)?(?![A-Z0-9]|[-_ ]?\d)", t):
        key = _norm(m.group(0))
        key = re.sub(r"L$", "", key) if re.fullmatch(r"(SOT|TSOT)\d+\dL", key) else key
        if key in AMBIGUOUS:
            return None, f"{m.group(0).strip()}: ambiguous ({AMBIGUOUS[key]})"
        if diode and re.match(r"(TO2[256]|D2?PAK)", key):
            return None, f"{m.group(0).strip()}: a diode in a TO / DPAK package has 2 or 3 leads"
        if weak and key in BARE_FAMILY and kind not in ("Q", "D", "LED"):
            return None, f"{m.group(0).strip()} in the description: the package family, pin count not given"
        name = ALIASES.get(key, "") if key in ALIASES else DIODE_ALIASES.get(key) if diode else None
        if key in ALIASES and name is None:
            return None, f"{m.group(0).strip()}: ambiguous"
        if key in DIODE_ALIASES and not diode and key not in ALIASES:
            return None, f"{m.group(0).strip()}: a diode package name on a part that is not a diode"
        if key in DIODE_ALIASES and key not in ALIASES and not DIODE_ALIASES[key].endswith("_Horizontal") and \
                NOT_DIODE.search(str(text)):                       # (axial names: _other_bend below)
            return None, f"{m.group(0).strip()}: {NOT_DIODE.search(str(text)).group(0).lower()} in the name: not the diode package"
        if name and name.startswith(("TO-220", "TO-92")) and NO_GO.search(str(text)):
            return None, f"{m.group(0).strip()}: horizontal mounting is not the standard footprint"
        if name and name.startswith("TO-92") and NO_GO_TO92.search(str(text)):
            return None, f"{m.group(0).strip()}: a wide or staggered lead form is not the standard 1.27 mm inline footprint"
        if name and name.endswith("_Horizontal") and _other_bend(str(text), name):
            return None, f"{m.group(0).strip()}: {_other_bend(str(text), name)}"
        if name and EP_WORD.search(str(text)):         # 'SOT-23-5-EP': the table's package has no exposed pad
            return None, f"{m.group(0).strip()} with an exposed pad: not the standard package (no pad under the body)"
        rest = str(text)[:m.start()] + " " + str(text)[m.end():]   # the words beside the name
        other = OTHER_LAND.search(rest)
        if name and other:
            return None, f"{m.group(0).strip()} {other.group(0).strip()}: not the plain {name} land (another lead form or size)"
        said = [int(x) for c in LEAD_COUNT.finditer(rest) for x in c.groups() if x]
        have = (packages().get(name) or {}).get("pin_count") if name else None
        if name and have and said and have not in said:
            return None, (f"{m.group(0).strip()} with {said[0]} leads: {name} has {have}; name the package with its "
                          "lead count (SOT-23-5, SOT-353)")
        if name and name in packages():
            return name, f"{m.group(0).strip()} -> {name}"
    return None, None


def footprint_for(texts, kind=None, mpn="", weak=()):
    """texts: footprint-like strings in priority order (BOM footprint, KiCad footprint names); weak: descriptions and
    values, trusted less (no JEDEC default sizes for IC families). kind: R C L LED D F or None.
    -> (table name, how) or (None, why)."""
    pk = packages()
    texts = [str(t) for t in texts if str(t or "").strip()]
    weak = [str(t) for t in weak if str(t or "").strip()]
    low = {n.lower(): n for n in pk}
    for t in texts:                                       # exact KiCad name, library prefix optional
        n = low.get(_strip_lib(t).strip().lower())
        if n and _norm(n) in AMBIGUOUS and _strip_lib(t) == t.strip():
            return None, f"{n}: ambiguous without a library prefix ({AMBIGUOUS[_norm(n)]})"
        if n:
            if NAME_RE.match(n) or not n.startswith(("R_", "C_", "L_", "LED_", "D_", "Fuse_")) or _chip_kind_ok(n, kind):
                return n, f"KiCad name {n}"
    for t in texts:                                       # 'R_0603_1608Metric_Pad0.98x0.95mm_HandSolder', '..._ThermalVias':
        v = LAND_VARIANT.search(t)                        # a land the table does not have; the plain one has other pads
        if v:
            return None, f"{_strip_lib(t).strip()}: the {v.group(1)} land variant is not in the standard table"
    for t in texts:                                       # another KiCad footprint ('R_Array_Convex_4x0603', 'TO-92_Wide',
        n = _strip_lib(t).strip()                         # 'SO-8_3.9x4.9mm_P1.27mm'): its own land, not the chip size
        if n in kicad_names() and n not in pk and (_kicad_own(t) or _norm(n) not in ALIASES and _norm(n) not in DIODE_ALIASES):
            return None, (f"{n}: KiCad's {kicad_names()[n]}:{n} is not in the checked standard table (its land is not "
                          "the table's land for the size or package in its name)")   # (bare 'TO-92' is the package)
    if kind in CHIP_PREFIX:                              # chip passives: size from the texts, then the MPN
        sizes = {}
        for t in texts:                                   # a footprint field naming a multi-pad part or another
            w = NOT_CHIP.search(t)                        # land on a chip size ('LED_RGB_1210', '4x0603 array')
            if w:
                return None, f"{_strip_lib(t).strip()}: names a {w.group(0).lower()}, not a two-pad chip land"
            n = _strip_lib(t).strip()                     # 'L_Wuerth_WE-PD-Typ-LS_1210': a maker's own land named
            if MAKER_LAND.match(n) or CHIP_SUFFIX.match(n):     # KiCad's way (a 12x12 mm inductor), with or without
                size = str(chip_size(t, strict=True) or chip_size(t) or "")   # a chip code it reads (R-FX10-1), not the chip
                size = "" if size.startswith("conflict") else " " + size if size else ""
                return None, f"{n}: names a maker's own land, not the{size} chip"
        for t in texts + weak:
            s = chip_size(t, strict=t in texts)
            if s:
                sizes.setdefault(s, t)
        mk, ms = mpn_size(mpn)
        if ms and (mk == kind or (mk == "C" and kind == "C") or (mk == "L" and kind == "L")):
            sizes.setdefault(ms, f"MPN {mpn}")
        elif ms and mk != kind:
            return None, f"MPN {mpn} looks like a {mk} ({ms}) but the part is a {kind}"
        real = {s for s in sizes if not s.startswith("conflict")}
        if real and all(w.startswith("MPN ") for w in sizes.values()):    # the size is the MPN's alone: a footprint
            for t in texts:                                               # name the reader can't place that still
                seen = set(re.findall(r"(?<![0-9])(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?![0-9])",
                                      _strip_lib(t)))                     # names another chip code ('RF-C0402' on a
                if seen and not seen & real:                              # GRM033 0201 part): which is right is unknown
                    return None, ("size codes disagree: " + ", ".join(f"{s} ({w})" for s, w in sizes.items())
                                  + f", {'/'.join(sorted(seen))} in the footprint name {_strip_lib(t).strip()}")
        if any(s.startswith("conflict") for s in sizes) or len(real) > 1:
            return None, "size codes disagree: " + ", ".join(f"{s} ({w})" for s, w in sizes.items())
        if real:
            s = real.pop()
            n = f"{CHIP_PREFIX[kind]}_{s}_{IMP2MET[s]}Metric"
            if n in pk:
                return n, f"{kind} chip {s} from {sizes[s]}"
            return None, f"{kind} chip {s}: not a standard KiCad size"
    why = None
    for t in texts + weak:
        n, how = named_package(t, diode=kind in ("D", "LED"), weak=t not in texts, kind=kind)
        if n or how:
            return n, how
        n, how = ic_package(t, defaults=t in texts)
        if n or (how and t in texts):
            return n, how + ("" if t in texts else " (from the description)") if how else how
        why = why or how
    return None, why or "no standard package named"


def _chip_kind_ok(name, kind):
    pre = name.split("_")[0]
    return kind is None or CHIP_PREFIX.get(kind) == pre or (kind == "D" and pre == "D")


# ---------------------------------------------------------------- symbols (KiCad .kicad_sym text)
def _pin(num, name, typ, x, y, ang, ln=2.54, hide_name=True, hidden=False):
    return (f'(pin {typ} line (at {x:g} {y:g} {ang}) (length {ln:g}){" hide" if hidden else ""} '
            f'(name "{name}" (effects (font (size 1.27 1.27))))'
            f' (number "{num}" (effects (font (size 1.27 1.27)))))')


def _poly(pts, fill="none", w=0.254):
    return (f"(polyline (pts {' '.join(f'(xy {x:g} {y:g})' for x, y in pts)}) (stroke (width {w:g}) (type default))"
            f" (fill (type {fill})))")


def _rect(x1, y1, x2, y2, fill="none", w=0.254):
    return f"(rectangle (start {x1:g} {y1:g}) (end {x2:g} {y2:g}) (stroke (width {w:g}) (type default)) (fill (type {fill})))"


def _circle(x, y, r, w=0.254):
    return f"(circle (center {x:g} {y:g}) (radius {r:g}) (stroke (width {w:g}) (type default)) (fill (type none)))"


def _arc(s, m, e, w=0.254):
    return f"(arc (start {s[0]:g} {s[1]:g}) (mid {m[0]:g} {m[1]:g}) (end {e[0]:g} {e[1]:g}) (stroke (width {w:g}) (type default)) (fill (type none)))"


def _diode_body(kind):
    """Anode right, cathode (bar) left at x = -1.27: pin 1 K must be the left pin (as KiCad's Device:D)."""
    g = [_poly([(1.27, 1.27), (1.27, -1.27), (-1.27, 0), (1.27, 1.27)], "none"), _poly([(-1.27, 0), (1.27, 0)])]
    bar = {"D": [(-1.27, 1.27), (-1.27, -1.27)],
           "D_Schottky": [(-0.635, 0.762), (-0.635, 1.27), (-1.27, 1.27), (-1.27, -1.27), (-1.905, -1.27), (-1.905, -0.762)],
           "D_Zener": [(-0.762, 1.27), (-1.27, 1.27), (-1.27, -1.27), (-1.778, -1.27)],
           "D_TVS": [(-0.762, 1.27), (-1.27, 1.27), (-1.27, -1.27), (-1.778, -1.27)]}
    g.append(_poly(bar.get(kind, bar["D"])))
    if kind == "LED":
        g += [_poly([(-0.508, -1.524), (0.762, -2.794)]), _poly([(0.762, -2.794), (0.0, -2.794)]), _poly([(0.762, -2.794), (0.762, -2.032)]),
              _poly([(0.762, -1.524), (2.032, -2.794)]), _poly([(2.032, -2.794), (1.27, -2.794)]), _poly([(2.032, -2.794), (2.032, -2.032)])]
    return g


SYMBOLS = {   # kind -> (KiCad-style name, reference, description, graphics, pins [(num, name, type, x, y, angle, length)])
              # pin ends on the 100 mil grid (±5.08 mm), so wires snap to them on Altium's default grid
    "R": ("R", "R", "Resistor", [_rect(-1.016, -2.54, 1.016, 2.54)],
          [("1", "~", "passive", 0, 5.08, 270, 2.54), ("2", "~", "passive", 0, -5.08, 90, 2.54)]),
    "R_US": ("R_US", "R", "Resistor, US symbol", [_poly([(0, 2.54), (0, 2.286), (1.016, 1.905), (-1.016, 1.143), (1.016, 0.381), (-1.016, -0.381),
                                                       (1.016, -1.143), (-1.016, -1.905), (0, -2.286), (0, -2.54)])],
             [("1", "~", "passive", 0, 5.08, 270, 2.54), ("2", "~", "passive", 0, -5.08, 90, 2.54)]),
    "C": ("C", "C", "Unpolarized capacitor", [_poly([(-2.032, 0.762), (2.032, 0.762)], w=0.508), _poly([(-2.032, -0.762), (2.032, -0.762)], w=0.508)],
          [("1", "~", "passive", 0, 5.08, 270, 4.318), ("2", "~", "passive", 0, -5.08, 90, 4.318)]),
    "CP": ("C_Polarized", "C", "Polarized capacitor", [_rect(-2.286, 0.508, 2.286, 1.016), _rect(-2.286, -0.508, 2.286, -1.016, "outline"),
                                                        _poly([(-1.778, 2.286), (-0.762, 2.286)]), _poly([(-1.27, 2.794), (-1.27, 1.778)])],
           [("1", "~", "passive", 0, 5.08, 270, 4.064), ("2", "~", "passive", 0, -5.08, 90, 4.064)]),
    "L": ("L", "L", "Inductor", [_arc((0, 2.54), (0.635, 1.905), (0, 1.27)), _arc((0, 1.27), (0.635, 0.635), (0, 0)),
                                 _arc((0, 0), (0.635, -0.635), (0, -1.27)), _arc((0, -1.27), (0.635, -1.905), (0, -2.54))],
          [("1", "1", "passive", 0, 5.08, 270, 2.54), ("2", "2", "passive", 0, -5.08, 90, 2.54)]),
    "LED": ("LED", "D", "Light emitting diode (pin 1 = cathode)", _diode_body("LED"),
            [("1", "K", "passive", -5.08, 0, 0, 3.81), ("2", "A", "passive", 5.08, 0, 180, 3.81)]),
}
SYMBOLS["FerriteBead"] = ("FerriteBead", "FB", "Ferrite bead",
                          [_poly([(-0.508, 2.286), (2.286, -0.508), (0.508, -2.286), (-2.286, 0.508), (-0.508, 2.286)]),
                           _poly([(0, 2.54), (0, 1.778)]), _poly([(0, -2.54), (0, -1.778)])],
                          [("1", "~", "passive", 0, 5.08, 270, 2.54), ("2", "~", "passive", 0, -5.08, 90, 2.54)])
# a ferrite bead: designator FB, its description, or a bead series' part number (Murata BLM / BLE, TDK MPZ / MMZ,
# Taiyo Yuden BK / FBMH, Laird / Wurth HZ / HI / MI / 742792 / 7427927, Bourns MH / MZ, Vishay ILBB)
FERRITE_MPN = re.compile(r"^(BLM\d|BLE\d|BLA\d|MPZ\d|MMZ\d|MAF\d|BK[PM]?\d{4}|FBMH|HZ\d{4}|HI\d{4}|MI\d{4}|7427\d|"
                         r"74279\d|MH\d{4}|MZ\d{4}|ILBB|FBMJ|BKH)")


def is_ferrite(designator="", description="", mpn=""):
    """A ferrite bead (not an inductor): every designator FB, or its description or part number says so."""
    refs = {r.upper() for r in re.findall(r"[A-Za-z]+(?=\d)", str(designator or ""))}
    return bool((refs and refs <= {"FB"}) or re.search(r"(?i)\bbeads?\b|ferrite[- ]chip|chip[- ]ferrite", str(description or ""))
                or FERRITE_MPN.match(_norm(mpn)))


# fuses, test points and plated mounting holes: KiCad's Device:Fuse / Polyfuse, Connector:TestPoint, Mechanical:MountingHole_Pad
# drawn the same way; only used on a land whose pads are exactly these pins (mechanical_symbol)
SYMBOLS["Fuse"] = ("Fuse", "F", "Fuse", [_rect(-0.762, -2.54, 0.762, 2.54), _poly([(0, 2.54), (0, -2.54)])],
                   [("1", "~", "passive", 0, 5.08, 270, 2.54), ("2", "~", "passive", 0, -5.08, 90, 2.54)])
SYMBOLS["Polyfuse"] = ("Polyfuse", "F", "Resettable fuse, polymeric positive temperature coefficient (PTC)",
                       [_rect(-0.762, -2.54, 0.762, 2.54), _poly([(0, 2.54), (0, -2.54)]),
                        _poly([(-1.524, -2.032), (-1.524, -1.524), (1.524, 1.524)])],
                       [("1", "~", "passive", 0, 5.08, 270, 2.54), ("2", "~", "passive", 0, -5.08, 90, 2.54)])
# thermistors and varistors: two-pin parts on a chip land like a resistor's, but never a resistor (their own symbol and
# part number; a 10k NTC must not become the library's plain 10k resistor). KiCad's Device:Thermistor_NTC / _PTC /
# Varistor, drawn the same way: a resistor body crossed by a bent line
_TH_BODY = [_rect(-1.016, -2.54, 1.016, 2.54), _poly([(-1.905, -3.175), (-1.905, -1.905), (1.905, 1.905)])]
_TWO_V = [("1", "~", "passive", 0, 5.08, 270, 2.54), ("2", "~", "passive", 0, -5.08, 90, 2.54)]
SYMBOLS["Thermistor_NTC"] = ("Thermistor_NTC", "TH", "Temperature dependent resistor, negative temperature coefficient",
                             _TH_BODY, _TWO_V)
SYMBOLS["Thermistor_PTC"] = ("Thermistor_PTC", "TH", "Temperature dependent resistor, positive temperature coefficient",
                             _TH_BODY, _TWO_V)
SYMBOLS["Varistor"] = ("Varistor", "RV", "Voltage dependent resistor", _TH_BODY, _TWO_V)
SENSOR_SYMBOL = {"NTC": "Thermistor_NTC", "PTC": "Thermistor_PTC", "VAR": "Varistor"}
SENSOR_FOLDER = {"NTC": "Thermistors", "PTC": "Thermistors", "TH": "Thermistors", "VAR": "Varistors"}
SYMBOLS["TestPoint"] = ("TestPoint", "TP", "Test point", [_circle(0, 3.302, 0.762)], [("1", "1", "passive", 0, 0, 90, 2.54)])
SYMBOLS["MountingHole_Pad"] = ("MountingHole_Pad", "H", "Mounting hole with a plated pad (pin 1)", [_circle(0, 1.27, 1.27, 0.508)],
                               [("1", "1", "passive", 0, -2.54, 90, 2.54)])
PTC = re.compile(r"(?i)\bPTC\b|poly ?fuse|polyswitch|(?<!non-)(?<!non )(?<!not )(?<!\bnon)resettable|\bPPTC\b|multifuse|"
                 r"\bMF-(?:[A-Z]*SM|R|USM|LSMF)|\b(?:nano|micro|mini)SMD|\b0ZC[A-Z]|"
                 r"\b(?:0402|0603|0805|1206|1210|1812|2016|2920)L\d{3}")     # Littelfuse PolySwitch chip PTCs: 1206L050
SYMBOLS["Crystal"] = ("Crystal", "Y", "Two pin crystal", [_rect(-1.143, 1.905, 1.143, -1.905), _poly([(-1.905, 1.27), (-1.905, -1.27)]),
                                                         _poly([(1.905, 1.27), (1.905, -1.27)])],
                      [("1", "~", "passive", -5.08, 0, 0, 3.175), ("2", "~", "passive", 5.08, 0, 180, 3.175)])
# fiducials and unplated mounting holes: KiCad's Mechanical:Fiducial / Mechanical:MountingHole have no pins (nothing
# connects to them); only used on a KiCad Fiducial / MountingHole land with no numbered pad (mechanical_symbol)
SYMBOLS["Fiducial"] = ("Fiducial", "FID", "Fiducial marker", [_circle(0, 0, 1.27, 0.508)], [])
SYMBOLS["MountingHole"] = ("MountingHole", "H", "Mounting hole, not connected", [_circle(0, 0, 1.27, 0.508)], [])
PINLESS = ("Fiducial", "MountingHole")          # symbol keys with no pins: the release check takes them without a pin
MECHANICAL = {"TestPoint": ("TP",), "MountingHole": ("H", "MH"), "Fuse": ("F",), "Crystal": ("Y", "X", "XTAL", "XT"),   # KiCad
              "Fiducial": ("FID", "FD", "FM")}
                                                                                                    # library -> designators


def mechanical_symbol(fp_source, designator, pads, text=""):
    """(symbol key, why) for a fuse, test point, mounting hole or 2-pad crystal built on one of KiCad's own lands of that library
    (fp_source 'kicad:TestPoint:TestPoint_Pad_D1.0mm', 'standard:Fuse:Fuse_1206_3216Metric'), or (None, why). Every
    designator of the line must be of that kind (TP, H / MH, F, FID) and the land's numbered copper pads (pads: their set)
    must be exactly the symbol's pins: a test point one pad 1; a plated mounting hole pad 1; a fuse 1 and 2; an
    unplated mounting hole and a fiducial none (their symbols have no pins, as KiCad's Mechanical: ones).
    text: the line's value, description and MPN: a PTC / resettable fuse gets the Polyfuse symbol."""
    parts = str(fp_source or "").split(":")
    if len(parts) != 3 or parts[0] not in ("kicad", "standard"):
        return None, "the land is not one of KiCad's own (no library to tell its kind)"
    lib = parts[1]
    row = re.search(r"_1x(\d{2})(?:-1MP)?(?:_|$)", parts[2])
    conn_refs = {"J", "P", "X", "CN", "CON", "XS", "XP", "TB", "USB"}
    refs = {re.match(r"[A-Z]+", r).group(0) for r in re.findall(r"[A-Za-z]+(?=\d)", str(designator or "").upper())}
    if lib == "Connector_USB" and re.match(r"USB_Micro-B_", parts[2]):
        if not refs or not refs <= conn_refs:
            return None, f"no standard symbol: designator {designator or '(none)'} on a {lib} land"
        if set(pads) != {"1", "2", "3", "4", "5", "SH"}:
            return None, f"USB Micro-B land with pads {sorted(pads)}"
        return "USB_Micro-B_Receptacle", "USB Micro-B receptacle, contacts 1-5 as the USB specification numbers them, shell SH"
    if lib.startswith(("Connector", "TerminalBlock")) and row and not re.search(r"(?i)MountingPin|_MP|Shield", parts[2]):
        n = int(row.group(1))                             # one row of n pins, KiCad's own land numbered 1..n: the
        mp = bool(re.search(r"-1MP(?:_|$)", parts[2]))    # generic single-row connector symbol (and the land's
        if not refs or not refs <= conn_refs:             # mounting pads as one MP pin)
            return None, f"no standard symbol: designator {designator or '(none)'} on a {lib} land"
        if set(pads) != {str(i) for i in range(1, n + 1)} | ({"MP"} if mp else set()) or not 1 <= n <= 40:
            return None, f"connector land 1x{n:02d} with pads {sorted(pads)}"
        return (f"Conn_01x{n:02d}" + ("_MountingPin" if mp else ""),
                f"single-row connector, pins 1-{n} as the land numbers them" + (", mounting pads MP" if mp else ""))
    want = MECHANICAL.get(lib)
    refs = {re.match(r"[A-Z]+", r).group(0) for r in re.findall(r"[A-Za-z]+(?=\d)", str(designator or "").upper())}
    if not want or not refs or not refs <= set(want):
        return None, f"no standard symbol: designator {designator or '(none)'} on a {lib} land"
    pads = set(pads)
    if lib == "Crystal":                              # KiCad's 2-pad crystal lands (4-pad ones have other pins)
        return ("Crystal", "crystal, 2 pins") if pads == {"1", "2"} else (None, f"crystal land with pads {sorted(pads)}")
    if lib == "Fiducial":                             # KiCad's fiducials: one copper dot with no pad number
        return ("Fiducial", "fiducial, no pad number: a symbol with no pins") if not pads else \
            (None, f"fiducial land with numbered pads {sorted(pads)}")
    if lib == "TestPoint":
        return ("TestPoint", "test point, one pad") if pads == {"1"} else (None, f"test point land with pads {sorted(pads)}")
    if lib == "MountingHole":
        if not pads:                                      # an unplated hole: nothing connects, so KiCad's pinless
            return "MountingHole", "mounting hole, no pad: a symbol with no pins"   # Mechanical:MountingHole
        return ("MountingHole_Pad", "mounting hole, plated pad 1") if pads == {"1"} else \
            (None, f"mounting hole land with pads {sorted(pads)}")
    if pads != {"1", "2"}:
        return None, f"fuse land with pads {sorted(pads)}"
    return ("Polyfuse", "resettable (PTC) fuse, 2 pins") if PTC.search(str(text)) else ("Fuse", "fuse, 2 pins")


for _k, _d in (("D", "Diode"), ("D_Schottky", "Schottky diode"), ("D_Zener", "Zener diode"), ("D_TVS", "TVS diode (unidirectional)")):
    SYMBOLS[_k] = (_k, "D", f"{_d} (pin 1 = cathode)", _diode_body(_k), [("1", "K", "passive", -5.08, 0, 0, 3.81), ("2", "A", "passive", 5.08, 0, 180, 3.81)])

# a TVS diode that conducts both ways (SMBJ24CA): two triangles tip to tip on one bar with bent ends, no cathode; pins
# A1 / A2 like KiCad's Device:D_TVS, either way round on the land
SYMBOLS["D_TVS_Bidirectional"] = (
    "D_TVS_Bidirectional", "D", "TVS diode, bidirectional (pins A1 / A2)",
    [_poly([(-2.54, 1.27), (-2.54, -1.27), (0, 0), (-2.54, 1.27)], "none"),
     _poly([(2.54, 1.27), (2.54, -1.27), (0, 0), (2.54, 1.27)], "none"),
     _poly([(0.508, 1.27), (0, 1.27), (0, -1.27), (-0.508, -1.27)]), _poly([(-2.54, 0), (2.54, 0)])],
    [("1", "A1", "passive", -5.08, 0, 0, 2.54), ("2", "A2", "passive", 5.08, 0, 180, 2.54)])

BJT = lambda pnp: [_circle(1.27, 0, 2.8194), _poly([(0.635, 1.905), (0.635, -1.905)], w=0.508), _poly([(0.635, 0.635), (2.54, 2.54)]),
                   _poly([(0.635, -0.635), (2.54, -2.54)]), _poly([(-2.54, 0), (0.635, 0)])] + (
    [_poly([(2.286, -1.778), (1.778, -2.286), (1.27, -1.27), (2.286, -1.778)], "outline")] if pnp else
    [_poly([(1.524, -2.286), (2.286, -1.524), (2.54, -2.54), (1.524, -2.286)], "outline")])
FET = lambda pch: [_circle(1.27, 0, 2.8194), _poly([(0.254, 1.905), (0.254, -1.905)]), _poly([(0.762, 2.286), (0.762, 1.27)], w=0.508),
                   _poly([(0.762, 0.508), (0.762, -0.508)], w=0.508), _poly([(0.762, -1.27), (0.762, -2.286)], w=0.508),
                   _poly([(0.762, 1.778), (2.54, 1.778), (2.54, 2.54)]), _poly([(0.762, -1.778), (2.54, -1.778), (2.54, -2.54)]),
                   _poly([(0.762, 0), (2.54, 0), (2.54, -1.778)]), _poly([(-2.54, 0), (0.254, 0)])] + (
    [_poly([(2.286, 0), (1.27, 0.381), (1.27, -0.381), (2.286, 0)], "outline")] if pch else
    [_poly([(1.016, 0), (2.032, 0.381), (2.032, -0.381), (1.016, 0)], "outline")])


def _three(kind, order):
    """3-pin discrete: order = pin names for pins 1, 2, 3 (B/E/C or G/S/D)."""
    pos = {"B": (-5.08, 0, 0), "G": (-5.08, 0, 0), "C": (2.54, 5.08, 270), "D": (2.54, 5.08, 270), "E": (2.54, -5.08, 90),
           "S": (2.54, -5.08, 90)}
    pins = [(str(i + 1), nm, "input" if nm in "BG" else "passive", *pos[nm], 2.54) for i, nm in enumerate(order)]
    g = BJT(kind == "PNP") if kind in ("NPN", "PNP") else FET(kind == "PMOS")
    desc = {"NPN": "NPN transistor", "PNP": "PNP transistor", "NMOS": "N-channel MOSFET", "PMOS": "P-channel MOSFET"}[kind]
    ref = "Q"
    return (f"Q_{kind}_{''.join(order)}", ref, f"{desc}, pins {', '.join(f'{i + 1}={n}' for i, n in enumerate(order))}", g, pins)


SO8_FET = "SSSGDDDD"      # a single MOSFET in an SO-8: pins 1-3 source, 4 gate, 5-8 drain (the industry's one order)


def so8_fet_orders():
    """The one order a single SO-8 MOSFET is built with, and the others a datasheet could print instead (the gate on
    another of pins 1-4, or drain and source sides swapped): a pin check must agree with the first and none of these."""
    alts = []
    for g in range(4):
        side = ["S"] * 4
        side[g] = "G"
        alts += ["".join(side) + "DDDD", "DDDD" + "".join(side)]
    return SO8_FET, [o for o in alts if o != SO8_FET]


def _eight(kind, order):
    """A single MOSFET with its terminals on several pins (SO-8: 'SSSGDDDD'): the 3-pin MOSFET drawing, each terminal's
    first pin drawn and the others stacked on it, hidden (as KiCad's own Q_NMOS_SSSGDDDD-style symbols)."""
    pos = {"G": (-5.08, 0, 0), "D": (2.54, 5.08, 270), "S": (2.54, -5.08, 90)}
    seen, pins = set(), []
    for i, nm in enumerate(order):
        pins.append((str(i + 1), nm, "input" if nm == "G" else "passive", *pos[nm], 2.54, True, nm in seen))
        seen.add(nm)
    desc = {"NMOS": "N-channel MOSFET", "PMOS": "P-channel MOSFET"}[kind]
    return (f"Q_{kind}_{order}", "Q", f"{desc}, pins {', '.join(f'{i + 1}={n}' for i, n in enumerate(order))}",
            FET(kind == "PMOS"), pins)


def _diode3(kind, order):
    """A single diode in a three-lead package (SOT-23, SC-70): order = the roles of pins 1, 2, 3, 'A' anode, 'K'
    cathode, 'N' not connected ('ANK': BAS16, BAT54). The diode as the 2-pin symbol draws it (cathode left), and the
    lead with no connection as a no-connect pin below the body, so every pad of the land has its pin."""
    if sorted(order) != ["A", "K", "N"]:
        raise KeyError(f"not a single diode's pin order: {order}")
    g = _diode_body(kind if kind in ("D", "D_Schottky", "D_Zener") else "D")
    at = {"K": ("K", "passive", -5.08, 0, 0, 3.81), "A": ("A", "passive", 5.08, 0, 180, 3.81),
          "N": ("NC", "no_connect", 0, -5.08, 90, 2.54)}
    pins = [(str(i + 1), *at[r]) for i, r in enumerate(order)]
    what = {"D": "Diode", "D_Schottky": "Schottky diode", "D_Zener": "Zener diode"}.get(kind, "Diode")
    roles = {"A": "anode", "K": "cathode", "N": "not connected"}
    return (f"{kind if kind in ('D', 'D_Schottky', 'D_Zener') else 'D'}_{order}", "D",
            f"{what}, three leads: {', '.join(f'{i + 1} = {roles[r]}' for i, r in enumerate(order))}", g, pins)


def _regulator(order, adj_names):
    """3-pin linear regulator box; order = names for pins 1..3 (VI, VO, GND or ADJ); tab = the VO pin's number."""
    side = {"VI": (-7.62, 0, 0), "VO": (7.62, 0, 180), "GND": (0, -7.62, 90), "ADJ": (0, -7.62, 90)}
    typ = {"VI": "power_in", "VO": "power_out", "GND": "power_in", "ADJ": "input"}
    pins = [(str(i + 1), nm, typ[nm], *side[nm], 2.54) for i, nm in enumerate(order)]
    return (f"Regulator_Linear_{'_'.join(order)}", "U", f"Linear regulator, pins {', '.join(f'{i + 1}={n}' for i, n in enumerate(order))}",
            [_rect(-5.08, 2.54, 5.08, -5.08, "background")], pins)


# MPN family -> (allowed packages (table names), symbol builder args)
FAMILY = [
    (r"^(MMBT(3904|2222A?|4401|5551|A42|A06|A05|2369)|BC8(17|18|46|47|48|49|50)|BCW3[123]|S8050|MMBT8050)", ("SOT-23",), ("NPN", "BEC")),
    (r"^(MMBT(3906|2907A?|4403|5401|A92|A56|A55)|BC8(07|08|56|57|58|59|60)|S8550|MMBT8550)", ("SOT-23",), ("PNP", "BEC")),
    (r"^(2N7002|BSS138|BSS123|AO340[0246]|AO341[68]|AO3422|SI230[24]|SI2312|DMG230[2]|IRLML(2502|6344|6346|0030|0040|0060|2030|2060|6244))", ("SOT-23",), ("NMOS", "GSD")),
    (r"^(BSS84|AO340[17]|AO341[359]|AO3423|SI230[15]|SI2307|DMG230[57]|IRLML(6402|6401|2244|2246|5103|5203|9301|9303))", ("SOT-23",), ("PMOS", "GSD")),
    (r"^(2N3904|2N3906|2N2222A?|2N2907A?|2N440[13]|2N5401|2N5551)", ("TO-92_Inline",), ("NPN|PNP", "EBC")),
    (r"^(BC3[23]7|BC54[6-9]|BC55[6-9]|BC550|BC560)", ("TO-92_Inline",), ("NPN|PNP", "CBE")),
    (r"^2N7000", ("TO-92_Inline",), ("NMOS", "SGD")),
    (r"^BS170", ("TO-92_Inline",), ("NMOS", "DGS")),
    (r"^(AMS1117|LM1117|LD1117S|NCP1117ST|TLV1117|SPX1117M3|HT1117|CJT1117)", ("SOT-223-3_TabPin2",), ("REG", "1117")),
    (r"^(L|LM|MC|UA|KA)?78\d\d", ("TO-220-3_Vertical",), ("REG", "78")),
    (r"^(L|LM|MC|UA|KA)?79\d\d", ("TO-220-3_Vertical",), ("REG", "79")),
    (r"^LM317T", ("TO-220-3_Vertical",), ("REG", "317")),
    (r"^(L|LM|MC|UA|KA)?78L\d\d", ("TO-92_Inline", "SOT-89-3"), ("REG", "78L")),
]
# anchored at the token end: 'SOT-23W', 'TO-92Mini-2', 'TO-92_Wide', 'SOT-223-5' are other lands, not the family's
# (lead-count suffixes of the family's own land: SOT-223-3L, TO-220AB-3, SOT-23_3L, TO-261-4; TO-92A / TO-92AP are
# makers' names for the inline TO-92)
FAMILY_PKG = {"SOT-23": r"\bSOT-?23(?:_3L?)?(?:_TR)?(?![-\w])", "TO-92_Inline": r"\bTO-?92(?:AP?)?(?![-\w])",
              "SOT-223-3_TabPin2": r"\bSOT-?223(?:-[34]L?)?(?![-\w])|\bTO-?261(?:AA)?(?:-[34])?(?![-\w])",
              "TO-220-3_Vertical": r"\bTO-?220(?:AB)?(?:-3L?)?(?![-\w])", "SOT-89-3": r"\bSOT-?89(?:-3L?)?(?![-\w])"}
PNP_RE = re.compile(r"^(2N3906|2N2907|2N4403|2N5401|BC3[2]7|BC55[6-9]|BC560)")


# The TVS series whose part numbers end in A for one direction and CA (or C) for both (Littelfuse, Vishay, Bourns,
# onsemi, ST all number SMAJ/SMBJ/SMCJ/P6KE/1.5KE this way): SMBJ5.0A is unidirectional, SMBJ5.0CA bidirectional
TVS_SERIES = re.compile(r"^(?:SMAJ|SMBJ|SMCJ|SMDJ|SMLJ|P4SMA|P6SMB|1SMB|SMF|P4KE|P6KE|1\.5KE|1\.5SMC|5KP|3KP|15KP)"
                        r"(\d{1,3}(?:\.\d)?)(C?)A(?:[-_].*|\b.*)?$", re.I)


def tvs_bidirectional(mpn, description=""):
    """True when the MPN is one of the A / CA numbered TVS series with CA or C (SMBJ24CA) and the description does not
    say unidirectional, or (no such MPN) the description says bidirectional and not unidirectional (a part search's
    'Bidirectional Channels: 1', PESD5V0S1BL). Anything else is not known to be bidirectional."""
    d = str(description or "")
    bi = re.search(r"(?i)\bbi-?directional|\bbidir|\bBI\b", d)
    uni = re.search(r"(?i)\buni-?directional|\bunidir|\bUNI\b", d)
    m = TVS_SERIES.match(str(mpn or "").strip())
    if m:
        return bool(m.group(2)) and not uni
    return bool(bi) and not uni


def tvs_unidirectional(mpn, description=""):
    """True when the MPN is one of the A / CA numbered TVS series with A alone (SMBJ5.0A, P6KE6.8A-E3/54) and the
    description does not say bidirectional, or (no such MPN) the description says unidirectional. A CA number, or a
    description and number that disagree, is never unidirectional (the part is held for its symbol)."""
    d = str(description or "")
    bi = re.search(r"(?i)\bbi-?directional|\bbidir|\bBI\b", d)
    uni = re.search(r"(?i)\buni-?directional|\bunidir|\bUNI\b", d)
    m = TVS_SERIES.match(str(mpn or "").strip())
    if m:
        return not m.group(2) and not bi
    return bool(uni) and not bi


def symbol_kind(kind, mpn, fp_name, description=""):
    """-> (symbol key, why) for a standard symbol, or (None, why)."""
    if kind in ("R", "C", "CP", "L", "LED"):
        return kind, "generic symbol"
    two_pin = fp_name and (fp_name.startswith(("D_", "LED_")) or (spec_pins(fp_name) == 2))
    if kind == "D":
        if not two_pin:
            return None, "diode in a package with more than 2 pins: pinout not standard"
        d = str(description or "") + " " + str(mpn or "")
        if re.match(r"^D_\d{4}", fp_name) and not re.search(r"diode|rectifier|zener|schottky|\bTVS\b|\bESD\b", d, re.I):
            return None, "chip-size D part: LED or diode is not stated (footprint is the same)"
        sub = "D_Zener" if re.search(r"zener|BZX|BZT|MMSZ|\bZD", d, re.I) else "D_TVS" if re.search(r"\bTVS\b|transient|SMAJ|SMBJ|SMCJ|P6KE|ESD", d, re.I) \
            else "D_Schottky" if re.search(r"schottky|\bBAT\d|\bSS\d|\bB5\d|\bMBR|\bSK\d|\bSB\d|1N58[1-9]", d, re.I) else "D"
        if sub == "D_TVS" and tvs_bidirectional(mpn, description):      # both ways: no cathode, pins A1 / A2 as
            return "D_TVS_Bidirectional", "2-pin bidirectional TVS diode, pins A1 / A2"     # KiCad's D_TVS
        if sub == "D_TVS" and not tvs_unidirectional(mpn, description):  # KiCad's D_TVS is bidirectional (A1/A2)
            return None, "TVS diode: uni- or bidirectional is not certain from the MPN; symbol left to the other sources"
        return sub, "2-pin diode, pin 1 = cathode"
    m = re.sub(r"[^A-Z0-9]", "", str(mpn or "").upper())
    for rx, pk, (what, pins) in FAMILY:
        if re.match(rx, m):
            if fp_name not in pk:
                return None, f"{mpn}: standard pinout known for {'/'.join(pk)}, not {fp_name}"
            return f"{what}:{pins}", f"{mpn}: {what} family pinout {pins}"
    return None, "no standard pinout for this part"


NOT_ONE_DIODE = re.compile(r"(?i)\b(?:ARR(?:AY|\.)?|DUAL|PAIRS?|COMMON|SERIES|BRIDGE|QUAD|\d\s*(?:ELEMENTS?|INDEPENDENT)|"
                           r"TVS|ESD|TRANSIENT|SUPPRESS\w*|LED|PHOTO\w*|VARACTOR|PIN\s+DIODE)\b")


def single_diode_kind(description, mpn=""):
    """The 2-pin symbol kind ('D', 'D_Schottky', 'D_Zener') for a part search's description of ONE plain diode
    ('DIODE SCHOTTKY 30V 200MA SOT23-3', 'DIODE STANDARD 100V 200MA SOT23', 'DIODE ZENER 5.1V'); an array, a pair
    (common anode / cathode, series), a bridge, a TVS / ESD part (its direction), an LED or photo diode: None."""
    d = str(description or "")
    if not re.search(r"(?i)\bDIODES?\b|\bRECTIFIER\b", d) or NOT_ONE_DIODE.search(d):
        return None
    if re.search(r"(?i)zener", d):
        return "D_Zener"
    return "D_Schottky" if re.search(r"(?i)schottky", d) else "D"


SINGLE_TRANSISTOR = re.compile(r"(?i)\b(?:MOSFETs?\s+(N|P)-CH(?:ANNEL)?\b|TRANS(?:ISTOR)?\s+(NPN|PNP)\b)")
NOT_SINGLE = re.compile(r"(?i)\b(?:PRE-?BIAS(?:ED)?|DIGITAL|DARL(?:INGTON)?|DUAL|ARRAY|JFET|IGBT|N/P|2\s*[NP])")


def transistor_type(description):
    """'NMOS' / 'PMOS' / 'NPN' / 'PNP' for a part search's description of one plain transistor ('MOSFET N-CH 30V
    500MA SOT23-3', 'TRANS PNP 60V 0.6A'); a pair, a pre-biased or Darlington one, a JFET: None."""
    d = str(description or "")
    m = SINGLE_TRANSISTOR.search(d)
    if not m or NOT_SINGLE.search(d):
        return None
    return f"{m.group(1).upper()}MOS" if m.group(1) else m.group(2).upper()


def spec_pins(name):
    try:
        return spec(name).get("pin_count")
    except KeyError:
        return None


CASE_EXTRA = re.compile(r"(?i)\bPC\s*Pads?\b|\bconvex\b|\bconcave\b|long\s+side|\barray\b|\bnetwork\b|"
                        r"\bterminals?\b|\bmodule\b|\bstack(?:ed)?\b|\bradial\b|\baxial\b|full\s*pack|long\s*body|"
                        r"\bvariant\b|\bformed\b|\bbent\b|non-?standard|\bisolated\b|\bwide\s*body\b|\btab\b")


# JEDEC / Philips axial diode outlines: a case text naming one may also say 'Axial' (DigiKey: 'DO-204AL, DO-41, Axial')
AXIAL_OUTLINE = re.compile(r"(?i)(?<![A-Z0-9])(?:DO-?(?:15|27|34|35|41|201\w{0,2}|204\w{2})|SOD-?(?:27|57|64|68|81))(?![A-Z0-9])")
CASE_SHAPE = re.compile(r"(?i)\s*,\s*(?:concave|convex)\s*$")       # the end caps' shape: '1206 (3216 Metric), Concave'
ONE_ELEMENT = re.compile(r"(?i)\bPTC\b|\bPOLYFUSE\b|\bRESETTABLE\b|\bFUSE\b|\bTHERMISTOR\b|\bVARISTOR\b")
MANY_ELEMENTS = re.compile(r"(?i)\barr(?:ays?|\.)?\b|\bnetworks?\b|\bbussed\b|\bisolated\b|\bquad\b|\bdual\b|\b\d+\s*R\b|"
                           r"\b\d+\s*(?:RES|RESISTORS?|CAPS?|CAPACITORS?|ELEMENTS?|LINES?|CH(?:ANNELS?)?|BITS?)\b|\d\s*x\s*\d|\bR\d+P\b")
PLAIN_CHIP_CASE = re.compile(r"(?i)\d{4,5}\s*\(\d{4,5}\s*Metric\)")


PKG_WORD = re.compile(r"(?i)(?<![A-Z0-9])(?:SOT|SC|TO|SOD|SOIC|SO|SOP|SSOP|TSSOP|MSOP|QFN|DFN|LQFP|TQFP|DO|DPAK|D2PAK|"
                      r"SMA|SMB|SMC|MELF)(?:-?\d+[A-Z]{0,2})?(?:-\d+)?(?![A-Z0-9])")


def names_only(text, land, group=False):
    """Does the text name a package, and is every package it names the land 'land' (a table name such as 'SOT-23')?
    -> (names one, all are it). group: a distributor's case group ('TO-236-3, SC-59, SOT-23-3', DigiKey's one value for
    the SOT-23 family): it must name the land, and no name it lists may be another land or an ambiguous one of the
    tables; a name the tables do not know (SC-59) is part of the group."""
    ok = {k for k, v in ALIASES.items() if v == land} | {_norm(land)}
    ok |= {k + "3" for k in ok if land in ("SOT-23",)}                  # 'SOT-23-3' / 'TO-236-3': the 3 leads of SOT-23
    words = [_norm(m.group(0)) for m in PKG_WORD.finditer(str(text or ""))]
    if not group:
        return bool(words), bool(words) and all(w in ok for w in words)
    known = set(ALIASES) | set(DIODE_ALIASES) | set(AMBIGUOUS)
    return bool(words), any(w in ok for w in words) and not any(w in known and w not in ok for w in words)


def case_text(text, desc=""):
    """A part search's case text ('8-SOIC (0.154\", 3.90mm Width)', '1812 (4532 Metric)') when it names only the case:
    one that adds pads or leads ('0603, 3 PC Pad', '1206, 4 Leads', 'Convex') or a lead count other than its own pin
    count is not the plain land of that name -> '' (not used). 'Concave'/'Convex' alone is the end caps' shape: with the
    same source's description of a one-element part ('PTC RESET FUSE 6V 500MA 1206': a fuse, PTC or thermistor) and
    nothing else in the text ('1206 (3216 Metric), Concave' only) it is that chip's plain land; an array's ('RES ARRAY
    4 RES'), another kind's or no description keeps it unused."""
    t = str(text or "").strip()
    if AXIAL_OUTLINE.search(t):                           # 'DO-204AL, DO-41, Axial': the outline is axial by its name;
        t = re.sub(r"(?i),?\s*\baxial\b", "", t).strip(" ,")   # the word says nothing more ('Axial' alone still names none)
    plain = CASE_SHAPE.sub("", t)
    if plain != t and PLAIN_CHIP_CASE.fullmatch(plain) and ONE_ELEMENT.search(str(desc or "")) \
            and not MANY_ELEMENTS.search(str(desc or "")):
        t = plain                                         # a fuse / PTC / thermistor chip: one element, two caps
    if not t or CASE_EXTRA.search(t) or not re.search(r"\d", t):    # '-', 'Nonstandard': names no case
        return ""
    leads = re.search(r"(?i)\b(\d{1,3})\s*Leads?\b", t)
    if leads:
        first = re.match(r"\s*(\d{1,3})-", t)
        if not first or first.group(1) != leads.group(1):
            return ""
    return t


HEADER_NOT_PLAIN = re.compile(r"(?i)RCPT|RECEPT|SOCKET|FEMALE|SHROUD|\bBOX|R/A|RIGHT\s*ANGLE|\bSMD\b|\bSMT\b|SURFACE|"
                              r"LOCK|\bKEY|POLARI|\bDUAL\b|DOUBLE|\b[2-9]\s*ROWS?\b|WIRE|IDC|BREAKAWAY|FRICTION|PRESS|"
                              r"SHUNT|JUMPER|SOLDER\s*CUP|\bPOSTS?\b|\bPIN\s*STRIP\b|SWISS|MACHINED")


def header_land(text):
    """KiCad's plain pin header land for a part-search description of a straight one ('CONN HEADER VERT 2POS 2.54MM'
    -> 'PinHeader_1x02_P2.54mm_Vertical'), or None. Two positions have one land whatever the rows; more need the text
    to say one row. Sockets, shrouded / boxed / latching, right-angle and SMD headers are other lands."""
    t = str(text or "")
    pos = re.search(r"(?i)\b(\d{1,2})\s*POS(?:ITIONS?)?\b", t)
    if not (re.search(r"(?i)\bHEADER\b", t) and re.search(r"(?i)\bVERT(?:ICAL)?\b", t) and pos
            and re.search(r"(?i)\b2\.54\s*MM\b|(?<![\d.])0?\.100?\s*(?:\"|IN)", t)) or HEADER_NOT_PLAIN.search(t):
        return None
    n = int(pos.group(1))
    if not 1 <= n <= 40 or (n > 2 and not re.search(r"(?i)\b(?:1|SINGLE)\s*ROW\b", t)):
        return None
    return f"PinHeader_1x{n:02d}_P2.54mm_Vertical"


CONN_KEY = re.compile(r"^Conn_01x(\d\d)(_MountingPin)?$")


def _conn_row(n, mp=False):
    """KiCad's Connector_Generic:Conn_01xNN: one row of n pins on the left, pin 1 at the top, numbered 1..n; mp: and
    its mounting pads as one pin MP at the bottom (Conn_01xNN_MountingPin, for KiCad's '..._1x04-1MP_...' lands)."""
    top = round((n // 2) * 2.54, 3)                       # pin 1's y: every pin on the 2.54 mm grid
    bottom = round(top - (n - 1) * 2.54 - 1.27, 3)
    pins = [(str(i + 1), "~", "passive", -5.08, round(top - i * 2.54, 3), 0, 3.81) for i in range(n)]
    g = [_rect(-1.27, top + 1.27, 1.27, bottom, "background")]
    if mp:
        pins.append(("MP", "MountPin", "passive", 0, round(bottom - 2.54, 3), 90, 2.54))
        return (f"Conn_01x{n:02d}_MountingPin", "J", f"Generic connector, single row, 01x{n:02d}, mounting pin", g, pins)
    return (f"Conn_01x{n:02d}", "J", f"Generic connector, single row, 01x{n:02d}", g, pins)


# USB Micro-B receptacle: the USB specification numbers its contacts (1 VBUS, 2 D-, 3 D+, 4 ID, 5 GND) and KiCad's
# USB_Micro-B lands number their pads the same, with the shell's pads SH. Power on top, ground and shield at the bottom
SYMBOLS_USB = {"USB_Micro-B_Receptacle": ("USB_Micro-B_Receptacle", "J", "USB Micro Type B connector",
                               [_rect(-5.08, 5.08, 5.08, -5.08, "background")],
                               [("1", "VBUS", "passive", 0, 7.62, 270, 2.54), ("2", "D-", "passive", 7.62, 0, 180, 2.54),
                                ("3", "D+", "passive", 7.62, 2.54, 180, 2.54), ("4", "ID", "passive", 7.62, -2.54, 180, 2.54),
                                ("5", "GND", "passive", 0, -7.62, 90, 2.54), ("SH", "Shield", "passive", -2.54, -7.62, 90, 2.54)])}


def symbol_text(key, mpn=""):
    """KiCad .kicad_sym text for a symbol key from symbol_kind; returns (text, symbol name)."""
    if CONN_KEY.match(key):
        name, ref, desc, g, pins = _conn_row(int(CONN_KEY.match(key).group(1)), bool(CONN_KEY.match(key).group(2)))
    elif key in SYMBOLS_USB:
        name, ref, desc, g, pins = SYMBOLS_USB[key]
    elif key.startswith("DIODE3:"):
        _, kind, order = key.split(":")
        name, ref, desc, g, pins = _diode3(kind, order)
    elif ":" in key:
        what, order = key.split(":")
        if what == "REG":
            fixed = order != "1117" or bool(re.search(r"-?(\d\.\d|\d{2}|\d-\d)(?![\d.])", re.sub(r"^.*?1117", "", str(mpn).upper())))
            gnd = "GND" if fixed else "ADJ"
            names = {"1117": [gnd, "VO", "VI"], "78": ["VI", "GND", "VO"], "79": ["GND", "VI", "VO"], "317": ["ADJ", "VO", "VI"],
                     "78L": ["VO", "GND", "VI"]}[order]
            name, ref, desc, g, pins = _regulator(names, ())
        elif len(order) > 3:
            name, ref, desc, g, pins = _eight(what, order)
        else:
            kind = what if "|" not in what else ("PNP" if PNP_RE.match(re.sub(r"[^A-Z0-9]", "", str(mpn).upper())) else "NPN")
            name, ref, desc, g, pins = _three(kind, list(order))
    else:
        name, ref, desc, g, pins = SYMBOLS[key]
    body = " ".join(g)
    pin_txt = " ".join(_pin(*p) for p in pins)
    # pin names hidden on 2-pin parts and on transistors (KiCad's Q_*: G / S / D written over the gate and channel lines)
    quiet = all(p[1] in ("~", "1", "2", "K", "A", "A1", "A2", "NC") for p in pins) or ref == "Q"
    hide_names = "(pin_names (offset 0) hide)" if quiet else "(pin_names (offset 0.254))"
    if key in SYMBOLS:                         # 2-pin passives and diodes: no "1"/"2" or K/A text on the body (as KiCad's Device:R)
        hide_names += " (pin_numbers hide)"
    flat = all(p[5] in (0, 180) for p in pins)  # horizontal 2-pin parts (diodes): labels above and below, not on the body
    at_ref, at_val = ("0 2.794", "0 -4.064") if flat else ("2.54 0", "-2.54 0")
    if key in SYMBOLS_USB:                     # labels above VBUS and below GND, off the body and its pin names
        at_ref, at_val = "-2.54 10.16", "2.54 -10.16"
    if CONN_KEY.match(key):                    # a connector's labels above and below its row of pins
        ys = [p[4] for p in pins]
        at_ref, at_val = f"0 {max(ys) + 2.54:g}", f"0 {min(ys) - 2.54:g}"
    text = (f'(kicad_symbol_lib (version 20220914) (generator "ecadlib_stdparts")\n'
            f'  (symbol "{name}" {hide_names} (in_bom {"no" if key in PINLESS else "yes"}) (on_board yes)\n'
            f'    (property "Reference" "{ref}" (at {at_ref} 0) (effects (font (size 1.27 1.27))))\n'
            f'    (property "Value" "{name}" (at {at_val} 0) (effects (font (size 1.27 1.27))))\n'
            f'    (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))\n'
            f'    (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))\n'
            f'    (property "Description" "{desc}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))\n'
            f'    (symbol "{name}_0_1" {body})\n'
            f'    (symbol "{name}_1_1" {pin_txt})))\n')
    return text, name


# ---------------------------------------------------------------- one part
# another diode named, or the LED word naming what the part is for ('for LED', 'LED driver', 'LED backlight boost')
_OTHER_DIODE = re.compile(r"schottky|zener|\bTVS\b|transient|rectifier|\bESD\b|varactor|varicap|switching diode|"
                          r"signal diode|photo ?diode|suppressor|protector|\bshunt\b|\bdiode\b", re.I)
# a diode / device type named before the LED's clause ('Zener diode for LED protection, white LED string'): it is
# the part unless the LED is its own clause's head noun (R-FX9-4)
_NAMED_OTHER = re.compile(r"schottky|zener|\bTVS\b|transient|rectifier|\bESD\b|varactor|varicap|photo ?diode|"
                          r"suppressor|protector|\bshunt\b", re.I)
_LED_HEAD = {"smd", "smt", "tht", "chip", "indicator", "lamp", "emitter", "emitting", "diode", "light", "package", "pcb",
             "through", "surface", "side", "top", "right", "reverse"}
# a word that only an LED has next to it: its colour, wavelength, size or package
_LED_LOOK = re.compile(r"\b(?:red|green|blue|white|yellow|amber|orange|pink|purple|violet|cyan|RGBW?|warm|cool|"
                       r"neutral|IR|infrared|UV|ultraviolet|bi-?colou?r|dual[ -]colou?r|super ?flux)\b|"
                       r"\d{3,4}\s?nm\b|(?<![\d.])\d(?:\.\d)?\s?mm\b|\b(?:3528|5050|2835|3014|5630|5730|PLCC-?\d?)\b", re.I)
_LED_FOR = re.compile(r"\b(?:for|in|of|to|with|drives?|driving|protects?|protecting)\b[^,;()]*$", re.I)
_LED_USE = re.compile(r"^[\s-]*(?:driver|backlight\w*|string|strip|lighting|boost|dimm\w*|protect\w*|surge|bias|supply|"
                      r"shunt|bypass)\b",
                      re.I)


def says_led(description):
    """The description says the part is an LED. Each LED word is read in its clause (up to a comma, semicolon or
    bracket): after 'for / in / with / drives ...' it names what the part is for ('Zener for LED string', 'TVS for
    5050 LED strip'): not this part. A colour, wavelength, size or LED package in the clause ('Zener-protected white
    LED', 'LED backlight white 3528') makes it the part. 'LED driver / backlight / lighting ...' with another diode
    named ('Schottky ..., LED backlight boost', 'LED driver diode') is that diode's use; else the LED is the head
    noun ('ESD protected LED', 'LED protect', 'Status LED')."""
    d = str(description or "")
    for m in re.finditer(r"\bLEDs?\b", d, re.I):
        a = max(d.rfind(c, 0, m.start()) for c in ",;()") + 1
        ends = [k for k in (d.find(c, m.end()) for c in ",;()") if k >= 0]
        pre, post = d[a:m.start()], d[m.end():min(ends) if ends else len(d)]
        if _LED_FOR.search(pre):
            continue
        nxt = re.match(r"[\s-]*([A-Za-z]+)", post)
        head = not nxt or nxt.group(1).lower() in _LED_HEAD or bool(_LED_LOOK.fullmatch(nxt.group(1)))
        if not head and _NAMED_OTHER.search(d[:a]):      # 'Zener ..., white LED string': the Zener's use
            continue
        if _LED_LOOK.search(pre + " " + post):
            return True
        if _LED_USE.search(post) and _OTHER_DIODE.search(d):
            continue
        return True
    return False


def part_kind(category, footprint="", description="", designator=""):
    """R C CP L LED D F Q U or None from the plan folder's last segment, the footprint and the description."""
    cat = str(category or "").split("/")[-1]
    fp, d = str(footprint or ""), str(description or "")
    if cat == "Resistors":
        return "R"
    if cat == "Capacitors":
        return "CP" if fp.startswith("CP_") or says_polarized(d) else "C"
    if cat == "Inductors":
        return "L"
    if cat == "Diodes":
        return "LED" if fp.startswith("LED_") or says_led(d) else "D"
    if re.search(r"\bfuse\b|polyfuse|PTC", d, re.I) and re.match(r"^F\d", str(designator or "")):
        return "F"
    return {"Transistors": "Q", "ICs": "U"}.get(cat)


# polarized capacitors (tantalum, polymer, aluminium) by MPN: their case is not a chip-capacitor land and pin 1 is +
POLAR_CAP = re.compile(r"^(T49[1-9]|T5[2-9]\d|TAJ|TPS[A-Z]|TPM|TRJ|TCJ|TCM|TCN|TCQ|TLJ|TPC|NOJ|NOS|F9[1-8]|293D|593D|298D|"
                       r"T8[36]|TR3|TP3|EEE|EEV|EEH|EEF|EEU|ECA|\d+TP[A-Z]|\d+SVP|\d+SEP|UWT|UWX|UUD|UUX|UCD|PCJ|PCF|PLV)")


def _kicad_own(t):
    """The KiCad footprint a text names as such, or None: an exact KiCad name, except a bare package name that is
    also the package's common name ('SOT-223', 'TO-92': the package, the part decides the land); with a library
    prefix ('Package_TO_SOT_THT:TO-92') it is KiCad's own footprint."""
    n = _strip_lib(t).strip()
    if n not in kicad_names():
        return None
    if n == str(t).strip() and (_norm(n) in ALIASES or _norm(n) in DIODE_ALIASES or _norm(n) in AMBIGUOUS):
        return None
    return n


def _names_other_land(texts, name):
    """A text names a KiCad footprint other than the table land `name` (the family fallback must not override it)."""
    return any(_kicad_own(t) not in (None, name) for t in texts if str(t or "").strip())


# tantalum EIA case (metric size - height) -> the table's land: KiCad's KEMET case lands, which KEMET and AVX share for
# these four cases (same body); other cases (low-profile, AVX-only) are not in the table
TANT_LAND = {"3216-18": "CP_EIA-3216-18_Kemet-A", "3528-21": "CP_EIA-3528-21_Kemet-B",
             "6032-28": "CP_EIA-6032-28_Kemet-C", "7343-31": "CP_EIA-7343-31_Kemet-D"}
# KiCad's tantalum lands of one EIA size are the same copper at every height (Capacitor_Tantalum_SMD, checked
# 2026-11-23: CP_EIA-3216-10/-12/-18, 3528-12/-15/-21, 6032-15/-20/-28, 7343-15/-20/-30/-31/-40/-43 share their size's
# pads); the generator sizes the land from the body's length and width, not its height. A height the table has no name
# for (KEMET T520B 3528-19, T495X 7343-43) takes its size's table land; the 3D body keeps the part's own height.
TANT_SIZE_LAND = {c.split("-")[0]: n for c, n in TANT_LAND.items()}
EIA_CASE = re.compile(r"(?<![\d.])(3216|3528|6032|7343|2012|1608|7260|7132)[-_ ](\d{2})(?![\d.])")
CASE_LETTER = re.compile(r"(?i)(?<![A-Za-z0-9])case[-_ ]?([A-E])(?![A-Za-z0-9])")


# not a solid tantalum: an aluminium can (Panasonic / Nichicon / Chemi-Con also letter their cans 'case B/C/D', a
# round body), a polymer or niobium part. Description words and the aluminium MPN series of POLAR_CAP.
NOT_TANTALUM = re.compile(r"(?i)electrolytic|alumin|\bALUM\b|\bAl[- ]?(?:e|elec|cap)\b|polymer|niobium")
ALUMINIUM_CAP = re.compile(r"^(EEE|EEV|EEH|EEF|EEU|ECA|UWT|UWX|UUD|UUX|UCD|PCJ|PCF|PLV|\d+SVP|\d+SEP)")
TANTALUM_CAP = re.compile(r"^(T49[1-9]|T5[2-9]\d|TAJ|TPS[A-Z]|TPM|TRJ|TCJ|TLJ|F9[1-8]|293D|593D|298D|T8[36]|TR3|TP3)")


# the EIA heights (0.1 mm) of each tantalum case size (KEMET / AVX / Vishay case tables): a part search gives the size
# ('1411 (3528 Metric)') and the body's maximum seated height ('0.083" (2.11mm)'), not the case code
EIA_HEIGHTS = {"3216": (10, 12, 16, 18), "3528": (12, 15, 19, 21), "6032": (15, 20, 28), "7343": (12, 15, 19, 20, 28, 31, 40, 43)}


def tantalum_eia(case, height):
    """A part search's case text and maximum height -> the tantalum's EIA case ('3528-21'), or None. The maximum is
    the code's height or up to 0.15 mm over it (293D B: 2.11 mm max, EIA 3528-21); exactly one of the size's heights
    must fit, or none is named. T520B (2.00 mm max) is 3528-19, a case the table does not have: no land."""
    size = re.findall(r"(?<!\d)(\d{4}) Metric\b", str(case or ""))
    mm = re.search(r"(\d+(?:\.\d+)?)\s*mm\b", str(height or ""))
    if len(set(size)) != 1 or size[0] not in EIA_HEIGHTS or not mm:
        return None
    h = float(mm.group(1))
    fit = [x for x in EIA_HEIGHTS[size[0]] if -0.005 <= h - x / 10 <= 0.15]
    return f"{size[0]}-{fit[0]}" if len(fit) == 1 else None


def tantalum_search_text(case, height, mpn=""):
    """The part search's case text as the tantalum land reads it: its EIA case from size and height ('3528-21 (1411
    (3528 Metric), height 2.11mm)'); '' when no height names one but the MPN's own case code (AVX TAJB: 3528-21) is
    that size (the code decides); else the text unchanged (it names no case: no land)."""
    from . import mpn_decode as MD
    eia = tantalum_eia(case, height)
    if eia:
        return f"{eia} ({case}, height {height})"
    d = MD.decode(mpn)
    size = re.findall(r"(?<!\d)(\d{4}) Metric\b", str(case or ""))
    if d and d["kind"] == "CP" and d.get("case") and len(set(size)) == 1 and d["case"].split("-")[0] == size[0]:
        return ""
    return case


def tantalum_land(texts, mpn="", description=""):
    """(table land, why) for a tantalum capacitor, or (None, why). Its EIA case comes from the footprint texts ('CP_EIA-
    3528-21_Kemet-B', 'CP_Tantalum_Case-B_EIA-3528-21_Reflow', '3528-21', 'Case B') and the MPN's own code (KEMET
    T491A..., AVX TAJB..., ecadlib.mpn_decode). Every one that names a case must name the same one; a footprint text
    that names another land (C_1206_3216Metric, a hand-solder variant) or no case at all builds nothing."""
    from . import mpn_decode as MD
    from .librarian import passive_value
    m = _norm(mpn)
    if NOT_TANTALUM.search(" ".join([str(description or "")] + [str(t or "") for t in texts])) or ALUMINIUM_CAP.match(m):
        return None, "not a solid tantalum (aluminium, polymer or niobium by its description or part number)"
    d = MD.decode(mpn)
    tantalum = bool(d and d["kind"] == "CP") or bool(TANTALUM_CAP.match(m)) or bool(re.search(
        r"(?i)tantal|\bTANT\b", " ".join([str(description or "")] + [str(t or "") for t in texts])))
    said = []                                             # (case, where)
    for t in texts:
        t = str(t or "").strip()
        if not t or (passive_value(t, "Capacitors") is not None or passive_value(t.lower(), "Capacitors") is not None) \
                and not EIA_CASE.search(t) and not CASE_LETTER.search(t):
            continue                                      # the Value cell ('10uF', '22u') names no land; '1206' does
        if LAND_VARIANT.search(t):
            return None, f"{t!r} is a land variant, not the table's plain tantalum land"
        cases = {f"{m.group(1)}-{m.group(2)}" for m in EIA_CASE.finditer(t)}
        letters = {MD.TANT_CASE.get(m.group(1).upper(), f"case {m.group(1).upper()}") for m in CASE_LETTER.finditer(t)}
        if not cases and not letters:
            return None, f"the footprint text {t!r} names no tantalum case"
        if not cases and not tantalum:                    # 'Case D' alone: aluminium cans use the letters too
            return None, f"{t!r} names a case letter, but nothing says the part is a tantalum (description or part number)"
        said += [(c, t) for c in cases | (letters if not cases else set())]
    if d and d["kind"] == "CP":
        said.append((d["case"], f"{mpn} ({d['series']} code)"))
    found = {c for c, _ in said}
    if not found:
        return None, "no tantalum case named (footprint, case letter or a KEMET T491 / AVX TAJ part number)"
    if len(found) > 1:
        return None, "two tantalum cases named: " + ", ".join(f"{c} ({w})" for c, w in said)
    case = found.pop()
    where = ", ".join(dict.fromkeys(w for _, w in said))
    if case in TANT_LAND:
        return TANT_LAND[case], f"tantalum case EIA {case} ({where})"
    size = case.split("-")[0]
    if size in TANT_SIZE_LAND and re.fullmatch(r"\d{4}-\d{2}", case):
        return TANT_SIZE_LAND[size], (f"tantalum case EIA {case} ({where}): KiCad's EIA {size} lands are the same "
                                      f"copper at every height, so its size's land; the 3D body keeps its own height")
    return None, f"tantalum case {case} is not in the standard table"


def resolve(texts, kind=None, mpn="", description=""):
    """What the standard source builds for one part:
    {"footprint": table name | None, "fp_how": str, "kicad_ref": "Lib:Name", "symbol": key | None, "sym_how": str}.
    A polarized capacitor (kind CP, or a C whose MPN is a tantalum / polymer / electrolytic series) gets nothing: the
    table has no polarized case, and a chip-capacitor land would lose the + mark."""
    if kind in ("NTC", "PTC", "VAR", "TH"):                # a thermistor / varistor: a resistor's chip land by size
        name, how = footprint_for(list(texts), "R", "", weak=[description])     # (not the resistor MPN codes)
        size = sensor_size(mpn)
        if not name and size:
            name, how = footprint_for([size], "R")[0], f"{mpn}: the series' size code says {size}"
        sym = SENSOR_SYMBOL.get(kind) if name else None
        return {"footprint": name, "fp_how": how, "kicad_ref": kicad_ref(name) if name else None, "symbol": sym,
                "sym_how": f"{SYMBOLS[sym][2].lower()}, 2 pins" if sym else
                "thermistor: NTC or PTC is not said" if kind == "TH" else "no chip land"}
    if kind == "C" and (POLAR_CAP.match(_norm(mpn)) or says_polarized(description) or
                        any(_strip_lib(str(t or "")).strip().startswith("CP_") for t in texts)):
        kind = "CP"                                       # a C on a polarized land ('CP_EIA-3528-21_Kemet-B') is one
    if kind == "CP":
        name, how = tantalum_land(texts, mpn, description)
        if name:
            return {"footprint": name, "fp_how": how, "kicad_ref": kicad_ref(name), "symbol": "CP",
                    "sym_how": "polarized capacitor, pin 1 = + as the land marks it"}
        return {"footprint": None, "fp_how": f"polarized capacitor: {how}", "kicad_ref": None, "symbol": None,
                "sym_how": "polarized capacitor: pin 1 = + only with a footprint that marks it"}
    multi = multi_terminal(description) if kind in CHIP_PREFIX else None
    if multi:                                             # 'RES ARRAY 4 RES 10K 1206': the size is the array's body
        return {"footprint": None, "fp_how": f"the description says {multi!r}: more than two terminals, not a two-pad "
                "chip land", "kicad_ref": None, "symbol": None, "sym_how": f"the description says {multi!r}: not a "
                "two-pin part"}
    fk = {"Q": None, "U": None}.get(kind, kind)
    other = DESC_LAND.search(str(description or "")) if kind in CHIP_PREFIX else None
    if other:                                             # 'LED 0603 reverse mount': the size is the body, not the land
        name, how = None, f"the description says {other.group(0)!r}: another land than the plain chip of that size"
    else:
        name, how = footprint_for(list(texts), fk, mpn, weak=[description])
        if name in TANT_LAND.values():                    # a tantalum land: only a polarized capacitor's (pin 1 = +)
            name, how = None, f"{name} is a polarized capacitor's land, but the part is not one (kind {kind or 'unknown'})"
    if not name:                                          # a known MPN family settles a bare family name (SOT-223, TO-220)
        fam = next((pk for rx, pk, _ in FAMILY if re.match(rx, _norm(mpn))), ())
        said = " ".join(map(str, texts)) + " " + str(description)
        hit = [p for p in fam if re.search(FAMILY_PKG[p], said, re.I)]
        if len(hit) == 1 and not POWER_PKG.search(said) and not NO_GO.search(said) and not LAND_VARIANT.search(said) \
                and not EP_WORD.search(said) and not any(NOT_CHIP.search(str(x or "")) for x in texts) \
                and not (hit[0].startswith("TO-92") and NO_GO_TO92.search(said)) and not _names_other_land(texts, hit[0]):
            name, how = hit[0], f"{mpn}: its family comes in {hit[0]} (named as {re.search(FAMILY_PKG[hit[0]], said, re.I).group(0)})"
    sym, sym_how = symbol_kind(kind, mpn, name, description) if (name or kind in ("R", "C", "CP", "L", "LED")) else (None, "no standard package")
    return {"footprint": name, "fp_how": how, "kicad_ref": kicad_ref(name) if name else None, "symbol": sym, "sym_how": sym_how}


# ---------------------------------------------------------------- checks and CLI
def bench(reference, names=None):
    """Compare table entries with KiCad. reference(name, lib) -> numbers dict or None.
    -> (rows, summary) like footprint_bench.run."""
    from . import footprint_bench as B
    rows = []
    for n in names or sorted(packages()):
        ref = reference(n, packages()[n]["lib"])
        if ref is None:
            rows.append({"name": n, "error": "no reference"})
            continue
        r = B.compare(B.numbers(build(n)[0]), ref)
        rows.append({"name": n, **{k: r[k] for k in ("pads", "pads_ok", "all_pads_ok", "courtyard_ok", "courtyard_diff", "worst_pos", "worst_size")}})
    done = [r for r in rows if "error" not in r]
    return rows, {"footprints": len(done), "unavailable": len(rows) - len(done), "all_pads_ok": sum(r["all_pads_ok"] for r in done),
                  "courtyards_ok": sum(r["courtyard_ok"] for r in done),
                  "both_ok": sum(r["all_pads_ok"] and r["courtyard_ok"] for r in done)}


def coverage(boms, rules=None):
    """How many BOM lines the standard source resolves offline (footprint; symbol too). Reads the BOMs like triage."""
    from . import altium_export as AE, bom_triage as T, rules as RU
    out = []
    for b in boms:
        lines, _ = T.read_bom(b, RU.bom_columns(RU.resolve(rules, Path(b).parent)))
        for ln in lines:
            if str(ln.get("dnp") or "").strip() or not any(str(ln.get(k) or "").strip() for k in ("mpn", "value", "footprint")):
                continue
            pre = re.match(r"[A-Z]+", str(ln.get("designator") or "").upper())     # the librarian's category (FB1
            cat = AE.CATEGORY.get(pre.group(0) if pre else "U", "Misc")             # "Ferrite bead 0603": Inductors)
            if cat in ("ICs", "Misc"):
                cat = AE.kind_of((ln.get("description"),), (ln.get("footprint"),)) or cat
            kind = part_kind(cat, ln.get("footprint"), ln.get("description"), ln.get("designator"))
            r = resolve([ln.get("footprint"), ln.get("value")], kind, ln.get("mpn") or "", ln.get("description") or "")
            out.append({"bom": str(b), "designator": str(ln.get("designator") or "")[:40], "mpn": ln.get("mpn") or "",
                        "footprint_text": ln.get("footprint") or "", "kind": kind or "", **r})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib stdparts", description=" ".join(__doc__.split("\n\n")[0].split()))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="what the standard source would build for one part")
    r.add_argument("text", nargs="*", help="footprint / package text(s), in priority order")
    r.add_argument("--kind", choices=["R", "C", "CP", "L", "LED", "D", "F", "Q", "U"])
    r.add_argument("--mpn", default="")
    r.add_argument("--description", default="")
    r.add_argument("--kicad-mod", help="write the footprint here")
    l_ = sub.add_parser("list", help="the packages in the table")
    l_.add_argument("pattern", nargs="?", default="")
    b = sub.add_parser("bench", help="every table entry against KiCad's official footprint")
    b.add_argument("--kicad", help="a kicad-footprints checkout (default: fetch from GitLab through the HTTP cache)")
    b.add_argument("--fixture", help="numbers-only reference file (.json or .json.gz)")
    from .firstrun import default_cache    # <workspace>/http_cache when a workspace is set up
    b.add_argument("--cache", default=default_cache())
    b.add_argument("--offline", action="store_true")
    c = sub.add_parser("coverage", help="BOM lines the standard source resolves")
    c.add_argument("bom", nargs="+")
    c.add_argument("--rules")
    for p in (r, l_, b, c):
        p.add_argument("--json", action="store_true", help="print JSON")
    a = ap.parse_args(argv)
    if a.cmd == "resolve":
        res = resolve(a.text, a.kind, a.mpn, a.description)
        if a.kicad_mod and res["footprint"]:
            Path(a.kicad_mod).parent.mkdir(parents=True, exist_ok=True)
            Path(a.kicad_mod).write_text(build(res["footprint"])[0], encoding="utf-8", newline="\n")
        print(json.dumps(res, indent=2) if a.json else
              f"footprint: {res['kicad_ref'] or '-'} ({res['fp_how']})\nsymbol:    {res['symbol'] or '-'} ({res['sym_how']})")
        return 0 if res["footprint"] or res["symbol"] else 1
    if a.cmd == "list":
        names = [n for n in sorted(packages()) if a.pattern.lower() in n.lower()]
        print(json.dumps({n: packages()[n]["lib"] for n in names}, indent=2) if a.json else "\n".join(kicad_ref(n) for n in names))
        return 0
    if a.cmd == "bench":
        from . import footprint_bench as B
        if a.fixture:
            op = gzip.open if a.fixture.endswith(".gz") else open
            with op(a.fixture, "rt", encoding="utf-8") as f:
                fx = json.load(f)
            fx = fx.get("footprints", fx)
            ref = lambda n, lib: fx.get(n)
        elif a.kicad:
            ref = lambda n, lib: (B.numbers((Path(a.kicad) / f"{lib}.pretty" / f"{n}.kicad_mod").read_text(encoding="utf-8"))
                                  if (Path(a.kicad) / f"{lib}.pretty" / f"{n}.kicad_mod").exists() else None)
        else:
            from .httpcache import CachedHTTP
            http = CachedHTTP(a.cache, min_interval=0.3, offline=a.offline, ttl_s=30 * 86400)
            ref = lambda n, lib: B.reference(n, lib, http)
        rows, s = bench(ref)
        if a.json:
            print(json.dumps({"summary": s, "rows": rows}, indent=2))
        else:
            for row in rows:
                if "error" in row or not (row["all_pads_ok"] and row["courtyard_ok"]):
                    print(f"  {row['name']}: " + (row.get("error") or f"pads {row['pads_ok']}/{row['pads']}, courtyard diff {row['courtyard_diff']}"))
            print(f"{s['both_ok']}/{s['footprints']} match KiCad (pads and courtyard); {s['unavailable']} without a reference")
        return 0 if s["both_ok"] == s["footprints"] and s["footprints"] else 1
    try:
        rows = coverage(a.bom, a.rules)
    except ValueError as e:                    # an empty BOM, headers not recognised: as triage says it
        from .firstrun import bom_hint
        print(f"ecadlib stdparts: cannot read BOM: {e}" + (bom_hint(e) or "\n  hint: a BOM is a CSV, TSV or XLSX "
              "with a header row (`ecadlib triage BOM` shows what was recognised)"), file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"  {row['designator']:<16} {row['mpn'][:24]:<24} {row['footprint_text'][:28]:<28} -> {row['kicad_ref'] or '-'}"
                  f"  sym {row['symbol'] or '-'}  ({row['fp_how']})")
        fp = sum(bool(r_["footprint"]) for r_ in rows)
        both = sum(bool(r_["footprint"] and r_["symbol"]) for r_ in rows)
        print(f"{len(rows)} lines: footprint for {fp}, footprint and symbol for {both}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------- thermistors and varistors
# what the line's words say: 'NTC thermistor 10k 3380K', 'Varistor 18V 0805', 'MOV 275VAC'. A PTC said with fuse
# words ('PTC resettable fuse', 'Polyfuse') is a fuse (part_kind 'F'), not a thermistor
VARISTOR_WORDS = re.compile(r"(?i)\bvaristors?\b|\bMOVs?\b|\bVDRs?\b|\bMLVs?\b|\bZNR\b|surge absorber")
THERMISTOR_WORDS = re.compile(r"(?i)thermistors?\b|\bposistors?\b")
NTC_WORD, PTC_WORD = re.compile(r"(?i)(?<![A-Za-z])NTC(?![A-Za-z])"), re.compile(r"(?i)(?<![A-Za-z])PTC(?![A-Za-z])")
FUSE_WORDS = re.compile(r"(?i)\bfuses?\b|poly ?fuse|polyswitch|resettable|\bPPTC\b")
# makers' thermistor / varistor series (the MPN, upper case without punctuation): Murata NCP / NCU (NTC), PRF (PTC);
# TDK NTCG, EPCOS B57 (NTC), B59 (PTC), B72 (SIOV varistors), AVR-M; Vishay NTCS / NTCLE / NTHS / NTCALUG (NTC),
# PTCSL / PTCEL (PTC); Panasonic ERT-J (NTC), ERZ / EZJ (varistors); Littelfuse V..MLA / V..LA / V..ZA; Bourns MOV-,
# CG....MLC
# (Murata's code is series + size + 2 letters + 3 value digits: NCP18XH103, PRF18BB471; onsemi's NCP1117 is a regulator)
SENSOR_MPN = (("NTC", re.compile(r"^(?:NC[PU](?:03|15|18|21|31)[A-Z]{2}\d{3}|NTCG\d|NTCS\d|NTCLE|NTCALUG|NTHS\d|B57\d|ERTJ[Z0-9])")),
              ("PTC", re.compile(r"^(?:PRF(?:03|15|18|21|31)[A-Z]{2}\d{3}|B59\d|PTCSL|PTCEL)")),
              ("VAR", re.compile(r"^(?:B72\d|AVRM\d|ERZ[A-Z0-9]|EZJ[A-Z0-9]|V\d+MLA|V\d+(?:LA|ZA)\d|MOV\d|CG\d{4}MLC)")))
SENSOR_REFS = {"TH": "TH", "RT": "TH", "NTC": "NTC", "PTC": "PTC", "VDR": "VAR", "MOV": "VAR"}


def sensor_kind(texts=(), mpn="", designator=""):
    """'NTC' / 'PTC' (thermistors), 'VAR' (varistor), 'TH' (a thermistor whose type nothing says: no symbol is
    chosen), 'conflict' (one source says thermistor, another varistor: held, built as neither) or None (not one),
    from the line's words (value, description, the part search's text), its part number's series (SENSOR_MPN) and its
    designators (TH / RT / NTC / PTC / VDR / MOV; RV is also a potentiometer's, so it says nothing). A bare 'PTC'
    says nothing either: with fuse words it is a resettable fuse (part_kind 'F'), with 'thermistor' a thermistor."""
    t = " ".join(str(x or "") for x in texts)
    th = bool(THERMISTOR_WORDS.search(t))
    if FUSE_WORDS.search(t) and not th:             # 'PTC resettable fuse' on PTC1 / F1: a fuse, whatever its letters
        return None
    said = {"VAR"} if VARISTOR_WORDS.search(t) else set()
    if NTC_WORD.search(t):
        said.add("NTC")
    if PTC_WORD.search(t) and th:
        said.add("PTC")
    if th and not said & {"NTC", "PTC"}:
        said.add("TH")
    m = _norm(mpn)
    said |= {k for k, rx in SENSOR_MPN if m and rx.match(m)}
    refs = {re.match(r"[A-Z]+", r).group(0) for r in re.findall(r"[A-Za-z]+(?=\d)", str(designator or "").upper())}
    if refs and refs <= set(SENSOR_REFS):
        said |= {SENSOR_REFS[r] for r in refs}
    if not said:
        return None
    if "VAR" in said:
        return "VAR" if said == {"VAR"} else "conflict"
    typed = said & {"NTC", "PTC"}
    return typed.pop() if len(typed) == 1 else "TH"


def sensor_size(mpn):
    """The imperial chip size a thermistor's or varistor's part number states, or None. Only the series whose size
    code is printed in the maker's part-number table: Murata NCP / NCU / PRF (03 0201, 15 0402, 18 0603, 21 0805, as
    GRM), TDK NTCG (063 0201, 103 0402, 163 0603: metric 0603 / 1005 / 1608), Panasonic ERT-J (Z 0201, 0 0402,
    1 0603), and the ones that print it (Vishay NTCS0603, Littelfuse V18MLA0805, Bourns CG0603MLC, TDK AVR-M1005)."""
    m = _norm(mpn)
    imp = r"(0201|0402|0603|0805|1206|1210)"
    rules = ((r"^(?:NCP|NCU|PRF)(03|15|18|21|31)", {"03": "0201", "15": "0402", "18": "0603", "21": "0805", "31": "1206"}),
             (r"^NTCG(06|10|16)3", {"06": "0201", "10": "0402", "16": "0603"}),
             (r"^ERTJ([Z01])", {"Z": "0201", "0": "0402", "1": "0603"}),
             (r"^NTCS" + imp, None), (r"^V\d+MLA" + imp, None), (r"^CG" + imp + "MLC", None),
             (r"^AVRM(0603|1005|1608|2012)", {"0603": "0201", "1005": "0402", "1608": "0603", "2012": "0805"}))
    for rx, conv in rules:
        g = re.match(rx, m)
        if g:
            return conv[g.group(1)] if conv else g.group(1)
    return None
