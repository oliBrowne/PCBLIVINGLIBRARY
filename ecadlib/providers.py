"""Pluggable part-source providers for BOM triage.

Provider interface:  lookup(line) -> SourceResult   (raise NotConfigured if credentials missing)
Implemented (unauthenticated, as used by easyeda2kicad 1.0.1):
  * JLCPCBSearch - POST keyword search, resolves MPN -> LCSC number
  * EasyEDA      - GET component JSON by LCSC number; reports symbol / footprint / 3D presence
Stubs (document auth, raise NotConfigured without keys): DigiKey, Mouser, Nexar, SnapMagic, SamacSys.
All response bodies are untrusted: parsed as JSON only, fields read defensively."""
import json, os
from dataclasses import dataclass, field


class NotConfigured(Exception):
    pass


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

    def __init__(self, http):
        self.http = http

    def configured(self):
        return all(os.environ.get(e) for e in self.env)

    def lookup(self, line) -> SourceResult:
        raise NotImplementedError


# ------------------------------------------------------------------ EasyEDA (LCSC id -> CAD data)
class EasyEDA(Provider):
    name = "easyeda"
    auth = "none (public endpoint used by easyeda2kicad; Referer https://easyeda.com/ header)"
    # easyeda2kicad 1.0.1: API_ENDPOINT = "https://easyeda.com/api/products/{lcsc_id}/components"
    # (older releases appended ?version=6.4.19.5; kept here since the server ignores/accepts it)
    URL = "https://easyeda.com/api/products/{}/components?version=6.4.19.5"
    HEADERS = {"Accept": "application/json, text/javascript, */*; q=0.01", "Referer": "https://easyeda.com/"}

    def lookup(self, line):
        lcsc = (line.get("lcsc") or "").strip().upper()
        if not lcsc:
            return SourceResult(self.name, found=None, error="no LCSC number")
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
    res.manufacturer = cpara.get("Manufacturer") or cpara.get("BOM_Manufacturer") or ""
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
    return res


# ------------------------------------------------------------------ JLCPCB parts search (MPN -> LCSC)
class JLCPCBSearch(Provider):
    name = "jlcpcb_search"
    auth = "none (anonymous JSON POST; Origin/Referer jlcpcb.com) - as used by easyeda2kicad search"
    URL = "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList"
    HEADERS = {"Content-Type": "application/json", "Origin": "https://jlcpcb.com", "Referer": "https://jlcpcb.com/parts"}

    def lookup(self, line):
        mpn = (line.get("mpn") or "").strip()
        if not mpn:
            return SourceResult(self.name, found=None, error="no MPN")
        body = json.dumps({"keyword": mpn, "currentPage": 1, "pageSize": 10})
        r = self.http.post(self.URL, body, headers=self.HEADERS)
        res = SourceResult(self.name, mpn=mpn)
        if not r.ok:
            res.error = r.error or f"HTTP {r.status}"
            return res
        try:
            from .kicad_index import norm_mpn
            items = (((r.json().get("data") or {}).get("componentPageInfo") or {}).get("list")) or []
            exact = [i for i in items if norm_mpn(i.get("componentModelEn")) == norm_mpn(mpn)]
        except (ValueError, AttributeError, TypeError) as e:          # untrusted body of an unexpected shape
            res.error = f"unparseable: {e}"
            return res
        best = (exact or [None])[0]
        res.found = bool(best)
        if best:
            res.lcsc = best.get("componentCode", "")
            res.manufacturer = best.get("componentBrandEn", "")
            res.package = best.get("componentSpecificationEn", "")
            res.extra = {"stock": best.get("stockCount"), "library_type": best.get("componentLibraryType")}
        return res


# ------------------------------------------------------------------ authenticated distributor / CAD stubs
class _Stub(Provider):
    docs = ""

    def lookup(self, line):
        if not self.configured():
            raise NotConfigured(f"{self.name}: set {', '.join(self.env)} ({self.auth})")
        raise NotImplementedError(f"{self.name}: credentials present but client not implemented in prototype. {self.docs}")


class DigiKey(_Stub):
    name, env = "digikey", ("DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")
    auth = "OAuth2 client-credentials app from developer.digikey.com (Product Information API v4)"
    docs = "POST https://api.digikey.com/products/v4/search/keyword with X-DIGIKEY-Client-Id + Bearer token."


class Mouser(_Stub):
    name, env = "mouser", ("MOUSER_API_KEY",)
    auth = "Search API key (free, mouser.com/api-hub) passed as ?apiKey="
    docs = "POST https://api.mouser.com/api/v1/search/partnumber?apiKey=KEY {SearchByPartRequest:{mouserPartNumber}}."


class Nexar(_Stub):
    name, env = "nexar", ("NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET")
    auth = "OAuth2 client-credentials (identity.nexar.com) for the Octopart/Nexar GraphQL API; quota-limited"
    docs = "POST https://api.nexar.com/graphql query supSearchMpn(q:...) {results{part{mpn manufacturer{name} cadModels...}}}."


class SnapMagic(_Stub):
    name, env = "snapmagic", ("SNAPMAGIC_API_TOKEN",)
    auth = "SnapMagic (ex-SnapEDA) partner API token - no self-serve public API; CAD downloads need a user login"
    docs = "Partner/enterprise API by agreement; otherwise manual download from snapeda.com."


class SamacSys(_Stub):
    name, env = "samacsys", ("SAMACSYS_USERNAME", "SAMACSYS_PASSWORD")
    auth = "componentsearchengine.com account; access is via Library Loader / partner API, no documented public REST"
    docs = "Library Loader desktop app or supplier-embedded (Mouser/RS 'ECAD model') links."


ALL = [JLCPCBSearch, EasyEDA, DigiKey, Mouser, Nexar, SnapMagic, SamacSys]
