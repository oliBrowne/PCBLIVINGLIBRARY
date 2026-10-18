"""Golden output snapshots: normalised fingerprints of the KiCad and Altium files ecadlib writes (`ecadlib eval golden`).

    ecadlib eval golden                  rebuild the parts and compare with ecadlib/eval/golden/*.txt (exit 1 + diff on a change)
    ecadlib eval golden --update         rewrite the snapshots (commit them: a deliberate change is one reviewed diff)
    ecadlib eval golden --group specs    one group only (testpack, demo, specs)

Groups (all offline, no API key):
  testpack  the six parts of `ecadlib testpack` (librarian `standard` + the pack's KiCad library, STEP bodies on)
  demo      the bundled demo BOM through Workspace.librarian_run + kicad_dblib (`ecadlib demo`)
  specs     footprint_gen / footprint_adv on every package_specs/*.json, advanced/*.json and every 20th entry of the
            standard table, written the librarian's way (kicad_import.convert_footprint -> altium_lib.write_pcblib)

A fingerprint is text, one line per thing that matters, so a diff reads like a review: footprint properties, every pad
(number, type, shape, position, size, drill, layers, rotation), graphics per layer as count + bounding box + hash,
3D model; symbol fields and every pin (number, name, type, position, length); Altium pads in mm and every Altium
parameter; DbLib / database-library rows. Left out, because they are not geometry or fields: uuids / tstamps,
generator stamps, Altium UNIQUEIDs, GUIDs, MODELID / MODEL.CHECKSUM, file-header DATE / TIME (Altium files are read
back with altium_lib's reader, so the OLE container's timestamps never enter). Numbers are rounded to 4 decimals."""
import argparse, csv, difflib, hashlib, io, json, os, re, sqlite3, sys, tempfile
from pathlib import Path

HERE = Path(__file__).parent / "golden"
GROUPS = ("testpack", "demo", "specs")
SPEC_STRIDE = 20                                     # every 20th standard-table entry (sorted names): 31 of 615
DROP_HEADS = {"uuid", "tstamp", "generator", "generator_version"}
GRAPHICS = {"fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve", "rectangle", "polyline", "circle", "arc",
            "bezier", "gr_line", "gr_rect", "gr_circle", "gr_arc", "gr_poly"}
VOLATILE = re.compile(r"(UNIQUEID|GUID|MODELID|CHECKSUM|^DATE$|^TIME$)", re.I)
NUM = re.compile(r"^-?\d+(\.\d+)?(e-?\d+)?$")
BARE = re.compile(r"^[A-Za-z0-9_.*&+:/-]+$")


# ---------------------------------------------------------------- normalising
def n(v):
    """A number as short stable text (4 decimals, no trailing zeros, no -0)."""
    x = round(float(v), 4)
    if x == 0:
        x = 0.0
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s


def _atom(a):
    a = str(a)
    if NUM.match(a):
        return n(a)
    return a if BARE.match(a) else json.dumps(a)


def canon(node):
    """S-expression -> one line, uuids / tstamps dropped, numbers normalised."""
    if not isinstance(node, list):
        return _atom(node)
    return "(" + " ".join(canon(c) for c in node if not (isinstance(c, list) and c and c[0] in DROP_HEADS)) + ")"


def _points(node, out):
    if isinstance(node, list):
        if node and node[0] in ("start", "end", "mid", "center", "xy", "at") and len(node) >= 3:
            try:
                out.append((float(node[1]), float(node[2])))
            except ValueError:
                pass
        for c in node[1:]:
            _points(c, out)
    return out


def _layer(node):
    from .. import sexpr
    l = sexpr.first(node, "layer")
    return str(l[1]) if l and len(l) > 1 else "-"


def _summary(kind, items, layer_of):
    """Graphics of one kind per layer: count, bounding box, hash of their canonical text."""
    groups = {}
    for it in items:
        groups.setdefault(layer_of(it), []).append(canon(it) if isinstance(it, list) else it)
    out = []
    for lay, cs in sorted(groups.items()):
        pts = [p for it in items if layer_of(it) == lay for p in (_points(it, []) if isinstance(it, list) else [])]
        box = f" bbox({n(min(p[0] for p in pts))},{n(min(p[1] for p in pts))},{n(max(p[0] for p in pts))},{n(max(p[1] for p in pts))})" if pts else ""
        h = hashlib.sha256("\n".join(cs).encode()).hexdigest()[:10]
        out.append(f"graphics {kind} {lay}: {len(cs)}{box} #{h}")
    return out


MANY_PADS = 16          # more pads than this (BGAs, big QFPs): pin 1 in full, the rest grouped by everything but position


def _pads(pads):
    """pads: [(number, x, y, rest, full line)] -> lines. Few pads: every line. Many: pad 1 / A1 in full, then one line
    per kind (same rest: shape, size, drill, layers ...) with count, bounding box of the centres and a hash of the lines."""
    if len(pads) <= MANY_PADS:
        return [p[4] for p in pads]
    out = [p[4] for p in pads if p[0] in ("1", "A1")][:1]
    kinds = {}
    for p in pads:
        kinds.setdefault(p[3], []).append(p)
    for rest, ps in sorted(kinds.items()):
        h = hashlib.sha256("\n".join(p[4] for p in ps).encode()).hexdigest()[:10]
        out.append(f"pads x{len(ps)} {rest} centres bbox({n(min(p[1] for p in ps))},{n(min(p[2] for p in ps))},"
                   f"{n(max(p[1] for p in ps))},{n(max(p[2] for p in ps))}) #{h}")
    return out


def kicad_mod(text):
    from .. import sexpr
    t = sexpr.parse(text)
    lines, gfx, pads = [f"footprint {_atom(t[1])}"], {}, []
    for c in t[2:]:
        if not isinstance(c, list) or not c or c[0] in DROP_HEADS:
            continue
        if c[0] == "pad":
            at = sexpr.first(c, "at") or ["at", "0", "0"]
            rest = canon([x for x in c if not (isinstance(x, list) and x and x[0] == "at")][2:])
            pads.append((str(c[1]), float(at[1]), float(at[2]), rest, canon(c)))
        elif c[0] in GRAPHICS:
            gfx.setdefault(c[0], []).append(c)
        elif c[0] in ("property", "fp_text"):
            at = sexpr.first(c, "at")
            lines.append(f"{c[0]} " + " ".join(_atom(x) for x in c[1:] if not isinstance(x, list)) +
                         f" layer={_layer(c)}" + (f" at={canon(at)}" if at else "") +
                         (" hidden" if any(x == "hide" or (isinstance(x, list) and x[:2] == ["hide", "yes"]) for x in c) else ""))
        else:
            lines.append(canon(c))
    lines += _pads(pads)
    for k in sorted(gfx):
        lines += _summary(k, gfx[k], _layer)
    return lines


def kicad_sym(text):
    """-> {symbol name: lines}."""
    from .. import sexpr
    t = sexpr.parse(text)
    out = {}
    for s in sexpr.find(t, "symbol"):
        lines, gfx, pins = [], {}, []

        def walk(node, unit):
            for c in node[2:] if node is not s else node[2:]:
                if not isinstance(c, list) or not c:
                    continue
                if c[0] == "symbol":
                    walk(c, str(c[1]))
                elif c[0] == "pin":
                    nm, no, at, ln = sexpr.first(c, "name") or ["", ""], sexpr.first(c, "number") or ["", ""], \
                        sexpr.first(c, "at"), sexpr.first(c, "length") or ["", "?"]
                    hid = any(x == "hide" or (isinstance(x, list) and x[:2] == ["hide", "yes"]) for x in c)
                    pins.append(f"pin {_atom(no[1])} {_atom(nm[1])} {c[1]} {c[2]} unit={unit} at={canon(at)} length={_atom(ln[1])}"
                                + (" hidden" if hid else ""))
                elif c[0] in GRAPHICS or c[0] == "text":
                    gfx.setdefault(c[0], []).append([*c, ["layer", f"unit{unit}"]])
                elif c[0] == "property":
                    at = sexpr.first(c, "at")
                    hid = any(x == "hide" or (isinstance(x, list) and x[:2] == ["hide", "yes"]) for x in sexpr.first(c, "effects") or []) \
                        or any(isinstance(x, list) and x[:2] == ["hide", "yes"] for x in c)
                    lines.append(f"property {_atom(c[1])}={_atom(c[2])}" + (f" at={canon(at)}" if at else "") + (" hidden" if hid else ""))
                elif node is s:
                    lines.append(canon(c))
        walk(s, "0")
        for k in sorted(gfx):
            lines += _summary(k, gfx[k], _layer)
        out[str(s[1])] = lines + pins
    return out


def _mm(u):
    from .. import altium_lib as AL
    return n(AL.to_mm(u))


def _params(d):
    return " ".join(f"{k}={v}" for k, v in sorted(d.items()) if not VOLATILE.search(k))


def pcblib(path):
    """-> {footprint name: lines} from altium_lib's reader (records, not bytes)."""
    from .. import altium_lib as AL
    out = {}
    for fp in AL.read_pcblib(path):
        lines, gfx, pads = [f"params {_params(fp['params'])}"], {}, []
        for r in fp["records"]:
            t = r["type"]
            if t == "pad":
                num = r["designator"] or '""'
                rest = (f"layer={r['layer']} size=({_mm(r['w'])},{_mm(r['h'])}) hole={_mm(r['hole'])} shape={r['shape']} "
                        f"rot={n(r['rot'])} corner={r['corner']} plated={r['plated']} paste={r['paste_mode']}/{_mm(r['paste'] or 0)} "
                        f"mask={r['mask_mode']}/{_mm(r['mask'] or 0)} hole_shape={r['hole_shape']} slot={_mm(r['slot'])} "
                        f"hole_rot={n(r['hole_rot'])}")
                pads.append((r["designator"], AL.to_mm(r["x"]), AL.to_mm(r["y"]), rest, f"pad {num} at=({_mm(r['x'])},{_mm(r['y'])}) {rest}"))
            elif t == "text":
                lines.append(f"text {json.dumps(r['text'])} layer={r['layer']} at=({_mm(r['x'])},{_mm(r['y'])}) h={_mm(r['h'])} "
                             f"w={_mm(r['w'])} rot={n(r['rot'])} mirror={r['mirror']}")
            elif t == "body":
                p = "|".join(x for x in r["params"].split("|") if not VOLATILE.search(x.split("=")[0]))
                lines.append(f"body layer={r['layer']} {p} pts=" + ";".join(f"{_mm(x)},{_mm(y)}" for x, y in r["pts"]))
            else:
                if t in ("track", "fill"):
                    pts, txt = [(r["x1"], r["y1"]), (r["x2"], r["y2"])], f"{t} {r['x1']} {r['y1']} {r['x2']} {r['y2']} {r.get('w')}"
                elif t == "arc":
                    pts = [(r["cx"] - r["r"], r["cy"] - r["r"]), (r["cx"] + r["r"], r["cy"] + r["r"])]
                    txt = f"arc {r['cx']} {r['cy']} {r['r']} {n(r['a0'])} {n(r['a1'])} {r['w']}"
                elif t == "region":
                    pts, txt = list(r["pts"]), f"region {r['params']} " + ";".join(f"{n(x)},{n(y)}" for x, y in r["pts"])
                else:
                    pts, txt = [], f"{t} {r['raw'][0].hex()}"
                gfx.setdefault((t, r["layer"]), []).append((pts, txt))
        lines += _pads(pads)
        for (t, lay), items in sorted(gfx.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            pts = [p for ps, _ in items for p in ps]
            box = f" bbox({_mm(min(p[0] for p in pts))},{_mm(min(p[1] for p in pts))},{_mm(max(p[0] for p in pts))},{_mm(max(p[1] for p in pts))})" if pts else ""
            lines.append(f"graphics {t} {lay}: {len(items)}{box} #{hashlib.sha256(chr(10).join(x for _, x in items).encode()).hexdigest()[:10]}")
        for prm, data in fp.get("models") or []:
            lines.append(f"model {_params(prm)} bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()[:16]}")
        out[fp["name"]] = lines
    return out


def schlib(path):
    """-> {symbol name: lines}: component fields, every pin, graphics as count + hash."""
    from .. import altium_lib as AL
    _, syms = AL.read_schlib(path)
    out = {}
    for s in syms:
        lines, gfx = [], {}
        for r in s["records"]:
            rec = r.get("RECORD")
            if rec == "2" and "num" in r:
                lines.append(f"pin {r['num']} {json.dumps(r['name'])} etype={r['etype']} orient={r['orient']} at=({r['x']},{r['y']}) "
                             f"length={r['length']} part={r['part']} mode={r['mode']} symbols={list(r['symbols'])} cong={r['conglomerate']}")
            elif rec in ("1", "34", "41", "45", "44", "46", "48"):
                lines.append(f"record{rec} " + _params({k: v for k, v in r.items() if k not in ("RECORD", "INDEXINSHEET")}))
            else:
                gfx.setdefault(rec, []).append(_params({k: v for k, v in r.items() if k not in ("INDEXINSHEET",)}))
        for rec, items in sorted(gfx.items(), key=lambda kv: str(kv[0])):
            lines.append(f"graphics record{rec}: {len(items)} #{hashlib.sha256(chr(10).join(items).encode()).hexdigest()[:10]}")
        out[s["name"]] = lines
    return out


def table_rows(path):
    """A CSV (DbLib table, parts export) or SQLite file -> sorted rows as lines."""
    path = Path(path)
    if path.suffix == ".sqlite":
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            out = []
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                cur = con.execute(f'SELECT * FROM "{t}"')
                cols = [d[0] for d in cur.description]
                out.append(f"table {t}: " + ",".join(cols))
                out += sorted(f"row {t}: " + json.dumps(list(r), ensure_ascii=False) for r in cur)
            return out
        finally:
            con.close()
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    return ["header " + json.dumps(rows[0], ensure_ascii=False)] + sorted("row " + json.dumps(r, ensure_ascii=False) for r in rows[1:]) if rows else []


def fingerprint_file(path, rel):
    """One output file -> {key: lines} (keys carry the relative path)."""
    p = Path(path)
    if p.suffix == ".kicad_mod":
        return {rel: kicad_mod(p.read_text(encoding="utf-8"))}
    if p.suffix == ".kicad_sym":
        return {f"{rel}:{k}": v for k, v in kicad_sym(p.read_text(encoding="utf-8")).items()}
    if p.suffix == ".PcbLib":
        return {f"{rel}:{k}": v for k, v in pcblib(p).items()}
    if p.suffix == ".SchLib":
        return {f"{rel}:{k}": v for k, v in schlib(p).items()}
    if p.suffix in (".csv", ".sqlite"):
        return {rel: table_rows(p)}
    raise ValueError(f"no fingerprint for {p.suffix}")


def fingerprint_tree(root, patterns):
    root, out = Path(root), {}
    for pat in patterns:
        for f in sorted(root.glob(pat)):
            out.update(fingerprint_file(f, f.relative_to(root).as_posix()))
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- the groups
def _quiet():
    return lambda *a, **k: None


def build_testpack(tmp):
    from .. import testpack
    out = Path(tmp) / "testpack"
    testpack.build(out, log=_quiet())
    return fingerprint_tree(out, ["library/models/KiCad/*.kicad_mod", "library/models/PcbLib/*.PcbLib",
                                  "library/models/SchLib/*.SchLib", "kicad/*.kicad_sym", "kicad/*.sqlite", "altium/*.csv"])


def build_demo(tmp):
    from .. import firstrun
    from ..app import Workspace
    root = Path(tmp) / "demo"
    firstrun.build_demo(Workspace(root, offline=True), "both", log=_quiet())
    return fingerprint_tree(root, ["librarian/*/models/KiCad/*.kicad_mod", "librarian/*/models/PcbLib/*.PcbLib",
                                   "librarian/*/models/SchLib/*.SchLib", "kicad/*/*.kicad_sym", "kicad/*/*.sqlite"])


def spec_names():
    from .. import footprint_bench as FB, stdparts
    own = sorted(p.stem for p in FB.SPECS.glob("*.json")) + sorted(p.stem for p in (FB.SPECS / "advanced").glob("*.json"))
    std = sorted(stdparts.packages())[::SPEC_STRIDE]
    return [("spec", x) for x in own] + [("standard", x) for x in std if x not in own]


def build_specs(tmp):
    from .. import altium_lib as AL, footprint_bench as FB, footprint_gen as F, kicad_import as K, rules as RU, stdparts
    out, d = {}, Path(tmp) / "specs"
    d.mkdir(parents=True, exist_ok=True)
    layers = RU.layers(RU.load({}))
    for kind, name in spec_names():
        spec = json.loads(FB.spec_path(name).read_text(encoding="utf-8")) if kind == "spec" else stdparts.spec(name)
        try:
            text, _ = F.build(spec)
        except Exception as e:                      # a spec the generator rejects is part of the snapshot too
            out[f"{kind}/{name}"] = [f"build failed: {type(e).__name__}: {e}"]
            continue
        out[f"{kind}/{name}.kicad_mod"] = kicad_mod(text)
        fp, t = K.convert_footprint(text, name, kind, [])
        try:
            K.add_body(fp, t, None, None, False, [], gen_step=False, spec=spec)
        except ValueError:                          # no height known: no body (as the librarian writes it)
            pass
        pcb = d / f"{AL.file_stem(name)}.PcbLib"
        AL.write_pcblib(pcb, [fp], layers)
        out.update({f"{kind}/{name}.PcbLib:{k}": v for k, v in pcblib(pcb).items()})
    return dict(sorted(out.items()))


BUILDERS = {"testpack": build_testpack, "demo": build_demo, "specs": build_specs}


def build(group):
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env["ECADLIB_NO_STORED_KEYS"] = "1"                # no key stored with `ecadlib keys set` either: never a paid call
    from unittest import mock
    with tempfile.TemporaryDirectory(prefix="ecadlib-golden-") as tmp, mock.patch.dict(os.environ, env, clear=True):
        cwd = os.getcwd()
        os.chdir(tmp)                                  # no ecadlib.toml of the caller's folder can change the outputs
        try:
            return BUILDERS[group](tmp)
        finally:
            os.chdir(cwd)


# ---------------------------------------------------------------- files, diff
def dumps(fps):
    out = []
    for k, lines in fps.items():
        out.append(f"### {k}")
        out += lines
    return "\n".join(out) + "\n"


def loads(text):
    fps, cur = {}, None
    for line in text.splitlines():
        if line.startswith("### "):
            cur = fps.setdefault(line[4:], [])
        elif cur is not None:
            cur.append(line)
    return fps


def path(group, d=None):
    return Path(d or HERE) / f"{group}.txt"


def diff(old, new, limit=80):
    """-> readable text: items added / removed, and a unified diff of each changed item (first `limit` lines)."""
    out = []
    for k in sorted(set(old) - set(new)):
        out.append(f"- removed: {k}")
    for k in sorted(set(new) - set(old)):
        out.append(f"+ added: {k}")
    for k in sorted(set(old) & set(new)):
        if old[k] != new[k]:
            out.append(f"~ changed: {k}")
            out += ["    " + l for l in list(difflib.unified_diff(old[k], new[k], "golden", "now", n=0, lineterm=""))[2:]]
    if len(out) > limit:
        out = out[:limit] + [f"... {len(out) - limit} more lines"]
    return "\n".join(out)


def check(groups=GROUPS, d=None):
    """-> {group: diff text ('' when the same)}."""
    res = {}
    for g in groups:
        f = path(g, d)
        old = loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        new = build(g)
        res[g] = diff(old, new) if old != new else ""
        if not f.exists():
            res[g] = f"no snapshot {f}: run `ecadlib eval golden --update`\n" + res[g]
    return res


def update(groups=GROUPS, d=None):
    out = {}
    for g in groups:
        f = path(g, d)
        f.parent.mkdir(parents=True, exist_ok=True)
        fps = build(g)
        f.write_text(dumps(fps), encoding="utf-8")
        out[g] = (f, len(fps))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib eval golden", description=__doc__.split("\n")[0])
    ap.add_argument("--update", action="store_true", help="rewrite the snapshots from this build")
    ap.add_argument("--group", help=f"comma list of {', '.join(GROUPS)} (default all)")
    ap.add_argument("--dir", help="snapshot folder (default ecadlib/eval/golden)")
    a = ap.parse_args(argv)
    groups = [g.strip() for g in a.group.split(",")] if a.group else list(GROUPS)
    bad = [g for g in groups if g not in GROUPS]
    if bad:
        ap.error(f"unknown group {bad[0]!r}; groups: {', '.join(GROUPS)}")
    if a.update:
        for g, (f, k) in update(groups, a.dir).items():
            print(f"{g}: {k} items -> {f}")
        print("review the diff (git diff ecadlib/eval/golden) and commit it with the change that caused it")
        return 0
    res, rc = check(groups, a.dir), 0
    for g, txt in res.items():
        if txt:
            rc = 1
            print(f"golden {g}: CHANGED\n{txt}")
        else:
            print(f"golden {g}: same")
    if rc:
        print("\nIf the change is deliberate: `ecadlib eval golden --update`, review `git diff ecadlib/eval/golden`, commit.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
