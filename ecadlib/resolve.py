"""Fix held parts: rank symbol / footprint candidates for a plan item, convert the chosen one, re-check it.

    python3 -m ecadlib.resolve out/librarian/plan.json list                      # held items and their reasons
    python3 -m ecadlib.resolve out/librarian/plan.json --part NAME show          # one item: halves, pins vs pads
    python3 -m ecadlib.resolve out/librarian/plan.json --part NAME list [--kind symbol|footprint] [--limit N]
    python3 -m ecadlib.resolve out/librarian/plan.json --part NAME choose symbol|footprint ID [--map PIN=PAD ...]
        [--lib DIR ...] [--cache out/http_cache] [--offline] [--json]

Candidates come from the official KiCad library name index (the librarian's cached GitLab index; files fetched
through httpcache), --lib KiCad library dirs (kicad_index), ecadlib's package specs (spec:<name>, built by
footprint_gen) and any `alternatives` the plan item records. Ids: kicad:<Lib>:<Name>, local:<Lib>:<Name>,
spec:<Name>. Names are ranked by the tokens they share with the item (footprint / symbol names, description,
folder, MPN; rare tokens weigh more), an MPN prefix, package and body size, the designator class's usual
libraries and, for connectors, the generic Conn_ symbol for the pad count. The best few are opened and their
pins (or pads) compared with the item's other half the way release_check does; a full match ranks first, and a
near miss gets a suggested pin map (e.g. a USB-C symbol's shield pin S1 onto the footprint's SH pads).
`choose` converts the candidate through kicad_import into <plan dir>/models (like the librarian), updates the
plan item (file, kicad_mod, source, librarian.tried "chosen by user") and librarian_state.json (so a librarian
re-run keeps the choice), then re-runs release_check on that item only. --map remaps symbol pin numbers to pad
numbers (choosing a footprint with --map re-converts the item's KiCad symbol with the map).
API (plain dicts / lists, nothing printed): held(plan), show(plan, part, src), candidates(item, kind, base, src),
choose(plan, part, kind, id, src=..., pin_map=...)."""
import argparse, fnmatch, json, math, os, re, sys
from pathlib import Path

from . import altium_lib as AL, footprint_gen as F, kicad_import as K, librarian as L, release_check as RC, rules as RU, sexpr
from .fsutil import write_text_atomic
from .httpcache import CachedHTTP
from .kicad_index import KiCadIndex, _body, norm_mpn, package_of

KINDS = ("symbol", "footprint")
REF_CLASS = {"Resistors": "R", "Capacitors": "C", "Inductors": "L", "Diodes": "D", "Transistors": "Q",
             "Crystals": "Y", "Connectors": "J", "ICs": "U"}
LIB_HINT = {"R": ("Device", "Resistor"), "C": ("Device", "Capacitor"), "L": ("Device", "Inductor"),
            "D": ("Device", "Diode", "LED"), "Q": ("Transistor", "Package_TO"), "Y": ("Device", "Oscillator", "Crystal"),
            "J": ("Connector",)}


class Sources:
    """Where candidates come from. Everything is lazy: nothing is fetched until a candidate list needs it."""

    def __init__(self, cache="out/http_cache", libs=(), offline=False, http=None, specs=L.SPECS, kicad=None):
        self.cache, self.libs, self.specs = Path(cache), [str(l) for l in libs], Path(specs) if specs else None
        self.http = http or CachedHTTP(cache, min_interval=0.5, offline=offline, timeout=60)
        self._kicad, self._index, self._pools = kicad, None, {}

    def kicad(self):
        if self._kicad is None:
            self._kicad = L.KiCadLibrary(self.http, self.cache / "kicad_library_index.json")
        return self._kicad

    def index(self):
        if self._index is None:
            self._index = KiCadIndex(self.libs)
        return self._index

    def pool(self, kind):
        """[(id, lib, name, tokens)] for every known name of this kind, plus the token document frequencies."""
        if kind not in self._pools:
            k, ix, rows = self.kicad(), self.index(), []
            names = k.symbols if kind == "symbol" else k.footprints
            rows += [(f"kicad:{lib}:{n}", lib, n) for n, libs in names.items() for lib in libs]
            if kind == "symbol":
                rows += [(f"local:{s.lib}:{s.name}", s.lib, s.name) for s in ix.symbols]
            else:
                rows += [(f"local:{r}", r.split(":")[0], r.split(":")[-1]) for r in ix.footprints]
                local = {r.split(":")[-1] for r in ix.footprints}
                if self.specs and self.specs.is_dir():
                    rows += [(f"spec:{f.stem}", "spec", f.stem) for f in sorted(self.specs.glob("*.json")) if f.stem not in local]
            rows = [(i, lib, n, tokens(n)) for i, lib, n in rows]
            df = {}
            for r in rows:
                for t in r[3]:
                    df[t] = df.get(t, 0) + 1
            self._pools[kind] = (rows, df)
        return self._pools[kind]


# ---------------------------------------------------------------- tokens, pins and pads
def tokens(*texts):
    """Lower-case name tokens; '16-pin' / '4Pin' -> '16p', '2x5' == '2x05', '1.27mm' -> 'p1.27mm', plural 's' dropped,
    CamelCase also split (PinHeader -> pinheader, pin, header)."""
    out = set()
    for t in texts:
        t = str(t or "")
        t += " " + re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t) if re.search(r"[a-z][A-Z]", t) else ""   # PinHeader: also pin, header
        s = re.sub(r"(\d+)[- ]?pins?(?![a-z])", r"\1p", t.lower())
        for w in re.split(r"[^a-z0-9.]+", s):
            w = w.strip(".")
            m = re.fullmatch(r"(\d{1,2})x(\d{1,2})", w)
            if m:
                out.add(f"{int(m[1])}x{int(m[2])}")
                continue
            if re.fullmatch(r"p?\d+(\.\d+)?mm", w):
                w = "p" + w.lstrip("p")
            if len(w) >= 3 or re.fullmatch(r"\d+p", w):
                out.add(w)
                if w.endswith("s") and len(w) > 4 and w[:-1].isalpha():
                    out.add(w[:-1])
    return out


def symbol_pins(chain, pin_map=None):
    """KiCad symbol chain -> sorted pin numbers (after pin_map) and its properties."""
    sym = K.convert_symbol(chain, pin_map=pin_map, notes=[])
    return sorted({pr[1]["num"] for pr in sym["prims"] if pr[0] == "pin"}), K._props(chain)


def footprint_pads(text):
    """.kicad_mod text -> [(designator, mechanical)]; mechanical as release_check counts it."""
    return [(str(p[1]), not p[1] or p[2] == "np_thru_hole" or bool(RC.MECH_PAD.fullmatch(str(p[1]))))
            for p in sexpr.find(sexpr.parse(text), "pad") if len(p) > 2]


def compare(pins, pads):
    """Pins vs pads with release_check's rule, plus a suggested pin_map for a near miss."""
    pins, every = set(pins), {d for d, _ in pads}
    numbered = {d for d, m in pads if not m}
    nopad, nopin = sorted(pins - every, key=_nat), sorted(numbered - pins, key=_nat)
    res = {"match": bool(pins) and bool(pads) and not nopad and not nopin, "pins": len(pins), "pads": len(numbered),
           "pins_without_pad": nopad, "pads_without_pin": nopin, "pin_map": None}
    free = sorted((every - pins) - {""}, key=_nat)
    if nopad and len(nopad) == len(nopin) <= 4:          # a small near miss, not a renumbering
        res["pin_map"] = dict(zip(nopad, nopin))
    elif len(nopad) == 1 and len(free) == 1 and not nopin:
        res["pin_map"] = {nopad[0]: free[0]}
    return res


def _nat(s):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", str(s))]


def _par(item):
    return {p["name"]: p["value"] for p in item.get("parameters", [])}


def _path(base, p):
    return RC._path(base, p)


def item_pins(item, base):
    """Pin numbers of the item's current .SchLib, or None."""
    f = _path(base, item["symbol"].get("file"))
    if not f or not f.exists():
        return None
    syms = AL.read_schlib(f)[1]
    return sorted({r["num"] for s in syms for r in s["records"] if r.get("RECORD") == "2"}, key=_nat)


def item_pads(item, base):
    """[(designator, mechanical)] of the item's current footprint (.kicad_mod, else .PcbLib), or None."""
    f = _path(base, item["footprint"].get("file"))
    if not f or not f.exists():
        return None
    km = RC._kicad_mod(item, base, f)
    if km:
        return footprint_pads(km.read_text(encoding="utf-8"))
    pads = [r for r in AL.read_pcblib(f)[0]["records"] if r["type"] == "pad"]
    return [(p["designator"], not p["designator"] or (p["hole"] and not p["plated"]) or bool(RC.MECH_PAD.fullmatch(p["designator"])))
            for p in pads]


# ---------------------------------------------------------------- loading a candidate
def load(cid, kind, src, rules=None):
    """Candidate id -> (KiCad text or symbol chain, extra) without writing anything.
    symbol: (chain, None); footprint: (kicad_mod text, height_mm or None). rules: the library rules (IPC density
    for spec: candidates); None = ecadlib's own."""
    origin, _, ref = cid.partition(":")
    if kind == "symbol":
        if origin == "kicad":
            return K.load_symbol(ref, src.http)[0], None
        if origin == "local":
            lib, name = ref.split(":", 1)
            f = next((p for r in src.libs for p in Path(r).rglob(f"{lib}.kicad_sym")), None) or \
                next((p for r in src.libs for p in Path(r).rglob(f"{name}.kicad_sym")), None)
            if not f:
                raise FileNotFoundError(f"{cid}: no {lib}.kicad_sym under --lib")
            return K.load_symbol(f"{f}:{name}", None)[0], None
    else:
        if origin == "kicad":
            return K.load_footprint(ref, src.http)[0], None
        if origin == "local":
            hit = src.index().footprints.get(ref)
            if not hit:
                raise FileNotFoundError(f"{cid}: not in the --lib footprints")
            spec = src.specs / f"{ref.split(':')[-1]}.json" if src.specs else None
            hm = json.loads(spec.read_text(encoding="utf-8")).get("height_max") if spec and spec.exists() else None
            return Path(hit["path"]).read_text(encoding="utf-8"), (F.dim(hm)[2] if hm else None)
        if origin == "spec":
            spec = json.loads((src.specs / f"{ref}.json").read_text(encoding="utf-8"))
            return F.build(spec, RU.density_for(spec, rules or RU.DEFAULTS))[0], (F.dim(spec["height_max"])[2] if spec.get("height_max") else None)
    raise ValueError(f"unknown {kind} candidate id {cid!r} (use kicad:Lib:Name, local:Lib:Name or spec:Name)")


# ---------------------------------------------------------------- ranking
def _query(item, kind):
    """{token: weight}. For a missing footprint the item's own footprint ref is usually just the MPN (KiCad did not
    know it), so its tokens count half; the description and the other half's KiCad name count fully."""
    par, other = _par(item), ("footprint" if kind == "symbol" else "symbol")
    full = [item.get("description"), par.get("Value"), str((item.get("source") or {}).get(other) or "").split(":")[-1],
            item[other].get("ref")]
    if kind == "symbol":
        full.append(item["folder"].split("/")[-1])
    q = {t: 0.5 for t in tokens(item["footprint"].get("ref"))} if kind == "footprint" else {}
    q.update({t: 1.0 for t in tokens(*full)})
    return q


def _alternatives(item, kind):
    alts = list(item.get("alternatives") or []) + list((item.get("librarian") or {}).get("alternatives") or [])
    out = {}
    for a in alts:
        a = {"id": a} if isinstance(a, str) else a
        if a.get("id") and a.get("kind", kind) == kind:
            out[a["id"]] = a.get("why", "")
    return out


def _name_scores(item, kind, src):
    rows, df = src.pool(kind)
    n, q = len(rows) or 1, _query(item, kind)
    idf = {t: w * math.log(n / df.get(t, 1)) for t, w in q.items()}
    mpn = norm_mpn(re.sub(r"\(.*", "", _par(item).get("Manufacturer Part Number", "") or item["name"]))
    cls = REF_CLASS.get(item["folder"].split("/")[-1], "")
    pkg = package_of(item["footprint"].get("ref"), item.get("description"))
    body = _body(item["footprint"].get("ref")) or _body(item.get("description"))
    out = {}
    for cid, lib, name, toks in rows:
        why, s = [], 0.0
        hit = sorted(q.keys() & toks)
        part = [t for t in q.keys() - toks if len(t) >= 5 and any(t in u or (len(u) >= 5 and u in t) for u in toks)]
        s += sum(idf[t] for t in hit) + 0.5 * sum(idf[t] for t in part)
        if hit or part:
            why.append("shares " + ", ".join(hit + [f"~{t}" for t in part]))
        nm = norm_mpn(name)
        k = len(os.path.commonprefix([nm, mpn]))
        if len(mpn) >= 5 and k >= max(5, int(len(mpn) * 0.6)) and (k == len(nm) or kind == "footprint"):
            s += 8 + 4 * (k == len(mpn))
            why.append(f"MPN prefix {mpn[:k]}")
        elif kind == "footprint" and len(mpn) >= 6 and mpn[:6] in nm:
            s += 4
            why.append(f"name contains {mpn[:6]}")
        if cls in LIB_HINT and lib.startswith(LIB_HINT[cls]) and s:
            s += 1
        for label, rx in (("pitch", r"p\d+(\.\d+)?mm"), ("grid", r"\d+x\d+")):     # stated pitch / pin grid must agree
            want, have = {t for t in q if re.fullmatch(rx, t)}, {t for t in toks if re.fullmatch(rx, t)}
            if want and have and not want & have:
                s -= 10
                why.append(f"{label} {'/'.join(sorted(have))}, not {'/'.join(sorted(want))}")
        if kind == "footprint":
            p = package_of(name)
            if pkg and p:
                s, _ = (s + 3, why.append(f"package {pkg}")) if p == pkg else (s - 10, why.append(f"package {p}, not {pkg}"))
            b = _body(name)
            if body and b:
                s, _ = (s + 2, why.append("body size agrees")) if b == body else (s - 10, why.append(f"body {b[0]}x{b[1]} mm differs"))
        if s > 0:
            out[cid] = (s, why)
    return out


def _generic_connectors(n):
    if n < 1:
        return []
    names = [f"Conn_01x{n:02d}"] + ([f"Conn_02x{n // 2:02d}_Odd_Even", f"Conn_02x{n // 2:02d}_Counter_Clockwise"] if n % 2 == 0 else [])
    return [f"kicad:Connector_Generic:{x}" for x in names]


def candidates(item, kind, base=".", src=None, limit=10, open_=12):
    """Ranked candidates for one half of a plan item: [{id, kind, name, origin, score, why[], count, match,
    pins_without_pad, pads_without_pin, pin_map, current, error?}]. `open_` = how many top names are opened."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    src = src or Sources()
    other = item_pads(item, base) if kind == "symbol" else item_pins(item, base)
    scored = _name_scores(item, kind, src)
    pool_ids = {r[0] for r in src.pool(kind)[0]}
    forced = {}
    for cid, why in _alternatives(item, kind).items():
        forced[cid] = (4.0, [f"recorded by the librarian{': ' + why if why else ''}"])
    cur = (item.get("source") or {}).get(kind)
    if cur and cur.startswith(("kicad:", "local:", "spec:")):
        forced.setdefault(cur, (0.0, ["current choice"]))
    if kind == "symbol" and REF_CLASS.get(item["folder"].split("/")[-1]) == "J" and other:
        for cid in _generic_connectors(len({d for d, m in other if not m})):
            if cid in pool_ids:
                forced.setdefault(cid, (3.0, [f"generic connector for {len({d for d, m in other if not m})} pads"]))
    for cid, (s, why) in forced.items():
        s0, w0 = scored.get(cid, (0.0, []))
        scored[cid] = (s0 + s, why + w0)
    top = sorted(scored.items(), key=lambda kv: (-kv[1][0], len(kv[0]), kv[0]))
    top = [kv for kv in top if kv[0] in forced] + [kv for kv in top if kv[0] not in forced][:max(open_ - len(forced), open_ // 2)]
    other_fp = str((item.get("source") or {}).get("footprint") or "").partition(":")[2]
    filters = _fp_filters(item, src) if kind == "footprint" else []
    out = []
    for cid, (s, why) in top:
        c = {"id": cid, "kind": kind, "name": cid.split(":")[-1], "origin": cid.split(":")[0], "why": list(why),
             "count": None, "match": None, "pins_without_pad": [], "pads_without_pin": [], "pin_map": None, "current": cid == cur}
        try:
            got, _ = load(cid, kind, src)
            if kind == "symbol":
                pins, props = symbol_pins(got)
                c["count"] = len(pins)
                if other_fp and props.get("Footprint", "").split(":")[-1] == other_fp.split(":")[-1]:
                    s += 3; c["why"].append("its default footprint is the item's footprint")
                if other_fp and _filter_hit(props.get("ki_fp_filters", ""), other_fp):
                    s += 2; c["why"].append("its footprint filter accepts the item's footprint")
                cmp = compare(pins, other) if other is not None else None
            else:
                pads = footprint_pads(got)
                c["count"] = len({d for d, m in pads if not m})
                if filters and _filter_hit(" ".join(filters), cid.partition(":")[2]):
                    s += 2; c["why"].append("the symbol's footprint filter accepts it")
                cmp = compare(other, pads) if other is not None else None
            if cmp:
                c.update({k: cmp[k] for k in ("match", "pins_without_pad", "pads_without_pin", "pin_map")})
                if cmp["match"]:
                    s += 10; c["why"].append(f"pins match the {'footprint' if kind == 'symbol' else 'symbol'}")
                else:
                    s += 3 * (cmp["pins"] == cmp["pads"]) + 5 * bool(cmp["pin_map"])
                    c["why"].append(f"{cmp['pins']} pins vs {cmp['pads']} pads" + (f": suggest pin_map {cmp['pin_map']}" if cmp["pin_map"] else ""))
        except Exception as e:
            s -= 5
            c["error"] = f"{type(e).__name__}: {e}"
        c["score"] = round(s, 1)
        out.append(c)
    out.sort(key=lambda c: (-c["score"], len(c["id"]), c["id"]))
    return out[:limit]


def _fp_filters(item, src):
    """ki_fp_filters of the item's symbol when it came from a KiCad library."""
    sid = str((item.get("source") or {}).get("symbol") or "")
    if not sid.startswith(("kicad:", "local:")):
        return []
    try:
        return symbol_pins(load(sid, "symbol", src)[0])[1].get("ki_fp_filters", "").split()
    except Exception:
        return []


def _filter_hit(filters, fp_ref):
    lib, _, name = fp_ref.rpartition(":")
    for f in str(filters or "").split():
        if fnmatch.fnmatchcase(f"{lib}:{name}" if ":" in f else name, f):
            return True
    return False


# ---------------------------------------------------------------- plan access and choosing
def _load_plan(plan):
    return json.loads(Path(plan).read_text(encoding="utf-8"))


def _item(items, part):
    it = next((i for i in items if i["name"] == part), None)
    if not it:
        raise KeyError(f"{part!r} is not in the plan")
    return it


def missing(item):
    return [k for k in KINDS if not item[k].get("file")]


def held(plan):
    """Held items: [{name, folder, missing[], hold_reasons[], source{}}]."""
    return [{"name": i["name"], "folder": i["folder"], "missing": missing(i), "hold_reasons": i.get("hold_reasons", []),
             "source": i.get("source") or {}} for i in _load_plan(plan) if i.get("status") == "held"]


def show(plan, part):
    """One item: status, reasons, each half's ref/file/source and the pins-vs-pads comparison."""
    base, it = Path(plan).parent, _item(_load_plan(plan), part)
    pins, pads = item_pins(it, base), item_pads(it, base)
    return {"name": it["name"], "status": it.get("status"), "hold_reasons": it.get("hold_reasons", []),
            "description": it.get("description"), "folder": it["folder"], "missing": missing(it),
            "symbol": dict(it["symbol"], source=(it.get("source") or {}).get("symbol"), pins=pins),
            "footprint": dict(it["footprint"], source=(it.get("source") or {}).get("footprint"),
                              pads=[d for d, _ in pads] if pads is not None else None),
            "pins_vs_pads": compare(pins, pads) if pins is not None and pads is not None else None,
            "tried": (it.get("librarian") or {}).get("tried", []), "review": (it.get("librarian") or {}).get("review", [])}


def _write(item, kind, cid, src, base, pin_map=None):
    """Convert candidate `cid` into <base>/models like the librarian; returns the librarian-style half record."""
    rules = RU.load(None, start=base)              # the house rules (layers, density), as the librarian uses them
    r = L.Run(base, src.http, None, src.libs, [], rules=rules)
    st = {"name": item["name"], "symbol": None, "footprint": None}
    got, height = load(cid, kind, src, rules)
    if kind == "footprint":
        r.put_fp(st, got, item["footprint"]["ref"], cid, src.http if cid.startswith("kicad:") else None, height, "package spec")
    else:
        mpn = _par(item).get("Manufacturer Part Number", "")
        shared = L.passive_kind(item) in ("R", "C", "CP", "L")
        notes = []
        sym = K.convert_symbol(got, item["symbol"]["ref"], None if shared else item["footprint"]["ref"],
                               [] if shared or not mpn else [("MPN", mpn)], pin_map, notes)
        if pin_map:
            notes.append("pin map " + ", ".join(f"{a}->{b}" for a, b in pin_map.items()))
        r.put_sym(st, sym, cid, notes)
    rec = dict(st[kind], chosen=True)
    if pin_map and kind == "symbol":
        rec["pin_map"] = dict(pin_map)
    return rec


def choose(plan, part, kind, candidate_id, base=None, src=None, pin_map=None):
    """Use candidate_id for one half of a plan item, write plan.json (and librarian_state.json when present), re-run
    release_check on that item. Returns {name, status, hold_reasons, warnings, file, source, notes, pins_vs_pads}."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    plan, src = Path(plan), src or Sources()
    base = Path(base) if base else plan.parent
    items = _load_plan(plan)
    it = _item(items, part)
    done = [(kind, candidate_id, pin_map if kind == "symbol" else None)]
    if pin_map and kind == "footprint":            # the map lives in the symbol: re-convert it
        sid = (it.get("source") or {}).get("symbol") or ""
        if not sid.startswith(("kicad:", "local:")):
            raise ValueError(f"--map with a footprint needs a KiCad symbol to re-convert; {part}'s symbol is from {sid or 'nowhere'}")
        done.append(("symbol", sid, pin_map))
    state_f = base / "librarian_state.json"
    state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else None
    shared_with = lambda k: [i["name"] for i in items if i is not it and i[k].get("file") and
                             i[k]["file"] == os.path.relpath(base / "models" / ("SchLib" if k == "symbol" else "PcbLib") /
                                                             f"{AL.file_stem(it[k]['ref'])}.{'SchLib' if k == 'symbol' else 'PcbLib'}", base)]
    shared = shared_with(kind)
    rewritten = {n for k, _, _ in done for n in shared_with(k)}      # every part whose model file this choice overwrites
    notes = []
    for k, cid, pm in done:
        rec = _write(it, k, cid, src, base, pm)
        it[k]["file"] = rec["file"]
        if k == "footprint":
            it[k]["kicad_mod"] = rec.get("kicad_mod")
        if pm:
            it[k]["pin_map"] = dict(pm)
        else:
            it[k].pop("pin_map", None)
        it.setdefault("source", {"symbol": None, "footprint": None})[k] = cid
        lib = it.setdefault("librarian", {"tried": [], "review": [], "notes": []})
        msg = f"resolve: {k} {cid} chosen by user" + (f" (pin map {pm})" if pm else "")
        lib.setdefault("tried", []).append(msg)
        notes += rec.get("notes", [])
        if state is not None and part in state:
            state[part][k] = rec
            state[part].setdefault("tried", []).append(msg)
    if shared:
        notes.append(f"{kind} file is shared with {', '.join(shared)}: they now use this {kind} too")
    RC.apply([it] + [i for i in items if i["name"] in rewritten], base)   # the parts sharing the overwritten file are
                                                                          # re-checked too: their old `ready` no longer holds
    write_text_atomic(plan, json.dumps(items, indent=2))
    if state is not None:
        write_text_atomic(state_f, json.dumps(state, indent=2))
    pins, pads = item_pins(it, base), item_pads(it, base)
    return {"name": part, "status": it["status"], "hold_reasons": it["hold_reasons"],
            "warnings": it["release_check"]["warnings"], "file": it[kind]["file"], "source": it["source"][kind],
            "notes": notes, "pins_vs_pads": compare(pins, pads) if pins is not None and pads is not None else None}


# ---------------------------------------------------------------- CLI
def _pin_map(pairs):
    out = {}
    for p in pairs or []:
        a, sep, b = p.partition("=")
        if not sep or not a or not b:
            raise SystemExit(f"--map wants PIN=PAD, got {p!r}")
        out[a] = b
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fix held plan items: rank symbol/footprint candidates, choose one, re-check")
    ap.add_argument("plan")
    ap.add_argument("--part", help="plan item (Part Number); without it, `list` shows the held items")
    ap.add_argument("--lib", action="append", default=[], help="local KiCad library dir (repeatable)")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--offline", action="store_true", help="use the HTTP cache only")
    ap.add_argument("--json", action="store_true", help="print the API result as JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="held items, or with --part the candidates for its missing half")
    ls.add_argument("--kind", choices=KINDS, help="default: the missing half (both when neither is missing)")
    ls.add_argument("--limit", type=int, default=10)
    sub.add_parser("show", help="one item: halves, sources, pins vs pads, hold reasons")
    ch = sub.add_parser("choose", help="use a candidate for one half of --part")
    ch.add_argument("kind", choices=KINDS)
    ch.add_argument("id", help="kicad:Lib:Name, local:Lib:Name or spec:Name")
    ch.add_argument("--map", action="append", metavar="PIN=PAD", help="symbol pin number -> pad number (repeatable)")
    a = ap.parse_args(argv)
    emit = (lambda r: print(json.dumps(r, indent=2))) if a.json else None
    src = lambda: Sources(a.cache, a.lib, a.offline)
    if a.cmd == "list" and not a.part:
        r = held(a.plan)
        if emit:
            return emit(r) or 0
        for h in r:
            print(f"{h['name']}  ({h['folder']}){'  missing ' + ' + '.join(h['missing']) if h['missing'] else ''}")
            for m in h["hold_reasons"]:
                print(f"    {m}")
        print(f"{len(r)} held")
        return 0
    if not a.part:
        ap.error(f"{a.cmd} needs --part")
    if a.cmd == "show":
        r = show(a.plan, a.part)
        if emit:
            return emit(r) or 0
        print(f"{r['name']}: {r['status']}  ({r['folder']}) {r['description'] or ''}")
        for k in KINDS:
            h = r[k]
            n = h.get("pins") if k == "symbol" else h.get("pads")
            print(f"  {k:<9} {h['ref']}  file={h.get('file') or '-'}  from={h.get('source') or '-'}"
                  + (f"  {len(n)} {'pins' if k == 'symbol' else 'pads'}" if n is not None else ""))
        c = r["pins_vs_pads"]
        if c:
            print(f"  pins vs pads: {'match' if c['match'] else 'pins without pad ' + str(c['pins_without_pad']) + ', pads without pin ' + str(c['pads_without_pin'])}"
                  + (f"; suggest --map {' --map '.join(f'{x}={y}' for x, y in c['pin_map'].items())}" if c and c["pin_map"] else ""))
        for m in r["hold_reasons"]:
            print(f"  held: {m}")
        return 0
    if a.cmd == "list":
        plan = _load_plan(a.plan)
        it, s = _item(plan, a.part), src()
        kinds = [a.kind] if a.kind else (missing(it) or list(KINDS))
        r = {k: candidates(it, k, Path(a.plan).parent, s, a.limit) for k in kinds}
        if emit:
            return emit(r) or 0
        for k, cs in r.items():
            print(f"{k} candidates for {a.part}:")
            for c in cs:
                flag = "match" if c["match"] else "-" if c["match"] is None else "differ"
                print(f"  {c['score']:>5}  {c['id']:<62} {c['count'] if c['count'] is not None else '?':>4} {'pins' if k == 'symbol' else 'pads'}  {flag:<6}"
                      f"{'  (current)' if c['current'] else ''}")
                print(f"         {'; '.join(c['why'])}" + (f"  ERROR {c['error']}" if c.get("error") else ""))
                if c["pin_map"]:
                    print(f"         suggest: --map {' --map '.join(f'{x}={y}' for x, y in c['pin_map'].items())}")
        return 0
    from .approvals import cli_plan_lock
    with cli_plan_lock(a.plan):                # the library agent's librarian may be rewriting this plan right now
        r = choose(a.plan, a.part, a.kind, a.id, src=src(), pin_map=_pin_map(a.map))
    if emit:
        emit(r)
    else:
        print(f"{a.part}: {a.kind} <- {r['source']} ({r['file']}); now {r['status']}")
        for m in r["hold_reasons"]:
            print(f"    held: {m}")
        for n in r["notes"]:
            print(f"    note: {n}")
    return 0 if r["status"] == "ready" else 2


if __name__ == "__main__":
    sys.exit(main())
