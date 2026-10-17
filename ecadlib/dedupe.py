"""Find duplicate parts across a plan and the libraries you already have; optionally merge plan references.

    python3 -m ecadlib.dedupe out/librarian/plan.json [--lib out/altium_models --lib out/altium/ecadlib_sqlite.DbLib ...]
        [--a365 out/a365_state.json] [--config dedupe.json] [--keep NAME] [--merge [--kinds mpn,footprint,symbol]]
        [--bom BOM.csv] [-o out/librarian/dedupe]
    (or: ecadlib dedupe PLAN ...)

What it compares (each plan item, plus everything under --lib: .SchLib, .PcbLib, .kicad_mod, .DbLib with its SQLite
or Excel file, .sqlite/.db/.xlsx tables, and the Altium 365 component list from a check-a365 --state file, which is
read from <plan dir>/a365_state.json when present):
  mpn        same normalised MPN under different part names. Normalising upper-cases, strips packaging suffixes
             (/TR, -TR, T&R, -REEL7, #TRPBF, (LF)(SN), NXP ',115', ...) and then all punctuation. Suffixes without a
             separator (TI's ...RGTR vs ...RGTT) are NOT stripped by default; add patterns in the config.
  footprint  identical footprint geometry under different footprint names: pads (number, position, effective size,
             drilled or not) to 0.01 mm and the courtyard box to 0.05 mm; .PcbLib and .kicad_mod hash alike
             (KiCad's y axis is flipped). Same name with different geometry is reported as a conflict.
  symbol     identical symbol pinout under different symbol names: designator prefix plus (pin number, pin name)
             for every pin. Symbols with fewer than symbol_min_pins (default 3) pins are not compared: every
             resistor and capacitor would match.
Each group gets a recommended keeper: --keep wins, then an Altium 365 component, then an existing library entry,
then the one most plan items use, a plan item that is ready, a part from the official KiCad library, the shortest
name. --merge rewrites the plan (the first copy is saved as plan.json.pre-dedupe, later ones timestamped): a duplicate plan item gets
`duplicate_of` and status `duplicate` (altium365 push skips it, release_check keeps it), a duplicate footprint or
symbol reference is pointed at the keeper's ref and file (`merged_from` keeps the old one). Library files, DbLib rows
and workspace components are never changed or deleted; they are only reported. Approvals of a rewritten item go
stale, as for any other change.
Outputs: <out>.md, <out>.csv (one row per group member), <out>_map.csv (kind, from, to: the libref / footprint /
part-number mapping for BOMs), and with --bom a copy of the BOM with its LibRef and Footprint columns remapped.
Config (dedupe.json next to the plan, or --config): {"strip_suffixes": [regex, ...], "default_suffixes": true,
"symbol_min_pins": 3, "keep": [names], "ignore": [names]}. Exit codes: 0 no duplicates, 1 duplicates found, 2 error."""
import argparse, configparser, contextlib, csv, datetime, hashlib, json, math, os, re, shutil, sqlite3, sys
from pathlib import Path, PureWindowsPath

from . import altium_lib as AL, sexpr
from . import sheetsafe as SS
from .kicad_index import norm_mpn
from .fsutil import write_text_atomic

DEFAULT_SUFFIXES = [
    r"#(TR)?PBF$", r"#TR$",                                   # Analog Devices / Linear Technology
    r"(?<=[0-9A-Z])TRPBF$", r"(?<=\d[A-Z])PBF$|(?<=\d)PBF$",  # Infineon/IR lead-free (+ tape and reel)
    r"(\((LF|SN|PBF|PB-?FREE)\))+$",                         # JST (LF)(SN)
    r"[-/ _](TR|T/R|T&R|TR7|TR13|R7|R13|TAPE|REEL|CT|CUT ?TAPE|BULK|TRAY|TUBE)$",
    r"-?REEL(7|13)?$", r"(T&R|T/R)$", r",\d{3}$",           # ADI -REEL7, NXP packing code ,115
]
CONFIG = {"strip_suffixes": [], "default_suffixes": True, "symbol_min_pins": 3, "keep": [], "ignore": []}
MPN_COLS = ("Manufacturer Part Number", "MPN", "Mfr Part Number")


def load_config(path=None, base=None):
    f = Path(path) if path else (Path(base) / "dedupe.json" if base else None)
    raw = json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else {}
    bad = set(raw) - set(CONFIG)
    if bad:
        raise ValueError(f"{f}: unknown keys {sorted(bad)}; known: {sorted(CONFIG)}")
    cfg = {**CONFIG, **raw}
    cfg["patterns"] = [re.compile(p, re.I) for p in (DEFAULT_SUFFIXES if cfg["default_suffixes"] else []) + list(cfg["strip_suffixes"])]
    return cfg


def mpn_key(s, cfg=None):
    """'LM1117MPX-3.3/NOPB/TR' -> 'LM1117MPX33NOPB'; '' for an empty MPN or a placeholder ('N/A', 'TBD'): parts whose
    MPN cell says N/A are not one part."""
    from .bom_triage import placeholder_mpn
    if placeholder_mpn(s):
        return ""
    pats = (cfg or load_config())["patterns"]
    s = str(s or "").strip().upper()
    for _ in range(4):                                       # suffixes can stack: -TR then (LF)
        before = s
        for p in pats:
            t = p.sub("", s).strip()
            if len(norm_mpn(t)) >= 3:                        # never strip a part number down to nothing
                s = t
        if s == before:
            break
    return norm_mpn(s)


# ---------------------------------------------------------------- geometry and pinout fingerprints
def _q(v, step=0.01):
    """Quantise to step, robust to the 1e-5 mm noise of mil/internal-unit conversion."""
    return math.floor(v / step + 0.501) * step


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def _pad_tuple(num, x, y, w, h, rot, hole):
    if round(rot) % 180 == 90:
        w, h = h, w
    return (str(num), round(_q(x), 2), round(_q(y), 2), round(_q(w), 2), round(_q(h), 2), round(rot % 90, 1), bool(hole))


def _box(xs, ys):
    return [round(_q(v, 0.05), 2) for v in (min(xs), min(ys), max(xs), max(ys))] if xs else None


def fp_geometry_pcblib(fp, courtyard=AL.MECH15):
    """courtyard: the layer id the library rules draw courtyards on; Mechanical 15 (ecadlib's own) when nothing is there."""
    pads = [_pad_tuple(r["designator"], AL.to_mm(r["x"]), AL.to_mm(r["y"]), AL.to_mm(r["w"]), AL.to_mm(r["h"]), r["rot"], r["hole"] > 0)
            for r in fp["records"] if r["type"] == "pad"]
    xs, ys = [], []
    shapes = ("track", "arc", "region", "fill")
    if not any(r["layer"] == courtyard for r in fp["records"] if r["type"] in shapes):
        courtyard = AL.MECH15
    for r in fp["records"]:
        if r["layer"] != courtyard:
            continue
        if r["type"] == "track":
            xs += [AL.to_mm(r["x1"]), AL.to_mm(r["x2"])]; ys += [AL.to_mm(r["y1"]), AL.to_mm(r["y2"])]
        elif r["type"] == "arc":
            xs += [AL.to_mm(r["cx"] - r["r"]), AL.to_mm(r["cx"] + r["r"])]; ys += [AL.to_mm(r["cy"] - r["r"]), AL.to_mm(r["cy"] + r["r"])]
        elif r["type"] in ("region", "fill"):
            pts = r.get("pts") or [(r["x1"], r["y1"]), (r["x2"], r["y2"])]
            xs += [AL.to_mm(p[0]) for p in pts]; ys += [AL.to_mm(p[1]) for p in pts]
    return {"pads": sorted(pads), "courtyard": _box(xs, ys)}


def fp_geometry_kicad(text):
    t = sexpr.parse(text)
    pads = []
    for p in sexpr.find(t, "pad"):
        layers = (sexpr.first(p, "layers") or ["layers"])[1:]
        if p[2] != "np_thru_hole" and not any(str(x).endswith(".Cu") for x in layers):
            continue                                       # paste-only apertures: no copper, not in the PcbLib
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        rot = float(at[3]) if len(at) > 3 else 0.0
        w, h = float(size[1]), float(size[2] if len(size) > 2 else size[1])
        pads.append(_pad_tuple(p[1], float(at[1]), -float(at[2]), w, h, rot, p[2] in ("thru_hole", "np_thru_hole")))
    cy = sexpr.courtyard_bbox(t)
    return {"pads": sorted(pads), "courtyard": _box([cy[0], cy[2]], [-cy[3], -cy[1]]) if cy else None}


def fp_hash(geom):
    return _digest([list(p) for p in geom["pads"]] + [geom["courtyard"]]) if geom and geom["pads"] else None


def _pin_name(s):
    return re.sub(r"\\", "", str(s or "")).strip().upper().lstrip("~") or ""


def sym_pinout(sym):
    """(designator prefix, sorted (number, name)) of an altium_lib read_schlib entry."""
    pins = sorted({(r["num"], _pin_name(r.get("name"))) for r in sym["records"] if r.get("RECORD") == "2"})
    des = next((r.get("TEXT", "") for r in sym["records"] if r.get("RECORD") == "34"), "")
    return re.match(r"[A-Za-z]*", des).group(0).upper(), pins


def sym_hash(pinout, min_pins=3):
    prefix, pins = pinout
    return _digest([prefix, [list(p) for p in pins]]) if len(pins) >= min_pins else None


# ---------------------------------------------------------------- collecting entries
class Collector:
    """Every part / footprint / symbol we know about, from the plan and the libraries."""

    def __init__(self, cfg, log=print):
        self.cfg, self.log = cfg, log
        self.parts = []          # {name, mpn, key, origin, where, plan_index, status, symbol, footprint}
        self.footprints = []     # {ref, file, origin, hash, used_by}
        self.symbols = []        # {ref, file, origin, hash, pins, used_by}
        self.errors = []
        self._seen_files = set()
        self._cache = {}

    def _read(self, f, reader):
        key = (str(Path(f).resolve()), reader.__name__)
        if key not in self._cache:
            try:
                self._cache[key] = reader(f)
            except Exception as e:                 # unreadable library file: report, keep going
                self._cache[key] = None
                self.errors.append(f"{f}: {type(e).__name__}: {e}")
        return self._cache[key]

    def _fp_from_file(self, f, ref=None):
        """[(ref, hash)] for a .PcbLib (one per footprint) or .kicad_mod."""
        f = Path(f)
        if f.suffix.lower() == ".kicad_mod":
            g = self._read(f, lambda p: fp_geometry_kicad(Path(p).read_text(encoding="utf-8")))
            return [(ref or f.stem, fp_hash(g))] if g else []
        fps = self._read(f, AL.read_pcblib) or []
        return [(fp["name"], fp_hash(fp_geometry_pcblib(fp, self.cfg.get("courtyard", AL.MECH15)))) for fp in fps if not ref or fp["name"] == ref or len(fps) == 1]

    def _sym_from_file(self, f, ref=None):
        r = self._read(Path(f), AL.read_schlib)
        syms = r[1] if r else []
        out = []
        for s in syms:
            if ref and s["name"] != ref and len(syms) > 1:
                continue
            po = sym_pinout(s)
            out.append((s["name"], sym_hash(po, self.cfg["symbol_min_pins"]), len(po[1])))
        return out

    def add_plan(self, items, base):
        from .a365_sync import is_a365
        base = Path(base)
        for i, it in enumerate(items):
            par = {p["name"]: p["value"] for p in it.get("parameters") or []}
            mpn = next((par[c] for c in MPN_COLS if par.get(c)), it["name"])
            ws = is_a365(it)                           # pulled from Altium 365 (ecadlib a365 pull): counts as library
            self.parts.append({"name": it["name"], "mpn": mpn, "key": mpn_key(mpn, self.cfg), "origin": "a365" if ws else "plan",
                               "where": "plan (Altium 365)" if ws else "plan",
                               "plan_index": i, "status": it.get("status", ""), "symbol": (it.get("symbol") or {}).get("ref") or "",
                               "footprint": (it.get("footprint") or {}).get("ref") or "", "source": it.get("source") or {}})
            for kind in ("footprint", "symbol"):
                m = it.get(kind) or {}
                f = m.get("file") and (Path(m["file"]) if Path(m["file"]).is_absolute() else base / m["file"])
                if kind == "footprint" and not (f and f.exists()) and m.get("kicad_mod"):
                    f = base / m["kicad_mod"]
                if not (f and f.exists()):
                    continue
                self._seen_files.add(str(f.resolve()))
                got = self._fp_from_file(f, m.get("ref")) if kind == "footprint" else self._sym_from_file(f, m.get("ref"))
                for g in got[:1]:
                    self._add_model(kind, m.get("ref") or g[0], f, "library" if ws else "plan", g[1], it["name"], it, *(g[2:]))

    def _add_model(self, kind, ref, f, origin, h, used_by=None, item=None, pins=None):
        lst = self.footprints if kind == "footprint" else self.symbols
        src = ((item or {}).get("source") or {}).get(kind) or ""
        e = next((x for x in lst if x["ref"] == ref and x["hash"] == h and x["origin"] == origin and str(x["file"]) == str(f)), None)
        if not e:
            e = {"ref": ref, "file": str(f), "origin": origin, "hash": h, "used_by": [], "source": src,
                 "kicad_mod": ((item or {}).get("footprint") or {}).get("kicad_mod") if kind == "footprint" else None}
            if pins is not None:
                e["pins"] = pins
            lst.append(e)
        if used_by:
            e["used_by"].append(used_by)

    def add_lib(self, path):
        p = Path(path)
        files = sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p]
        for f in files:
            suf = f.suffix.lower()
            if str(f.resolve()) in self._seen_files:
                continue
            if suf in (".pcblib", ".kicad_mod"):
                for ref, h in self._fp_from_file(f):
                    self._add_model("footprint", ref, f, "library", h)
            elif suf == ".schlib":
                for ref, h, n in self._sym_from_file(f):
                    self._add_model("symbol", ref, f, "library", h, pins=n)
            elif suf == ".dblib":
                self.add_dblib(f)
            elif suf in (".sqlite", ".db", ".xlsx") and str(f.resolve()) not in self._seen_files:
                self.add_table_file(f)
            else:
                continue
            self._seen_files.add(str(f.resolve()))

    def add_dblib(self, f):
        """A .DbLib points at its database with a Windows path; look for that file name next to the .DbLib."""
        text = Path(f).read_text(errors="replace", encoding="utf-8")
        m = re.search(r"(?:Data Source|Database|DBQ)=([^;\r\n]+)", text, re.I)
        if not m:
            self.errors.append(f"{f}: no database in ConnectionString"); return
        name = PureWindowsPath(m.group(1).strip().strip('"')).name
        db = next((c for c in (Path(m.group(1).strip()), Path(f).parent / name) if c.exists()), None)
        if not db:
            self.errors.append(f"{f}: database {name} not found next to it"); return
        if str(db.resolve()) not in self._seen_files:
            self.add_table_file(db, via=f)
            self._seen_files.add(str(db.resolve()))

    def add_table_file(self, f, via=None):
        tables = {}
        try:
            if Path(f).suffix.lower() == ".xlsx":
                import pandas as pd
                tables = {k: v.fillna("").astype(str).to_dict("records") for k, v in pd.read_excel(f, sheet_name=None, dtype=str).items()}
            else:
                from urllib.parse import quote       # a folder named "Lib #2" must not end the URI path (read nothing)
                with contextlib.closing(sqlite3.connect(f"file:{quote(Path(f).resolve().as_posix(), safe='/:')}?mode=ro", uri=True)) as con:
                    con.row_factory = sqlite3.Row
                    for (t,) in con.execute("select name from sqlite_master where type='table'"):
                        tables[t] = [dict(r) for r in con.execute(f'select * from "{t}"')]
        except Exception as e:
            self.errors.append(f"{f}: {type(e).__name__}: {e}"); return
        for t, rows in tables.items():
            for r in rows:
                pn = str(r.get("Part Number") or "").strip()
                if not pn:
                    continue
                mpn = next((str(r[c]) for c in MPN_COLS if str(r.get(c) or "").strip()), pn)
                self.parts.append({"name": pn, "mpn": mpn, "key": mpn_key(mpn, self.cfg), "origin": "dblib",
                                   "where": f"{via or f} [{t}]", "status": "", "symbol": str(r.get("Library Ref") or ""),
                                   "footprint": str(r.get("Footprint Ref") or ""), "source": {}})

    def add_a365(self, f):
        """check-a365 / a365 pull --state ({name: snapshot}, a365 pull's with `parameters`) or a list of workspace
        component nodes (with parameters)."""
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        nodes = data if isinstance(data, list) else [{"name": k, **(v if isinstance(v, dict) else {})} for k, v in data.items()]
        for n in nodes:
            par = n["parameters"] if isinstance(n.get("parameters"), dict) else \
                {p["name"]: p["value"] for p in ((n.get("details") or {}).get("parameters") or [])}
            mpn = next((par[c] for c in MPN_COLS if par.get(c)), n["name"])
            self.parts.append({"name": n["name"], "mpn": mpn, "key": mpn_key(mpn, self.cfg), "origin": "a365", "where": str(f),
                               "status": "released", "symbol": "", "footprint": "", "source": {}})


# ---------------------------------------------------------------- grouping
def _score_part(p, keep):
    return (p["name"] in keep, p["origin"] == "a365", p["origin"] == "dblib", p["status"] == "ready",
            str((p.get("source") or {}).get("symbol") or "").startswith("kicad:"), -len(p["mpn"]), -len(p["name"]), p["name"])


def _score_model(m, keep):
    return (m["ref"] in keep, m["origin"] == "library", len(m["used_by"]), str(m.get("source") or "").startswith("kicad:"),
            -len(m["ref"]), m["ref"])


def find_groups(col, keep=(), ignore=()):
    """-> list of {kind, key, keeper, members, conflict}. members: dicts with `name` (part / ref)."""
    keep, ignore, groups = set(keep), set(ignore), []
    by = {}
    for p in col.parts:
        if p["key"] and p["name"] not in ignore:
            by.setdefault(p["key"], []).append(p)
    for k, ps in sorted(by.items()):
        names = {p["name"] for p in ps}
        if len(names) < 2:
            continue                                          # same part in the plan, the DbLib and the workspace
        rep = {}
        for p in sorted(ps, key=lambda p: _score_part(p, keep), reverse=True):
            rep.setdefault(p["name"], p)                      # one member per name, its best origin
        members = sorted(rep.values(), key=lambda p: _score_part(p, keep), reverse=True)
        groups.append({"kind": "mpn", "key": k, "keeper": members[0]["name"], "members": members})
    for kind, lst in (("footprint", col.footprints), ("symbol", col.symbols)):
        by, refs = {}, {}
        for m in lst:
            if m["ref"] in ignore:
                continue
            refs.setdefault(m["ref"], set()).add(m["hash"])
            if m["hash"]:
                by.setdefault(m["hash"], []).append(m)
        for h, ms in sorted(by.items()):
            rep = {}
            for m in ms:                                      # merge same-ref entries (plan + library copy)
                r = rep.setdefault(m["ref"], {**m, "used_by": [], "files": []})
                r["used_by"] += m["used_by"]
                if m["origin"] == "library":                  # an existing library copy: its file is the one to keep
                    r["origin"] = "library"
                    r["files"].insert(0, m["file"])
                else:
                    r["files"].append(m["file"])
            if len(rep) < 2:
                continue
            members = sorted(rep.values(), key=lambda m: _score_model(m, keep), reverse=True)
            for m in members:
                m["name"] = m["ref"]
            groups.append({"kind": kind, "key": h, "keeper": members[0]["ref"], "members": members})
        for ref, hs in sorted(refs.items()):
            if len(hs - {None}) > 1:
                ver = {}                                      # one member per distinct geometry / pinout
                for m in (m for m in lst if m["ref"] == ref):
                    v = ver.setdefault(m["hash"], {**m, "name": f"{ref} #{(m['hash'] or 'unhashed')[:6]}", "used_by": [], "files": []})
                    v["used_by"] += m["used_by"]; v["files"].append(m["file"])
                groups.append({"kind": f"{kind}_conflict", "key": ref, "keeper": "", "members": list(ver.values())})
    return groups


# ---------------------------------------------------------------- merge
def _rel(f, base):
    try:
        return os.path.relpath(Path(f).resolve(), Path(base).resolve())
    except ValueError:                                        # other drive on Windows
        return str(Path(f).resolve())


def merge(items, groups, base, kinds=("mpn", "footprint", "symbol"), who="ecadlib dedupe"):
    """Rewrite plan references to each group's keeper. Returns the mapping rows {kind, from, to, file, parts}."""
    by = {i["name"]: i for i in items}
    mapping, today = [], datetime.date.today().isoformat()
    for g in groups:
        if g["kind"] not in kinds:
            continue
        keeper = next(m for m in g["members"] if m["name"] == g["keeper"])
        if g["kind"] == "mpn":
            for m in g["members"][1:]:
                it = by.get(m["name"])                       # the plan item of that name, whatever origin ranked first
                if it and it.get("a365") and it.get("status") == "existing":
                    it = None                                # a workspace component: reported, never rewritten
                mapping.append({"kind": "part", "from": m["name"], "to": keeper["name"], "file": "",
                                "parts": m["name"] if it else "", "note": "" if it else f"{m['origin']} entry: not changed"})
                if it and it["name"] != keeper["name"]:
                    it["duplicate_of"], it["status"] = keeper["name"], "duplicate"
                    it["dedupe"] = {"by": who, "date": today, "mpn_key": g["key"], "keeper_origin": keeper["origin"]}
            continue
        kind = g["kind"]
        ext = ".pcblib" if kind == "footprint" else ".schlib"        # a plan item needs the Altium file, not a .kicad_mod
        kfile = next((f for f in keeper.get("files") or [keeper["file"]] if f.lower().endswith(ext)), None)
        kmod = next((i["footprint"].get("kicad_mod") for i in items if kind == "footprint" and kfile
                     and (i.get("footprint") or {}).get("ref") == keeper["ref"] and i["footprint"].get("kicad_mod")
                     and Path(_rel(kfile, base)) == Path(i["footprint"].get("file") or "")), None)
        for m in g["members"][1:]:
            if not kfile:
                mapping.append({"kind": kind, "from": m["ref"], "to": keeper["ref"], "file": "", "parts": "",
                                "note": f"keeper has no {'.PcbLib' if kind == 'footprint' else '.SchLib'}: not changed"})
                continue
            changed = []
            for name in m["used_by"]:
                it = by.get(name)
                if not it or (it.get(kind) or {}).get("ref") != m["ref"]:
                    continue
                old = dict(it[kind])
                new = {"ref": keeper["ref"], "file": _rel(kfile, base)}
                if kmod:
                    new["kicad_mod"] = kmod
                it[kind] = new
                it.setdefault("merged_from", {})[kind] = {**old, "by": who, "date": today}
                changed.append(name)
            mapping.append({"kind": kind, "from": m["ref"], "to": keeper["ref"], "file": _rel(kfile, base),
                            "parts": ", ".join(changed), "note": "" if changed else "library only: not changed"})
    return mapping


def remap_bom(bom, mapping, out):
    """Copy of the BOM with LibRef / Footprint cells mapped to the keepers (other columns untouched)."""
    from . import bom_triage as T
    import pandas as pd
    p = Path(bom)
    raw = (T._read_excel(p) if p.suffix.lower() in (".xlsx", ".xlsm", ".xls") else T._read_csv(p)).fillna("")   # the BOM sheet,
                                                                                  # as read_bom picks it (not a cover sheet)
    hdr = max(range(min(10, len(raw))), key=lambda i: len(T.map_headers(list(raw.iloc[i]))))
    headers = [str(h).strip() for h in raw.iloc[hdr]]
    cols = T.map_headers(headers)
    m = {k: {r["from"]: r["to"] for r in mapping if r["kind"] == k} for k in ("symbol", "footprint")}
    n = 0
    for field, kind in (("libref", "symbol"), ("footprint", "footprint")):
        if field not in cols:
            continue
        j = headers.index(cols[field])
        for i in range(hdr + 1, len(raw)):
            v = str(raw.iat[i, j]).strip()
            lib, _, ref = v.rpartition(":")
            if ref in m[kind]:
                raw.iat[i, j] = (f"{lib}:" if lib else "") + m[kind][ref]
                n += 1
    out = Path(out)
    SS.frame(raw).to_csv(out, header=False, index=False)
    return out, n


# ---------------------------------------------------------------- reports & CLI
def report_rows(groups):
    rows = []
    for gi, g in enumerate(groups, 1):
        for m in g["members"]:
            rows.append({"group": gi, "kind": g["kind"], "key": g["key"], "member": m["name"],
                         "keeper": "yes" if g["keeper"] and m["name"] == g["keeper"] else "",
                         "origin": m["origin"], "where": m.get("where") or "; ".join(m.get("files") or [m.get("file", "")]),
                         "mpn": m.get("mpn", ""), "used_by": ", ".join(m.get("used_by") or []), "status": m.get("status", "")})
    return rows


def write_reports(groups, mapping, stem, plan, errors=()):
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    rows = report_rows(groups)
    fields = ["group", "kind", "key", "member", "keeper", "origin", "where", "mpn", "used_by", "status"]
    with open(stem.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = SS.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    mp = stem.parent / f"{stem.name}_map.csv"
    with open(mp, "w", newline="", encoding="utf-8") as f:
        w = SS.DictWriter(f, fieldnames=["kind", "from", "to", "file", "parts", "note"]); w.writeheader(); w.writerows(mapping)
    count = lambda k: sum(g["kind"] == k for g in groups)
    md = [f"# Duplicates - `{plan}`", "",
          f"{count('mpn')} part groups (same MPN), {count('footprint')} footprint groups (same geometry), "
          f"{count('symbol')} symbol groups (same pinout); {count('footprint_conflict') + count('symbol_conflict')} name conflicts.", ""]
    for gi, g in enumerate(groups, 1):
        title = {"mpn": f"MPN `{g['key']}`", "footprint": "footprint geometry", "symbol": "symbol pinout",
                 "footprint_conflict": f"footprint `{g['key']}`: same name, different geometry",
                 "symbol_conflict": f"symbol `{g['key']}`: same name, different pinout"}[g["kind"]]
        md += [f"## {gi}. {title}" + (f" -> keep **{g['keeper']}**" if g["keeper"] else ""), ""]
        for m in g["members"]:
            where = m.get("where") or ", ".join(m.get("files") or [m.get("file", "")])
            md.append(f"- {'**keep** ' if g['keeper'] and m['name'] == g['keeper'] else ''}`{m['name']}` ({m['origin']}"
                      + (f", MPN {m['mpn']}" if m.get("mpn") and m["mpn"] != m["name"] else "")
                      + (f", used by {', '.join(m['used_by'])}" if m.get("used_by") else "") + f"): {where}")
        md.append("")
    if mapping:
        md += ["## Mapping (written to the plan with --merge; library files are never changed)", "",
               "| Kind | From | To | Plan items | Note |", "|---|---|---|---|---|"]
        md += [f"| {r['kind']} | {r['from']} | {r['to']} | {r['parts'] or '-'} | {r['note'] or '-'} |" for r in mapping]
    if errors:
        md += ["", "## Could not read", ""] + [f"- {e}" for e in errors]
    stem.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return stem, mp


def backup(plan):
    """Copy the plan before --merge rewrites it. The first copy is plan.json.pre-dedupe and is never overwritten (a
    second merge used to replace the original with the already merged plan); later ones get a UTC timestamp."""
    plan = Path(plan)
    first = plan.with_name(plan.name + ".pre-dedupe")
    dst = first
    if first.exists():
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        dst, n = plan.with_name(f"{first.name}-{stamp}"), 1
        while dst.exists():
            n += 1
            dst = plan.with_name(f"{first.name}-{stamp}-{n}")
    shutil.copyfile(plan, dst)
    return dst


def run(plan, libs=(), a365=None, config=None, keep=(), do_merge=False, kinds=("mpn", "footprint", "symbol"), out=None,
        bom=None, who="ecadlib dedupe", log=print):
    plan = Path(plan)
    base = plan.parent
    cfg = load_config(config, base)
    from . import rules as RU                 # house rules may draw the courtyard on another mechanical layer
    cfg["courtyard"] = RU.layer_id(RU.load(None, start=base)["layers"]["courtyard"])
    items = json.loads(plan.read_text(encoding="utf-8"))
    col = Collector(cfg, log)
    col.add_plan(items, base)
    for lib in libs:
        if not Path(lib).exists():
            raise FileNotFoundError(f"--lib {lib}: not found")
        col.add_lib(lib)
    a365 = a365 or (base / "a365_state.json" if (base / "a365_state.json").exists() else None)
    if a365:
        col.add_a365(a365)
    groups = find_groups(col, list(keep) + cfg["keep"], cfg["ignore"])
    if do_merge:
        backup(plan)
        mapping = merge(items, groups, base, kinds, who)
        write_text_atomic(plan, json.dumps(items, indent=2))
    else:
        mapping = merge(json.loads(json.dumps(items)), groups, base, kinds, who)   # dry run on a copy: what would change
    stem, mp = write_reports(groups, mapping, out or base / "dedupe", plan, col.errors)
    bom_out = remap_bom(bom, mapping, Path(stem).parent / f"{Path(bom).stem}_deduped.csv") if bom else None
    return {"groups": groups, "mapping": mapping, "report": stem.with_suffix(".md"), "map": mp, "bom": bom_out,
            "errors": col.errors, "items": items}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find duplicate parts, footprints and symbols across a plan and existing libraries")
    ap.add_argument("plan")
    ap.add_argument("--lib", action="append", default=[], help="library file or folder (.SchLib .PcbLib .kicad_mod .DbLib .sqlite .xlsx); repeatable")
    ap.add_argument("--a365", help="check-a365 --state file or a JSON list of workspace components (default <plan dir>/a365_state.json)")
    ap.add_argument("--config", help="dedupe config JSON (default <plan dir>/dedupe.json)")
    ap.add_argument("--keep", action="append", default=[], help="force this part / footprint / symbol name as keeper (repeatable)")
    ap.add_argument("--merge", action="store_true", help="rewrite the plan's references to the keepers")
    ap.add_argument("--kinds", default="mpn,footprint,symbol", help="what --merge rewrites (default %(default)s)")
    ap.add_argument("--bom", help="also write a copy of this BOM with LibRef / Footprint mapped to the keepers")
    ap.add_argument("-o", "--out", help="report stem (default <plan dir>/dedupe)")
    a = ap.parse_args(argv)
    from .approvals import cli_plan_lock
    try:
        with cli_plan_lock(a.plan) if a.merge else contextlib.nullcontext():   # the agent may be rewriting this plan
            r = run(a.plan, a.lib, a.a365, a.config, a.keep, a.merge, tuple(k for k in a.kinds.split(",") if k), a.out, a.bom)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"dedupe: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    for g in r["groups"]:
        print(f"  {g['kind']:<18} keep {g['keeper'] or '-':<28} " + ", ".join(m["name"] for m in g["members"] if m["name"] != g["keeper"]))
    for e in r["errors"]:
        print(f"  unreadable: {e}")
    acted = [m for m in r["mapping"] if m["parts"]]
    print(f"{len(r['groups'])} groups; {len(acted)} plan rewrites {'applied' if a.merge else 'proposed (use --merge)'}"
          f" -> {r['report']}, {r['map']}" + (f", BOM {r['bom'][0]} ({r['bom'][1]} cells)" if r["bom"] else ""))
    return 1 if r["groups"] else 0


if __name__ == "__main__":
    sys.exit(main())
