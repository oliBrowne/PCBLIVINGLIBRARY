"""Change-control corpus run (#105): classify every symbol and footprint that changed between two KiCad library
releases, the numbers docs/change-control.md publishes.

    python scripts/change_corpus.py OLD_FOOTPRINTS OLD_SYMBOLS NEW_FOOTPRINTS NEW_SYMBOLS -o out/change_corpus [--sample 50]

The folders are kicad-footprints / kicad-symbols checkouts or release tarballs, unpacked (9.x keeps a library in one
Lib.kicad_sym, 10.x in Lib.kicad_symdir/NAME.kicad_sym: both are read). Writes OUT.json (every change, per part) and
OUT.md (counts per class, every identity / electrical part, and a seeded sample to check by hand).

Every part the classifier calls less than electrical is also read a second, independent way (raw pin number, name, type
and visibility; raw pad numbers, positions and sizes): a part where that read differs but the classifier found nothing
electrical (or nothing geometry-major for a pad) is listed as a possible miss, to check by hand."""
import argparse, json, random, sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecadlib import change_diff as CD, change_gate as CG, fingerprint as FP, sexpr  # noqa: E402


def footprints(root):
    return {f"{p.parent.stem}:{p.stem}": p for p in Path(root).glob("*.pretty/*.kicad_mod")}


def symbols(root):
    """{Lib:Name: (file, name)} over both layouts."""
    out = {}
    for f in Path(root).glob("*.kicad_sym"):
        for s in sexpr.find(sexpr.parse(f.read_text(encoding="utf-8")), "symbol"):
            out[f"{f.stem}:{s[1]}"] = (f, str(s[1]))
    for f in Path(root).glob("*.kicad_symdir/*.kicad_sym"):
        out[f"{f.parent.stem}:{f.stem}"] = (f, f.stem)
    return out


def _fp_one(args):
    key, a, b = args
    try:
        fa, fb = (FP.build(key, None, *FP.kicad_footprint(Path(p).read_text(encoding="utf-8")), tool="kicad",
                           footprint_ref=key.split(":", 1)[1]) for p in (a, b))
        return key, [{"class": c["class"], "text": c["text"]} for c in CD.diff(fa, fb)], None
    except Exception as e:                     # an unreadable file is a finding too
        return key, None, f"{type(e).__name__}: {e}"


_LIBS = {}


def _sym_fp(f, name):
    f = Path(f)
    if f.parent.suffix == ".kicad_symdir":
        sym, props = FP.kicad_sym_file(f, name)
    else:                                      # one parse per library file per worker
        if f not in _LIBS:
            _LIBS[f] = {str(s[1]): s for s in sexpr.find(sexpr.parse(f.read_text(encoding="utf-8")), "symbol")}
        syms, chain = _LIBS[f], [_LIBS[f][name]]
        while sexpr.first(chain[-1], "extends") and len(chain) < 8:
            chain.append(syms[str(sexpr.first(chain[-1], "extends")[1])])
        sym, props = FP.kicad_symbol(chain)
    params = {k: v for k, v in props.items() if k not in ("Reference", "Footprint", "ki_fp_filters", "ki_keywords")}
    return FP.build(name, sym, params=params, tool="kicad")


def _sym_one(args):
    key, a, b = args
    try:
        return key, [{"class": c["class"], "text": c["text"]} for c in CD.diff(_sym_fp(*a), _sym_fp(*b))], None
    except Exception as e:
        return key, None, f"{type(e).__name__}: {e}"


def _raw(v):
    v = str(v)
    return "" if v == "~" else v


def _pins_raw(chain):
    """{number: [(name, type, hidden)]} straight from the file: the base symbol of an `extends` chain, normal view."""
    out = {}
    for u in sexpr.find(chain[-1], "symbol"):
        tail = str(u[1]).rsplit("_", 2)
        if len(tail) == 3 and tail[-1].isdigit() and int(tail[-1]) > 1:
            continue
        for g in u[2:]:
            if isinstance(g, list) and g and g[0] == "pin":
                hide = "hide" in g[1:] or bool(sexpr.first(g, "hide") and str(sexpr.first(g, "hide")[1]) == "yes")
                num = _raw((sexpr.first(g, "number") or ["", ""])[1])
                out.setdefault(num, []).append((_raw((sexpr.first(g, "name") or ["", ""])[1]).upper(), str(g[1]), hide))
    return {k: sorted(v) for k, v in out.items()}


def _sym_chain(f, name):
    f = Path(f)
    if f not in _LIBS:
        _LIBS[f] = {str(s[1]): s for s in sexpr.find(sexpr.parse(f.read_text(encoding="utf-8")), "symbol")}
    syms = _LIBS[f]
    chain = [syms[name]]
    while sexpr.first(chain[-1], "extends") and len(chain) < 8:
        parent = str(sexpr.first(chain[-1], "extends")[1])
        if parent not in syms:                 # 10.x: the parent is its own file in the same .kicad_symdir
            for s in sexpr.find(sexpr.parse((f.parent / f"{parent}.kicad_sym").read_text(encoding="utf-8")), "symbol"):
                syms[str(s[1])] = s
        chain.append(syms[parent])
    return chain


def _pads_raw(path):
    """[(number, type, x, y, w, h, rotation)] straight from a .kicad_mod, to 1 um."""
    out = []
    for g in sexpr.find(sexpr.parse(Path(path).read_text(encoding="utf-8")), "pad"):
        at, size = sexpr.first(g, "at") or ["at", 0, 0], sexpr.first(g, "size") or ["size", 0, 0]
        out.append((str(g[1]), str(g[2]), round(float(at[1]), 3), round(float(at[2]), 3), round(float(size[1]), 3),
                    round(float(size[2]), 3), round(float(at[3]) % 360, 1) if len(at) > 3 else 0.0))
    return sorted(out)


def _cross_one(args):
    """A possible miss for one part, or None: the independent read differs where the classifier saw nothing."""
    kind, key, a, b, classes = args
    try:
        if kind == "symbols":
            pa, pb = _pins_raw(_sym_chain(*a)), _pins_raw(_sym_chain(*b))
            if pa != pb and not {"identity", "electrical"} & classes:
                diff = sorted(k for k in set(pa) | set(pb) if pa.get(k) != pb.get(k))
                return key, "pins differ: " + "; ".join(f"{k}: {pa.get(k)} -> {pb.get(k)}" for k in diff[:6])
        else:
            pa, pb = _pads_raw(a), _pads_raw(b)
            if sorted(p[0] for p in pa) != sorted(p[0] for p in pb) and not {"identity", "electrical"} & classes:
                return key, "pad numbers differ"
            if pa != pb and not {"identity", "electrical", "geometry-major"} & classes:
                return key, "pads differ: " + "; ".join(str(x) for x in sorted(set(pa) ^ set(pb))[:4])
    except Exception as e:
        return key, f"cross-check unreadable: {type(e).__name__}: {e}"
    return None


def run(old_fp, old_sym, new_fp, new_sym, jobs=None):
    res = {"footprints": {}, "symbols": {}, "errors": {}, "only_old": {}, "only_new": {}}
    for kind, lister, one, o, n in (("footprints", footprints, _fp_one, old_fp, new_fp),
                                    ("symbols", symbols, _sym_one, old_sym, new_sym)):
        a, b = lister(o), lister(n)
        res["only_old"][kind], res["only_new"][kind] = sorted(set(a) - set(b)), sorted(set(b) - set(a))
        todo = [(k, a[k], b[k]) for k in sorted(set(a) & set(b))]
        res[f"{kind}_compared"] = len(todo)
        with ProcessPoolExecutor(jobs) as ex:
            for key, ch, err in ex.map(one, todo, chunksize=64):
                if err:
                    res["errors"][f"{kind}:{key}"] = err
                elif ch:
                    res[kind][key] = ch
        res.setdefault("possible_misses", {})[kind] = {}
        cross = [(kind, k, a[k], b[k], {c["class"] for c in res[kind].get(k, [])}) for k, _, _ in todo]
        with ProcessPoolExecutor(jobs) as ex:
            for hit in ex.map(_cross_one, cross, chunksize=64):
                if hit:
                    res["possible_misses"][kind][hit[0]] = hit[1]
    return res


def summarise(res, sample=50, seed=105):
    lines = ["# Change-control corpus run", ""]
    pol = CG.POLICY
    for kind in ("footprints", "symbols"):
        worst = Counter(min((c["class"] for c in ch), key=CD.RANK.get) for ch in res[kind].values())
        held = sum(CG.decide(ch, pol)["action"] == "hold" for ch in res[kind].values())
        lines += [f"## {kind.capitalize()}", "",
                  f"{res[f'{kind}_compared']} in both releases, {len(res[kind])} changed, {held} held under the default policy; "
                  f"{len(res['only_old'][kind])} removed, {len(res['only_new'][kind])} added (new or renamed parts are "
                  "new parts: nothing released to compare).", "",
                  "| Worst class | Parts |", "|---|---|"] + [f"| {c} | {worst.get(c, 0)} |" for c in CD.CLASSES] + [""]
    for kind in ("symbols", "footprints"):
        miss = res.get("possible_misses", {}).get(kind, {})
        lines += [f"## Independent cross-check, {kind}: {len(miss)} possible misses", "",
                  f"Every {kind[:-1]} read again from the raw file ({'pin number, name, type, visibility' if kind == 'symbols' else 'pad number, type, position, size, rotation'}); "
                  "listed where that read differs but the classifier reported no electrical"
                  f"{'' if kind == 'symbols' else ' or geometry-major'} change.", ""]
        lines += [f"- `{k}`: {v}"[:400] for k, v in sorted(miss.items())[:200]] + [""]
    errs = res["errors"]
    lines += [f"Unreadable: {len(errs)}", ""] + [f"- `{k}`: {v}" for k, v in sorted(errs.items())[:40]] + [""]
    for kind in ("symbols", "footprints"):
        hot = {k: ch for k, ch in res[kind].items() if any(c["class"] in ("identity", "electrical") for c in ch)}
        lines += [f"## Every {kind[:-1]} with an identity or electrical change ({len(hot)})", ""]
        for k, ch in sorted(hot.items()):
            lines.append(f"- `{k}`: " + "; ".join(c["text"] for c in ch if c["class"] in ("identity", "electrical"))[:400])
        lines.append("")
    rnd = random.Random(seed)
    pool = [(kind, k) for kind in ("symbols", "footprints") for k in sorted(res[kind])]
    pick = rnd.sample(pool, min(sample, len(pool)))
    lines += [f"## Hand-check sample ({len(pick)} changed parts, seed {seed})", "",
              "| Part | Classified | Changes (first 4) |", "|---|---|---|"]
    for kind, k in pick:
        ch = res[kind][k]
        lines.append(f"| `{k}` | {CD.worst(ch)} ({CG.decide(ch, pol)['action']}) | "
                     + "; ".join(c["text"] for c in ch[:4]).replace("|", "/")[:300] + " |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("old_footprints"); ap.add_argument("old_symbols")
    ap.add_argument("new_footprints"); ap.add_argument("new_symbols")
    ap.add_argument("-o", "--out", default="out/change_corpus")
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("-j", "--jobs", type=int)
    a = ap.parse_args(argv)
    res = run(a.old_footprints, a.old_symbols, a.new_footprints, a.new_symbols, a.jobs)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(res, indent=1, sort_keys=True), encoding="utf-8")
    out.with_suffix(".md").write_text(summarise(res, a.sample), encoding="utf-8")
    print(f"-> {out.with_suffix('.md')} and .json")


if __name__ == "__main__":
    main()
