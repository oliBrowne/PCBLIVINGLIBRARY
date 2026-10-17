"""Lifecycle watcher: keep the library current.

Checks every part against lifecycle sources, maps each source's wording to one status, diffs the result
against the last snapshot and reports what changed (.md + .csv). Exit 1 when any part got worse, for CI.

    python -m ecadlib.lifecycle examples/sample_bom.csv out/altium/parts.sqlite out/altium365/plan.json \
        [--a365 WORKSPACE_URL] [--providers nexar,digikey,mouser,jlcpcb_search] \
        [--state out/lifecycle_state.json] [-o out/lifecycle_report] [--update-a365 [--apply]]

Inputs (auto-detected): a BOM (.csv/.xlsx, via bom_triage.read_bom), the DbLib parts.sqlite (altium_export),
an Altium 365 plan.json (altium365 plan), and with --a365 the managed components of a workspace (Nexar desLibrary).

Statuses by severity: active < nrnd < ltb < obsolete (EOL counts as obsolete); unknown is unranked.
A part got worse when its status rose in severity or its stock fell to zero.
Exit codes: 0 nothing got worse, 1 a part got worse (--strict: also a new risky part or one that became
unknown), 2 bad input or a failed Altium 365 update, 3 no source answered for any part (secrets / network).

Response shapes: each provider's `shapes` says what is confirmed from public docs and what is assumed.
None of these hosts was reachable from the sandbox this was written in; parsers are tested against mocks."""
import argparse, csv, json, os, re, sqlite3, sys
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from . import providers as P
from . import sheetsafe as SS
from .altium365 import M_UPDATE_PARAMS, Nexar as DesignClient
from .httpcache import CachedHTTP
from .kicad_index import norm_mpn

SEVERITY = {"active": 0, "nrnd": 1, "ltb": 2, "obsolete": 3}
LABEL = {"active": "Active", "nrnd": "NRND", "ltb": "Last Time Buy", "obsolete": "Obsolete", "unknown": "Unknown"}
WORDING = [   # first match wins; order matters ('Discontinued at Digi-Key' is nrnd, plain 'discontinued' obsolete)
    (r"last time buy|\bltb\b|final buy", "ltb"),
    (r"\bnrnd\b|not (recommended )?for new design|discontinued at digi[ -]?key", "nrnd"),
    (r"obsolete|\beol\b|end of life|discontinued|no longer (manufactured|available)|inactive", "obsolete"),
    (r"^(active|production|in production|new|new product|preliminary|released)\b", "active"),
]


def normalise(text):
    t = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return next((st for rx, st in WORDING if re.search(rx, t)), "unknown")


def _int(v):
    m = re.search(r"\d[\d,]*", str(v)) if v is not None else None
    return int(m.group(0).replace(",", "")) if m else None


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class LifecycleResult:
    provider: str
    found: bool | None = None          # None = could not determine
    status: str = "unknown"
    raw: str = ""                      # the provider's own wording
    stock: int | None = None
    mpn: str = ""
    manufacturer: str = ""
    url: str = ""
    note: str = ""
    error: str = ""


def _pick(items, part, mpn_of, mfr_of):
    """Exact (normalised) MPN match; prefer one whose manufacturer agrees with the part's."""
    exact = [i for i in items if isinstance(i, dict) and norm_mpn(mpn_of(i)) == norm_mpn(part["mpn"])]
    want = norm_mpn(part.get("manufacturer"))[:6]
    return next((i for i in exact if want and want in norm_mpn(mfr_of(i))), exact[0] if exact else None)


def _http_error(res, r):
    res.error = re.sub(r"(?i)(api_?key=)[^&\s'\")]*", r"\1***", r.error or f"HTTP {r.status}")   # may be cut mid-key
    return res


# ------------------------------------------------------------------ Nexar Supply (Octopart)
class NexarSupply(P.Nexar):
    shapes = ("CONFIRMED: token POST identity.nexar.com/connect/token, client_credentials, scope supply.domain "
              "(support.nexar.com 'Authorization'); query fields validated against tests/nexar/schema.graphql; "
              "specs[].attribute.shortname 'lifecyclestatus' with values Production/New/NRND/EOL/Obsolete "
              "(support.nexar.com 'Supply: Lifecycle information for parts'; needs a plan with Tech Specs + Lifecycle). "
              "ASSUMED: displayValue holds that wording. The schema has no median-stock field: totalAvail is used.")
    TOKEN_URL = os.environ.get("NEXAR_TOKEN_URL", "https://identity.nexar.com/connect/token")
    API_URL = os.environ.get("NEXAR_API_URL", "https://api.nexar.com/graphql")
    QUERY = """query ($q: String!, $limit: Int) {
  supSearchMpn(q: $q, limit: $limit) {
    hits
    results { part { mpn manufacturer { name } octopartUrl totalAvail avgAvail
      specs { attribute { shortname } displayValue value } } }
  }
}"""
    _token = _token_err = None

    def configured(self):
        return bool(os.environ.get("NEXAR_SUPPLY_TOKEN")) or super().configured()

    def token(self):
        if self._token or self._token_err:
            return self._token
        tok = os.environ.get("NEXAR_SUPPLY_TOKEN")
        if not tok:
            body = urlencode({"grant_type": "client_credentials", "client_id": os.environ["NEXAR_CLIENT_ID"],
                              "client_secret": os.environ["NEXAR_CLIENT_SECRET"], "scope": "supply.domain"})
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

    def check(self, part):
        if not self.configured():
            raise P.NotConfigured(f"{self.name}: set NEXAR_SUPPLY_TOKEN or {', '.join(self.env)} ({self.auth})")
        res = LifecycleResult(self.name, mpn=part["mpn"])
        if not part["mpn"]:
            res.error = "no MPN"
            return res
        tok = self.token()
        if not tok:
            res.error = self._token_err
            return res
        body = json.dumps({"query": self.QUERY, "variables": {"q": part["mpn"], "limit": 5}})
        r = self.http.post(self.API_URL, body, headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        if not r.ok:
            return _http_error(res, r)
        try:
            return parse_nexar(r.json(), part, res)
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res


def parse_nexar(data, part, res):
    if data.get("errors"):
        res.error = "; ".join(str(e.get("message", "?")) for e in data["errors"] if isinstance(e, dict))
        return res
    results = ((data.get("data") or {}).get("supSearchMpn") or {}).get("results") or []
    best = _pick([x.get("part") for x in results if isinstance(x, dict)], part,
                 lambda p: p.get("mpn"), lambda p: (p.get("manufacturer") or {}).get("name"))
    res.found = bool(best)
    if best:
        spec = next((s for s in best.get("specs") or [] if (s.get("attribute") or {}).get("shortname") == "lifecyclestatus"), {})
        res.raw = spec.get("displayValue") or spec.get("value") or ""
        res.status = normalise(res.raw)
        res.stock = _int(best.get("totalAvail"))
        res.manufacturer, res.url = (best.get("manufacturer") or {}).get("name", ""), best.get("octopartUrl", "")
        if not spec:
            res.note = "no lifecyclestatus spec (plan without Tech Specs/Lifecycle?)"
    return res


# ------------------------------------------------------------------ DigiKey Product Information v4
class DigiKeyLifecycle(P.DigiKey):
    shapes = ("CONFIRMED (DigiKey v4 OpenAPI): GET /products/v4/search/{productNumber}/productdetails, header "
              "X-DIGIKEY-Client-Id; Product.ManufacturerProductNumber, Product.ProductStatus ($ref ProductStatusV4), "
              "Product.QuantityAvailable, Product.Discontinued, Product.EndOfLife. ASSUMED: response wrapper "
              "{Product: ...}, ProductStatusV4 = {Id, Status} with wording like 'Active', 'Not For New Designs', "
              "'Last Time Buy', 'Obsolete', 'Discontinued at Digi-Key'; token URL api.digikey.com/v1/oauth2/token.")
    TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
    URL = "https://api.digikey.com/products/v4/search/{}/productdetails"
    _token = _token_err = None

    def token(self):
        if not (self._token or self._token_err):
            body = urlencode({"client_id": os.environ["DIGIKEY_CLIENT_ID"], "client_secret": os.environ["DIGIKEY_CLIENT_SECRET"],
                              "grant_type": "client_credentials"})
            r = self.http.post(self.TOKEN_URL, body, cache=False, headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                self._token = r.json().get("access_token") if r.ok else None
            except ValueError:
                pass
            if not self._token:
                self._token_err = f"token request failed: {r.error or f'HTTP {r.status}'}"
        return self._token

    def check(self, part):
        if not self.configured():
            raise P.NotConfigured(f"{self.name}: set {', '.join(self.env)} ({self.auth})")
        res = LifecycleResult(self.name, mpn=part["mpn"])
        if not part["mpn"]:
            res.error = "no MPN"
            return res
        if not self.token():
            res.error = self._token_err
            return res
        r = self.http.get(self.URL.format(quote(part["mpn"], safe="")), headers={
            "X-DIGIKEY-Client-Id": os.environ["DIGIKEY_CLIENT_ID"], "Authorization": f"Bearer {self._token}",
            "Accept": "application/json"})
        if r.status == 404:
            res.found = False
            return res
        if not r.ok:
            return _http_error(res, r)
        try:
            return parse_digikey(r.json(), part, res)
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res


def parse_digikey(data, part, res):
    p = data.get("Product") if isinstance(data.get("Product"), dict) else data
    if norm_mpn(p.get("ManufacturerProductNumber")) != norm_mpn(part["mpn"]):
        res.found, res.note = False, f"best match {p.get('ManufacturerProductNumber')!r} is not this MPN"
        return res
    res.found = True
    words = [(p.get("ProductStatus") or {}).get("Status") or ""]
    if p.get("EndOfLife"):
        words.append("End of Life")
    if p.get("Discontinued"):
        words.append("Discontinued at Digi-Key")
    words = [w for w in words if w]
    res.raw = "; ".join(words)
    known = [s for s in map(normalise, words) if s != "unknown"]
    res.status = max(known, key=SEVERITY.get) if known else "unknown"
    res.stock = _int(p.get("QuantityAvailable"))
    res.manufacturer = (p.get("Manufacturer") or {}).get("Name", "") if isinstance(p.get("Manufacturer"), dict) else ""
    res.url = p.get("ProductUrl") or ""
    return res


# ------------------------------------------------------------------ Mouser Search API v1
class MouserLifecycle(P.Mouser):
    shapes = ("CONFIRMED (Mouser swagger api.mouser.com/api/docs/V1): POST /api/v1/search/partnumber?apiKey=, body "
              "{SearchByPartRequest: {mouserPartNumber, partSearchOptions}}; response {Errors: [], SearchResults: "
              "{NumberOfResult, Parts: [{ManufacturerPartNumber, Manufacturer, LifecycleStatus, Availability, "
              "AvailabilityInStock, ProductDetailUrl}]}}, all strings. ASSUMED: partSearchOptions 'Exact'; "
              "LifecycleStatus wording ('New Product', 'Not Recommended for New Designs', 'End of Life', 'Obsolete') "
              "and that it is empty for normal production parts (read as active).")
    URL = "https://api.mouser.com/api/v1/search/partnumber?apiKey={}"

    def check(self, part):
        if not self.configured():
            raise P.NotConfigured(f"{self.name}: set {', '.join(self.env)} ({self.auth})")
        res = LifecycleResult(self.name, mpn=part["mpn"])
        if not part["mpn"]:
            res.error = "no MPN"
            return res
        key = os.environ["MOUSER_API_KEY"]
        body = json.dumps({"SearchByPartRequest": {"mouserPartNumber": part["mpn"], "partSearchOptions": "Exact"}})
        r = self.http.post(self.URL.format(quote(key, safe="")), body, cache=False,      # key is in the URL: never on disk
                           headers={"Content-Type": "application/json", "Accept": "application/json"})
        if not r.ok:
            return _http_error(res, r)
        try:
            return parse_mouser(r.json(), part, res)
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res


def parse_mouser(data, part, res):
    errs = [e for e in data.get("Errors") or [] if isinstance(e, dict)]
    if errs:
        res.error = "; ".join(str(e.get("Message") or e.get("Code") or e) for e in errs)
        return res
    best = _pick((data.get("SearchResults") or {}).get("Parts") or [], part,
                 lambda p: p.get("ManufacturerPartNumber"), lambda p: p.get("Manufacturer"))
    res.found = bool(best)
    if best:
        res.raw = best.get("LifecycleStatus") or ""
        res.status = normalise(res.raw) if res.raw else "active"
        if not res.raw:
            res.note = "no LifecycleStatus: read as active"
        res.stock = _int(best.get("AvailabilityInStock"))
        if res.stock is None and re.search(r"in stock", str(best.get("Availability")), re.I):
            res.stock = _int(best.get("Availability"))
        res.manufacturer, res.url = best.get("Manufacturer") or "", best.get("ProductDetailUrl") or ""
    return res


# ------------------------------------------------------------------ JLCPCB / LCSC (stock only)
class JLCLifecycle(P.JLCPCBSearch):
    shapes = ("ASSUMED (undocumented; field paths from easyeda2kicad 1.0.1, as in providers.JLCPCBSearch): "
              "data.componentPageInfo.list[] with componentCode, componentModelEn, componentBrandEn, stockCount. "
              "No lifecycle field is known, so this source reports stock only; a search by LCSC code is assumed to work.")

    def check(self, part):
        lcsc, mpn = (part.get("lcsc") or "").upper(), part["mpn"]
        res = LifecycleResult(self.name, mpn=mpn)
        if not (lcsc or mpn):
            res.error = "no MPN or LCSC number"
            return res
        r = self.http.post(self.URL, json.dumps({"keyword": lcsc or mpn, "currentPage": 1, "pageSize": 10}), headers=self.HEADERS)
        if not r.ok:
            return _http_error(res, r)
        try:
            return parse_jlc(r.json(), part, res)
        except (ValueError, AttributeError, TypeError) as e:
            res.error = f"unparseable response: {e}"
            return res


def parse_jlc(data, part, res):
    items = [i for i in (((data.get("data") or {}).get("componentPageInfo") or {}).get("list") or []) if isinstance(i, dict)]
    lcsc = (part.get("lcsc") or "").upper()
    best = (next((i for i in items if str(i.get("componentCode", "")).upper() == lcsc), None) if lcsc
            else _pick(items, part, lambda i: i.get("componentModelEn"), lambda i: i.get("componentBrandEn")))
    res.found = bool(best)
    if best:
        res.stock = _int(best.get("stockCount"))
        res.manufacturer = best.get("componentBrandEn") or ""
        res.url = f"https://jlcpcb.com/partdetail/{best.get('componentCode', '')}"
        res.note = f"LCSC {best.get('componentCode', '')}"
    elif lcsc:
        res.note = f"{lcsc} not listed"
    return res


PROVIDERS = [NexarSupply, DigiKeyLifecycle, MouserLifecycle, JLCLifecycle]


# ------------------------------------------------------------------ part lists
def _part(mpn, manufacturer="", lcsc="", name="", source="", component_id="", a365_value=None):
    return {"mpn": (mpn or "").strip(), "manufacturer": (manufacturer or "").strip(), "lcsc": (lcsc or "").strip().upper(),
            "name": name or "", "sources": [source] if source else [], "component_id": component_id,
            "a365_value": a365_value}


def key_of(p):
    return norm_mpn(p["mpn"]) if p["mpn"] else f"LCSC:{p['lcsc']}" if p["lcsc"] else ""


def _from_params(prm, name, source, **kw):
    lcsc = prm.get("Supplier Part Number 1", "") if prm.get("Supplier 1", "").upper() in ("LCSC", "JLCPCB") else ""
    return _part(prm.get("Manufacturer Part Number", ""), prm.get("Manufacturer", ""), lcsc, name, source, **kw)


def parts_from_bom(path, columns=None):
    from .altium_export import to_rows
    from .bom_triage import part_numbers, read_bom
    lines = []
    for ln in read_bom(path, columns)[0]:
        if ln.get("dnp"):
            continue
        pns = part_numbers(ln)
        lines.append(dict(ln, mpn=pns[0] if pns else ""))     # Altium Comment/LibRef part numbers count too
    return [_from_params(r, r["Part Number"], f"bom:{Path(path).name}")
            for df in to_rows(lines).values() for r in df.to_dict("records")]


def parts_from_dblib(path):
    out = []
    from urllib.parse import quote                   # a folder named "Lib #2" or "a?b" must not end the URI path
    with closing(sqlite3.connect(f"file:{quote(Path(path).resolve().as_posix(), safe='/:')}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for r in con.execute(f'SELECT * FROM "{t}"'):
                r = {k: str(r[k] or "") for k in r.keys()}
                out.append(_from_params(r, r.get("Part Number", ""), f"dblib:{t}"))
    return out


def parts_from_plan(path):
    return [_from_params({p["name"]: p["value"] for p in it.get("parameters", [])}, it["name"], "plan")
            for it in json.loads(Path(path).read_text(encoding="utf-8"))]


Q_LIBRARY = """query ($ws: String!, $after: String) {
  desLibrary(workspaceUrl: $ws) {
    components(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id name isManaged manufacturerParts { companyName partNumber } details { parameters { name value } } }
    }
  }
}"""

Q_FIND_PARAMS = """query ($ws: String!, $name: String!) {
  desLibrary(workspaceUrl: $ws) {
    components(first: 1, where: {name: {eq: $name}}) { nodes { id name isManaged details { parameters { name value } } } }
  }
}"""


def _node_params(node):
    return {p["name"]: p["value"] for p in ((node.get("details") or {}).get("parameters") or [])}


def parts_from_a365(client, ws, param="Lifecycle"):
    """Managed (released) components of a workspace library; MPN from parameters, else the first part choice."""
    out, after = [], None
    while True:
        conn = client.gql(Q_LIBRARY, {"ws": ws, "after": after})["desLibrary"]["components"] or {}
        for n in conn.get("nodes") or []:
            if not n.get("isManaged"):
                continue
            prm, mp = _node_params(n), (n.get("manufacturerParts") or [{}])[0] or {}
            prm.setdefault("Manufacturer Part Number", mp.get("partNumber", ""))
            prm.setdefault("Manufacturer", mp.get("companyName", ""))
            out.append(_from_params(prm, n["name"], "a365", component_id=n["id"], a365_value=prm.get(param)))
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return out
        after = page.get("endCursor")


def read_inputs(paths, columns=None):
    """columns: the rules' [bom] columns ({field: your header}) for BOM inputs."""
    parts = []
    for p in paths:
        ext = Path(p).suffix.lower()
        parts += (parts_from_dblib(p) if ext in (".sqlite", ".db", ".sqlite3") else
                  parts_from_plan(p) if ext == ".json" else parts_from_bom(p, columns))
    return parts


def merge(parts):
    """One entry per MPN (or LCSC number); fields from later inputs only fill gaps."""
    out, skipped = {}, []
    for p in parts:
        k = key_of(p)
        if not k:
            skipped.append(p["name"])
            continue
        cur = out.setdefault(k, p)
        if cur is not p:
            for f in ("manufacturer", "lcsc", "name", "component_id", "a365_value"):
                cur[f] = cur[f] or p[f]
            cur["sources"] = list(dict.fromkeys(cur["sources"] + p["sources"]))
    return out, skipped


# ------------------------------------------------------------------ checking, state, diff
def check_parts(parts, provs):
    unconfigured, out = set(), {}
    for k, p in parts.items():
        results = []
        for prov in provs:
            try:
                results.append(prov.check(p))
            except P.NotConfigured:
                unconfigured.add(prov.name)
        out[k] = combine(p, results)
    return out, sorted(unconfigured)


def combine(part, results):
    """Worst known status across sources (most conservative); highest stock any source reports."""
    known = [r for r in results if r.status != "unknown"]
    stocks = [r.stock for r in results if r.stock is not None]
    return {**{f: part[f] for f in ("mpn", "manufacturer", "lcsc", "name", "sources", "component_id", "a365_value")},
            "status": max(known, key=lambda r: SEVERITY[r.status]).status if known else "unknown",
            "stock": max(stocks) if stocks else None,
            "raw": "; ".join(f"{r.provider}: {r.raw or '-'}" + (f" (stock {r.stock})" if r.stock is not None else "")
                             for r in results if r.found),
            "notes": "; ".join(f"{r.provider}: {r.error or r.note or 'not found'}" for r in results
                               if r.error or r.note or r.found is False),
            "answered": any(r.found is not None and not r.error for r in results),
            "results": [asdict(r) for r in results]}


def classify(prev, cur):
    """new | unchanged | worse | better | unknown, comparing with the last *known* values in the snapshot."""
    if prev is None:
        return "new"
    ps, pk, cs, ck = prev.get("status", "unknown"), prev.get("stock"), cur["status"], cur["stock"]
    if cs == "unknown" and ck is None:
        return "unknown" if ps != "unknown" or pk is not None else "unchanged"
    worse = better = False
    if cs != "unknown":
        d = SEVERITY[cs] - SEVERITY.get(ps, 0)                 # never-known counts as active
        worse, better = d > 0, d < 0 and ps != "unknown"
    if ck is not None and pk is not None:
        worse |= pk > 0 and ck == 0
        better |= pk == 0 and ck > 0
    return "worse" if worse else "better" if better else "unchanged"


def risky(cur):
    return SEVERITY.get(cur["status"], 0) > 0 or cur["stock"] == 0


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "updated": None, "parts": {}}


def diff(state, current):
    rows = []
    for k, cur in current.items():
        prev = state["parts"].get(k)
        rows.append({"key": k, "change": classify(prev, cur), "prev_status": (prev or {}).get("status", ""),
                     "prev_stock": (prev or {}).get("stock"), "prev_checked": (prev or {}).get("checked", ""), **cur})
    order = {"worse": 0, "unknown": 1, "new": 2, "better": 3, "unchanged": 4}
    return sorted(rows, key=lambda r: (order[r["change"]], -SEVERITY.get(r["status"], -1), r["mpn"]))


def next_state(state, current, now):
    """An unknown observation never overwrites a known status or stock: diffs compare against what was last seen."""
    parts = dict(state["parts"])
    for k, cur in current.items():
        prev = parts.get(k) or {}
        known = cur["status"] != "unknown"
        st = cur["status"] if known else prev.get("status", "unknown")
        parts[k] = {"mpn": cur["mpn"], "manufacturer": cur["manufacturer"], "name": cur["name"], "status": st,
                    "raw": cur["raw"] if known else prev.get("raw", ""),
                    "stock": cur["stock"] if cur["stock"] is not None else prev.get("stock"),
                    "since": prev.get("since") if prev.get("status") == st and prev.get("since") else now,
                    "seen": now if known else prev.get("seen"), "checked": now}
    return {"version": 1, "updated": now, "parts": parts}


def save_state(state, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def exit_code(rows, strict=False):
    bad = [r for r in rows if r["change"] == "worse"]
    if strict:
        bad += [r for r in rows if r["change"] == "unknown" or (r["change"] == "new" and risky(r))]
    return 1 if bad else 0


# ------------------------------------------------------------------ Altium 365 write-back
def a365_updates(rows, client, ws, apply=False, param="Lifecycle", note="ecadlib lifecycle watch"):
    """Set `param` on released components whose value differs from the checked status (unknown is never written).
    Without a client (dry-run, no token) the component id is a placeholder and the current value is not known."""
    out = []
    for r in rows:
        if r["status"] == "unknown" or not r["name"]:
            continue
        want, cid, cur = LABEL[r["status"]], r.get("component_id"), r.get("a365_value")
        rec = {"name": r["name"], "mpn": r["mpn"], "value": want, "action": "", "detail": ""}
        try:
            cur_params = None
            if client:                           # fetch the current revision: a replace must carry every parameter
                nodes = client.gql(Q_FIND_PARAMS, {"ws": ws, "name": r["name"]})["desLibrary"]["components"] or {}
                node = (nodes.get("nodes") or [None])[0]
                if not node or not node.get("isManaged"):
                    rec["action"] = "skip"
                    rec["detail"] = "not in workspace" if not node else "not a released (managed) component"
                    out.append(rec)
                    continue
                cur_params = _node_params(node)
                cid, cur = node["id"], cur_params.get(param)
            if cur == want:
                rec["action"] = "unchanged"
                out.append(rec)
                continue
            # replaceExisting=false *adds* a second Lifecycle parameter, so send the full merged set with true
            params = [{"name": k, "value": v} for k, v in {**(cur_params or {}), param: want}.items()]
            if apply:
                rec["action"], rec["detail"] = "updated", client.update_params(cid, params, note, replace=True)
            else:
                inp = {"componentId": cid or f"<id of {r['name']}>", "releaseNote": note,
                       "replaceExisting": cur_params is not None, "parameters": params}
                rec["action"], rec["detail"] = "dry_run", {"mutation": "desUpdateComponentRevisionParameters",
                                                           "query": M_UPDATE_PARAMS, "variables": {"input": inp}}
                if cur_params is None:
                    rec["note"] = "no token: current parameters unknown; --apply fetches them and replaces the full set"
        except Exception as e:                   # keep going; report per part
            rec["action"], rec["detail"] = "error", f"{type(e).__name__}: {e}"
        out.append(rec)
    return out


# ------------------------------------------------------------------ reports
COLS = ["change", "mpn", "manufacturer", "name", "prev_status", "status", "prev_stock", "stock", "raw", "lcsc",
        "sources", "prev_checked", "notes"]


def write_reports(rows, out, meta, provs, unconfigured, http, a365=None):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = SS.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows({**r, "sources": ",".join(r["sources"])} for r in rows)
    st = lambda s: LABEL.get(s, s or "-")
    sk = lambda v: "-" if v is None else str(v)
    counts = {c: sum(r["change"] == c for r in rows) for c in ("worse", "unknown", "new", "better", "unchanged")}
    md = [f"# Lifecycle watch - {meta['now']}", "", f"- inputs: {', '.join(meta['inputs']) or '-'}",
          f"- state: `{meta['state']}` (previous snapshot: {meta['prev'] or 'none'})",
          f"- parts checked: {len(rows)}" + (f"; skipped without MPN or LCSC: {', '.join(meta['skipped'])}" if meta["skipped"] else ""),
          "", "## Summary", ""] + [f"- **{k}**: {v}" for k, v in counts.items()]
    md += ["", "Statuses now: " + ", ".join(f"{LABEL[s]} {n}" for s in LABEL
                                            if (n := sum(r["status"] == s for r in rows)))]
    hdr = ["| Change | MPN | Manufacturer | Part | Was | Now | Stock was -> now | Source wording | Notes |",
           "|---|---|---|---|---|---|---|---|---|"]
    line = lambda r: (f"| {r['change']} | {r['mpn'] or r['lcsc']} | {r['manufacturer']} | {r['name']} | {st(r['prev_status'])} | "
                      f"{st(r['status'])} | {sk(r['prev_stock'])} -> {sk(r['stock'])} | {r['raw']} | {r['notes'][:160]} |")
    changed = [r for r in rows if r["change"] != "unchanged"]
    md += ["", "## Changes since last snapshot", ""] + ((hdr + [line(r) for r in changed]) if changed else ["None."])
    md += ["", "## All parts", ""] + hdr + [line(r) for r in rows]
    md += ["", "## Sources", "", "| provider | configured | auth | response shape |", "|---|---|---|---|"]
    md += [f"| {p.name} | {'no' if p.name in unconfigured else 'yes'} | {p.auth} | {p.shapes} |" for p in provs]
    if a365 is not None:
        md += ["", f"## Altium 365 `Lifecycle` parameter ({meta['a365_mode']})", "", "| component | MPN | value | action | detail |",
               "|---|---|---|---|---|"]
        md += [f"| {u['name']} | {u['mpn']} | {u['value']} | {u['action']} | "
               f"{u['detail'] if isinstance(u['detail'], str) else 'see ' + out.name + '_a365.json'} |" for u in a365]
    hosts = {}
    for m, u, s, cached in http.log:
        hosts.setdefault(re.sub(r"^https?://([^/]+).*", r"\1", u), []).append(("cache " if cached else "") + str(s))
    md += ["", "## HTTP (this run)", ""] + [f"- `{h}`: {len(v)} requests -> {sorted(set(v))}" for h, v in hosts.items()]
    out.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Watch parts for lifecycle changes (NRND, LTB, obsolete, out of stock)")
    ap.add_argument("inputs", nargs="*", help="BOM (.csv/.xlsx), DbLib parts.sqlite, or Altium 365 plan.json")
    ap.add_argument("--a365", metavar="WORKSPACE_URL", help="also watch the managed components of this workspace (NEXAR_TOKEN)")
    ap.add_argument("--providers", default="nexar,digikey,mouser,jlcpcb_search")
    ap.add_argument("--state", default="out/lifecycle_state.json", help="snapshot of the last-seen status per MPN")
    ap.add_argument("-o", "--out", default="out/lifecycle_report", help="report path stem (.md and .csv written)")
    ap.add_argument("--strict", action="store_true", help="also fail on new parts already NRND+/out of stock, and on became-unknown")
    ap.add_argument("--no-save", action="store_true", help="do not update the snapshot")
    ap.add_argument("--update-a365", action="store_true", help="write a Lifecycle parameter to released A365 components")
    ap.add_argument("--workspace", help="workspace URL for --update-a365 (default: --a365)")
    ap.add_argument("--apply", action="store_true", help="send the A365 updates (default: dry run)")
    ap.add_argument("--param", default="Lifecycle", help="A365 parameter name")
    ap.add_argument("--offline", action="store_true", help="use HTTP cache only")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--max-age", type=float, default=20, help="hours a cached response stays fresh")
    ap.add_argument("--rate", type=float, default=1.0, help="min seconds between requests per host")
    a = ap.parse_args(argv)
    ws = a.workspace or a.a365
    if not a.inputs and not a.a365:
        ap.error("give at least one input file or --a365 WORKSPACE_URL")
    if a.update_a365 and not ws:
        ap.error("--update-a365 needs --workspace or --a365")
    token = os.environ.get("NEXAR_TOKEN")
    if (a.a365 or a.apply) and not token:
        ap.error("set NEXAR_TOKEN (Nexar user token with design.domain, user.access, openid scopes)")
    client = DesignClient(token) if token and (a.a365 or a.update_a365) else None

    try:
        parts = read_inputs(a.inputs) + (parts_from_a365(client, a.a365, a.param) if a.a365 else [])
    except Exception as e:
        print(f"cannot read parts: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    parts, skipped = merge(parts)
    if not parts:
        print("no parts with an MPN or LCSC number in the inputs", file=sys.stderr)
        return 2

    http = CachedHTTP(a.cache, min_interval=a.rate, ttl_s=a.max_age * 3600, offline=a.offline)
    want = a.providers.split(",")
    provs = [c(http) for c in PROVIDERS if c.name in want]
    current, unconfigured = check_parts(parts, provs)
    state, now = load_state(a.state), _now()
    rows = diff(state, current)

    upd = None
    if a.update_a365:
        upd = a365_updates(rows, client, ws, apply=a.apply, param=a.param)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(f"{a.out}_a365.json").write_text(json.dumps(upd, indent=2), encoding="utf-8")
    meta = {"now": now, "inputs": list(a.inputs) + ([f"a365:{a.a365}"] if a.a365 else []), "state": a.state,
            "prev": state.get("updated"), "skipped": skipped, "a365_mode": "applied" if a.apply else "dry run"}
    counts = write_reports(rows, a.out, meta, provs, unconfigured, http, upd)
    if not a.no_save:
        save_state(next_state(state, current, now), a.state)

    for r in rows:
        if r["change"] != "unchanged":
            print(f"{r['change']:<9} {(r['mpn'] or r['lcsc'])[:26]:<26} {LABEL.get(r['prev_status'], '-'):<13} -> "
                  f"{LABEL[r['status']]:<13} stock {r['prev_stock']} -> {r['stock']}  {r['notes'][:70]}")
    print("  ".join(f"{k} {v}" for k, v in counts.items()) + f"   -> {Path(a.out).with_suffix('.md')} and .csv")
    if unconfigured:
        print(f"not configured: {', '.join(unconfigured)}")
    code = exit_code(rows, a.strict)
    if not any(r["answered"] for r in rows):
        print("no source answered for any part (secrets missing, quota, or network blocked)", file=sys.stderr)
        return code or 3
    if upd and any(u["action"] == "error" for u in upd):
        print(f"some Altium 365 updates failed (see {a.out}_a365.json)", file=sys.stderr)
        return code or 2
    return code


if __name__ == "__main__":
    sys.exit(main())
