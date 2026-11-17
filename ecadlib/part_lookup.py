"""Part identification through the official distributor APIs: Nexar (Octopart), DigiKey and Mouser, plus JLCPCB's
search. providers.py asks these clients who makes a part, what it is, its datasheet link, package and LCSC number.

Price, stock and alternates (`ecadlib sourcing`) were cut on 2026-10-19: Octopart and DigiKey do that better. The
offers a source returns are still read, only for the LCSC number (an LCSC offer's SKU).

Auth reuses the lifecycle clients (lifecycle.py): NEXAR_CLIENT_ID/NEXAR_CLIENT_SECRET (or NEXAR_SUPPLY_TOKEN),
DIGIKEY_CLIENT_ID/DIGIKEY_CLIENT_SECRET, MOUSER_API_KEY. JLCPCB needs none. Responses are cached by httpcache
(query-string secrets such as Mouser's apiKey are redacted from cache keys and files; token requests are never
cached). Response shapes: each provider's `shapes` says what was checked against public docs or the Nexar schema
and what is assumed."""
import json, os, re, time
from dataclasses import dataclass, field
from urllib.parse import quote

from . import lifecycle as L, providers as P
from .kicad_index import norm_mpn, package_of, same_mpn
from . import userconfig as UC


def _int(v):
    return L._int(v)


# ------------------------------------------------------------------ numbers and specs
_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "R": 1.0}


def si(s):
    """'10 kOhms' / '100nF' / '0.1 µF' / '4k7' / '1/10W' / '0.1W, 1/10W' / '16 VDC' -> float (first number), else None.
    Capital P / N / U before F or H are pico / nano / micro ('10UF', '100NF'); '1E-7 F' is an exponent."""
    s = str(s or "").strip()
    m = re.match(r"(\d+)([pnuµμmkKMGR])(\d+)\b", s)                      # 4k7, 4R7, 2n2
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * _SI[m.group(2)]
    m = re.search(r"(\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?\s*([pnuµμmkKMG]|[PNU](?=[FH]))?", s)
    if not m:
        return None
    v = float(m.group(1)) / (float(m.group(2)) if m.group(2) else 1.0)
    return v * _SI[(m.group(3) or "").replace("P", "p").replace("N", "n").replace("U", "u")]


def pct(s):
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(s or ""))
    return float(m.group(1)) if m else None


def dielectric(s):
    m = re.search(r"\b(C0G|NP0|COG|NPO|X8R|X7R|X7S|X7T|X6S|X5R|Y5V|Z5U)\b", str(s or "").upper())
    return {"COG": "C0G", "NPO": "NP0"}.get(m.group(1), m.group(1)) if m else None


SPEC_RX = [   # (canonical, regex on the attribute name/shortname, parser); first match per attribute wins
    ("value:R", r"^resistance\b", si), ("value:F", r"^capacitance\b", si), ("value:H", r"^inductance\b", si),
    ("tolerance", r"tolerance", pct),
    ("voltage", r"voltage.*(rat|dc)|rated voltage|voltagerating|^voltage$", si),
    ("dielectric", r"dielectric|temperature ?coefficient|tempco", dielectric),
    ("power", r"^power|power ?rating", si),
    ("current", r"current.*rat|rated current|currentrating|^current$", si),
    ("package", r"case|package|^size$", lambda v: package_of(v)),
    ("height_text", r"^height\b", lambda v: str(v or "").strip()[:60] or None),   # DigiKey 'Height - Seated (Max)':
]                                                                                   # a tantalum's EIA height code


def specs_from_pairs(pairs):
    """[(attribute name, value text)] from any source -> {value, unit, tolerance, voltage, dielectric, power, current, package}."""
    out = {}
    for name, val in pairs:
        n = str(name or "").strip().lower().replace("_", " ")
        for key, rx, parse in SPEC_RX:
            if not re.search(rx, n):
                continue
            if key == "package" and re.search(r"\bmm\b|metric", n):      # Mouser 'Case Code - mm: 0402' is 01005
                break
            if key == "package" and str(val or "").strip():   # the text as the source wrote it ('8-SOIC (0.154", 3.90mm
                text = str(val).strip()[:80]                  # Width)'): package_of reads chip codes only; DigiKey's
                if "case" in n:                               # 'Package / Case' says more than 'Supplier Device Package'
                    out["package_text"] = text
                else:
                    out.setdefault("package_text", text)
            v = parse(val)
            if v is not None:
                if key.startswith("value:"):
                    out.setdefault("value", v)
                    out.setdefault("unit", key[6:])
                else:
                    out.setdefault(key, v)
            break
    return out


# ------------------------------------------------------------------ offers and buying
@dataclass
class Offer:
    source: str                        # provider that reported it
    distributor: str
    sku: str = ""
    stock: int | None = None
    moq: int = 1
    mult: int = 1
    breaks: list = field(default_factory=list)      # [(qty, unit price)] ascending
    currency: str = ""
    lead_days: int | None = None
    packaging: str = ""
    url: str = ""
    authorized: bool | None = None


@dataclass
class PartInfo:
    provider: str
    found: bool | None = None
    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    lifecycle: str = "unknown"
    lifecycle_raw: str = ""
    specs: dict = field(default_factory=dict)
    offers: list = field(default_factory=list)
    similar: list = field(default_factory=list)     # PartInfo candidates from the source's own alternates
    lead_days: int | None = None
    url: str = ""
    note: str = ""
    error: str = ""
    datasheet: str = ""                # the source's datasheet link (official APIs: Nexar bestDatasheet, DigiKey, Mouser)
    datasheets: list = field(default_factory=list)   # every datasheet link the source gave, best first (Nexar's
                                       # documentCollections hold distributor-hosted copies too)
    makers: list = field(default_factory=list)       # every maker the source lists this exact MPN under (one name per
                                       # company); [] = the source does not say (DigiKey's product details: one
                                       # product), so it never fills the Manufacturer alone (the triage fills a maker
                                       # only from a source that names exactly one)


class NexarSourcing(L.NexarSupply):
    shapes = ("CHECKED against tests/nexar/schema.graphql (validated in tests): supSearchMpn/supSearch(q, country, "
              "currency, limit, inStockOnly), SupPart.sellers{company{name} isAuthorized offers{sku inventoryLevel moq "
              "orderMultiple factoryLeadDays packaging clickUrl prices{quantity price currency convertedPrice "
              "convertedCurrency}}}, estimatedFactoryLeadDays, similarParts, specs{attribute{name shortname} displayValue}. "
              "ASSUMED: attribute names for passives (Resistance, Capacitance, Tolerance, Voltage Rating (DC), Dielectric, "
              "Power Rating, Case/Package); similarParts may be empty without a plan that includes it.")
    FIELDS = """fragment F on SupPart {
  mpn manufacturer { name } octopartUrl shortDescription estimatedFactoryLeadDays bestDatasheet { url }
  documentCollections { name documents { name url mimeType } }
  specs { attribute { name shortname } displayValue value }
  sellers { company { name } isAuthorized
    offers { sku inventoryLevel moq orderMultiple factoryLeadDays packaging clickUrl
      prices { quantity price currency convertedPrice convertedCurrency } } }
}"""
    Q_PART = FIELDS + """
query ($q: String!, $limit: Int, $country: String!, $currency: String!) {
  supSearchMpn(q: $q, limit: $limit, country: $country, currency: $currency) { hits results { part { ...F } } }
}"""
    Q_PART_ALT = FIELDS + """
query ($q: String!, $limit: Int, $country: String!, $currency: String!) {
  supSearchMpn(q: $q, limit: $limit, country: $country, currency: $currency) {
    hits results { part { ...F similarParts { ...F } } } }
}"""
    Q_SEARCH = FIELDS + """
query ($q: String!, $limit: Int, $country: String!, $currency: String!) {
  supSearch(q: $q, limit: $limit, country: $country, currency: $currency, inStockOnly: true) { hits results { part { ...F } } }
}"""
    country, currency = "US", "USD"

    def _need(self):
        if not self.configured():
            raise P.NotConfigured(f"{self.name}: set NEXAR_SUPPLY_TOKEN or {', '.join(self.env)} ({self.auth})")

    def _gql(self, query, variables):
        tok = self.token()
        if not tok:
            return None, self._token_err
        r = self.http.post(self.API_URL, json.dumps({"query": query, "variables": variables}),
                           headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        if not r.ok:
            return None, L._http_error(L.LifecycleResult(self.name), r).error
        try:
            d = r.json()
        except ValueError as e:
            return None, f"unparseable response: {e}"
        if d.get("errors"):
            return None, "; ".join(str(e.get("message", "?")) for e in d["errors"] if isinstance(e, dict))
        return d.get("data") or {}, ""

    def _vars(self, q, limit):
        return {"q": q, "limit": limit, "country": self.country, "currency": self.currency}

    def part(self, p, alternates=False):
        self._need()
        res = PartInfo(self.name, mpn=p["mpn"])
        if not p["mpn"]:
            res.error = "no MPN"
            return res
        data, err = self._gql(self.Q_PART_ALT if alternates else self.Q_PART, self._vars(p["mpn"], 5))
        if err:
            res.error = err
            return res
        try:
            return parse_nexar_part(data, p, res, self.currency)
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res


def _links(urls):
    """Datasheet links as given, http(s) only, each once ("//mm.digikey.com/..." is DigiKey's protocol-relative form)."""
    out = []
    for u in urls:
        u = str(u or "").strip()
        u = "https:" + u if u.startswith("//") else u
        if re.match(r"https?://", u, re.I) and u not in out:
            out.append(u)
    return out


def nexar_info(part, provider, currency):
    info = PartInfo(provider, True, part.get("mpn") or "", (part.get("manufacturer") or {}).get("name") or "",
                    part.get("shortDescription") or "", url=part.get("octopartUrl") or "",
                    lead_days=_int(part.get("estimatedFactoryLeadDays")),
                    datasheet=str((part.get("bestDatasheet") or {}).get("url") or ""))
    info.datasheets = _links([info.datasheet] + [d.get("url") for c in part.get("documentCollections") or [] if isinstance(c, dict)
                                                 and re.search(r"data\s?sheet", str(c.get("name")), re.I)
                                                 for d in c.get("documents") or [] if isinstance(d, dict)
                                                 and re.search(r"pdf", str(d.get("mimeType") or "pdf"), re.I)])
    pairs = []
    for s in part.get("specs") or []:
        a = s.get("attribute") or {}
        if a.get("shortname") == "lifecyclestatus":
            info.lifecycle_raw = s.get("displayValue") or s.get("value") or ""
            info.lifecycle = L.normalise(info.lifecycle_raw)
            continue
        val = s.get("displayValue") or s.get("value")
        pairs += [(a.get("name"), val), (a.get("shortname"), val)]
    info.specs = specs_from_pairs(pairs)
    for seller in part.get("sellers") or []:
        name = (seller.get("company") or {}).get("name") or "?"
        for o in seller.get("offers") or []:
            byq = {}
            for pp in o.get("prices") or []:
                q = _int(pp.get("quantity"))
                if q is None:
                    continue
                native = str(pp.get("currency") or "").upper() == currency.upper()
                price = money(pp.get("price") if native else pp.get("convertedPrice"))   # 'N/A' is no price, not
                if price is not None and (q not in byq or native):                     # a lost part
                    byq[q] = price
            info.offers.append(Offer(provider, name, o.get("sku") or "", _int(o.get("inventoryLevel")),
                                     _int(o.get("moq")) or 1, _int(o.get("orderMultiple")) or 1, sorted(byq.items()),
                                     currency.upper(), _int(o.get("factoryLeadDays")) or info.lead_days,
                                     o.get("packaging") or "", o.get("clickUrl") or "", seller.get("isAuthorized")))
    info.similar = [nexar_info(s, provider, currency) for s in part.get("similarParts") or [] if isinstance(s, dict)]
    return info


def parse_nexar_part(data, p, res, currency="USD"):
    results = ((data or {}).get("supSearchMpn") or {}).get("results") or []
    parts = [x.get("part") for x in results if isinstance(x, dict)]
    mpn_of, mfr_of = (lambda x: x.get("mpn")), (lambda x: ((x.get("manufacturer") or {}).get("name")))
    best = L._pick(parts, p, mpn_of, mfr_of)
    if not best:
        res.found = False
        return res
    info = nexar_info(best, res.provider, currency)
    # known limit: only the results asked for (part(): 5, as Nexar counts every part it returns against the plan's
    # quota); a second maker of the exact MPN ranked lower is missed and the MPN reads as one maker's
    info.makers = L.exact_makers(parts, p["mpn"], mpn_of, mfr_of)
    return info


class DigiKeySourcing(L.DigiKeyLifecycle):
    shapes = ("CHECKED (DigiKey v4 OpenAPI field names, also seen in third-party clients): GET /products/v4/search/{pn}/"
              "productdetails -> Product{ManufacturerProductNumber, Manufacturer{Name}, Description{ProductDescription}, "
              "ProductUrl, QuantityAvailable, ProductStatus{Status}, Parameters[{ParameterText, ValueText}], "
              "ProductVariations[{DigiKeyProductNumber, PackageType{Name}, StandardPricing[{BreakQuantity, UnitPrice}], "
              "MinimumOrderQuantity, QuantityAvailableforPackageType}]}; endpoints /search/keyword and /{pn}/substitutions "
              "exist. ASSUMED: ManufacturerLeadWeeks (weeks), SearchLocaleUsed{Currency}, keyword body {Keywords, Limit, "
              "Offset} -> {Products: [...]}, substitutions -> {ProductSubstitutes: [{ManufacturerProductNumber, "
              "Manufacturer{Name}, SubstituteType, QuantityAvailable}]}. Digi-Reel variations are skipped (reeling fee).")
    KEYWORD_URL = "https://api.digikey.com/products/v4/search/keyword"
    SUBS_URL = "https://api.digikey.com/products/v4/search/{}/substitutions"
    country, currency = "US", "USD"

    def _headers(self, tok=None):
        return {"X-DIGIKEY-Client-Id": UC.secret("DIGIKEY_CLIENT_ID"), "Authorization": f"Bearer {tok or self._token}",
                "Accept": "application/json", "Content-Type": "application/json",
                "X-DIGIKEY-Locale-Site": self.country, "X-DIGIKEY-Locale-Currency": self.currency}

    def _need(self):
        if not self.configured():
            raise P.NotConfigured(f"{self.name}: set {', '.join(self.env)} ({self.auth})")
        return self.token()

    # DigiKey answers HTTP 429 for two limits: a per-minute one (wait the seconds it names, then ask again) and the free
    # plan's 1000 a day (hours): that one ends DigiKey's use for the rest of the run, so the other lines are not each
    # sent to be refused, and every line it leaves says so (the next run, after the reset, asks only for those: the
    # answers already had are in the HTTP cache)
    BURST_MAX = 61.0                     # s: a 429 asking for a longer wait than this is the daily limit
    sleep = staticmethod(time.sleep)
    _spent = ""                          # the daily-limit error once DigiKey has given it in this run
    _spent_until = 0.0                   # time.monotonic() when DigiKey said it resets (a long-running app asks again)

    @property
    def spent(self):
        """The daily-limit error while DigiKey's allowance is used up, else ''."""
        return self._spent if self._spent and time.monotonic() < self._spent_until else ""

    @staticmethod
    def _limit_seconds(r):
        """How long a 429 says to wait (Retry-After header, else DigiKey's 'Retry in N seconds'), or None."""
        h = getattr(r, "headers", None) or {}
        v = next((str(x) for k, x in h.items() if str(k).lower() == "retry-after"), "")
        if not re.fullmatch(r"\s*\d+(\.\d+)?\s*", v):
            m = re.search(r"(?i)retry\s+in\s+(\d+(?:\.\d+)?)\s*s", str(getattr(r, "text", "") or "")[:2000])
            v = m.group(1) if m else ""
        try:
            return float(v) if v.strip() else None
        except ValueError:
            return None

    def _limit_wait(self, r):
        """Seconds to wait before asking again after this 429, or None (no wait named, or the daily limit)."""
        s = self._limit_seconds(r)
        return s if s is not None and s <= self.BURST_MAX else None

    def _paced(self, send):
        """send() once; a per-minute 429 is waited out and asked once more; a daily one marks DigiKey spent."""
        r = send()
        if r.status != 429:
            return r
        w = self._limit_wait(r)
        if w is not None:
            self.sleep(max(w, 1.0))
            r = send()
            if r.status != 429:
                return r
        s = self._limit_seconds(r)
        if s is not None and s > self.BURST_MAX:
            self._spent = _digikey_error(r) + "; not asked again this run"
            self._spent_until = time.monotonic() + s
        return r

    def _shared(self, p, res):
        """productdetails found nothing: the keyword search lists every maker's listing of exactly this number. The
        case comes only from the BOM maker's own listing, or, with no maker on the line, only when every listing's case
        is the same (onsemi's SS34 is SMC, Vishay's is SMA). A renamed part (another number) is never taken."""
        res.found = False
        body = json.dumps({"Keywords": p["mpn"], "Limit": 10, "Offset": 0})
        r = self._paced(lambda: self.http.post(self.KEYWORD_URL, body, headers=self._headers()))
        if r.status == 401:                        # the token ran out between the two requests: renew once
            self._token = None
            if self.token():
                r = self._paced(lambda: self.http.post(self.KEYWORD_URL, body, headers=self._headers()))
        if not r.ok:
            res.found, res.error = None, (self.spent or _digikey_error(r)) if r.status == 429 else _digikey_error(r)
            return res
        try:
            d = r.json()
            hits = [x for x in (d.get("ExactMatches") or []) + (d.get("Products") or []) if isinstance(x, dict)
                    and same_mpn(x.get("ManufacturerProductNumber"), p["mpn"])]
            maker = lambda x: (x.get("Manufacturer") or {}).get("Name", "") if isinstance(x.get("Manufacturer"), dict) else ""
            if (p.get("manufacturer") or "").strip():
                every = hits
                hits = [x for x in hits if P.same_maker(p["manufacturer"], maker(x))]
                if every and not hits:             # listed, but only by another maker: said, never taken (a wrong
                    others = list(dict.fromkeys(maker(x) for x in every if maker(x)))   # maker on the line may be
                    res.note = (f"DigiKey lists {p['mpn']} only from {', '.join(others[:3])}, not "   # a wrong part)
                                f"{p['manufacturer'].strip()}: check the BOM's maker")
                    return res
            else:                                  # no maker: "every listing agrees" needs every listing on the page
                listed = d.get("Products") if isinstance(d.get("Products"), list) else []
                total = d.get("ProductsCount")
                if isinstance(total, int) and total > len(listed):
                    if hits:
                        res.note = f"DigiKey lists {total} results for {p['mpn']}; with no maker on the line not all were seen"
                    return res
            if not hits:
                near = near_numbers(p["mpn"], [x.get("ManufacturerProductNumber") for x in
                                               (d.get("ExactMatches") or []) + (d.get("Products") or []) if isinstance(x, dict)])
                if near:                                   # named for the person to check, never used
                    res.note = f"DigiKey lists no {p['mpn']}, only {', '.join(near)}: check the BOM's part number"
                return res
            cases = {_case_key(x) for x in hits}
            if len(cases) > 1:
                res.note = (f"several makers sell {p['mpn']} in different cases ("
                            + "; ".join(sorted(f"{maker(x)}: {_case_text(x) or '?'}" for x in hits)[:4]) + ")")
                return res
            return digikey_info(hits[0], self.name, (d.get("SearchLocaleUsed") or {}).get("Currency") or self.currency)
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res

    def part(self, p, alternates=False):
        res = PartInfo(self.name, mpn=p["mpn"])
        if not self._need():
            res.error = self._token_err
            return res
        if not p["mpn"]:
            res.error = "no MPN"
            return res
        if self.spent:                             # the daily allowance is used up: no line is sent to be refused
            res.error = self.spent
            return res
        r = self._paced(lambda: self.authed_get(self.URL.format(quote(p["mpn"], safe="")), self._headers))
        if r.status == 404:                        # also what productdetails says when several makers sell this number
            return self._shared(p, res)
        if not r.ok:
            res.error = (self.spent or _digikey_error(r)) if r.status == 429 else _digikey_error(r)
            return res
        try:
            d = r.json()
            prod = d.get("Product") if isinstance(d.get("Product"), dict) else d
            if not same_mpn(prod.get("ManufacturerProductNumber"), p["mpn"]):
                # productdetails answers its best match: the exact number may still be listed (by another maker, or
                # as one of several), which the keyword search shows; the best match itself is never taken
                best = str(prod.get("ManufacturerProductNumber") or "")
                res = self._shared(p, res)
                if not res.found and not res.error and not res.note:
                    res.note = f"DigiKey's nearest listing is {best}, not {p['mpn']}: check the BOM's part number"
                return res
            return digikey_info(prod, self.name, (d.get("SearchLocaleUsed") or {}).get("Currency") or self.currency)
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res


def near_numbers(mpn, listed, n=3):
    """Up to n listed part numbers that start with this one or that it starts with (PCA9685PW / PCA9685PW,118, the
    ordering code with a packaging suffix; TR/3216FF2-R, a reel prefix; CRCW0402100RFKEA / CRCW0402100RFKED share all
    but the last letters), shown to the person as what to check. Never used as the part: a suffix can change it."""
    want, out = norm_mpn(mpn), []
    if len(want) < 4:
        return out
    for m in listed:
        k = norm_mpn(m)
        if not k or k == want or m in out:
            continue
        common = len(os.path.commonprefix([k, want]))
        if k.startswith(want) or want.startswith(k) or (k.endswith(want) and len(k) - len(want) <= 3) \
                or common >= max(6, len(want) - 2):
            out.append(str(m))
    return out[:n]


def _digikey_error(r):
    if r.status == 429:
        s = DigiKeySourcing._limit_seconds(r)
        if s is not None and s <= DigiKeySourcing.BURST_MAX:
            return "DigiKey: HTTP 429: per-minute request limit reached (asked again after the wait it named)"
        return "DigiKey: HTTP 429: request limit reached (the free plan allows 1000 a day; it resets at midnight UTC)"
    return L._http_error(L.LifecycleResult("digikey"), r).error


def _case_text(prod):
    for x in prod.get("Parameters") or []:
        if isinstance(x, dict) and re.search(r"^package\s*/\s*case$", str(x.get("ParameterText") or ""), re.I):
            return str(x.get("ValueText") or "").strip()
    return ""


def _case_key(prod):
    """Every package text a listing gives (Package / Case and Supplier Device Package): digikey_info falls back on the
    second when the first is missing, so two listings agree only when both match."""
    out = []
    for x in prod.get("Parameters") or []:
        if isinstance(x, dict) and re.search(r"package|case", str(x.get("ParameterText") or ""), re.I) \
                and not re.search(r"packag(e|ing)\s*type|^packaging$", str(x.get("ParameterText") or ""), re.I):
            out.append((str(x.get("ParameterText") or "").strip().lower(), str(x.get("ValueText") or "").strip()))
    return tuple(sorted(out))


def digikey_info(prod, provider, currency):
    desc = prod.get("Description")
    desc = (desc.get("ProductDescription") or desc.get("DetailedDescription") or "") if isinstance(desc, dict) else str(desc or "")
    m = prod.get("Manufacturer")
    st = prod.get("ProductStatus")
    words = [str((st.get("Status") if isinstance(st, dict) else st) or "")] +(["End of Life"] if prod.get("EndOfLife") else []) \
        + (["Discontinued at Digi-Key"] if prod.get("Discontinued") else [])
    known = [s for s in map(L.normalise, [w for w in words if w]) if s != "unknown"]
    lead = _int(prod.get("ManufacturerLeadWeeks"))
    info = PartInfo(provider, True, prod.get("ManufacturerProductNumber") or "", (m.get("Name") if isinstance(m, dict) else m) or "",
                    desc, max(known, key=L.SEVERITY.get) if known else "unknown", "; ".join(w for w in words if w),
                    url=prod.get("ProductUrl") or "", lead_days=lead * 7 if lead is not None else None,
                    datasheet=str(prod.get("DatasheetUrl") or ""))
    info.datasheets = _links([info.datasheet] + [x.get("Url") for x in prod.get("MediaLinks") or [] if isinstance(x, dict)
                                                 and re.search(r"data\s?sheet", str(x.get("MediaType")), re.I)])
    info.specs = specs_from_pairs([(x.get("ParameterText"), x.get("ValueText")) for x in prod.get("Parameters") or []
                                   if isinstance(x, dict)])
    for v in prod.get("ProductVariations") or []:
        if not isinstance(v, dict):                # an offer of an odd shape is no offer, not a lost part
            continue
        pkg = (v.get("PackageType") or {}).get("Name") or ""
        if "digi-reel" in pkg.lower():
            continue
        info.offers.append(Offer(provider, "Digi-Key", v.get("DigiKeyProductNumber") or "",
                                 _int(v.get("QuantityAvailableforPackageType")) if v.get("QuantityAvailableforPackageType") is not None
                                 else _int(prod.get("QuantityAvailable")),
                                 _int(v.get("MinimumOrderQuantity")) or 1, 1,
                                 sorted((_int(b.get("BreakQuantity")), money(b.get("UnitPrice"))) for b in v.get("StandardPricing") or []
                                        if isinstance(b, dict) and money(b.get("UnitPrice")) is not None and _int(b.get("BreakQuantity"))),
                                 str(currency).upper(), info.lead_days, pkg, info.url, True))
    return info


class MouserSourcing(L.MouserLifecycle):
    shapes = ("CHECKED (Mouser swagger + third-party clients): POST /api/v1/search/partnumber and /search/keyword "
              "(?apiKey=), body {SearchByKeywordRequest: {keyword, records, startingRecord, searchOptions}}; Parts[] "
              "{ManufacturerPartNumber, Manufacturer, MouserPartNumber, Description, AvailabilityInStock, Min, Mult, "
              "LeadTime ('84 Days'), PriceBreaks[{Quantity, Price ('$0.10', a string), Currency}], ProductAttributes"
              "[{AttributeName, AttributeValue}], LifecycleStatus}. ASSUMED: SuggestedReplacement (an MPN string); "
              "ProductAttributes rarely carry electrical specs, so the Description is parsed too.")
    KEYWORD_URL = "https://api.mouser.com/api/v1/search/keyword?apiKey={}"

    RATE_WAIT = 61.0          # s: Mouser's per-minute allowance is back after a minute
    _limited = False          # a refusal that a minute's wait did not end (a daily cap or a bad key): no more waits

    def _post(self, url, body):
        if not self.configured():
            raise P.NotConfigured(f"{self.name}: set {', '.join(self.env)} ({self.auth})")
        # the key is a query parameter: httpcache redacts apiKey= from the cache key and the stored URL
        send = lambda: self.http.post(url.format(quote(UC.secret("MOUSER_API_KEY"), safe="")), json.dumps(body),
                                      headers={"Content-Type": "application/json", "Accept": "application/json"})
        r = send()
        if getattr(r, "status", 0) in (403, 429) and not self._limited:   # over the 30 a minute: wait it out once
            import time
            time.sleep(self.RATE_WAIT)
            r = send()
            if getattr(r, "status", 0) in (403, 429):
                self._limited = True
        return r

    def part(self, p, alternates=False):
        res = PartInfo(self.name, mpn=p["mpn"])
        r = self._post(self.URL, {"SearchByPartRequest": {"mouserPartNumber": p["mpn"], "partSearchOptions": "Exact"}}) \
            if p["mpn"] else None
        if r is None:
            res.error = "no MPN"
            return res
        if not r.ok:
            res.error = L._http_error(L.LifecycleResult(self.name), r).error
            return res
        try:
            parts, err = _mouser_parts(r.json())
            if err:
                res.error = err
                return res
            best = L._pick(parts, p, lambda x: x.get("ManufacturerPartNumber"), lambda x: x.get("Manufacturer"))
            if not best:
                res.found = False
                res.note = _miss_note("Mouser", p, parts, lambda x: x.get("ManufacturerPartNumber"),
                                      lambda x: x.get("Manufacturer"))
                return res
            info = mouser_info(best, self.name)
            info.makers = L.exact_makers(parts, p["mpn"], lambda x: x.get("ManufacturerPartNumber"),
                                         lambda x: x.get("Manufacturer"))
            rep = str(best.get("SuggestedReplacement") or "").strip()
            if rep and norm_mpn(rep) != norm_mpn(p["mpn"]):
                info.similar.append(PartInfo(self.name, True, rep, note="Mouser suggested replacement"))
            return info
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res


def _miss_note(source, p, items, mpn_of, mfr_of):
    """Why a source that answered found no listing of this exact part, for the person to check ('' = it lists nothing
    like it): the number is listed only by other makers, or only longer / near numbers are."""
    exact = [i for i in items if isinstance(i, dict) and same_mpn(mpn_of(i), p["mpn"])]
    if exact:
        others = list(dict.fromkeys(str(mfr_of(i) or "").strip() for i in exact if str(mfr_of(i) or "").strip()))
        return (f"{source} lists {p['mpn']} only from {', '.join(others[:3])}, not {(p.get('manufacturer') or '').strip()}:"
                " check the BOM's maker") if others else ""
    near = near_numbers(p["mpn"], [mpn_of(i) for i in items if isinstance(i, dict)])
    return f"{source} lists no {p['mpn']}, only {', '.join(near)}: check the BOM's part number" if near else ""


def _mouser_parts(d):
    errs = [e for e in d.get("Errors") or [] if isinstance(e, dict)]
    if errs:
        return [], "; ".join(str(e.get("Message") or e.get("Code") or e) for e in errs)
    return [x for x in (d.get("SearchResults") or {}).get("Parts") or [] if isinstance(x, dict)], ""


def money(s):
    """'$0.10' / '0,10 €' / '1.234,50 €' / '1,234.50' / 0.1 -> float."""
    if isinstance(s, (int, float)):
        return float(s)
    t = re.sub(r"[^\d.,]", "", str(s or ""))
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".") if len(t.split(",")[-1]) != 3 or re.match(r"0?,", t) else t.replace(",", "")
        # ("0,125 €" is 0.125: a thousands separator never follows a leading 0)
    try:
        return float(t)
    except ValueError:
        return None


def lead_days(s):
    """'84 Days' / '18 Weeks' / 12 -> days."""
    n = _int(s)
    if n is None:
        return None
    return n * 7 if re.search(r"week", str(s), re.I) else n


def mouser_info(x, provider):
    status = x.get("LifecycleStatus") or ""
    stock = _int(x.get("AvailabilityInStock"))
    if stock is None and re.search(r"in stock", str(x.get("Availability")), re.I):
        stock = _int(x.get("Availability"))
    info = PartInfo(provider, True, x.get("ManufacturerPartNumber") or "", x.get("Manufacturer") or "",
                    x.get("Description") or "", L.normalise(status) if status else "active", status,
                    url=x.get("ProductDetailUrl") or "", lead_days=lead_days(x.get("LeadTime")),
                    datasheet=str(x.get("DataSheetUrl") or ""))
    info.datasheets = _links([info.datasheet])
    info.specs = specs_from_pairs([(a.get("AttributeName"), a.get("AttributeValue")) for a in x.get("ProductAttributes") or []
                                   if isinstance(a, dict)])
    breaks, cur = [], ""
    for b in x.get("PriceBreaks") or []:
        if isinstance(b, dict) and _int(b.get("Quantity")) and (pr := money(b.get("Price"))) is not None:
            breaks.append((_int(b.get("Quantity")), pr))
            cur = cur or str(b.get("Currency") or "")
    info.offers.append(Offer(provider, "Mouser", x.get("MouserPartNumber") or "", stock, _int(x.get("Min")) or 1,
                             _int(x.get("Mult")) or 1, sorted(breaks), cur.upper(), info.lead_days, "", info.url, True))
    return info


class JLCSourcing(L.JLCLifecycle):
    shapes = ("ASSUMED (undocumented JLCPCB parts endpoint; field paths from easyeda2kicad and public scrapers): "
              "data.componentPageInfo.list[] {componentCode, componentModelEn, componentBrandEn, stockCount, "
              "componentSpecificationEn (package), describe, componentPrices[{startNumber, endNumber, productPrice}] in USD, "
              "leastNumber (MOQ, if present)}. Stock and price are JLCPCB assembly stock, written as Supplier 'LCSC' "
              "with the C-number. No lifecycle data. A line with an LCSC number the search refuses or misses is looked up "
              "directly (EasyEDA product endpoint, result.lcsc {number, price, min, step, stock}, ASSUMED USD).")

    def part(self, p, alternates=False):
        res = PartInfo(self.name, mpn=p["mpn"])
        lcsc, mpn = (p.get("lcsc") or "").upper(), p["mpn"]
        if not (lcsc or mpn):
            res.error = "no MPN or LCSC number"
            return res
        items, err = self._query(lcsc or mpn)
        best = None if err else (next((i for i in items if str(i.get("componentCode", "")).upper() == lcsc), None) if lcsc
                                 else L._pick(items, p, lambda i: i.get("componentModelEn"), lambda i: i.get("componentBrandEn")))
        if best:
            return jlc_info(best, self.name)
        if lcsc:                       # the search refused or missed: the C-number is asked of LCSC directly instead
            direct = self._lcsc_direct(lcsc)
            if direct.found:
                return direct
            if err and direct.error:
                res.error = f"{err}; LCSC {lcsc} direct: {direct.error}"
                return res
            res.note = direct.note
        elif err:
            res.error = err
            return res
        res.found = False
        return res

    def _lcsc_direct(self, lcsc):
        """Price and stock for one LCSC number from the product endpoint EasyEDA serves (result.lcsc {number, price,
        min, step, stock}, USD; the same lookup providers.EasyEDA makes), so a refused JLCPCB search hides nothing."""
        res = PartInfo(self.name)
        r = self.http.get(P.EasyEDA.URL.format(quote(lcsc, safe="")), headers=P.EasyEDA.HEADERS)
        if not r.ok:
            res.error = r.error or f"HTTP {r.status}"
            return res
        try:
            d = r.json()
            src = P.parse_easyeda(d, P.SourceResult(P.EasyEDA.name, lcsc=lcsc))
            o = (d.get("result") or {}).get("lcsc") if src.found else None
            if not isinstance(o, dict) or str(o.get("number") or lcsc).upper() != lcsc:
                res.found, res.note = False, f"{lcsc} not listed"
                return res
            url = f"https://www.lcsc.com/product-detail/{lcsc}.html"
            info = PartInfo(self.name, True, src.mpn, src.manufacturer, src.extra.get("description", ""), url=url,
                            note=f"LCSC {lcsc} looked up directly")
            info.specs = {k: v for k, v in {"package": package_of(src.package)}.items() if v}
            moq = _int(o.get("min")) or 1
            price = o.get("price")
            breaks = sorted((_int(q), float(v)) for q, v in price if _int(q)) if isinstance(price, list) \
                else [(moq, float(price))] if price is not None else []
            info.offers.append(Offer(self.name, "LCSC", lcsc, _int(o.get("stock")), moq, _int(o.get("step")) or 1,
                                     breaks, "USD", None, "", url, True))
            return info
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res

    def _query(self, kw, size=10):
        r = self.post(kw, size)
        if not r.ok:
            return [], L._http_error(L.LifecycleResult(self.name), r).error
        try:
            return [i for i in (((r.json().get("data") or {}).get("componentPageInfo") or {}).get("list") or [])
                    if isinstance(i, dict)], ""
        except (ValueError, AttributeError, TypeError) as e:
            return [], f"unparseable response: {e}"


def jlc_info(i, provider):
    code = str(i.get("componentCode") or "")
    desc = " ".join(str(i.get(k) or "") for k in ("describe", "componentSpecificationEn")).strip()
    info = PartInfo(provider, True, i.get("componentModelEn") or "", i.get("componentBrandEn") or "", desc,
                    url=f"https://jlcpcb.com/partdetail/{code}" if code else "")
    info.specs = {k: v for k, v in {"package": package_of(i.get("componentSpecificationEn"))}.items() if v}
    breaks = sorted((_int(b.get("startNumber")), float(b["productPrice"])) for b in i.get("componentPrices") or []
                    if isinstance(b, dict) and b.get("productPrice") is not None and _int(b.get("startNumber")))
    info.offers.append(Offer(provider, "LCSC", code, _int(i.get("stockCount")), _int(i.get("leastNumber")) or 1, 1,
                             breaks, "USD", None, "", info.url, True))
    return info


PROVIDERS = [NexarSourcing, DigiKeySourcing, MouserSourcing, JLCSourcing]


# ------------------------------------------------------------------ merging what the sources say
