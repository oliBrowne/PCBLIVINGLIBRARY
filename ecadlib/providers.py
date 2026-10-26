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
  * EasyEDA      - GET component JSON by LCSC number; the only free source of ready symbol / footprint / 3D data
Stubs (document auth, raise NotConfigured without keys): SnapMagic, SamacSys (no self-serve public API).
All response bodies are untrusted: parsed as JSON only, fields read defensively."""
import json, os, re, unicodedata
from dataclasses import dataclass, field
from . import userconfig as UC


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


# words that say what kind of company it is, not which one ("Rohm Semiconductor" is Rohm, "Vishay Intertechnology" Vishay)
_MAKER_WORDS = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "LLC", "GMBH", "AG",
                "SA", "SAS", "SPA", "BV", "NV", "KK", "PLC", "AB", "OY", "THE", "GROUP", "HOLDINGS", "INTERNATIONAL",
                "SEMICONDUCTOR", "SEMICONDUCTORS", "SEMI", "ELECTRONICS", "ELECTRONIC", "ELECTRIC", "TECHNOLOGY",
                "TECHNOLOGIES", "TECH", "INDUSTRIES", "INDUSTRIAL", "DEVICES", "COMPONENTS", "PRODUCTS", "SYSTEMS",
                "MICROELECTRONICS", "INTEGRATED", "INTERTECHNOLOGY", "DIVISION", "DIV", "MANUFACTURING", "MFG"}
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
                "FREESCALE": "NXP",                                              # 2015 (Nexperia is not NXP: below)
                "NATIONAL": "TEXASINSTRUMENTS", "NATSEMI": "TEXASINSTRUMENTS",   # National Semiconductor, 2011
                "LT": "ANALOG", "DALLAS": "ANALOG",                              # Linear Technology; Dallas -> Maxim
                "MICREL": "MICROCHIP", "MICROSEMI": "MICROCHIP", "ACTEL": "MICROCHIP", "SUPERTEX": "MICROCHIP",
                "SMSC": "MICROCHIP", "STANDARDMICROSYSTEMS": "MICROCHIP", "SST": "MICROCHIP",
                "SILICONSTORAGE": "MICROCHIP",                                   # Silicon Storage Technology
                "AVAGO": "BROADCOM", "SPANSION": "INFINEON", "ZETEX": "DIODES", "EXAR": "MAXLINEAR",
                "XILINX": "AMD", "ADVANCEDMICRO": "AMD", "ALTERA": "INTEL",
                "SAMESKY": "CUI", "SEIKOEPSON": "EPSON", "NANJINGQINHENG": "WCH", "JIANGSUQINHENG": "WCH",
                "TE": "TECONNECTIVITY", "TYCO": "TECONNECTIVITY", "AMP": "TECONNECTIVITY",
                "WE": "WURTHELEKTRONIK", "WURTH": "WURTHELEKTRONIK", "GD": "GIGADEVICE",
                "EPCOS": "TDK", "IXYS": "LITTELFUSE", "SILICONIX": "VISHAY", "FCI": "AMPHENOL", "AMSOSRAM": "OSRAM",
                "ELPIDA": "MICRON", "NUMONYX": "MICRON", "WOLFSON": "CIRRUSLOGIC"}
# Nexperia (NXP's Standard Products until 2017) is its own company now, with its own lifecycles and datasheets for the
# discretes and logic NXP once sold: an NXP line is not matched to a Nexperia listing (nor Philips / Freescale to it).


def _maker_words(name):
    """A maker name's own words (upper case, accents folded, a Chinese alias in brackets, company-kind words and a
    sales region after the name left out)."""
    name = clean_maker(name or "") if isinstance(name, str) else ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()       # Würth -> Wurth
    words = re.findall(r"[A-Z0-9]+", re.sub(r"[-'&.]", "", name.upper()))
    kept = [w for w in words if w not in _MAKER_WORDS] or words
    return kept[:1] + [w for w in kept[1:] if w not in _MAKER_REGION]


def _maker_words_all(name):
    name = clean_maker(name or "") if isinstance(name, str) else ""
    return re.findall(r"[A-Z0-9]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper())


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
    if kept and kept[0] == "VISHAY":              # Vishay Dale / Siliconix / Intertechnology / Sprague: one maker
        return "VISHAY"
    joined = "".join(kept)
    return _MAKER_ALIAS.get(joined, joined)          # the whole name only: "ON Shore Technology" is not onsemi


def same_maker(a, b):
    """Do two maker names name the same company? Equal keys; one key starting the other when the shorter has at
    least 4 letters ("Vishay" / "Vishay Siliconix", "Murata" / "Murata Manufacturing"); or a one-word name of 3+
    letters that is the other's first word ("MDD" / "MDD Microdiode"). Never a substring elsewhere: "ON" is not in
    "Monolithic", "TI" is not "Tiger", "ON" is not "ON Shore Technology"."""
    for x, y in ((a, b), (b, a)):              # DigiKey's "Analog Devices Inc./Maxim Integrated": either name
        if isinstance(x, str) and "/" in x and any(same_maker(part, y) for part in x.split("/") if part.strip()):
            return True
    ka, kb = maker_key(a), maker_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = sorted((ka, kb), key=len)
    if len(short) >= 4 and long_.startswith(short):
        return True
    wa, wb = _maker_words(a), _maker_words(b)
    return any(len(x) == 1 and len(x[0]) >= 3 and y and y[0] == x[0] for x, y in ((wa, wb), (wb, wa)))


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
        m = re.search(r"(?<![A-Z0-9])(C\d{1,9})(?![0-9])", raw.upper())   # 'C2040', 'LCSC C2040', a product link:
        lcsc = m.group(1) if m else ""                                    # the number only goes in the URL
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
        return parse_jlcpcb(r, mpn, res, maker=line.get("manufacturer") or "")

    def lookup_lcsc(self, lcsc):
        """The part with this LCSC number (a search for the number; only that exact code counts): its LCSC
        description for a BOM that gave the number, which EasyEDA's answer usually lacks."""
        res = SourceResult(self.name, lcsc=lcsc)
        r = self.post(lcsc)
        if not r.ok:
            res.error = r.error or f"HTTP {r.status}"
            return res
        return parse_jlcpcb(r, "", res, code=lcsc)


def parse_jlcpcb(r, mpn, res, code="", maker=""):
    """One JLCPCB search answer -> res (exact MPN match only, or with code the part with that LCSC number; the field
    paths easyeda2kicad reads). maker: the BOM's manufacturer; of several exact matches (clones sell under the same
    MPN, in other packages too) the one by that maker is taken, not the first listed."""
    try:
        from .kicad_index import norm_mpn
        items = (((r.json().get("data") or {}).get("componentPageInfo") or {}).get("list")) or []
        exact = [i for i in items if (str(i.get("componentCode") or "").strip().upper() == code.upper() if code else
                                      norm_mpn(i.get("componentModelEn")) == norm_mpn(mpn))]
    except (ValueError, AttributeError, TypeError) as e:          # untrusted body of an unexpected shape
        res.error = f"unparseable: {e}"
        return res
    best = next((i for i in exact if maker and same_maker(maker, i.get("componentBrandEn"))), (exact or [None])[0])
    code = str((best or {}).get("componentCode") or "").strip().upper()
    if best and not re.fullmatch(r"C\d{1,9}", code):   # untrusted: only a real LCSC number goes on (into EasyEDA's URL)
        res.error = f"unparseable: componentCode {code[:20]!r} is not an LCSC number"
        return res
    res.found = bool(best)
    if best:
        res.lcsc = code
        res.manufacturer = clean_maker(best.get("componentBrandEn", ""))
        res.package = best.get("componentSpecificationEn", "")
        res.extra = {"stock": best.get("stockCount"), "library_type": best.get("componentLibraryType")}
        desc = describe(best.get("describe") or best.get("description"))
        if desc:                           # JLCPCB/LCSC's description of the part (a category + package says too little)
            res.extra["description"] = desc
    return res


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
        return res
    res.mpn, res.manufacturer, res.url = info.mpn, info.manufacturer, info.url
    specs = getattr(info, "specs", {}) or {}
    res.package = clean_text(specs.get("package") or specs.get("case/package") or "", 80)
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

    def token(self):
        if not (self._token or self._token_err):
            from urllib.parse import urlencode
            body = urlencode({"client_id": UC.secret("DIGIKEY_CLIENT_ID"), "client_secret": UC.secret("DIGIKEY_CLIENT_SECRET"),
                              "grant_type": "client_credentials"})
            r = self.http.post(self.TOKEN_URL, body, cache=False, headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                self._token = r.json().get("access_token") if r.ok else None
            except ValueError:
                pass
            if not self._token:
                self._token_err = f"token request failed: {r.error or f'HTTP {r.status}'}"
        return self._token


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
ALL = [Nexar, DigiKey, Mouser, JLCPCBSearch, EasyEDA, SnapMagic, SamacSys]
# what triage / the librarian ask when neither --providers nor [providers] order says: the official APIs first (each
# is skipped with a "not configured" note until its key is set), then the unofficial EasyEDA route
DEFAULT_ORDER = "nexar,digikey,mouser,jlcpcb_search,easyeda"


def unofficial_allowed(rules):
    """[providers] unofficial (default true): false keeps the undocumented endpoints (EasyEDA, JLCPCB search) out."""
    from . import rules as RU
    return bool(((rules or {}).get("providers") or {}).get("unofficial", RU.DEFAULTS["providers"]["unofficial"]))


def fence(provs, rules):
    """provs (classes or instances) without the undocumented ones when [providers] unofficial = false."""
    return list(provs) if unofficial_allowed(rules) else [p for p in provs if getattr(p, "official", True)]
