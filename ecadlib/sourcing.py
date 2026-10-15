"""Second sources and cost: price, stock and alternates for every BOM line.

    python -m ecadlib.sourcing examples/sample_bom.csv [--qty 100] [--alternates] [--write-plan out/altium365/plan.json] \
        [--providers nexar,digikey,mouser,jlcpcb_search] [--currency USD] [--country US] [-o out/sourcing] [--offline]

Per fitted BOM line: need = BOM qty per board (Qty column, else designator count) x --qty boards. Every offer from every
distributor is costed at the order quantity it would actually take (MOQ, order multiple, price breaks; buying up to the
next break is used when that is cheaper in total). The chosen offer is the cheapest one whose stock covers the order;
the report also gives MOQ, order multiple, lead time, and flags:
  no_stock            no distributor has any stock            low_stock          stock < --low-stock-factor x need,
  single_distributor  only one distributor has stock                             or no single offer covers the need
  lifecycle:<status>  NRND / last-time-buy / obsolete          not_found / no_price / no_mpn
  no_answer           no source answered for this line (not configured, blocked, quota)
With --alternates:
  * generic passives (designators R, C, L): drop-in parts by parametric match against the part's own specs (from the
    sources) and the BOM value/description: same value and package; tolerance no wider; voltage, power and current
    ratings no lower; capacitor dielectric the same or a better class (C0G/NP0 > X8R > X7R > X7S/X7T > X6S > X5R > Y5V).
    A candidate whose data cannot confirm every required parameter is listed as `unconfirmed`, never used.
    Flag `single_source` when no confirmed drop-in (passives) or no candidate at all (others) was found.
  * everything else (ICs, connectors, crystals, discretes): Nexar similarParts, DigiKey substitutions and Mouser's
    suggested replacement, always marked `needs engineer review`. They are never substituted or written as alternates.
  * NRND / last-time-buy / obsolete candidates are dropped (unknown lifecycle is kept and marked).
--write-plan PLAN (an altium365 plan.json, changed in place) fills empty `Supplier 1/2` + `Supplier Part Number 1/2`
parameters (an existing Supplier, e.g. the BOM's LCSC number, is never replaced) and, for passives, confirmed drop-ins as
`Alternate Manufacturer N` / `Alternate Manufacturer Part Number N`. Filled names leave `cleared`, so `altium365 push`
sends them instead of deleting them. Prices and IC review candidates go to `item["sourcing"]` only (not parameters, so
a price change does not cause a new revision on every push).
Outputs: <out>.md and <out>.csv (one row per BOM line, with Altium-style Supplier columns).
Exit codes: 0 every fitted line can be bought from stock, 1 some line cannot (no stock / not found / no price),
2 bad input, 3 no source answered (secrets missing, quota, or network blocked).

Auth reuses the lifecycle clients (lifecycle.py): NEXAR_CLIENT_ID/NEXAR_CLIENT_SECRET (or NEXAR_SUPPLY_TOKEN; pricing
needs a Nexar plan with pricing), DIGIKEY_CLIENT_ID/DIGIKEY_CLIENT_SECRET, MOUSER_API_KEY. JLCPCB needs none. Responses
are cached by httpcache (query-string secrets such as Mouser's apiKey are redacted from cache keys and files; token
requests are never cached). Response shapes: each provider's `shapes` says what was checked against public docs or the
Nexar schema and what is assumed. None of these hosts was reachable from the sandbox this was written in."""
import argparse, csv, json, math, os, re, sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from . import lifecycle as L, providers as P
from . import sheetsafe as SS
from .bom_triage import count_refs, part_numbers, read_bom
from .httpcache import CachedHTTP
from .kicad_index import norm_mpn, package_of, parse_value

RISKY = {"nrnd", "ltb", "obsolete"}
SUPPLIER = {"digikey": "Digi-Key", "mouser": "Mouser", "lcsc": "LCSC", "farnell": "Farnell", "newark": "Newark",
            "arrow": "Arrow", "tme": "TME", "rs": "RS"}
DIELECTRIC = {"C0G": 0, "NP0": 0, "X8R": 1, "X7R": 2, "X7S": 3, "X7T": 3, "X6S": 4, "X5R": 5, "Y5V": 6, "Z5U": 6}
KIND_WORD = {"R": "resistor", "C": "capacitor", "L": "inductor"}
KIND_UNIT = {"R": "R", "C": "F", "L": "H"}
REQUIRED = {"R": ("tolerance", "power"), "C": ("voltage", "dielectric", "tolerance"), "L": ("current", "tolerance")}


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _int(v):
    return L._int(v)


def dist_key(name):
    """'Digi-Key Electronics' -> digikey, 'Mouser Electronics' -> mouser, 'LCSC'/'JLCPCB' -> lcsc."""
    k = re.sub(r"[^a-z]", "", str(name or "").lower())
    for short in ("digikey", "mouser", "farnell", "newark", "arrow"):
        if k.startswith(short):
            return short
    if k.startswith(("lcsc", "jlc")):
        return "lcsc"
    if k.startswith("rscomponents") or k == "rs" or k.startswith("rsonline"):
        return "rs"
    return k


# ------------------------------------------------------------------ numbers and specs
_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "R": 1.0}


def si(s):
    """'10 kOhms' / '100nF' / '0.1 µF' / '4k7' / '1/10W' / '0.1W, 1/10W' / '16 VDC' -> float (first number), else None."""
    s = str(s or "").strip()
    m = re.match(r"(\d+)([pnuµμmkKMGR])(\d+)\b", s)                      # 4k7, 4R7, 2n2
    if m:
        return float(f"{m.group(1)}.{m.group(3)}") * _SI[m.group(2)]
    m = re.search(r"(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?\s*([pnuµμmkKMG]?)", s)
    if not m:
        return None
    v = float(m.group(1)) / (float(m.group(2)) if m.group(2) else 1.0)
    return v * _SI[m.group(3)]


def pct(s):
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(s or ""))
    return float(m.group(1)) if m else None


def dielectric(s):
    m = re.search(r"\b(C0G|NP0|COG|NPO|X8R|X7R|X7S|X7T|X6S|X5R|Y5V|Z5U)\b", str(s or "").upper())
    return {"COG": "C0G", "NPO": "NP0"}.get(m.group(1), m.group(1)) if m else None


def fmt_si(v, unit=""):
    for p, f in (("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1.0), ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12)):
        if abs(v) >= f * 0.999:
            n = v / f
            return f"{n:.3g}{p}{'' if unit == 'R' else unit}"
    return f"{v:g}{unit}"


SPEC_RX = [   # (canonical, regex on the attribute name/shortname, parser); first match per attribute wins
    ("value:R", r"^resistance\b", si), ("value:F", r"^capacitance\b", si), ("value:H", r"^inductance\b", si),
    ("tolerance", r"tolerance", pct),
    ("voltage", r"voltage.*(rat|dc)|rated voltage|voltagerating|^voltage$", si),
    ("dielectric", r"dielectric|temperature ?coefficient|tempco", dielectric),
    ("power", r"^power|power ?rating", si),
    ("current", r"current.*rat|rated current|currentrating|^current$", si),
    ("package", r"case|package|^size$", lambda v: package_of(v)),
]


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
            v = parse(val)
            if v is not None:
                if key.startswith("value:"):
                    out.setdefault("value", v)
                    out.setdefault("unit", key[6:])
                else:
                    out.setdefault(key, v)
            break
    return out


def specs_from_text(text, kind=None):
    """Specs from a description such as '100nF ±10% 16V X7R 0402' or 'RES SMD 10K OHM 1% 1/10W 0603'."""
    t, out = str(text or ""), {}
    if kind == "C":
        m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([pnuµμmPNU]?)F\b", t)       # DigiKey writes 0.1UF, 1000PF
    elif kind == "L":
        m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([nuµμmNU]?)H\b", t)
    elif kind == "R":
        m = (re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([kKM]?)\s*(?:Ω|(?i:ohms?)\b)", t)
             or re.search(r"(?<![\w.])(\d+)([kKMR])(\d+)\b", t))
    else:
        m = None
    if m:
        out["value"] = (float(f"{m.group(1)}.{m.group(3)}") * _SI[m.group(2)] if m.re.groups == 3
                        else float(m.group(1)) * _SI[m.group(2) if kind == "R" else m.group(2).lower()])
        out["unit"] = KIND_UNIT[kind]
    if (v := pct(t)) is not None:
        out["tolerance"] = v
    if m := re.search(r"(?<![\w./])(\d+(?:\.\d+)?)\s*(k?)V(?:DC)?\b", t):
        out["voltage"] = float(m.group(1)) * (1e3 if m.group(2) else 1.0)
    if m := re.search(r"(?<![\w./])(\d+/\d+|\d+(?:\.\d+)?)\s*(m?)W\b", t):
        out["power"] = si(m.group(1)) * (1e-3 if m.group(2) else 1.0)
    if kind == "L" and (m := re.search(r"(?<![\w./])(\d+(?:\.\d+)?)\s*(m?)A\b", t)):
        out["current"] = float(m.group(1)) * (1e-3 if m.group(2) else 1.0)
    if d := dielectric(t):
        out["dielectric"] = d
    if p := package_of(t):
        out["package"] = p
    return out


def _close(a, b):
    return abs(a - b) <= 1e-3 * max(abs(a), abs(b), 1e-15)


def drop_in(req, cand, kind):
    """-> ('drop_in' | 'unconfirmed' | 'mismatch', reasons). Value and package must match; every other parameter
    the original part states must be met or bettered; a parameter the candidate does not state is 'unconfirmed'."""
    bad, missing = [], []
    if cand.get("value") is None:
        missing.append("value")
    elif not _close(cand["value"], req["value"]) or cand.get("unit", KIND_UNIT[kind]) != KIND_UNIT[kind]:
        bad.append(f"value {fmt_si(cand['value'], cand.get('unit', ''))}")
    if not cand.get("package"):
        missing.append("package")
    elif cand["package"] != req["package"]:
        bad.append(f"package {cand['package']}")
    for k in REQUIRED[kind]:
        if req.get(k) is None:
            continue
        c = cand.get(k)
        if c is None:
            missing.append(k)
        elif k == "tolerance" and c > req[k] + 1e-9:
            bad.append(f"tolerance {c:g}% > {req[k]:g}%")
        elif k in ("voltage", "power", "current") and c < req[k] * (1 - 1e-6):
            bad.append(f"{k} {fmt_si(c)} < {fmt_si(req[k])}")
        elif k == "dielectric" and DIELECTRIC.get(c, 99) > DIELECTRIC.get(req[k], -1):
            bad.append(f"dielectric {c} (need {req[k]} or better)")
    if bad:
        return "mismatch", bad
    if missing:
        return "unconfirmed", [f"not stated: {', '.join(missing)}"]
    return "drop_in", []


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


def _ceil_to(q, mult):
    mult = max(int(mult or 1), 1)
    return int(math.ceil(q / mult) * mult)


def buy(offer, need):
    """Cheapest way to get >= need units from one offer: order quantity respects MOQ, order multiple and the first
    price break; ordering up to a later break is chosen when the total is lower. None when the offer has no prices."""
    br = sorted((int(q), float(p)) for q, p in offer.breaks if q and p is not None and p >= 0)
    if not br or need <= 0:
        return None
    q0 = _ceil_to(max(need, offer.moq or 1, br[0][0]), offer.mult)
    best = None
    for q in sorted({q0} | {_ceil_to(b, offer.mult) for b, _ in br if b > q0}):
        unit = [p for b, p in br if b <= q][-1]
        total = round(q * unit, 6)
        if best is None or total < best["ext"] - 1e-9:
            best = {"order_qty": q, "unit": unit, "ext": total}
    best["covers"] = offer.stock is not None and offer.stock >= best["order_qty"]
    return best


def rank_offers(offers, need, currency):
    """[(offer, buy)] for priced offers in `currency`: full-stock offers by total cost, then partial stock (most
    stock first), then no/unknown stock (shortest lead time first)."""
    rows = [(o, b) for o in offers if (o.currency or currency).upper() == currency.upper() and (b := buy(o, need))]
    tier = lambda ob: 0 if ob[1]["covers"] else 1 if (ob[0].stock or 0) > 0 else 2
    return sorted(rows, key=lambda ob: (tier(ob), ob[1]["ext"] if tier(ob) == 0 else 0,
                                        -(ob[0].stock or 0) if tier(ob) == 1 else 0,
                                        ob[0].lead_days if ob[0].lead_days is not None else 10 ** 6, ob[1]["ext"]))


# ------------------------------------------------------------------ providers (auth from lifecycle.py)
class NexarSourcing(L.NexarSupply):
    shapes = ("CHECKED against tests/nexar/schema.graphql (validated in tests): supSearchMpn/supSearch(q, country, "
              "currency, limit, inStockOnly), SupPart.sellers{company{name} isAuthorized offers{sku inventoryLevel moq "
              "orderMultiple factoryLeadDays packaging clickUrl prices{quantity price currency convertedPrice "
              "convertedCurrency}}}, estimatedFactoryLeadDays, similarParts, specs{attribute{name shortname} displayValue}. "
              "ASSUMED: attribute names for passives (Resistance, Capacitance, Tolerance, Voltage Rating (DC), Dielectric, "
              "Power Rating, Case/Package); similarParts may be empty without a plan that includes it.")
    FIELDS = """fragment F on SupPart {
  mpn manufacturer { name } octopartUrl shortDescription estimatedFactoryLeadDays
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

    def search(self, q, limit=20):
        self._need()
        data, err = self._gql(self.Q_SEARCH, self._vars(q, limit))
        if err:
            return [], err
        try:
            return [nexar_info(x["part"], self.name, self.currency) for x in
                    ((data.get("supSearch") or {}).get("results") or []) if isinstance(x, dict) and isinstance(x.get("part"), dict)], ""
        except (ValueError, AttributeError, TypeError) as e:
            return [], f"unparseable response: {e}"

    def substitutes(self, p):
        return [], ""


def nexar_info(part, provider, currency):
    info = PartInfo(provider, True, part.get("mpn") or "", (part.get("manufacturer") or {}).get("name") or "",
                    part.get("shortDescription") or "", url=part.get("octopartUrl") or "",
                    lead_days=_int(part.get("estimatedFactoryLeadDays")))
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
                price = pp.get("price") if native else pp.get("convertedPrice")
                if price is not None and (q not in byq or native):
                    byq[q] = float(price)
            info.offers.append(Offer(provider, name, o.get("sku") or "", _int(o.get("inventoryLevel")),
                                     _int(o.get("moq")) or 1, _int(o.get("orderMultiple")) or 1, sorted(byq.items()),
                                     currency.upper(), _int(o.get("factoryLeadDays")) or info.lead_days,
                                     o.get("packaging") or "", o.get("clickUrl") or "", seller.get("isAuthorized")))
    info.similar = [nexar_info(s, provider, currency) for s in part.get("similarParts") or [] if isinstance(s, dict)]
    return info


def parse_nexar_part(data, p, res, currency="USD"):
    results = ((data or {}).get("supSearchMpn") or {}).get("results") or []
    best = L._pick([x.get("part") for x in results if isinstance(x, dict)], p,
                   lambda x: x.get("mpn"), lambda x: (x.get("manufacturer") or {}).get("name"))
    if not best:
        res.found = False
        return res
    return nexar_info(best, res.provider, currency)


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

    def _headers(self):
        return {"X-DIGIKEY-Client-Id": os.environ["DIGIKEY_CLIENT_ID"], "Authorization": f"Bearer {self._token}",
                "Accept": "application/json", "Content-Type": "application/json",
                "X-DIGIKEY-Locale-Site": self.country, "X-DIGIKEY-Locale-Currency": self.currency}

    def _need(self):
        if not self.configured():
            raise P.NotConfigured(f"{self.name}: set {', '.join(self.env)} ({self.auth})")
        return self.token()

    def part(self, p, alternates=False):
        res = PartInfo(self.name, mpn=p["mpn"])
        if not self._need():
            res.error = self._token_err
            return res
        if not p["mpn"]:
            res.error = "no MPN"
            return res
        r = self.http.get(self.URL.format(quote(p["mpn"], safe="")), headers=self._headers())
        if r.status == 404:
            res.found = False
            return res
        if not r.ok:
            res.error = L._http_error(L.LifecycleResult(self.name), r).error
            return res
        try:
            d = r.json()
            prod = d.get("Product") if isinstance(d.get("Product"), dict) else d
            if norm_mpn(prod.get("ManufacturerProductNumber")) != norm_mpn(p["mpn"]):
                res.found, res.note = False, f"best match {prod.get('ManufacturerProductNumber')!r} is not this MPN"
                return res
            return digikey_info(prod, self.name, (d.get("SearchLocaleUsed") or {}).get("Currency") or self.currency)
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res

    def search(self, q, limit=20):
        if not self._need():
            return [], self._token_err
        r = self.http.post(self.KEYWORD_URL, json.dumps({"Keywords": q, "Limit": limit, "Offset": 0}), headers=self._headers())
        if not r.ok:
            return [], L._http_error(L.LifecycleResult(self.name), r).error
        try:
            d = r.json()
            cur = (d.get("SearchLocaleUsed") or {}).get("Currency") or self.currency
            return [digikey_info(x, self.name, cur) for x in d.get("Products") or [] if isinstance(x, dict)], ""
        except (ValueError, AttributeError, TypeError) as e:
            return [], f"unparseable response: {e}"

    def substitutes(self, p):
        if not self._need():
            return [], self._token_err
        r = self.http.get(self.SUBS_URL.format(quote(p["mpn"], safe="")), headers=self._headers())
        if r.status == 404:
            return [], ""
        if not r.ok:
            return [], L._http_error(L.LifecycleResult(self.name), r).error
        try:
            out = []
            for s in r.json().get("ProductSubstitutes") or []:
                if not isinstance(s, dict):
                    continue
                m = s.get("Manufacturer")
                out.append(PartInfo(self.name, True, s.get("ManufacturerProductNumber") or "",
                                    (m.get("Name") if isinstance(m, dict) else m) or "", s.get("Description") or "",
                                    url=s.get("ProductUrl") or "", note=f"DigiKey substitute: {s.get('SubstituteType') or '?'}"))
            return out, ""
        except (ValueError, AttributeError, TypeError) as e:
            return [], f"unparseable response: {e}"


def digikey_info(prod, provider, currency):
    desc = prod.get("Description")
    desc = (desc.get("ProductDescription") or desc.get("DetailedDescription") or "") if isinstance(desc, dict) else str(desc or "")
    m = prod.get("Manufacturer")
    words = [(prod.get("ProductStatus") or {}).get("Status") or ""] + (["End of Life"] if prod.get("EndOfLife") else []) \
        + (["Discontinued at Digi-Key"] if prod.get("Discontinued") else [])
    known = [s for s in map(L.normalise, [w for w in words if w]) if s != "unknown"]
    lead = _int(prod.get("ManufacturerLeadWeeks"))
    info = PartInfo(provider, True, prod.get("ManufacturerProductNumber") or "", (m.get("Name") if isinstance(m, dict) else m) or "",
                    desc, max(known, key=L.SEVERITY.get) if known else "unknown", "; ".join(w for w in words if w),
                    url=prod.get("ProductUrl") or "", lead_days=lead * 7 if lead is not None else None)
    info.specs = specs_from_pairs([(x.get("ParameterText"), x.get("ValueText")) for x in prod.get("Parameters") or []
                                   if isinstance(x, dict)])
    for v in prod.get("ProductVariations") or []:
        pkg = (v.get("PackageType") or {}).get("Name") or ""
        if "digi-reel" in pkg.lower():
            continue
        info.offers.append(Offer(provider, "Digi-Key", v.get("DigiKeyProductNumber") or "",
                                 _int(v.get("QuantityAvailableforPackageType")) if v.get("QuantityAvailableforPackageType") is not None
                                 else _int(prod.get("QuantityAvailable")),
                                 _int(v.get("MinimumOrderQuantity")) or 1, 1,
                                 sorted((_int(b.get("BreakQuantity")), float(b["UnitPrice"])) for b in v.get("StandardPricing") or []
                                        if isinstance(b, dict) and b.get("UnitPrice") is not None and _int(b.get("BreakQuantity"))),
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

    def _post(self, url, body):
        if not self.configured():
            raise P.NotConfigured(f"{self.name}: set {', '.join(self.env)} ({self.auth})")
        # the key is a query parameter: httpcache redacts apiKey= from the cache key and the stored URL
        return self.http.post(url.format(quote(os.environ["MOUSER_API_KEY"], safe="")), json.dumps(body),
                              headers={"Content-Type": "application/json", "Accept": "application/json"})

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
                return res
            info = mouser_info(best, self.name)
            rep = str(best.get("SuggestedReplacement") or "").strip()
            if rep and norm_mpn(rep) != norm_mpn(p["mpn"]):
                info.similar.append(PartInfo(self.name, True, rep, note="Mouser suggested replacement"))
            return info
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res

    def search(self, q, limit=20):
        r = self._post(self.KEYWORD_URL, {"SearchByKeywordRequest": {"keyword": q, "records": min(limit, 50),
                                                                       "startingRecord": 0, "searchOptions": "InStock"}})
        if not r.ok:
            return [], L._http_error(L.LifecycleResult(self.name), r).error
        try:
            parts, err = _mouser_parts(r.json())
            return [mouser_info(x, self.name) for x in parts], err
        except (ValueError, AttributeError, TypeError) as e:
            return [], f"unparseable response: {e}"

    def substitutes(self, p):
        return [], ""


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
                    url=x.get("ProductDetailUrl") or "", lead_days=lead_days(x.get("LeadTime")))
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
              "with the C-number. No lifecycle data.")

    def part(self, p, alternates=False):
        res = PartInfo(self.name, mpn=p["mpn"])
        lcsc, mpn = (p.get("lcsc") or "").upper(), p["mpn"]
        if not (lcsc or mpn):
            res.error = "no MPN or LCSC number"
            return res
        items, err = self._query(lcsc or mpn)
        if err:
            res.error = err
            return res
        best = (next((i for i in items if str(i.get("componentCode", "")).upper() == lcsc), None) if lcsc
                else L._pick(items, p, lambda i: i.get("componentModelEn"), lambda i: i.get("componentBrandEn")))
        if not best:
            res.found = False
            return res
        return jlc_info(best, self.name)

    def _query(self, kw, size=10):
        r = self.http.post(self.URL, json.dumps({"keyword": kw, "currentPage": 1, "pageSize": size}), headers=self.HEADERS)
        if not r.ok:
            return [], L._http_error(L.LifecycleResult(self.name), r).error
        try:
            return [i for i in (((r.json().get("data") or {}).get("componentPageInfo") or {}).get("list") or [])
                    if isinstance(i, dict)], ""
        except (ValueError, AttributeError, TypeError) as e:
            return [], f"unparseable response: {e}"

    def search(self, q, limit=20):
        items, err = self._query(q, limit)
        return [jlc_info(i, self.name) for i in items], err

    def substitutes(self, p):
        return [], ""


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
def merge_infos(infos):
    """One view of a part across sources: first non-empty field in provider order, worst known lifecycle, all offers
    (a distributor SKU reported both directly and through Nexar is kept once, the direct one)."""
    found = [i for i in infos if i.found]
    if not found:
        return None
    m = PartInfo("+".join(i.provider for i in found), True)
    for i in found:
        m.mpn, m.manufacturer = m.mpn or i.mpn, m.manufacturer or i.manufacturer
        m.description, m.url = m.description or i.description, m.url or i.url
        for k, v in i.specs.items():
            m.specs.setdefault(k, v)
        if m.lead_days is None:
            m.lead_days = i.lead_days
        m.similar += i.similar
    known = [i for i in found if i.lifecycle != "unknown"]
    if known:
        w = max(known, key=lambda i: L.SEVERITY[i.lifecycle])
        m.lifecycle = w.lifecycle
    m.lifecycle_raw = "; ".join(f"{i.provider}: {i.lifecycle_raw}" for i in found if i.lifecycle_raw)
    seen = {}
    for o in sorted((o for i in found for o in i.offers), key=lambda o: o.source == "nexar"):
        k = (dist_key(o.distributor), norm_mpn(o.sku) or id(o))
        seen.setdefault(k, o)
    m.offers = list(seen.values())
    return m


def merge_candidates(cands):
    by = {}
    for c in cands:
        if c.mpn:
            by.setdefault(norm_mpn(c.mpn), []).append(c)
    out = []
    for group in by.values():
        m = merge_infos(group)
        m.note = "; ".join(dict.fromkeys(c.note for c in group if c.note))
        out.append(m)
    return out


# ------------------------------------------------------------------ BOM lines
def line_kind(ln):
    m = re.match(r"[A-Z]+", (ln.get("designator") or "").strip().upper())
    return m.group(0) if m and m.group(0) in KIND_WORD else None


def line_qty(ln):
    q = _int(ln.get("qty"))
    return q if q else (count_refs(ln.get("designator") or "") or 1)


def required_specs(ln, kind, part):
    """What a drop-in must meet: the part's own specs (from the sources) first, then the BOM value, footprint and
    description."""
    req = dict(part.specs) if part else {}
    if part:
        for k, v in specs_from_text(part.description, kind).items():
            req.setdefault(k, v)
    v, unit = parse_value(ln.get("value"), True)
    if v is not None and unit in ("", KIND_UNIT[kind]):
        req.setdefault("value", v)
    pkg = package_of(ln.get("footprint"), ln.get("description"), ln.get("value"))
    if pkg:
        req.setdefault("package", pkg)
    for k, val in specs_from_text(f"{ln.get('value', '')} {ln.get('description', '')}", kind).items():
        req.setdefault(k, val)
    req["unit"] = KIND_UNIT[kind]
    return req


def _offer_row(o, b):
    return {"distributor": o.distributor, "sku": o.sku, "source": o.source, "stock": o.stock, "moq": o.moq, "mult": o.mult,
            "order_qty": b["order_qty"], "unit": b["unit"], "ext": b["ext"], "covers": b["covers"], "lead_days": o.lead_days,
            "packaging": o.packaging, "url": o.url}


def suppliers(ranked):
    """Best offer per distributor, in rank order -> [(Supplier name, part number)]."""
    out, seen = [], set()
    for o, b in ranked:
        k = dist_key(o.distributor)
        if k in seen or not o.sku:
            continue
        seen.add(k)
        out.append((SUPPLIER.get(k, o.distributor), o.sku))
    return out


def evaluate_alternates(part, line_mpn, kind, req, cands, need, currency):
    """Score candidate parts. Passives: parametric drop-in check. Others: listed for engineer review only."""
    rows, orig = [], norm_mpn(line_mpn)
    for c in merge_candidates(cands):
        if norm_mpn(c.mpn) == orig:
            continue
        if kind:
            spec = dict(c.specs)
            for k, v in specs_from_text(c.description, kind).items():
                spec.setdefault(k, v)
            verdict, why = drop_in(req, spec, kind)
            if verdict == "mismatch":
                continue
        else:
            verdict, why = "needs engineer review", []
        if c.lifecycle in RISKY:
            continue                                               # never propose an NRND / LTB / obsolete part
        if c.lifecycle == "unknown":
            why = why + ["lifecycle unknown"]
        ranked = rank_offers(c.offers, need, currency)
        best = _offer_row(*ranked[0]) if ranked else None
        same_mfr = bool(part and part.manufacturer and norm_mpn(c.manufacturer)[:6] == norm_mpn(part.manufacturer)[:6])
        rows.append({"mpn": c.mpn, "manufacturer": c.manufacturer, "verdict": verdict, "lifecycle": c.lifecycle,
                     "notes": "; ".join(why + ([c.note] if c.note else [])), "best": best, "same_manufacturer": same_mfr,
                     "sources": c.provider})
    order = {"drop_in": 0, "needs engineer review": 0, "unconfirmed": 1}
    rows.sort(key=lambda r: (order[r["verdict"]], not (r["best"] and r["best"]["covers"]), r["same_manufacturer"],
                             r["best"]["ext"] if r["best"] else float("inf"), r["mpn"]))
    return rows


def source_line(i, ln, provs, build_qty, currency, alternates, low_factor, unconfigured):
    per_board = line_qty(ln)
    need = per_board * build_qty
    pns = part_numbers(ln)
    mpn = pns[0] if pns else ""
    kind = line_kind(ln)
    row = {"line": i, "designator": ln.get("designator", ""), "qty_per_board": per_board, "need": need, "mpn": mpn,
           "manufacturer": ln.get("manufacturer", ""), "lcsc": ln.get("lcsc", ""), "value": ln.get("value", ""),
           "kind": KIND_WORD.get(kind, "other"), "flags": [], "notes": [], "answered": False, "offers": [], "alternates": [],
           "suppliers": [], "chosen": None, "lifecycle": "unknown", "distributors_in_stock": 0, "lead_days": None}
    p = {"mpn": mpn, "manufacturer": ln.get("manufacturer", ""), "lcsc": ln.get("lcsc", "")}
    if not (mpn or p["lcsc"]):
        row["flags"].append("no_mpn")
    infos = []
    for prov in provs:
        if not (mpn or (p["lcsc"] and isinstance(prov, JLCSourcing))):
            continue
        try:
            r = prov.part(p, alternates=alternates)
        except P.NotConfigured:
            unconfigured.add(prov.name)
            continue
        infos.append(r)
        if r.error:
            row["notes"].append(f"{prov.name}: {r.error}")
        elif r.found is False:
            row["notes"].append(f"{prov.name}: not found" + (f" ({r.note})" if r.note else ""))
    row["answered"] = any(r.found is not None and not r.error for r in infos)
    if (mpn or p["lcsc"]) and not row["answered"]:
        row["flags"].append("no_answer")
    part = merge_infos(infos)
    if (mpn or p["lcsc"]) and row["answered"] and not part:
        row["flags"].append("not_found")
    if part:
        row["manufacturer"] = row["manufacturer"] or part.manufacturer
        row["lifecycle"] = part.lifecycle
        if part.lifecycle in RISKY:
            row["flags"].append(f"lifecycle:{part.lifecycle}")
        skipped = sorted({o.currency for o in part.offers if o.currency and o.currency.upper() != currency.upper()})
        if skipped:
            row["notes"].append(f"offers in {', '.join(skipped)} not compared (want {currency})")
        ranked = rank_offers(part.offers, need, currency)
        row["offers"] = [_offer_row(o, b) for o, b in ranked]
        in_stock = {dist_key(o.distributor) for o in part.offers if (o.stock or 0) > 0}
        row["distributors_in_stock"] = len(in_stock)
        row["suppliers"] = suppliers(ranked)
        if not ranked:
            row["flags"].append("no_price")
        else:
            row["chosen"] = row["offers"][0]
            c = row["chosen"]
            row["lead_days"] = c["lead_days"] if c["lead_days"] is not None else part.lead_days
        if not in_stock:
            row["flags"].append("no_stock")
        else:
            if not (row["chosen"] and row["chosen"]["covers"]):
                row["flags"].append("low_stock")
            elif row["chosen"]["stock"] < low_factor * need:
                row["flags"].append("low_stock")
            if len(in_stock) == 1:
                row["flags"].append("single_distributor")
    if alternates and (part or kind):
        cands, req, searched = list(part.similar) if part else [], None, bool(part)
        if kind:
            req = required_specs(ln, kind, part)
            if req.get("value") is None or not req.get("package"):
                row["notes"].append("alternates: value or package unknown, no parametric search")
            else:
                q = " ".join(x for x in (fmt_si(req["value"], KIND_UNIT[kind]), req["package"], KIND_WORD[kind],
                                         req.get("dielectric") if kind == "C" else None) if x)
                for prov in provs:
                    try:
                        found, err = prov.search(q)
                    except P.NotConfigured:
                        unconfigured.add(prov.name)
                        continue
                    cands += found
                    searched |= not err
                    if err:
                        row["notes"].append(f"{prov.name} search: {err}")
        elif part:
            for prov in provs:
                try:
                    found, err = prov.substitutes(p)
                except P.NotConfigured:
                    continue
                cands += found
                if err:
                    row["notes"].append(f"{prov.name} substitutes: {err}")
        if not kind or req.get("value") is not None and req.get("package"):
            row["alternates"] = evaluate_alternates(part, mpn, kind, req, cands, need, currency)
            if not any(a["verdict"] in ("drop_in", "needs engineer review") for a in row["alternates"]):
                if searched:                                   # a source answered and offered nothing usable
                    row["flags"].append("single_source")
            elif not kind:
                row["flags"].append("alternates_need_review")
        row["required"] = {k: v for k, v in (req or {}).items()}
    row["notes"] = list(dict.fromkeys(row["notes"]))
    return row


def source_bom(lines, provs, build_qty=1, currency="USD", alternates=False, low_factor=3.0):
    unconfigured, rows = set(), []
    for i, ln in enumerate(lines, 1):
        if ln.get("dnp"):
            rows.append({"line": i, "designator": ln.get("designator", ""), "qty_per_board": line_qty(ln), "need": 0,
                         "mpn": (part_numbers(ln) or [""])[0], "manufacturer": ln.get("manufacturer", ""), "lcsc": ln.get("lcsc", ""),
                         "value": ln.get("value", ""), "kind": "dnp", "flags": ["dnp"], "notes": ["do not populate: not bought"],
                         "answered": False, "offers": [], "alternates": [], "suppliers": [], "chosen": None,
                         "lifecycle": "", "distributors_in_stock": 0, "lead_days": None})
            continue
        rows.append(source_line(i, ln, provs, build_qty, currency, alternates, low_factor, unconfigured))
    return rows, sorted(unconfigured)


def rollup(rows, build_qty, currency):
    fitted = [r for r in rows if "dnp" not in r["flags"]]
    priced = [r for r in fitted if r["chosen"]]
    total = round(sum(r["chosen"]["ext"] for r in priced), 4)
    full = [r for r in priced if r["chosen"]["covers"]]
    per_dist = {}
    for r in fitted:
        for o in r["offers"]:
            if o["covers"]:
                d = per_dist.setdefault(SUPPLIER.get(dist_key(o["distributor"]), o["distributor"]), {"lines": 0, "cost": 0.0, "seen": set()})
                if r["line"] not in d["seen"]:
                    d["seen"].add(r["line"])
                    d["lines"] += 1
                    d["cost"] += min(x["ext"] for x in r["offers"] if x["covers"] and x["distributor"] == o["distributor"])
    lead = [r["lead_days"] for r in fitted if r["lead_days"] is not None and not (r["chosen"] and r["chosen"]["covers"])]
    return {"currency": currency, "build_qty": build_qty, "lines": len(fitted), "priced": len(priced),
            "in_stock": len(full), "total": total, "per_board": round(total / build_qty, 4) if build_qty else None,
            "complete": len(priced) == len(fitted) and len(full) == len(fitted),
            "longest_lead_days": max(lead) if lead else None,
            "per_distributor": {k: {"lines": v["lines"], "cost": round(v["cost"], 4)} for k, v in
                                sorted(per_dist.items(), key=lambda kv: (-kv[1]["lines"], kv[1]["cost"]))}}


# ------------------------------------------------------------------ plan write-back
def write_plan(plan_path, rows, build_qty, currency, now=None, max_alternates=2):
    """Fill empty Supplier/Alternate parameters of an altium365 plan in place. Returns [(item name, [params set])]."""
    path = Path(plan_path)
    items = json.loads(path.read_text(encoding="utf-8"))
    by = {}
    for r in rows:
        if r["mpn"] and "dnp" not in r["flags"]:
            by.setdefault(norm_mpn(r["mpn"]), r)
    changed = []
    for it in items:
        par = {p["name"]: p["value"] for p in it.get("parameters", [])}
        r = by.get(norm_mpn(par.get("Manufacturer Part Number", "")))
        if not r:
            continue
        sets = {}
        have_pn = {norm_mpn(par.get(f"Supplier Part Number {n}")) for n in (1, 2)} - {""}
        have_d = {dist_key(par.get(f"Supplier {n}")) for n in (1, 2)} - {""}
        sug = [(s, pn) for s, pn in r["suppliers"] if norm_mpn(pn) not in have_pn and dist_key(s) not in have_d]
        for n in (1, 2):
            if not str(par.get(f"Supplier {n}", "")).strip() and not str(par.get(f"Supplier Part Number {n}", "")).strip() and sug:
                s, pn = sug.pop(0)
                sets[f"Supplier {n}"], sets[f"Supplier Part Number {n}"] = s, pn
        drop = [a for a in r["alternates"] if a["verdict"] == "drop_in" and a["lifecycle"] not in RISKY]
        if r["kind"] in KIND_WORD.values():
            have_alt = {norm_mpn(par.get(f"Alternate Manufacturer Part Number {n}")) for n in range(1, max_alternates + 1)} - {""}
            drop = [a for a in drop if norm_mpn(a["mpn"]) not in have_alt]
            for n in range(1, max_alternates + 1):
                if not str(par.get(f"Alternate Manufacturer Part Number {n}", "")).strip() and drop:
                    a = drop.pop(0)
                    sets[f"Alternate Manufacturer {n}"], sets[f"Alternate Manufacturer Part Number {n}"] = a["manufacturer"], a["mpn"]
        for k, v in sets.items():                            # same rule as librarian._set_par: a set value is not cleared
            it["parameters"] = [p for p in it["parameters"] if p["name"] != k] + [{"name": k, "value": v}]
        it["cleared"] = [c for c in it.get("cleared", []) if c not in sets]
        it["sourcing"] = {"checked": now or _now(), "build_qty": build_qty, "currency": currency, "need": r["need"],
                          "chosen": r["chosen"], "flags": r["flags"], "lifecycle": r["lifecycle"],
                          "suppliers": [list(s) for s in r["suppliers"]],
                          "alternates": [{k: a[k] for k in ("mpn", "manufacturer", "verdict", "lifecycle", "notes")}
                                         for a in r["alternates"][:5]]}
        changed.append((it["name"], sorted(sets)))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return changed


# ------------------------------------------------------------------ reports
COLS = ["line", "designator", "qty_per_board", "need", "mpn", "manufacturer", "kind", "lifecycle", "distributor", "sku",
        "unit_price", "order_qty", "ext_price", "currency", "stock", "moq", "mult", "lead_days", "distributors_in_stock",
        "flags", "Supplier 1", "Supplier Part Number 1", "Supplier 2", "Supplier Part Number 2", "alternates", "notes"]


def _alt_text(a):
    b = a["best"]
    price = f" {b['unit']:.4g}@{b['order_qty']}" if b else ""
    return f"{a['manufacturer']} {a['mpn']} [{a['verdict']}{', ' + a['notes'] if a['notes'] else ''}]{price}"


def csv_row(r, currency):
    c = r["chosen"] or {}
    s = r["suppliers"] + [("", "")] * 2
    return {**{k: r.get(k, "") for k in ("line", "designator", "qty_per_board", "need", "mpn", "manufacturer", "kind",
                                         "lifecycle", "distributors_in_stock")},
            "distributor": c.get("distributor", ""), "sku": c.get("sku", ""), "unit_price": c.get("unit", ""),
            "order_qty": c.get("order_qty", ""), "ext_price": c.get("ext", ""), "currency": currency if c else "",
            "stock": c.get("stock", ""), "moq": c.get("moq", ""), "mult": c.get("mult", ""),
            "lead_days": "" if r["lead_days"] is None else r["lead_days"], "flags": " ".join(r["flags"]),
            "Supplier 1": s[0][0], "Supplier Part Number 1": s[0][1], "Supplier 2": s[1][0], "Supplier Part Number 2": s[1][1],
            "alternates": "; ".join(_alt_text(a) for a in r["alternates"][:5]), "notes": "; ".join(r["notes"])}


def write_reports(rows, out, meta, provs, unconfigured, http, roll, plan_changes=None):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cur = roll["currency"]
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        w = SS.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(csv_row(r, cur) for r in rows)
    money_ = lambda v: "-" if v is None or v == "" else f"{v:,.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    md = [f"# Sourcing - `{meta['bom']}`", "", f"- checked: {meta['now']}", f"- build quantity: {roll['build_qty']} boards",
          f"- currency: {cur}", "", "## Cost rollup", "",
          f"- **total**: {money_(roll['total'])} {cur} for {roll['build_qty']} boards "
          f"(**{money_(roll['per_board'])} {cur} per board**; order quantities include MOQ and price-break buy-up)",
          f"- lines priced: {roll['priced']} / {roll['lines']}; fully in stock: {roll['in_stock']} / {roll['lines']}",
          f"- longest lead time of a line not in stock: {roll['longest_lead_days'] if roll['longest_lead_days'] is not None else '-'} days"]
    if not roll["complete"]:
        md.append("- **the total is incomplete**: some lines are unpriced or not in stock (see flags)")
    flags = {}
    for r in rows:
        for fl in r["flags"]:
            flags.setdefault(fl, []).append(r["mpn"] or r["designator"])
    md += ["", "## Flags", ""] + ([f"- **{k}** ({len(v)}): {', '.join(v)}" for k, v in sorted(flags.items())] or ["None."])
    md += ["", "## Lines", "", f"| # | Ref | Need | MPN | Lifecycle | Chosen | Unit | Order qty | Ext ({cur}) | Stock | MOQ | Lead (d) | Flags |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        c = r["chosen"] or {}
        md.append(f"| {r['line']} | {r['designator'][:18]} | {r['need']} | {r['mpn']} | {L.LABEL.get(r['lifecycle'], r['lifecycle'] or '-')} | "
                  f"{(c.get('distributor', '') + ' ' + c.get('sku', '')).strip() or '-'} | {money_(c.get('unit'))} | {c.get('order_qty', '-')} | "
                  f"{money_(c.get('ext'))} | {c.get('stock', '-') if c.get('stock') is not None else '-'} | {c.get('moq', '-')} | "
                  f"{r['lead_days'] if r['lead_days'] is not None else '-'} | {' '.join(r['flags'])} |")
    md += ["", "## Cost if bought from one distributor", "", "Lines each distributor can supply in full from stock, and their cost.", "",
           "| distributor | lines | cost |", "|---|---|---|"]
    md += [f"| {k} | {v['lines']} / {roll['lines']} | {money_(v['cost'])} |" for k, v in roll["per_distributor"].items()] or ["| - | - | - |"]
    alts = [r for r in rows if r["alternates"] or "single_source" in r["flags"]]
    if meta.get("alternates"):
        md += ["", "## Alternates", "",
               "Passives: parametric drop-ins (`drop_in` = every parameter the original states is met or bettered; "
               "`unconfirmed` = the source does not state everything, check before use). "
               "ICs and other parts: **needs engineer review**, never substituted automatically. NRND/LTB/obsolete candidates are dropped.", ""]
        for r in alts:
            req = r.get("required") or {}
            reqs = ", ".join(f"{k} {fmt_si(v, req.get('unit', '')) if k == 'value' else f'{v:g}' if isinstance(v, float) else v}"
                             for k, v in req.items() if k != "unit")
            md += [f"### {r['line']}. {r['mpn'] or r['designator']} ({r['kind']})" + (f" - need {reqs}" if reqs else ""), ""]
            md += [f"- {_alt_text(a)}" for a in r["alternates"][:8]] or ["- no alternate found (single source)"]
            md.append("")
    md += ["", "## Sources", "", "| provider | configured | auth | response shape |", "|---|---|---|---|"]
    md += [f"| {p.name} | {'no' if p.name in unconfigured else 'yes'} | {p.auth} | {p.shapes} |" for p in provs]
    if plan_changes is not None:
        md += ["", f"## Plan write-back (`{meta['plan']}`)", ""]
        md += [f"- {n}: {', '.join(ks) if ks else 'no empty fields to fill (sourcing notes recorded)'}" for n, ks in plan_changes] or ["No plan item matched a BOM MPN."]
    hosts = {}
    for m, u, s, cached in http.log:
        hosts.setdefault(re.sub(r"^https?://([^/]+).*", r"\1", u), []).append(("cache " if cached else "") + str(s))
    md += ["", "## HTTP (this run)", ""] + [f"- `{h}`: {len(v)} requests -> {sorted(set(v))}" for h, v in hosts.items()]
    out.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price, stock, second sources and alternates for a BOM")
    ap.add_argument("bom")
    ap.add_argument("--qty", type=int, default=1, help="number of boards to build (default 1)")
    ap.add_argument("--alternates", action="store_true", help="find drop-in passives and IC alternates (for review)")
    ap.add_argument("--write-plan", metavar="PLAN", help="altium365 plan.json to fill Supplier/Alternate parameters in (in place)")
    ap.add_argument("--providers", default="nexar,digikey,mouser,jlcpcb_search")
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--country", default="US")
    ap.add_argument("--low-stock-factor", type=float, default=3.0, help="flag low_stock when stock < factor x need")
    ap.add_argument("-o", "--out", default="out/sourcing", help="report path stem (.md and .csv written)")
    ap.add_argument("--offline", action="store_true", help="use HTTP cache only")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--max-age", type=float, default=12, help="hours a cached response stays fresh (prices move)")
    ap.add_argument("--rate", type=float, default=1.0, help="min seconds between requests per host")
    a = ap.parse_args(argv)
    if a.qty < 1:
        ap.error("--qty must be at least 1")
    try:
        from . import rules as RU
        rules = RU.load(None, start=Path(a.bom).resolve().parent)     # its [bom] columns name your headers
        lines, _ = read_bom(a.bom, RU.bom_columns(rules), rules)
    except Exception as e:
        print(f"cannot read BOM: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if a.write_plan and not Path(a.write_plan).is_file():
        print(f"plan not found: {a.write_plan}", file=sys.stderr)
        return 2
    http = CachedHTTP(a.cache, min_interval=a.rate, ttl_s=a.max_age * 3600, offline=a.offline)
    want = a.providers.split(",")
    provs = [c(http) for c in PROVIDERS if c.name in want]
    for p in provs:
        p.country, p.currency = a.country, a.currency.upper()
    rows, unconfigured = source_bom(lines, provs, a.qty, a.currency.upper(), a.alternates, a.low_stock_factor)
    roll = rollup(rows, a.qty, a.currency.upper())
    now = _now()
    changes = write_plan(a.write_plan, rows, a.qty, a.currency.upper(), now) if a.write_plan else None
    write_reports(rows, a.out, {"bom": a.bom, "now": now, "alternates": a.alternates, "plan": a.write_plan},
                  provs, unconfigured, http, roll, changes)
    for r in rows:
        c = r["chosen"] or {}
        print(f"{r['line']:>3} {(r['mpn'] or r['designator'])[:24]:<24} need {r['need']:<6} "
              f"{(c.get('distributor', '-') + ' ' + c.get('sku', ''))[:30]:<30} {c.get('ext', '-')!s:<10} {' '.join(r['flags'])}")
    print(f"total {roll['total']} {roll['currency']} for {a.qty} boards ({roll['per_board']} per board); "
          f"{roll['priced']}/{roll['lines']} priced, {roll['in_stock']}/{roll['lines']} in stock -> {Path(a.out).with_suffix('.md')} and .csv")
    if changes is not None:
        print(f"plan updated: {sum(bool(k) for _, k in changes)} items got new fields -> {a.write_plan}")
    if unconfigured:
        print(f"not configured: {', '.join(unconfigured)}")
    fitted = [r for r in rows if "dnp" not in r["flags"]]
    if fitted and not any(r["answered"] for r in fitted):
        print("no source answered for any line (secrets missing, quota, or network blocked)", file=sys.stderr)
        return 3
    return 0 if roll["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
