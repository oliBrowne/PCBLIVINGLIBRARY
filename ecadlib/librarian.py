"""One command from BOM to library: triage the BOM, fill every missing symbol and footprint, write the Altium
models and the altium365 plan, run the release check, and stop with a review list.

    python3 -m ecadlib.librarian examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints -o out/librarian \
        [--sources kicad,easyeda,datasheet] [--allow PART] [--box] [--refresh [PART ...]] [--refresh-index] [--offline]

Per plan item (one per unique part), parts the triage found in a --lib library are converted from there; for
anything still missing, the sources are tried in --sources order, each filling only what is still missing:
  kicad      the official KiCad libraries on gitlab.com. A name index of kicad-symbols (KiCad 9 <Lib>.kicad_symdir/
             <Name>.kicad_sym, so derived symbols/aliases are names too) and kicad-footprints is built once from the
             GitLab tree API and cached (<cache>/kicad_library_index.json, 30 days). Symbol by MPN: exact name, a name
             with KiCad's `x` wildcards (STM32F103C8Tx), or the MPN minus an ordering suffix (TPS62162DSG for
             TPS62162DSGR); generic connectors by value (Conn_01x04). Footprint: the BOM footprint name, the symbol's
             Footprint property, a footprint named after the MPN, or the shortest name starting with the BOM footprint.
             R/C/L/LED use Device:R/C/C_Polarized/L/LED and <Family>_SMD:<X>_<imperial>_<metric>Metric by package.
  easyeda    LCSC number (BOM, or found by MPN in triage) -> EasyEDA component JSON -> easyeda_convert -> KiCad text
  datasheet  datasheet URL (BOM column, KiCad symbol, EasyEDA/LCSC link) + ANTHROPIC_API_KEY -> ecadlib.datasheet;
             the result is marked `verify` so release_check holds it until a person has looked (or --allow)
Footprints without a KiCad STEP model get a package-shaped STEP from step_gen embedded (--box: extruded box).
Everything is converted through kicad_import into <out>/models/{SchLib,PcbLib} (plus the .kicad_mod in
<out>/models/KiCad for footprint_check). Outputs: <out>/plan.json (each item records `source`, `librarian.tried`
and the release-check result), <out>/review.{md,csv}, <out>/release_check.{md,csv}, <out>/triage.{md,csv}.
Resumable: <out>/librarian_state.json remembers finished parts; a re-run skips them unless --refresh (all, or the
named parts) or their BOM line changed. --allow PART is recorded in plan.json and survives re-runs."""
import argparse, csv, json, os, re, sys, time
from pathlib import Path

import pandas as pd

from . import (altium365 as A365, altium_lib as AL, bom_triage as T, datasheet as D, easyeda_convert as EC,
               footprint_gen as F, kicad_import as K, providers as P, release_check as RC, sexpr)
from .httpcache import CachedHTTP
from .kicad_index import KiCadIndex, METRIC_TO_IMPERIAL, norm_mpn, package_of

TREE = "https://gitlab.com/api/v4/projects/kicad%2Flibraries%2F{repo}/repository/tree?ref=master&recursive=true&per_page=100&page={page}"
SOURCES = ("kicad", "easyeda", "datasheet")
IMP2MET = {v: k for k, v in METRIC_TO_IMPERIAL.items()}
PASSIVE = {"R": ("Device:R", "Resistor_SMD:R_{}_{}Metric"), "C": ("Device:C", "Capacitor_SMD:C_{}_{}Metric"),
           "CP": ("Device:C_Polarized", None), "L": ("Device:L", "Inductor_SMD:L_{}_{}Metric"), "LED": ("Device:LED", "LED_SMD:LED_{}_{}Metric")}
SPECS = Path(F.__file__).with_name("package_specs")


# ---------------------------------------------------------------- official KiCad library name index
class KiCadLibrary:
    def __init__(self, http, cache_file, refresh=False, max_age=30 * 86400):
        self.http, self.error, self.symbols, self.footprints = http, None, {}, {}
        f = Path(cache_file)
        if f.exists() and not refresh and (http.offline or time.time() - f.stat().st_mtime < max_age):
            self._load(f)
        else:
            try:
                self.symbols = self._crawl("kicad-symbols", ".kicad_symdir", ".kicad_sym")
                self.footprints = self._crawl("kicad-footprints", ".pretty", ".kicad_mod")
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(json.dumps({"built": time.strftime("%Y-%m-%d"), "symbols": self.symbols, "footprints": self.footprints}))
            except IOError as e:
                self.error = str(e)
                if f.exists():                 # a stale index beats none
                    self._load(f)
        self._norm, self._wild, self._fpn = {}, [], []
        for name, libs in self.symbols.items():
            self._norm.setdefault(norm_mpn(name), []).extend(f"{l}:{name}" for l in libs)
            if re.search(r"(?<![0-9])x|x(?![0-9])", name) and re.search(r"[A-Z0-9]x", name) and not any(l.startswith("Connector") for l in libs):
                rx = "".join("[A-Z0-9]" if c == "x" else re.escape(c.upper()) for c in re.sub(r"[^A-Za-z0-9]", "", name))
                self._wild.append((re.compile(rx), [f"{l}:{name}" for l in libs]))
        self._fpn = [(norm_mpn(n), f"{libs[0]}:{n}") for n, libs in self.footprints.items()]

    def _load(self, f):
        d = json.loads(f.read_text())
        self.symbols, self.footprints = d["symbols"], d["footprints"]

    def _crawl(self, repo, dsuf, fsuf):
        out = {}
        for page in range(1, 2000):
            r = self.http.get(TREE.format(repo=repo, page=page))
            if not r.ok:
                raise IOError(f"{repo} tree page {page}: {r.error or f'HTTP {r.status}'}")
            items = r.json()
            for it in items:
                lib, _, fn = it.get("path", "").partition("/")
                if it.get("type") == "blob" and lib.endswith(dsuf) and fn.endswith(fsuf) and "/" not in fn:
                    out.setdefault(fn[:-len(fsuf)], []).append(lib[:-len(dsuf)])
            if len(items) < 100:
                return out
        raise IOError(f"{repo}: tree listing did not end")

    @staticmethod
    def _pick(refs):
        refs = sorted(dict.fromkeys(refs))
        return refs[0], (f" (also in {', '.join(r.split(':')[0] for r in refs[1:])})" if len(refs) > 1 else "")

    def find_symbol(self, mpn):
        """MPN -> ('Lib:Name', how) or (None, None)."""
        nm = norm_mpn(mpn)
        if len(nm) < 3:
            return None, None
        if nm in self._norm:
            ref, more = self._pick(self._norm[nm])
            return ref, "exact name" + more
        hits = [r for rx, refs in self._wild if rx.fullmatch(nm) for r in refs]
        if hits:
            ref, more = self._pick(hits)
            return ref, "name with KiCad x wildcards" + more
        raw = [i for i, c in enumerate(str(mpn)) if c.isalnum()]     # positions of the normalised characters
        for k in range(len(nm) - 1, max(5, int(len(nm) * 0.6)) - 1, -1):
            sep = raw[k] > raw[k - 1] + 1                             # a '-', '/', '(' ... right before the suffix
            if nm[:k] in self._norm and (len(nm) - k <= 2 or (sep and len(nm) - k <= 6)):
                ref, more = self._pick(self._norm[nm[:k]])
                return ref, f"name + ordering suffix '{str(mpn)[raw[k]:]}' (check the variant)" + more
        return None, None

    def generic_connector(self, value):
        for n in (value, f"{value}_Odd_Even"):
            libs = [l for l in self.symbols.get(n, []) if l == "Connector_Generic"]
            if libs and re.fullmatch(r"Conn_\d\dx\d\d.*", n):
                return f"{libs[0]}:{n}", f"generic connector symbol by value {value}"
        return None, None

    def footprint(self, ref):
        if not ref:
            return None
        lib, _, name = ref.rpartition(":")
        libs = self.footprints.get(name, [])
        return f"{lib}:{name}" if lib in libs else f"{libs[0]}:{name}" if libs and not lib else None

    def footprint_by_mpn(self, mpn):
        nm = norm_mpn(re.sub(r"\(.*", "", str(mpn)))
        hits = sorted((len(n), ref) for n, ref in self._fpn if len(nm) >= 6 and nm in n)
        return hits[0][1] if hits else None

    def footprint_by_prefix(self, name):
        hits = sorted((len(n), f"{libs[0]}:{n}") for n, libs in self.footprints.items() if name and n.startswith(name + "_"))
        return hits[0][1] if hits else None


# ---------------------------------------------------------------- helpers
def passive_kind(item):
    cat, fp = item["folder"].split("/")[-1], item["footprint"]["ref"] or ""
    if cat == "Resistors":
        return "R"
    if cat == "Capacitors":
        return "CP" if fp.startswith("CP_") else "C"
    if cat == "Inductors":
        return "L"
    if cat == "Diodes" and (fp.startswith("LED_") or re.search(r"\bLED\b", item.get("description") or "", re.I)):
        return "LED"
    return None


def _par(item):
    return {p["name"]: p["value"] for p in item.get("parameters", [])}


def _set_par(item, k, v):
    item["parameters"] = [p for p in item["parameters"] if p["name"] != k] + [{"name": k, "value": v}]
    item["cleared"] = [c for c in item.get("cleared", []) if c != k]


def _line_for(item, lines, rows):
    par = _par(item)
    mpn, val = par.get("Manufacturer Part Number", ""), par.get("Value", "")
    for ln, row in zip(lines, rows):
        if (mpn and ln.get("mpn") == mpn) or (not mpn and not ln.get("mpn") and ln.get("value", "") == val):
            return ln, row
    return {}, {}


class Run:
    def __init__(self, out, http, index, libs, sources, api_key=None, kicad_cache=None, refresh_index=False, gen_step=True):
        self.out, self.http, self.index, self.libs, self.sources = Path(out), http, index, libs, sources
        self.models = self.out / "models"
        for d in ("PcbLib", "SchLib", "KiCad"):
            (self.models / d).mkdir(parents=True, exist_ok=True)
        self.api_key, self.written, self.gen_step = api_key, {}, gen_step
        self._kicad, self._kicad_args = None, (kicad_cache, refresh_index)

    def kicad(self):
        if self._kicad is None:
            self._kicad = KiCadLibrary(self.http, *self._kicad_args)
        return self._kicad

    def rel(self, p):
        return os.path.relpath(p, self.out)

    # ---- writers: everything goes through kicad_import's converters
    def put_fp(self, st, text, name, source, http=None, height=None, height_src="", spec=None):
        stem = AL.file_stem(name)
        pcb = self.models / "PcbLib" / f"{stem}.PcbLib"
        if str(pcb) in self.written:
            st["footprint"] = dict(self.written[str(pcb)], notes=[f"shared with {self.written[str(pcb)]['by']}"])
            return
        notes = []
        fp, t = K.convert_footprint(text, name, source.split(":", 1)[-1], notes)
        try:
            K.add_body(fp, t, http, height, False, notes, gen_step=self.gen_step, spec=spec,      # a part-specific
                       height_src=(height_src or "caller") if height else None)                      # height beats the table
        except ValueError as e:
            notes.append(f"no 3D body: {e}")
        kmod = self.models / "KiCad" / f"{stem}.kicad_mod"
        kmod.write_text(text)
        AL.write_pcblib(pcb, [fp])
        st["footprint"] = {"source": source, "file": self.rel(pcb), "kicad_mod": self.rel(kmod), "notes": notes, "by": st["name"]}
        self.written[str(pcb)] = st["footprint"]

    def put_sym(self, st, sym, source, notes=()):
        path = self.models / "SchLib" / f"{AL.file_stem(sym['name'])}.SchLib"
        if str(path) in self.written:
            st["symbol"] = dict(self.written[str(path)], notes=[f"shared with {self.written[str(path)]['by']}"])
            return
        AL.write_schlib(path, [sym])
        st["symbol"] = {"source": source, "file": self.rel(path), "notes": list(notes), "by": st["name"]}
        self.written[str(path)] = st["symbol"]

    def put_kicad_sym(self, st, chain, name, fp_name, source, extra=()):
        notes = []
        sym = K.convert_symbol(chain, name, fp_name, list(extra), None, notes)
        self.put_sym(st, sym, source, notes)

    # ---- per part
    def resolve(self, item, ln, row):
        par = _par(item)
        mpn = par.get("Manufacturer Part Number", "")
        st = {"name": item["name"], "symbol": None, "footprint": None, "tried": [], "review": [], "verify": [],
              "found": {}, "hint_fp": ""}
        if (row.get("status") or "").startswith("dnp"):
            st["tried"].append("DNP line: not fetched")
            return st
        kind, fp_name, sym_name = passive_kind(item), item["footprint"]["ref"], item["symbol"]["ref"]
        try:
            self.local(st, row, kind, fp_name, sym_name)
        except Exception as e:
            st["tried"].append(f"local library: failed ({type(e).__name__}: {e})")
        for s in self.sources:
            if st["symbol"] and st["footprint"]:
                break
            try:
                getattr(self, f"src_{s}")(st, item, ln, row, kind, mpn, fp_name, sym_name)
            except Exception as e:            # one bad source or part must not stop the run
                st["tried"].append(f"{s}: failed ({type(e).__name__}: {e})")
        return st

    def local(self, st, row, kind, fp_name, sym_name):
        ls, lf = (row.get("local_symbol") or "").split(" ")[0], (row.get("local_footprint") or "").split(" ")[0]
        if ls and not kind:
            lib, name = ls.split(":", 1)
            f = next((p for r in self.libs for p in Path(r).rglob(f"{lib}.kicad_sym")), None)
            if f:
                chain, _ = K.load_symbol(f"{f}:{name}", None)
                st["hint_fp"] = K._props(chain).get("Footprint", "")
                self.put_kicad_sym(st, chain, sym_name, fp_name, f"local:{ls}")
        if lf and lf in self.index.footprints:
            text = Path(self.index.footprints[lf]["path"]).read_text()
            spec = SPECS / f"{lf.split(':')[-1]}.json"
            hm = json.loads(spec.read_text()).get("height_max") if spec.exists() else None
            self.put_fp(st, text, fp_name, f"local:{lf}", None, F.dim(hm)[2] if hm else None, f"package spec {spec.name}")
        if ls or lf:
            st["tried"].append(f"local library: symbol {'from ' + ls if st['symbol'] else 'not used' if ls else 'none'}, "
                               f"footprint {'from ' + lf if st['footprint'] else 'none'}")

    def src_kicad(self, st, item, ln, row, kind, mpn, fp_name, sym_name):
        lib = self.kicad()
        if not lib.symbols:
            st["tried"].append(f"kicad: library index unavailable ({lib.error})")
            return
        sym_ref = how = fp_ref = fp_how = None
        if kind:
            sym_ref, how = PASSIVE[kind][0], "generic passive symbol"
            pkg = package_of(fp_name, item.get("description"))
            if PASSIVE[kind][1] and pkg in IMP2MET:
                fp_ref, fp_how = lib.footprint(PASSIVE[kind][1].format(pkg, IMP2MET[pkg])), f"by package {pkg}"
        elif not st["symbol"]:
            sym_ref, how = lib.find_symbol(mpn)
            if not sym_ref:
                sym_ref, how = lib.generic_connector(ln.get("value", ""))
        chain = None
        if sym_ref and not st["symbol"]:
            chain, _ = K.load_symbol(sym_ref, self.http)
            props = K._props(chain)
            st["hint_fp"] = st["hint_fp"] or props.get("Footprint", "")
            if str(props.get("Datasheet", "")).startswith("http"):
                st["found"].setdefault("datasheet", (props["Datasheet"], f"KiCad symbol {sym_ref}"))
            if props.get("Description"):
                st["found"].setdefault("description", (props["Description"], f"KiCad symbol {sym_ref}"))
            shared = kind in ("R", "C", "CP", "L")            # one generic symbol for every value: no MPN, no footprint link
            extra = [] if shared or not mpn else [("MPN", mpn)]
            self.put_kicad_sym(st, chain, sym_name, None if shared else fp_name, f"kicad:{sym_ref}", extra)
            st["tried"].append(f"kicad: symbol {sym_ref} ({how})")
            if "check" in how:
                st["review"].append(f"symbol {sym_ref} matched by {how}")
        elif not st["symbol"]:
            st["tried"].append(f"kicad: no symbol named like {mpn or '(no MPN)'}")
        if st["footprint"]:
            return
        bom_fp = ln.get("footprint", "")
        for cand, why in ((fp_ref, fp_how), (lib.footprint(st["hint_fp"]), "symbol's Footprint property"), (lib.footprint(bom_fp), "BOM footprint name"),
                          (None if kind else lib.footprint_by_mpn(mpn), "footprint named after the MPN"),
                          (lib.footprint_by_prefix(bom_fp.split(":")[-1]), f"shortest KiCad name starting with '{bom_fp}'")):
            if cand:
                text, _ = K.load_footprint(cand, self.http)
                self.put_fp(st, text, fp_name, f"kicad:{cand}", self.http)
                st["tried"].append(f"kicad: footprint {cand} ({why})")
                if why.startswith(("shortest", "footprint named")):
                    st["review"].append(f"footprint {cand} chosen by {why}: compare with the datasheet land pattern")
                return
        st["tried"].append(f"kicad: no footprint for '{bom_fp or fp_name}'")

    def src_easyeda(self, st, item, ln, row, kind, mpn, fp_name, sym_name):
        m = re.match(r"C\d+", ln.get("lcsc") or row.get("lcsc") or "")
        if not m:
            st["tried"].append("easyeda: no LCSC number")
            return
        lcsc = m.group(0)
        r = self.http.get(P.EasyEDA.URL.format(lcsc), headers=P.EasyEDA.HEADERS)
        if not r.ok:
            st["tried"].append(f"easyeda {lcsc}: {r.error or f'HTTP {r.status}'}")
            return
        data = r.json()
        res = P.parse_easyeda(data, P.SourceResult("easyeda", lcsc=lcsc))
        if not res.found:
            st["tried"].append(f"easyeda {lcsc}: no component")
            return
        if mpn and res.mpn and norm_mpn(res.mpn) != norm_mpn(mpn):
            st["tried"].append(f"easyeda {lcsc}: is {res.mpn}, BOM says {mpn}; not used")
            st["review"].append(f"LCSC {lcsc} is {res.mpn}, not {mpn}: fix the BOM's LCSC number")
            return
        result = data["result"]
        url = (result.get("lcsc") or {}).get("url") or ""
        if url.startswith("http"):
            st["found"].setdefault("datasheet", (url, f"EasyEDA {lcsc} (lcsc.url)"))
        if res.manufacturer:
            st["found"].setdefault("manufacturer", (res.manufacturer, f"EasyEDA {lcsc}"))
        got = []
        if not st["footprint"]:
            pkg = (result.get("packageDetail") or {})
            notes = []
            text = EC.footprint_to_kicad(pkg.get("dataStr") or {}, pkg.get("title") or fp_name, notes)
            self.put_fp(st, text, fp_name, f"easyeda:{lcsc}", None)
            st["footprint"]["notes"] = notes + st["footprint"]["notes"]
            got.append(f"footprint {pkg.get('title', '')}")
        if not st["symbol"]:
            ds = result.get("dataStr") or {}
            if not ds.get("shape") and result.get("subparts"):
                st["tried"].append(f"easyeda {lcsc}: multi-unit symbol (subparts) not converted")
            else:
                notes = []
                text = EC.symbol_to_kicad(ds, sym_name, fp_name, {"MPN": mpn, "LCSC": lcsc, "Manufacturer": res.manufacturer}, notes)
                chain = [K._find_symbol(sexpr.parse(text), sym_name)]
                self.put_kicad_sym(st, chain, sym_name, fp_name, f"easyeda:{lcsc}", [("MPN", mpn)] if mpn else [])
                st["symbol"]["notes"] = notes + st["symbol"]["notes"]
                got.append("symbol")
        st["tried"].append(f"easyeda {lcsc}: " + (", ".join(got) or "nothing needed"))
        st["review"].append(f"EasyEDA {lcsc} conversion: check pads and pins against the datasheet (native converter, format assumed)")

    def src_datasheet(self, st, item, ln, row, kind, mpn, fp_name, sym_name):
        par = _par(item)
        urls = [(ln.get("datasheet", ""), "BOM"), st["found"].get("datasheet", ("", "")), (par.get("ComponentLink2URL", ""), "LCSC datasheet link")]
        url, where = next(((u, w) for u, w in urls if str(u).startswith("http")), (None, None))
        if not url:
            st["tried"].append("datasheet: no datasheet URL known")
            return
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            st["tried"].append(f"needs datasheet reader (no API key): {url} ({where})")
            return
        source, offset, hint = D.load_source(url)
        headers, body = D.build_request(source, mpn or item["name"], fp_name, hint)
        ext, msg = D.call(headers, body, key)
        res = D.process(ext, mpn or item["name"], url, D.MODEL, offset, msg)
        D.write(res, self.out / "datasheet")
        got = []
        if not st["footprint"]:
            text, _ = F.build(res["spec"])
            h = F.dim(res["spec"]["height_max"])[2] if res["spec"].get("height_max") else None
            self.put_fp(st, text, fp_name, f"datasheet:{url}", None, h, "datasheet height_max", res["spec"])
            got.append("footprint")
        if not st["symbol"]:
            self.put_sym(st, AL.symbol_from_json(dict(res["symbol"], name=sym_name, footprint=fp_name)), f"datasheet:{url}")
            got.append("symbol")
        st["tried"].append(f"datasheet reader ({where}): {', '.join(got)}; {len(res['needs_review'])} review items")
        st["verify"].append(f"datasheet reader output ({', '.join(got)}): compare with {url}; see datasheet/*.review.md"
                            + (f" ({len(res['needs_review'])} items flagged)" if res["needs_review"] else ""))


def _fingerprint(item, ln, row):
    return json.dumps([item["symbol"]["ref"], item["footprint"]["ref"], ln.get("mpn"), ln.get("lcsc"), ln.get("value"),
                       ln.get("footprint"), ln.get("datasheet"), row.get("local_symbol"), row.get("local_footprint")])


def _done(st, out):
    return all(st.get(k) and (Path(out) / st[k]["file"]).exists() for k in ("symbol", "footprint"))


def apply_state(item, st, ln):
    """Copy a part's state into its plan item: model files, sources, filled-in fields, what was tried."""
    for k in ("symbol", "footprint"):
        if st.get(k):
            item[k]["file"] = st[k]["file"]
            if k == "footprint":
                item[k]["kicad_mod"] = st[k].get("kicad_mod")
    item["source"] = {k: (st.get(k) or {}).get("source") for k in ("symbol", "footprint")}
    par, fields = _par(item), {}
    if ln.get("datasheet", "").startswith("http"):
        _set_par(item, "ComponentLink2Description", "Datasheet"); _set_par(item, "ComponentLink2URL", ln["datasheet"]); fields["ComponentLink2URL"] = "BOM"
    elif par.get("ComponentLink2URL"):
        fields["ComponentLink2URL"] = "LCSC datasheet URL pattern (not fetched)"
    elif "datasheet" in st.get("found", {}):
        u, w = st["found"]["datasheet"]
        _set_par(item, "ComponentLink2Description", "Datasheet"); _set_par(item, "ComponentLink2URL", u); fields["ComponentLink2URL"] = w
    if not par.get("Manufacturer") and "manufacturer" in st.get("found", {}):
        v, w = st["found"]["manufacturer"]
        _set_par(item, "Manufacturer", v); fields["Manufacturer"] = w
    if not str(item.get("description") or "").strip() and "description" in st.get("found", {}):
        item["description"], fields["Description"] = st["found"]["description"]
    item["field_sources"] = fields
    item["verify"] = list(st.get("verify", []))
    item["librarian"] = {"tried": st.get("tried", []), "review": st.get("review", []),
                         "notes": [n for k in ("symbol", "footprint") for n in (st.get(k) or {}).get("notes", [])]}


def review_rows(items):
    rows = []
    for it in items:
        why = [f"no {k} found" for k in ("symbol", "footprint") if not it[k].get("file")]
        if why:
            why += it["librarian"]["tried"]
        why += [h for h in it.get("hold_reasons", []) if not re.match(r"files: no (symbol|footprint) model", h)] + it["librarian"]["review"]
        rows.append({"part": it["name"], "status": it["status"], "symbol_from": it["source"]["symbol"] or "",
                     "footprint_from": it["source"]["footprint"] or "", "needs_human": "yes" if why else "",
                     "why": "; ".join(dict.fromkeys(why)), "tried": "; ".join(it["librarian"]["tried"])})
    return rows


def write_review(rows, out, bom):
    out = Path(out)
    with open(out / "review.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["part"])
        w.writeheader(); w.writerows(rows)
    src = lambda s: (s or "-").split(":")[0]
    counts = {}
    for r in rows:
        counts[(src(r["symbol_from"]), src(r["footprint_from"]))] = counts.get((src(r["symbol_from"]), src(r["footprint_from"])), 0) + 1
    md = [f"# Librarian review - `{bom}`", "",
          f"{len(rows)} parts: {sum(r['status'] == 'ready' for r in rows)} ready for Altium 365, "
          f"{sum(r['status'] == 'held' for r in rows)} held, {sum(bool(r['needs_human']) for r in rows)} need a person.", "",
          "Sources (symbol / footprint): " + ", ".join(f"{a} / {b}: {n}" for (a, b), n in sorted(counts.items())), "",
          "| Part | Status | Symbol from | Footprint from |", "|---|---|---|---|"]
    md += [f"| {r['part']} | {r['status']} | {r['symbol_from'] or '-'} | {r['footprint_from'] or '-'} |" for r in rows]
    md += ["", "## Needs a person", ""]
    md += [f"- **{r['part']}** ({r['status']}): " + "; ".join(r["why"].split("; ")) for r in rows if r["needs_human"]] or ["Nothing."]
    md += ["", "## What was tried", ""] + [f"- **{r['part']}**: {r['tried'] or '-'}" for r in rows]
    (out / "review.md").write_text("\n".join(md) + "\n")


def run(bom, libs=(), out="out/librarian", sources=SOURCES, refresh=None, refresh_index=False, allow=(), offline=False,
        cache="out/http_cache", rate=0.5, providers="jlcpcb_search,easyeda", http=None, api_key=None, log=print, gen_step=True):
    """Whole pipeline; returns the plan items. refresh: None = resume, [] = redo all, [names] = redo those."""
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    http = http or CachedHTTP(cache, min_interval=rate, offline=offline, timeout=60)
    bad = set(sources) - set(SOURCES)
    if bad:
        raise ValueError(f"unknown sources {sorted(bad)}; use {','.join(SOURCES)}")
    lines, mapping = T.read_bom(bom)
    index = KiCadIndex(libs)
    provs = [c(http) for c in P.ALL if c.name in providers.split(",")]
    rows = T.triage(lines, index, provs, http)
    T.write_reports(rows, mapping, provs, http, out / "triage", bom)
    items = A365.plan(lines, pd.DataFrame(rows, dtype=str).fillna("") if rows else None)
    state_f, plan_f = out / "librarian_state.json", out / "plan.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    old = {i["name"]: i for i in json.loads(plan_f.read_text())} if plan_f.exists() else {}
    r = Run(out, http, index, libs, list(sources), api_key, Path(cache) / "kicad_library_index.json", refresh_index, gen_step)
    todo = []
    for it in items:
        ln, row = _line_for(it, lines, rows)
        if not it["footprint"]["ref"]:
            it["footprint"]["ref"] = AL.lib_name(it["name"])
        fpr, st = _fingerprint(it, ln, row), state.get(it["name"])
        redo = refresh is not None and (not refresh or it["name"] in refresh)
        if st and st.get("key") == fpr and _done(st, out) and not redo:
            st["skipped"] = True
            for k in ("symbol", "footprint"):
                r.written.setdefault(str(out / st[k]["file"]), st[k])
        else:
            todo.append((it, ln, row, fpr))
    for it, ln, row, fpr in todo:
        st = r.resolve(it, ln, row)
        st["key"], st["skipped"] = fpr, False
        state[it["name"]] = st
    for it in items:
        apply_state(it, state[it["name"]], _line_for(it, lines, rows)[0])
        if old.get(it["name"], {}).get("allow"):
            it["allow"] = old[it["name"]]["allow"]
        log(f"  {'skip ' if state[it['name']].get('skipped') else 'done ' if _done(state[it['name']], out) else 'open '}"
            f"{it['name']:<28} sym={it['source']['symbol'] or '-'}  fp={it['source']['footprint'] or '-'}")
    state = {k: v for k, v in state.items() if k in {i["name"] for i in items}}
    for n in set(allow) - set(state):
        log(f"  --allow {n}: not a part in this plan (names are the plan's Part Numbers)")
    state_f.write_text(json.dumps(state, indent=2))
    checks = RC.apply(items, out, allow)
    plan_f.write_text(json.dumps(items, indent=2))
    RC.write_report(checks, out / "release_check", plan_f)
    write_review(review_rows(items), out, bom)
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description="BOM -> library: triage, fill missing symbols/footprints, Altium models, plan, release check")
    ap.add_argument("bom")
    ap.add_argument("--lib", action="append", default=[], help="local KiCad library dir (repeatable)")
    ap.add_argument("-o", "--out", default="out/librarian")
    ap.add_argument("--sources", default=",".join(SOURCES), help="order of sources to try (default %(default)s)")
    ap.add_argument("--refresh", nargs="*", metavar="PART", help="redo finished parts: all, or the named ones")
    ap.add_argument("--refresh-index", action="store_true", help="rebuild the KiCad library name index")
    ap.add_argument("--allow", action="append", default=[], metavar="PART", help="release PART despite its release-check failures")
    ap.add_argument("--providers", default="jlcpcb_search,easyeda", help="triage providers (default %(default)s)")
    ap.add_argument("--offline", action="store_true", help="use the HTTP cache only")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--rate", type=float, default=0.5, help="min seconds between requests per host")
    ap.add_argument("--box", action="store_true", help="extruded 3D box instead of a generated STEP when there is no KiCad model")
    a = ap.parse_args(argv)
    items = run(a.bom, a.lib, a.out, [s for s in a.sources.split(",") if s], a.refresh, a.refresh_index, a.allow,
                a.offline, a.cache, a.rate, a.providers, gen_step=not a.box)
    held = sum(i["status"] != "ready" for i in items)
    print(f"{len(items)} parts: {len(items) - held} ready, {held} held -> {Path(a.out) / 'plan.json'}; review {Path(a.out) / 'review.md'}")
    return 2 if held else 0


if __name__ == "__main__":
    sys.exit(main())
