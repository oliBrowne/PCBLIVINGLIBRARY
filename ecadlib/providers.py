"""Pluggable part-source providers for BOM triage.

Provider interface:  lookup(line) -> SourceResult   (raise NotConfigured if credentials missing)
Official APIs (documented, with your own free developer keys), asked first by default:
  * Nexar        - Octopart/Altium supply API (GraphQL): MPN -> manufacturer, description, datasheet, and the LCSC
                   number when LCSC is one of the part's sellers (NEXAR_CLIENT_ID/SECRET or NEXAR_SUPPLY_TOKEN)
  * DigiKey      - Product Information API v4 (DIGIKEY_CLIENT_ID/SECRET)
  * Mouser       - Search API v1 (MOUSER_API_KEY)
  These identify the part and give its datasheet; none of them hands out symbol or footprint geometry.
Unofficial (official = False: undocumented endpoints that easyeda2kicad also uses; they can change without notice,
so they are asked last, after the official APIs, and `[providers] unofficial = false` in ecadlib.toml turns them off
(`ecadlib init` asks), and .github/workflows/sources-canary.yml checks them
against the live sites every week):
  * JLCPCBSearch - POST keyword search, resolves MPN -> LCSC number (only when no official source found it)
  * LCSCSearch   - the same through jlcsearch (community parts index) + LCSC's product page JSON, when JLCPCB's
                   search refused or found nothing
  * EasyEDA      - GET component JSON by LCSC number; the only free source of ready symbol / footprint / 3D data
Stubs (document auth, raise NotConfigured without keys): SnapMagic, SamacSys (no self-serve public API).
All response bodies are untrusted: parsed as JSON only, fields read defensively."""
import json, os, re, time, unicodedata
from dataclasses import dataclass, field
from . import userconfig as UC


# an answer that says this account may not ask (Nexar: "You have exceeded your part limit of 0"): the source was
# reached and refused on quota, so a part it did not look up is not "unreachable" and a miss elsewhere is a real miss
_ACCOUNT_LIMIT = re.compile(r"exceeded .{0,40}limit|part limit|quota (exceeded|reached)|limit (exceeded|reached)|"
                            r"too many requests for (this|your) (plan|account)", re.I)


def account_limit(error):
    """Does a source's error say the account's quota or plan limit is used up (not a network failure)?"""
    return bool(error and _ACCOUNT_LIMIT.search(str(error)))


class NotConfigured(Exception):
    pass


def clean_text(v, limit=250):
    """One line of plain text from an untrusted field (None / non-text -> ''), cut at `limit` characters."""
    if not isinstance(v, str):
        return ""
    v = " ".join(v.split())
    return v if len(v) <= limit else v[:limit - 3].rstrip() + "..."


CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff"


def clean_maker(name):
    """EasyEDA / LCSC maker names carry a Chinese alias: 'TI(德州仪器)' -> 'TI', 'YAGEO（国巨）' -> 'YAGEO'. A name
    that is only CJK is kept as it is (there is nothing else to call the maker)."""
    name = clean_text(name, 120)
    short = re.sub(rf"\s*[(（][^()（）]*[{CJK}][^()（）]*[)）]\s*$", "", name).strip()
    return short or name


# the maker a datasheet link names by its domain (www.ti.com/lit/... -> Texas Instruments); None: a shop or a mirror
MAKER_DOMAINS = {"st.com": "STMicroelectronics", "ti.com": "Texas Instruments", "microchip.com": "Microchip", "analog.com": "Analog Devices",
                 "nxp.com": "NXP", "onsemi.com": "onsemi", "vishay.com": "Vishay", "infineon.com": "Infineon", "diodes.com": "Diodes Inc.",
                 "nexperia.com": "Nexperia", "maximintegrated.com": "Maxim Integrated", "allegromicro.com": "Allegro MicroSystems",
                 "littelfuse.com": "Littelfuse", "power.com": "Power Integrations", "powerint.com": "Power Integrations",
                 "silabs.com": "Silicon Labs", "murata.com": "Murata", "wolfspeed.com": "Wolfspeed", "skyworksinc.com": "Skyworks",
                 "semicon-storage.com": "Toshiba", "toshiba.com": "Toshiba", "rohm.com": "ROHM", "broadcom.com": "Broadcom",
                 "kemet.com": "KEMET", "diotec.com": "Diotec", "cypress.com": "Cypress", "renesas.com": "Renesas", "akm.com": "AKM",
                 "bourns.com": "Bourns", "issi.com": "ISSI", "wch-ic.com": "WCH", "microsemi.com": "Microsemi", "osram-os.com": "ams OSRAM",
                 "espressif.com": "Espressif", "irf.com": "Infineon (IR)", "latticesemi.com": "Lattice", "ftdichip.com": "FTDI",
                 "ams.com": "ams OSRAM", "sitime.com": "SiTime", "xilinx.com": "AMD Xilinx", "altera.com": "Intel (Altera)",
                 "intersil.com": "Renesas (Intersil)", "nordicsemi.com": "Nordic Semiconductor", "linear.com": "Analog Devices",
                 "fairchildsemi.com": "onsemi (Fairchild)", "ixys.com": "IXYS", "mps.com": "Monolithic Power Systems",
                 "monolithicpower.com": "Monolithic Power Systems", "richtek.com": "Richtek", "semtech.com": "Semtech",
                 "torexsemi.com": "Torex", "ablic.com": "ABLIC", "sii-ic.com": "ABLIC", "jst-mfg.com": "JST", "molex.com": "Molex",
                 "hirose.com": "Hirose", "we-online.com": "Wurth Elektronik", "te.com": "TE Connectivity", "kingbright.com": "Kingbright",
                 "kingbrightusa.com": "Kingbright", "liteon.com": "Lite-On", "panasonic.com": "Panasonic", "lcsc.com": None,
                 "krhro.com": "HRO", "samtec.com": "Samtec", "amphenol.com": "Amphenol", "cui.com": "CUI", "sameskydevices.com": "Same Sky",
                 "melexis.com": "Melexis", "zilog.com": "Zilog", "atmel.com": "Microchip (Atmel)",
                 "national.com": "Texas Instruments (National)", "awinic.com": "Awinic", "gigadevice.com": "GigaDevice",
                 "winbond.com": "Winbond", "neutrik.com": "Neutrik", "cuidevices.com": "CUI Devices",
                 "switchcraft.com": "Switchcraft", "lumberg.com": "Lumberg", "amphenolrf.com": "Amphenol RF", "kycon.com": "Kycon", "macronix.com": "Macronix", "cree.com": "Wolfspeed (Cree)", "ween-semi.com": "WeEn"}


def maker_from_url(url):
    """The maker a link's domain names (MAKER_DOMAINS, a subdomain too: www.ti.com), or None."""
    m = re.match(r"https?://([^/:]+)", url or "")
    if not m:
        return None
    parts = m.group(1).lower().split(".")
    for k in range(len(parts) - 1):
        d = ".".join(parts[k:])
        if d in MAKER_DOMAINS:
            return MAKER_DOMAINS[d]
    return None


# words that say what kind of company it is, not which one ("Rohm Semiconductor" is Rohm, "Vishay Intertechnology" Vishay)
_MAKER_WORDS = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "LLC", "GMBH", "AG",
                "SA", "SAS", "SPA", "BV", "NV", "KK", "PLC", "AB", "OY", "THE", "GROUP", "HOLDINGS", "INTERNATIONAL",
                "SEMICONDUCTOR", "SEMICONDUCTORS", "SEMI", "ELECTRONICS", "ELECTRONIC", "ELECTRIC", "TECHNOLOGY",
                "TECHNOLOGIES", "TECH", "INDUSTRIES", "INDUSTRIAL", "DEVICES", "COMPONENTS", "PRODUCTS", "SYSTEMS",
                "MICROELECTRONICS", "INTEGRATED", "INTERTECHNOLOGY", "DIVISION", "DIV", "MANUFACTURING", "MFG",
                "ELEC", "ELECT", "ELECTR", "ELECTRON",       # DigiKey's short forms: "Shenzhen Sunlord Elec"
                "AND"}                                       # "Alpha and Omega" is "Alpha & Omega"
# a city or province before a Chinese / Taiwanese maker's own name ("Shenzhen Sunlord", "Yangzhou Yangjie Elec Tech"):
# dropped when a name follows it
_MAKER_PLACE = {"SHENZHEN", "YANGZHOU", "SHANGHAI", "DONGGUAN", "JIANGSU", "GUANGDONG", "GUANGZHOU", "BEIJING",
                "HANGZHOU", "SUZHOU", "WUXI", "XIAMEN", "NINGBO", "ZHEJIANG", "FUJIAN", "CHANGZHOU", "NANJING",
                "CHENGDU", "WUHAN", "TIANJIN", "FOSHAN", "ZHUHAI", "HUIZHOU", "ZHONGSHAN", "SHANDONG", "ANHUI", "HUNAN",
                "HUBEI", "SICHUAN", "TAIWAN", "TAIPEI", "HSINCHU", "KAOHSIUNG", "HONGKONG"}
# where a distributor's sales arm is, after the maker's own name ("NXP USA Inc.", "Murata Electronics North America",
# "GigaDevice Semiconductor (HK) Limited"): not which company it is
_MAKER_REGION = {"USA", "US", "AMERICA", "AMERICAS", "NORTH", "EUROPE", "ASIA", "PACIFIC", "JAPAN", "CHINA", "HK",
                 "GLOBAL", "UNLIMITED"}
# the short names makers go by, and the old names of makers bought by another (their parts are sold under the new
# name now), -> the words of the company's full name (after _MAKER_WORDS are dropped). One table: both ways.
_MAKER_ALIAS = {"TI": "TEXASINSTRUMENTS", "BURRBROWN": "TEXASINSTRUMENTS", "ON": "ON", "ONSEMI": "ON",
                "ST": "ST", "STM": "ST", "ADI": "ANALOG", "MPS": "MONOLITHICPOWER", "AOS": "ALPHAOMEGA", "NXP": "NXP", "PHILIPS": "NXP",
                "FAIRCHILD": "ON", "IR": "INFINEON", "RECTIFIER": "INFINEON", "CYPRESS": "INFINEON",
                "MICROCHIPTECHNOLOGY": "MICROCHIP", "ATMEL": "MICROCHIP", "DIODESINC": "DIODES", "DIODES": "DIODES",
                "RENESAS": "RENESAS", "IDT": "RENESAS", "INTERSIL": "RENESAS",
                "LTC": "ANALOG", "MAXIM": "ANALOG", "HITTITE": "ANALOG",   # now Analog Devices
                "KYOCERAAVX": "AVX", "KYOCERA": "AVX", "SILICONLABS": "SILICONLABORATORIES", "SILABS": "SILICONLABORATORIES",
                # acquisitions: the old maker's name on a BOM, the buyer's at the distributor (and the other way)
                "FREESCALE": "NXP",                                              # 2015 (Nexperia: _MAKER_MOVED below)
                "NATIONAL": "TEXASINSTRUMENTS", "NATSEMI": "TEXASINSTRUMENTS",   # National Semiconductor, 2011
                "LT": "ANALOG", "DALLAS": "ANALOG",                              # Linear Technology; Dallas -> Maxim
                "MICREL": "MICROCHIP", "MICROSEMI": "MICROCHIP", "ACTEL": "MICROCHIP", "SUPERTEX": "MICROCHIP",
                "SMSC": "MICROCHIP", "STANDARDMICROSYSTEMS": "MICROCHIP", "SST": "MICROCHIP",
                "SILICONSTORAGE": "MICROCHIP",                                   # Silicon Storage Technology
                "AVAGO": "BROADCOM", "SPANSION": "INFINEON", "ZETEX": "DIODES", "EXAR": "MAXLINEAR",
                "XILINX": "AMD", "ADVANCEDMICRO": "AMD", "ALTERA": "INTEL",
                "SAMESKY": "CUI", "SEIKOEPSON": "EPSON", "QINHENG": "WCH", "SEMCO": "SAMSUNGELECTROMECHANICS",
                "GCT": "GLOBALCONNECTOR", "HDSC": "HUADA",
                "TE": "TECONNECTIVITY", "TYCO": "TECONNECTIVITY", "AMP": "TECONNECTIVITY",
                "WE": "WURTHELEKTRONIK", "WURTH": "WURTHELEKTRONIK", "GD": "GIGADEVICE",
                "EPCOS": "TDK", "IXYS": "LITTELFUSE", "SILICONIX": "VISHAY", "FCI": "AMPHENOL", "AMSOSRAM": "OSRAM",
                "ELPIDA": "MICRON", "NUMONYX": "MICRON", "WOLFSON": "CIRRUSLOGIC"}
# a first word many companies share ("Analog Devices" / "Analog Power", "General Electric" / "General Semiconductor"):
# alone it names no company, so a name that is only it keeps its company-kind words, and no other name matches it by
# starting with it
_MAKER_GENERIC = {"ANALOG", "GENERAL", "AMERICAN", "NATIONAL", "MICRO", "SILICON", "POWER", "LINEAR", "DIGITAL",
                  "ADVANCED", "UNITED", "CENTRAL", "STANDARD", "UNIVERSAL", "PRECISION", "APPLIED", "SMART", "NEW"}
_MAKER_LEGAL = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "LLC", "GMBH", "AG",
                "SA", "SAS", "SPA", "BV", "NV", "KK", "PLC", "AB", "OY", "THE"}
_MAKER_ALIAS.update({"ANALOGDEVICES": "ANALOG", "NATIONALSEMICONDUCTOR": "TEXASINSTRUMENTS",
                     "CENTRAL": "CENTRALSEMICONDUCTOR", "TEXAS": "TEXASINSTRUMENTS",    # "Central" alone on a BOM
                     "PERICOM": "DIODES", "DIALOG": "RENESAS", "SIPEX": "MAXLINEAR", "CATALYST": "ON"})   # bought
# 2026-11-23 review round 2 (I7): one company under two names, whose own listing was refused as another maker's (a
# needless hold). Only names that are the same company, or one it bought whose parts it sells under the same numbers.
_MAKER_ALIAS.update({"MCC": "MICROCOMMERCIAL",                           # Micro Commercial Components / Co
                     "AUSTRIAMICROSYSTEMS": "OSRAM",     # ams-OSRAM (AMSOSRAM above). Bare "AMS" is not added: on a BOM it
                                                         # is also Advanced Monolithic Systems (AMS1117), as the tests pin
                     "UTC": "UNISONIC",                                  # Unisonic Technologies' brand
                     "SKYWORKSSOLUTIONS": "SKYWORKS",
                     "ISSI": "SILICONSOLUTION", "ISSISILICONSOLUTION": "SILICONSOLUTION",   # Integrated Silicon Solution Inc. (DigiKey: "ISSI, Integrated ...")
                     "MXIC": "MACRONIX",
                     "INVENSENSE": "TDK", "TDKINVENSENSE": "TDK",        # TDK since 2017, listed as "TDK InvenSense"
                     "RFMD": "QORVO", "RFMICRO": "QORVO", "TRIQUINT": "QORVO",   # RF Micro Devices + TriQuint = Qorvo, 2015
                     "HRS": "HIROSE",                                    # Hirose Electric's brand
                     "JAE": "JAPANAVIATION"})                            # Japan Aviation Electronics
# the words of a company name an alias maps to, where it has several: where a shorter name may end in it ("Texas" /
# "TI"), and where not ("Teco" is no "TE" = TE Connectivity)
_ALIAS_WORDS = {"TEXASINSTRUMENTS": ["TEXAS", "INSTRUMENTS"], "TECONNECTIVITY": ["TE", "CONNECTIVITY"],
                "WURTHELEKTRONIK": ["WURTH", "ELEKTRONIK"], "MONOLITHICPOWER": ["MONOLITHIC", "POWER"],
                "ALPHAOMEGA": ["ALPHA", "OMEGA"], "SILICONLABORATORIES": ["SILICON", "LABORATORIES"],
                "SAMSUNGELECTROMECHANICS": ["SAMSUNG", "ELECTROMECHANICS"], "GLOBALCONNECTOR": ["GLOBAL", "CONNECTOR"],
                "CIRRUSLOGIC": ["CIRRUS", "LOGIC"], "CENTRALSEMICONDUCTOR": ["CENTRAL", "SEMICONDUCTOR"]}
# a company's other makers under its own brand, named on their own (distributors list both): the whole key only, so
# "Wurth" is Würth Elektronik and Würth Electronics Midcom, never any "Wurth <anything>" (Würth Industrie, a fastener
# trader), and "Catalyst Lighting" is no Catalyst Semiconductor (onsemi)
_MAKER_GROUP = {"WURTHELEKTRONIK": {"WURTHMIDCOM"},
                # 2026-11-23 review round 2 (I7): a line that moved to another company with its part numbers. The two
                # keys stay apart (no alias), so each still matches its own other names ("Cree" / "Cree LED", the LED
                # company Wolfspeed sold, is not Wolfspeed): only this pair is one maker.
                "CREE": {"WOLFSPEED"},                         # Cree's SiC / RF parts: Cree renamed itself Wolfspeed, 2021
                "SILICONLABORATORIES": {"SKYWORKS"}}           # Silicon Labs' isolation / timing / power parts, 2021
# Nexperia (NXP's Standard Products until 2017) is its own company now, and sells the discretes and logic NXP (and
# Philips before it) once sold under the same type numbers (BC847C,215; 74HC595D,118). 2026-11-23 review round 2 (I7):
# a BOM that still says NXP for such a part was held as "only from Nexperia", so the two names are one maker for a
# number both list. By the name as written, not by key: Freescale (NXP's key) never made Nexperia's parts.
_MAKER_MOVED = {"NXP": {"NEXPERIA"}, "PHILIPS": {"NEXPERIA"}}


def _maker_words(name):
    """A maker name's own words (upper case, accents folded, a Chinese alias in brackets, company-kind words and a
    sales region after the name left out)."""
    name = clean_maker(name or "") if isinstance(name, str) else ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()       # Würth -> Wurth
    words = re.findall(r"[A-Z0-9]+", re.sub(r"[-'&.]", "", name.upper()))
    kept = [w for w in words if w not in _MAKER_WORDS] or words
    if len(kept) > 1 and kept[0] in _MAKER_PLACE:  # "Shenzhen Sunlord", "Yangzhou Yangjie": the city is not the company
        kept = kept[1:]
    return kept[:1] + [w for w in kept[1:] if w not in _MAKER_REGION]


def _maker_words_all(name):
    name = clean_maker(name or "") if isinstance(name, str) else ""
    return re.findall(r"[A-Z0-9]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper())


def _key_words(name):
    """The words a maker's key is made of: _maker_words, or for a name that is only a shared first word ('Analog
    Devices', 'General Semiconductor') its words but the legal form and a sales region."""
    kept = _maker_words(name)
    if len(kept) == 1 and kept[0] in _MAKER_GENERIC:
        words = [w for w in _maker_words_all(name) if w not in _MAKER_LEGAL]
        kept = (words[:1] + [w for w in words[1:] if w not in _MAKER_REGION]) or kept
    return kept


def maker_key(name):
    """A maker's name reduced to which company it is: letters and digits of its words, company-kind words left out,
    a Chinese alias in brackets dropped, short and old names mapped (TI -> Texas Instruments, onsemi / ON
    Semiconductor -> ON, STMicroelectronics -> ST, Freescale -> NXP). '' when nothing is left."""
    kept = _maker_words(name)
    if kept[:2] == ["TEXAS", "INSTRUMENTS"]:
        return "TEXASINSTRUMENTS"
    if kept[:1] == ["LINEAR"] and {"TECHNOLOGY", "TECH"} & set(_maker_words_all(name)):   # Linear Technology (not
        return "ANALOG"                                                  # Linear Integrated Systems): Analog Devices now
    if "".join(kept).startswith("STMICRO"):                              # STMicroelectronics, ST Micro
        return "ST"
    if _maker_words_all(name)[:2] == ["INTEGRATED", "DEVICE"]:            # Integrated Device Technology (IDT), not
        return "RENESAS"                                                 # "Device Engineering": Renesas since 2019
    if kept and kept[0] == "VISHAY":              # Vishay Dale / Siliconix / Intertechnology / Sprague: one maker
        return "VPG" if "PRECISION" in kept else "VISHAY"    # Vishay Precision Group: its own company since 2010
    joined = "".join(_key_words(name))
    return _MAKER_ALIAS.get(joined, joined)          # the whole name only: "ON Shore Technology" is not onsemi


def same_maker(a, b):
    """Do two maker names name the same company? Equal keys; one key starting the other at a word's end when the shorter
    has at least 4 letters and is no shared first word ("Murata" / "Murata Power Solutions", not "Micron" / "Micronas"
    or "Analog Devices" / "Analog Power"); or a one-word name of 3+
    letters that is the other's first word ("MDD" / "MDD Microdiode"). Never a substring elsewhere: "ON" is not in
    "Monolithic", "TI" is not "Tiger", "ON" is not "ON Shore Technology"."""
    for x, y in ((a, b), (b, a)):              # DigiKey's "Analog Devices Inc./Maxim Integrated", "EPCOS - TDK
        if not isinstance(x, str):             # Electronics", "Amphenol ICC (FCI)": either name
            continue
        parts = [re.sub(r"(?i)^\s*(?:formerly|previously|now)\s+", "", p)   # DigiKey's "Aratas (Formerly Omron
                 for p in re.split(r"/|\s+-\s+|[()（）]", x) if p.strip()]      # Components)" is Omron too
        if len(parts) > 1 and any(same_maker(p, y) for p in parts       # "Vishay ... - Diodes Division": a division
                                  if "DIVISION" not in _maker_words_all(p)   # names a part of the company, not another
                                  and not set(_maker_words_all(p)) <= _MAKER_REGION | _MAKER_PLACE | _MAKER_WORDS):
            return True
    ka, kb = maker_key(a), maker_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    (short, _), (long_, ln) = sorted(((ka, a), (kb, b)), key=lambda t: len(t[0]))
    if len(short) >= 4 and short not in _MAKER_GENERIC and long_.startswith(short):
        words = _key_words(ln)                   # at a word's end: "Murata" / "Murata Power Solutions", not "Micron" /
        split = [w for w in re.findall(r"[A-Z0-9]+", re.sub(r"['.]", "", unicodedata.normalize("NFKD", clean_maker(ln))
                 .encode("ascii", "ignore").decode().upper())) if w not in _MAKER_WORDS]      # "Micronas"; "Wakefield-Vette"
        if "".join(words) != long_:              # the long key is an alias's company ("TE" -> TECONNECTIVITY): its
            words, split = _ALIAS_WORDS.get(long_, [long_]), []          # own words, so "Teco" is no "TE"
        if any("".join(ws[:i]) == short for ws in (words, split) for i in range(1, len(ws) + 1)):
            return True
    if kb in _MAKER_GROUP.get(ka, ()) or ka in _MAKER_GROUP.get(kb, ()):   # Würth Elektronik / Würth Electronics Midcom
        return True
    ra, rb = "".join(_key_words(a)), "".join(_key_words(b))                # the names as written, before any alias
    if rb in _MAKER_MOVED.get(ra, ()) or ra in _MAKER_MOVED.get(rb, ()):   # NXP / Nexperia
        return True
    wa, wb = _maker_words(a), _maker_words(b)
    return any(len(x) == 1 and len(x[0]) >= 3 and x[0] not in _MAKER_GENERIC and y and y[0] == x[0] and ky.startswith(kx)
               for x, y, kx, ky in ((wa, wb, ka, kb), (wb, wa, kb, ka)))   # not "Vishay" / VPG


_KNOWN_MAKERS = set()


def _known_maker(name):
    """Is this name, whole, a maker this module knows by name (the alias tables, the makers' datasheet domains)?"""
    if not _KNOWN_MAKERS:
        _KNOWN_MAKERS.update(_MAKER_ALIAS, _MAKER_ALIAS.values(), _MAKER_GROUP, *_MAKER_GROUP.values(), _MAKER_MOVED,
                             *_MAKER_MOVED.values(), (maker_key(m) for m in MAKER_DOMAINS.values()))
        _KNOWN_MAKERS.discard("")
    return maker_key(name) in _KNOWN_MAKERS


def other_makers(maker, listed):
    """The other makers a listing's maker field names beside `maker` (2026-11-23 review round 3, A9: "onsemi / Vishay"
    and "Vishay (onsemi second source)" were Vishay's listing, so onsemi's SMC case went to Vishay's SMA part). A part
    of the field (between / , ; | & + "and" "or" " - " and brackets) counts when it is, or begins with, a maker known
    by name that is not `maker`: a division, a brand or a former name of one company is not another maker ("Vishay
    Beyschlag/Draloric/BC Components", "EPCOS - TDK Electronics", "Aratas (Formerly Omron Components)")."""
    if not isinstance(listed, str) or not isinstance(maker, str):
        return []
    out = []
    for part in re.split(r"[/,;|&+()（）]|\s+-\s+|\s+(?:and|or)\s+", clean_maker(listed)):
        words = _maker_words_all(part)
        if not words or set(words) <= _MAKER_REGION | _MAKER_PLACE | _MAKER_WORDS or "DIVISION" in words \
                or re.match(r"(?i)\s*(?:formerly|previously|now)\s", part):
            continue
        first = words[0] if len(words[0]) >= 4 and words[0] not in _MAKER_GENERIC | _MAKER_WORDS | _MAKER_PLACE else ""
        for name in (part.strip(), first):
            if name and _known_maker(name) and not same_maker(maker, name) and not same_maker(maker, part):
                out.append(part.strip())
                break
    return out


def describe(text="", category="", package=""):
    """The part description a source gives: its own description text, else '<category>, <package>' when it names a
    category (both are the distributor's words; nothing is made up). '' when it gives neither."""
    text = clean_text(text)
    if text:
        return text
    category = clean_text(category, 120)
    return ", ".join(x for x in (category, clean_text(package, 80)) if x) if category else ""


@dataclass
class SourceResult:
    provider: str
    found: bool | None = None          # None = could not determine (error)
    lcsc: str = ""
    mpn: str = ""
    manufacturer: str = ""
    package: str = ""
    has_symbol: bool | None = None
    has_footprint: bool | None = None
    has_3d: bool | None = None
    url: str = ""
    error: str = ""
    extra: dict = field(default_factory=dict)


def result_shape_problem(r):
    """What is wrong with a SourceResult's field types (a third-party plugin's), or '': triage reads them as written."""
    text = ("provider", "lcsc", "mpn", "manufacturer", "package", "url", "error")
    bad = [f for f in text if not isinstance(getattr(r, f), str)]
    bad += [f for f in ("found", "has_symbol", "has_footprint", "has_3d") if getattr(r, f) not in (None, True, False)]
    bad += [] if r.extra is None or isinstance(r.extra, dict) else ["extra"]
    return ", ".join(f"{f} is {type(getattr(r, f)).__name__}" for f in bad)


class Provider:
    name = "base"
    auth = "none"                       # human-readable description of the credentials needed
    env = ()                            # environment variables that must be set
    official = True                     # False: an undocumented endpoint ([providers] unofficial = false drops it)

    def __init__(self, http):
        self.http = http

    def configured(self):
        return all(UC.secret(e) for e in self.env)

    def lookup(self, line) -> SourceResult:
        raise NotImplementedError


# ------------------------------------------------------------------ EasyEDA (LCSC id -> CAD data)
class EasyEDA(Provider):
    name = "easyeda"
    auth = "none (unofficial: undocumented endpoint used by easyeda2kicad; Referer https://easyeda.com/ header)"
    official = False
    # easyeda2kicad 1.0.1: API_ENDPOINT = "https://easyeda.com/api/products/{lcsc_id}/components"
    # (older releases appended ?version=6.4.19.5; kept here since the server ignores/accepts it)
    URL = "https://easyeda.com/api/products/{}/components?version=6.4.19.5"
    HEADERS = {"Accept": "application/json, text/javascript, */*; q=0.01", "Referer": "https://easyeda.com/"}

    def lookup(self, line):
        raw = str(line.get("lcsc") or "").strip()
        lcsc = lcsc_number(raw)                                           # the number only goes in the URL
        if not lcsc:
            return SourceResult(self.name, found=None, error=f"{raw[:30]!r} is not an LCSC number" if raw else "no LCSC number")
        url = self.URL.format(lcsc)
        r = self.http.get(url, headers=self.HEADERS)
        res = SourceResult(self.name, lcsc=lcsc, url=f"https://www.lcsc.com/product-detail/{lcsc}.html")
        if not r.ok:
            res.error = r.error or f"HTTP {r.status}"
            return res
        try:
            return parse_easyeda(r.json(), res)
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res


def lcsc_number(raw):
    """The LCSC number in a cell: 'C2040', 'LCSC C2040', or a product link's own number ('..._C1663.html', '.../C2040'),
    not a C0G / C1 in the link's slug before it. '' when there is none, or two different ones in free text."""
    up = str(raw or "").strip().upper()
    for pat in (r"(?:LCSC\W*)?(C\d{1,9})", r".*_(C\d{1,9})\.HTML?(?:[?#].*)?", r".*/(C\d{1,9})(?:\.HTML?)?/?(?:[?#].*)?"):
        m = re.fullmatch(pat, up)
        if m:
            return m.group(1)
    found = set(re.findall(r"(?<![A-Z0-9])(C\d{1,9})(?![0-9A-Z])", up))     # a whole word: not the C0 of a C0G
    return found.pop() if len(found) == 1 else ""                         # two different numbers: neither


def parse_easyeda(data, res):
    """Field paths follow easyeda2kicad's importer: result.dataStr.head.c_para (symbol),
    result.packageDetail.dataStr (footprint), 'SVGNODE~{json}' shape with c_etype outline3D (3D)."""
    if not isinstance(data, dict) or data.get("success") is False or not data.get("result"):
        res.found = False
        return res
    r = data["result"]
    ds = r.get("dataStr") or {}
    cpara = (ds.get("head") or {}).get("c_para") or {}
    pkg = r.get("packageDetail") or {}
    pshape = (pkg.get("dataStr") or {}).get("shape") or []
    res.found = True
    res.mpn = cpara.get("Manufacturer Part") or cpara.get("BOM_Manufacturer Part") or r.get("title", "")
    res.manufacturer = clean_maker(cpara.get("Manufacturer") or cpara.get("BOM_Manufacturer") or "")
    res.package = cpara.get("package") or pkg.get("title", "")
    res.has_symbol = bool(ds.get("shape")) or bool(r.get("subparts"))
    res.has_footprint = bool(pshape)
    res.has_3d = False
    for s in pshape:
        if isinstance(s, str) and s.startswith("SVGNODE~"):
            try:
                node = json.loads(s.split("~", 1)[1])
            except ValueError:
                continue
            attrs = node.get("attrs", {})
            if attrs.get("c_etype") == "outline3D" or attrs.get("uuid"):
                res.has_3d = True
                res.extra["model_uuid"] = attrs.get("uuid", "")
    res.extra["footprint_name"] = pkg.get("title", "")
    desc = describe(r.get("description"))
    if desc:                                   # result.description (the part's own text) only: its tag and EasyEDA's
        res.extra["description"] = desc        # footprint name ("Crystals, CRYSTAL-SMD_L7.0-W4.0-LS7.8") say too little
    return res


# ------------------------------------------------------------------ JLCPCB parts search (MPN -> LCSC)
class JLCPCBSearch(Provider):
    name = "jlcpcb_search"
    note_label = "jlcpcb"
    auth = "none (unofficial: anonymous JSON POST; Origin/Referer jlcpcb.com) - as used by easyeda2kicad search"
    official = False
    URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
    HEADERS = {"Content-Type": "application/json", "Origin": "https://jlcpcb.com", "Referer": "https://jlcpcb.com/parts"}
    REFUSED = {403: "JLCPCB refused the request (403)", 429: "JLCPCB refused the request (429, too many requests)"}

    def post(self, keyword, size=10):
        """One search request. JLCPCB answers 403 (or 429) when it blocks automated searches: that is reported as
        'JLCPCB refused the request (403)' and the rest of the run sends JLCPCB nothing more (no retries, no hammering;
        the refusal is kept on the HTTP client, so every JLCPCB source sharing it stops). Callers go on to their other
        sources."""
        from .httpcache import Response
        refused = getattr(self.http, "_jlcpcb_refused", None)
        if refused:
            return Response(refused[0], "", error=f"{refused[1]}; not asked again this run")
        r = self.http.post(self.URL, json.dumps({"keyword": keyword, "currentPage": 1, "pageSize": size}), headers=self.HEADERS)
        if r.status in self.REFUSED and not r.ok:
            msg = self.REFUSED[r.status]
            try:
                self.http._jlcpcb_refused = (r.status, msg)
            except AttributeError:            # a client that takes no attributes: nothing remembered
                pass
            return Response(r.status, r.text, error=f"{msg}: the site blocks automated part searches from here; "
                                                    "other sources are still tried (set the LCSC number in the BOM to skip the search)")
        return r

    def lookup(self, line):
        mpn = (line.get("mpn") or "").strip()
        if not mpn:
            return SourceResult(self.name, found=None, error="no MPN")
        r = self.post(mpn)
        res = SourceResult(self.name, mpn=mpn)
        if not r.ok:
            res.error = r.error or f"HTTP {r.status}"
            return res
        return parse_jlcpcb(r, mpn, res, maker=line.get("manufacturer") or "", package=bom_land(line))

    def lookup_lcsc(self, lcsc):
        """The part with this LCSC number (a search for the number; only that exact code counts): its LCSC
        description for a BOM that gave the number, which EasyEDA's answer usually lacks."""
        res = SourceResult(self.name, lcsc=lcsc)
        r = self.post(lcsc)
        if not r.ok:
            res.error = r.error or f"HTTP {r.status}"
            return res
        return parse_jlcpcb(r, "", res, code=lcsc)


def bom_land(line):
    """The land a BOM line names, for comparing with a source's package: its footprint cell, else the package word in
    its description ('Dual op amp SOIC-8' -> 'SOIC-8'), else ''."""
    fp = str(line.get("footprint") or "").strip()
    if fp:
        return fp
    from .kicad_index import PKG_RE
    m = PKG_RE.search(str(line.get("description") or ""))
    return m.group(0) if m else ""


def parse_jlcpcb(r, mpn, res, code="", maker="", package=""):
    """One JLCPCB search answer -> res (exact MPN match only, or with code the part with that LCSC number; the field
    paths easyeda2kicad reads). maker: the BOM's manufacturer; package: the BOM's land (bom_land). Of several exact
    matches (clones sell under the same MPN, one maker in DIP-8 and SOP-8 too) the one by that maker in that package
    is taken, then the maker's, then one in that package, then the first listed; when the BOM names a land and the
    one taken is listed in another, extra['package_note'] says so. When the BOM names a maker and every listing is by
    a known other maker, none is taken: not found, with extra['note'] saying who lists it (pick_listing)."""
    try:
        items = (((r.json().get("data") or {}).get("componentPageInfo") or {}).get("list")) or []
    except (ValueError, AttributeError, TypeError) as e:          # untrusted body of an unexpected shape
        res.error = f"unparseable: {e}"
        return res
    return pick_listing(items, mpn, res, code=code, maker=maker, package=package)


def pick_listing(items, mpn, res, code="", maker="", package=""):
    """LCSC listings in JLCPCB's search shape (componentCode, componentModelEn, componentBrandEn,
    componentSpecificationEn, describe, dataManualUrl, stockCount, componentLibraryType) -> res: the choice
    parse_jlcpcb describes. The LCSC search route (LCSCSearch) builds the same rows from its own answers."""
    try:
        from .kicad_index import norm_mpn
        exact = [i for i in items if (str(i.get("componentCode") or "").strip().upper() == code.upper() if code else
                                      norm_mpn(i.get("componentModelEn")) == norm_mpn(mpn))]
    except (ValueError, AttributeError, TypeError) as e:          # untrusted body of an unexpected shape
        res.error = f"unparseable: {e}"
        return res
    from .altium_export import lands_differ
    land = str(package or "").strip()

    def by_maker(i):
        return bool(maker) and same_maker(maker, i.get("componentBrandEn"))

    def in_land(i):                        # an empty listing package says nothing, so it is not "in the BOM's land"
        spec = str(i.get("componentSpecificationEn") or "").strip()
        return bool(land and spec) and not lands_differ(land, spec)
    rest = exact
    if not code and maker_key(maker) and not any(by_maker(i) for i in exact):
        # the BOM names a maker and no listing is by it: a listing by a known other maker (a clone under the same
        # MPN) is not this part, so its LCSC number and package are not taken; said, as DigiKey's keyword route says
        # it (2026-11-23 review 1.11: TI's LM358DR got HGSEMI's C-number). A listing naming no maker still counts.
        rest = [i for i in exact if not maker_key(clean_maker(i.get("componentBrandEn", "")))]
        if exact and not rest:
            others = list(dict.fromkeys(m for m in (clean_maker(i.get("componentBrandEn", "")) for i in exact) if m))
            res.found = False
            res.extra = {**(res.extra or {}), "other_maker": True,
                         "note": clean_text(f"LCSC lists {mpn} only from {', '.join(others[:3])}, not {str(maker).strip()}: "
                                            "check the BOM's maker", 300)}
            return res
    best = next((i for i in exact if by_maker(i) and in_land(i)), None) or next((i for i in exact if by_maker(i)), None) \
        or next((i for i in rest if in_land(i)), None) or (rest or [None])[0]
    code = str((best or {}).get("componentCode") or "").strip().upper()
    if best and not re.fullmatch(r"C\d{1,9}", code):   # untrusted: only a real LCSC number goes on (into EasyEDA's URL)
        res.error = f"unparseable: componentCode {code[:20]!r} is not an LCSC number"
        return res
    res.found = bool(best)
    if best:
        res.lcsc = code
        res.manufacturer = clean_maker(best.get("componentBrandEn", ""))
        makers = {}                        # every maker LCSC lists this exact MPN under (clones share MPNs): one
        for i in exact:                    # maker_key -> its name, so a caller can tell one maker from several
            m = clean_maker(i.get("componentBrandEn", ""))
            if m:
                makers.setdefault(maker_key(m) or m.upper(), m)
        res.package = best.get("componentSpecificationEn", "")
        res.extra = {"stock": best.get("stockCount"), "library_type": best.get("componentLibraryType"),
                     "makers": sorted(makers.values())}
        desc = describe(best.get("describe") or best.get("description"))
        if desc:                           # JLCPCB/LCSC's description of the part (a category + package says too little)
            res.extra["description"] = desc
        sheet = str(best.get("dataManualUrl") or "").strip()
        if re.fullmatch(r"https?://[^\s\"'<>]{4,2000}", sheet):   # LCSC's own copy of the part's datasheet: the answer
            res.extra["datasheet"] = sheet  # names the file itself, where the lcsc.com/datasheet/<C>.pdf pattern
                                            # now gives a web page (IFX25401TBV, 2026-11-13); untrusted: only a link
        spec = clean_text(best.get("componentSpecificationEn", ""), 80)
        if land and spec and not in_land(best):
            res.extra["package_note"] = (f"LCSC {code} ({res.manufacturer or 'maker not listed'}) is listed as {spec}, "
                                         f"the BOM names {clean_text(land, 80)}: check the package before using its footprint")
    return res


# ------------------------------------------------------------------ LCSC search (MPN -> LCSC, second route)
def _lcsc_code(v):
    """'C7593' from a listing's LCSC number (7593, '7593', 'C7593'), '' when it is not one (untrusted field)."""
    if isinstance(v, bool):
        return ""
    t = str(v).strip().upper() if isinstance(v, (int, str)) else ""
    t = t if t.startswith("C") else "C" + t
    return t if re.fullmatch(r"C\d{1,9}", t) else ""


class LCSCSearch(Provider):
    """A second way to the LCSC number when JLCPCB's search refuses (it answered 403 after 12 of 998 lines in the
    1000-part run, 2026-11-17) or lists nothing: MPN -> LCSC number by the jlcsearch parts index (tscircuit's free
    community index of JLCPCB/LCSC parts, MIT, no key), then each exact match's own LCSC product page data by its
    number (wmsc.lcsc.com, the JSON LCSC's website reads: maker, package, datasheet link, description).

    Only an exact MPN counts, never a near one; a product page whose number or MPN differs from the listing is not
    used. The maker is named only when every exact listing's product page was read and names one (clones share
    MPNs); otherwise extra['makers_partial'] tells triage the makers listed are not all of them. The rows go
    through pick_listing, the same choice as JLCPCB's search. A 403/429 stops that site for the rest of the run."""
    name = "lcsc_search"
    note_label = "lcsc"
    auth = ("none (unofficial: jlcsearch.tscircuit.com/api/search, a community index of JLCPCB/LCSC parts; "
            "wmsc.lcsc.com/ftps/wm/product/detail, LCSC's website JSON)")
    official = False
    SEARCH_URL = "https://jlcsearch.tscircuit.com/api/search?q={}&limit=20"
    DETAIL_URL = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={}"
    HEADERS = {"Accept": "application/json"}
    SITES = {"jlcsearch": "the jlcsearch parts index", "wmsc": "LCSC's product page"}
    DETAIL_CAP = 6                       # product pages read per MPN (clone families are listed many times)

    def _get(self, url, site):
        from .httpcache import Response
        refused = (getattr(self.http, "_lcsc_refused", None) or {}).get(site)
        if refused:
            return Response(refused[0], "", error=f"{refused[1]}; not asked again this run")
        r = self.http.get(url, headers=self.HEADERS)
        if r.status in (403, 429) and not r.ok:
            msg = f"{self.SITES[site]} refused the request ({r.status})"
            try:
                memo = getattr(self.http, "_lcsc_refused", None)
                if memo is None:
                    memo = self.http._lcsc_refused = {}
                memo[site] = (r.status, msg)
            except AttributeError:
                pass
            return Response(r.status, r.text, error=msg)
        return r

    def detail(self, code):
        """LCSC's product data for one number: a JLCPCB-shaped row, or (None, error). Only an answer for that very
        number counts (result.productCode); `{"result": null}` is no such product."""
        r = self._get(self.DETAIL_URL.format(code), "wmsc")
        if not r.ok:
            return None, r.error or f"HTTP {r.status}"
        try:
            data = r.json()
            res = data.get("result") if isinstance(data, dict) else None
        except (ValueError, AttributeError) as e:
            return None, f"unparseable: {e}"
        if not isinstance(res, dict) or not res:
            return None, ""
        if _lcsc_code(res.get("productCode")) != code:
            return None, f"answered for {clean_text(str(res.get('productCode')), 20)!r}, not {code}"
        model = clean_text(res.get("productModel"), 80)
        sheet = res.get("pdfUrl")
        sheet = sheet[0] if isinstance(sheet, list) and sheet else sheet
        return {"componentCode": code, "componentModelEn": model, "componentBrandEn": clean_text(res.get("brandNameEn"), 80),
                "componentSpecificationEn": clean_text(res.get("encapStandard"), 80),
                "describe": res.get("productIntroEn") or res.get("productDescEn") or "",
                "dataManualUrl": sheet if isinstance(sheet, str) else "",
                "stockCount": res.get("stockNumber") if isinstance(res.get("stockNumber"), int) else None}, ""

    def lookup(self, line):
        from .kicad_index import norm_mpn
        mpn = (line.get("mpn") or "").strip()
        res = SourceResult(self.name, mpn=mpn)
        if not mpn:
            res.error = "no MPN"
            return res
        from urllib.parse import quote
        r = self._get(self.SEARCH_URL.format(quote(mpn, safe="")), "jlcsearch")
        if not r.ok:
            res.error = r.error or f"HTTP {r.status}"
            return res
        try:
            comps = r.json().get("components")
            comps = [c for c in comps if isinstance(c, dict)] if isinstance(comps, list) else None
        except (ValueError, AttributeError) as e:
            res.error = f"unparseable: {e}"
            return res
        if comps is None:
            res.error = "unparseable: no components list in the answer"
            return res
        want, rows = norm_mpn(mpn), {}
        for c in comps:
            code = _lcsc_code(c.get("lcsc"))
            if code and want and norm_mpn(c.get("mfr")) == want and code not in rows:
                rows[code] = c
        if not rows:
            res.found = False
            return res
        items, unread, notes = [], 0, []
        for n, (code, c) in enumerate(rows.items()):
            d, err = self.detail(code) if n < self.DETAIL_CAP else (None, "")
            if d and norm_mpn(d["componentModelEn"]) != want:     # LCSC's page names another part: not this listing
                notes.append(f"{code} is {d['componentModelEn'] or 'another part'} on LCSC, not {mpn}: not used")
                continue
            if d and not d["componentBrandEn"]:                    # a page naming no maker: its maker is not known
                unread += 1
            if not d:
                unread += 1
                if err:
                    notes.append(f"{code}: {err}")
                d = {"componentCode": code, "componentModelEn": clean_text(c.get("mfr"), 80), "componentBrandEn": "",
                     "componentSpecificationEn": clean_text(c.get("package"), 80), "describe": c.get("description") or "",
                     "dataManualUrl": "", "stockCount": c.get("stock") if isinstance(c.get("stock"), int) else None}
            items.append(d)
        if not items:
            res.found = False
            res.extra = {"note": "; ".join(notes)[:300]} if notes else {}
            return res
        pick_listing(items, mpn, res, maker=line.get("manufacturer") or "", package=bom_land(line))
        if res.found and unread:         # a listing whose maker is not known may be a clone's: the makers read are
            if len(items) > 1:           # not all of them (triage treats the part as sold by several, naming none)
                res.extra["makers_partial"] = True
            elif not res.manufacturer:
                res.extra["makers"] = []
        if res.found and notes:
            res.extra["note"] = "; ".join(notes)[:300]
        return res

    def lookup_lcsc(self, lcsc):
        """The part with this LCSC number, from its LCSC product page (description, datasheet, maker, package)."""
        res = SourceResult(self.name, lcsc=lcsc)
        code = lcsc_number(lcsc)
        if not code:
            res.error = f"{str(lcsc)[:30]!r} is not an LCSC number"
            return res
        d, err = self.detail(code)
        if not d:
            if err:
                res.error = err
            else:
                res.found = False
            return res
        return pick_listing([d], "", res, code=code)


# the part searches that turn an MPN into an LCSC number (triage asks them only while no LCSC number is known)
LCSC_SEARCHES = (JLCPCBSearch, LCSCSearch)


# ------------------------------------------------------------------ authenticated distributor / CAD stubs
class _Stub(Provider):
    docs = ""

    def lookup(self, line):
        if not self.configured():
            raise NotConfigured(f"{self.name}: set {', '.join(self.env)} ({self.auth})")
        raise NotImplementedError(f"{self.name}: credentials present but client not implemented in prototype. {self.docs}")


def lcsc_sku(text):
    """'C2040' from an LCSC SKU or product link, '' when it is not one."""
    m = re.search(r"(?<![A-Za-z0-9])(C\d{3,9})(?![0-9])", str(text or ""))
    return m.group(1) if m else ""


def from_part_info(info, res):
    """part_lookup.PartInfo -> SourceResult. Official sources identify the part (MPN, maker, description, datasheet,
    package, LCSC number via LCSC's offer); they carry no CAD geometry, so has_symbol / has_footprint stay None and
    triage goes on to the next source."""
    if info.error:
        res.error = info.error
        return res
    res.found = info.found
    if not info.found:
        if getattr(info, "note", ""):          # why it is not this part (another maker's listing, a near number)
            res.extra["note"] = clean_text(info.note, 300)
        return res
    res.mpn, res.manufacturer, res.url = info.mpn, info.manufacturer, info.url
    if getattr(info, "makers", None):      # every maker the source lists the exact MPN under (bom_triage's maker fill)
        res.extra["makers"] = [clean_maker(m) for m in info.makers]
    specs = getattr(info, "specs", {}) or {}
    res.package = clean_text(specs.get("package_text") or specs.get("package") or specs.get("case/package") or "", 80)
    if specs.get("device_package"):        # DigiKey's 'Supplier Device Package' ('32-TQFP (7x7)', 'SC-59')
        res.extra["device_package"] = clean_text(specs["device_package"], 80)
    if isinstance(specs.get("dims"), dict) and specs["dims"]:    # its measurements as written (ecadlib.search_land)
        res.extra["dims"] = {str(k): clean_text(v, 80) for k, v in specs["dims"].items()}
    if specs.get("height_text"):           # the body's height as the source writes it ('0.083" (2.11mm)')
        res.extra["height"] = clean_text(specs["height_text"], 60)
    for o in info.offers:
        if re.search(r"\blcsc\b", str(o.distributor), re.I) and lcsc_sku(o.sku):
            res.lcsc = lcsc_sku(o.sku)
            break
    desc = describe(info.description)
    if desc:
        res.extra["description"] = desc
    links = [u for u in (getattr(info, "datasheets", None) or [getattr(info, "datasheet", "")]) if re.match(r"https?://", str(u))]
    if links:
        res.extra["datasheet"], res.extra["datasheets"] = links[0], links
    return res


class _Official(Provider):
    """Delegates to the part_lookup client of the same API (one per provider, so its OAuth token is fetched once)."""
    client_cls = ""

    def _client(self):
        from . import part_lookup as S
        cls = getattr(S, self.client_cls)
        if isinstance(self, cls):
            return self
        if getattr(self, "_src", None) is None:
            self._src = cls(self.http)
        return self._src

    def lookup(self, line):
        if not self.configured():
            raise NotConfigured(f"{self.name}: set {', '.join(self.env)} ({self.auth})")
        mpn = (line.get("mpn") or "").strip()
        res = SourceResult(self.name, mpn=mpn)
        if not mpn:
            res.error = "no MPN"
            return res
        try:
            info = self._client().part({"mpn": mpn, "manufacturer": (line.get("manufacturer") or "").strip(),
                                        "lcsc": (line.get("lcsc") or "").strip()})
            return from_part_info(info, res)
        except (ValueError, AttributeError, TypeError, KeyError) as e:   # an answer of an unexpected shape (a JSON
            res.error = f"unparseable response: {type(e).__name__}: {e}"[:200]   # list, say) is this source's error,
            return res                                                   # not a crashed triage


class DigiKey(_Official):
    name, env = "digikey", ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")
    auth = "OAuth2 client-credentials app from developer.digikey.com (Product Information API v4)"
    client_cls = "DigiKeySourcing"
    TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
    _token = _token_err = None
    _token_due = 0.0                       # time.monotonic() after which the token is renewed before use

    def token(self):
        """DigiKey's client-credentials token lasts about ten minutes (expires_in), so a long run renews it."""
        if self._token and time.monotonic() >= self._token_due:
            self._token = None
        if not (self._token or self._token_err):
            from urllib.parse import urlencode
            body = urlencode({"client_id": UC.secret("DIGIKEY_CLIENT_ID"), "client_secret": UC.secret("DIGIKEY_CLIENT_SECRET"),
                              "grant_type": "client_credentials"})
            r = self.http.post(self.TOKEN_URL, body, cache=False, headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                d = r.json() if r.ok else {}
            except ValueError:
                d = {}
            d = d if isinstance(d, dict) else {}
            tok = d.get("access_token")
            self._token = tok if isinstance(tok, str) and tok else None
            try:                                   # an odd expires_in never throws away a good token: assume ten minutes
                life = float(d.get("expires_in", 600))
            except (ValueError, TypeError):
                life = 600.0
            if life != life or life < 0 or life == float("inf"):
                life = 600.0
            if not self._token:
                self._token_err = f"token request failed: {r.error or f'HTTP {r.status}'}"
            self._token_due = time.monotonic() + max(0.0, life - 60)      # a minute early, never mid-request
        return self._token

    def authed_get(self, url, headers):
        """GET with the bearer token; a 401 (DigiKey ended the token early) renews it once and asks again.
        headers: a function of the token, so the retry carries the new one."""
        r = self.http.get(url, headers=headers(self._token))
        if r.status == 401:
            self._token = None
            if self.token():
                r = self.http.get(url, headers=headers(self._token))
        return r


class Mouser(_Official):
    name, env = "mouser", ("MOUSER_API_KEY",)
    auth = "Search API key (free, mouser.com/api-hub) passed as ?apiKey="
    client_cls = "MouserSourcing"


class Nexar(_Official):
    name, env = "nexar", ("NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET")
    auth = "OAuth2 client-credentials (identity.nexar.com) for the Octopart/Nexar GraphQL API; quota-limited"
    client_cls = "NexarSourcing"
    TOKEN_URL = os.environ.get("NEXAR_TOKEN_URL", "https://identity.nexar.com/connect/token")
    API_URL = os.environ.get("NEXAR_API_URL", "https://api.nexar.com/graphql")
    _token = _token_err = None

    def configured(self):
        return bool(UC.secret("NEXAR_SUPPLY_TOKEN")) or super().configured()

    def token(self):
        if self._token or self._token_err:
            return self._token
        tok = UC.secret("NEXAR_SUPPLY_TOKEN")
        if not tok:
            from urllib.parse import urlencode
            body = urlencode({"grant_type": "client_credentials", "client_id": UC.secret("NEXAR_CLIENT_ID"),
                              "client_secret": UC.secret("NEXAR_CLIENT_SECRET"), "scope": "supply.domain"})
            r = self.http.post(self.TOKEN_URL, body, cache=False,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                tok = r.json().get("access_token") if r.ok else None
            except ValueError:
                tok = None
            if not tok:
                self._token_err = f"token request failed: {r.error or f'HTTP {r.status}'}"
        self._token = tok
        return tok


class SnapMagic(_Stub):
    name, env = "snapmagic", ("SNAPMAGIC_API_TOKEN",)
    auth = "SnapMagic (ex-SnapEDA) partner API token - no self-serve public API; CAD downloads need a user login"
    docs = "Partner/enterprise API by agreement; otherwise manual download from snapeda.com."


class SamacSys(_Stub):
    name, env = "samacsys", ("SAMACSYS_USERNAME", "SAMACSYS_PASSWORD")
    auth = "componentsearchengine.com account; access is via Library Loader / partner API, no documented public REST"
    docs = "Library Loader desktop app or supplier-embedded (Mouser/RS 'ECAD model') links."


# official APIs first, the unofficial EasyEDA route after them (JLCPCB search before EasyEDA: it finds the LCSC number
# EasyEDA is keyed by). This order is also what "every built-in" means (the web UI / MCP workspace's triage default).
ALL = [Nexar, DigiKey, Mouser, JLCPCBSearch, LCSCSearch, EasyEDA, SnapMagic, SamacSys]
# what triage / the librarian ask when neither --providers nor [providers] order says: the official APIs first (each
# is skipped with a "not configured" note until its key is set), then the unofficial EasyEDA route
DEFAULT_ORDER = "nexar,digikey,mouser,jlcpcb_search,lcsc_search,easyeda"


def unofficial_allowed(rules):
    """[providers] unofficial (default true): false keeps the undocumented endpoints (EasyEDA, JLCPCB search) out."""
    from . import rules as RU
    return bool(((rules or {}).get("providers") or {}).get("unofficial", RU.DEFAULTS["providers"]["unofficial"]))


def fence(provs, rules):
    """provs (classes or instances) without the undocumented ones when [providers] unofficial = false."""
    return list(provs) if unofficial_allowed(rules) else [p for p in provs if getattr(p, "official", True)]
