"""Edit a held part's datasheet footprint by hand: rename pads, join pads on one net. The check stays as strict as it is.

    ecadlib fp-edit PLAN --part P show                     the pad table as read, with the check's problems
    ecadlib fp-edit PLAN --part P rename OLD=NEW [...]     renumber pads (the symbol pin with that number follows)
    ecadlib fp-edit PLAN --part P same-net A,B[,C] [...]   pads on one net, which may overlap
    ecadlib fp-edit PLAN --part P undo                     drop this part's edits

A pad table the datasheet reader returned can fail the pad-table check (footprint_adv.check_pad_table) for reasons
a person can settle from the datasheet: a connector that names its pads T, R, S instead of 1, 2, 3 (every footprint
needs a pad 1 or A1, so a person picks which pad is 1), or two pads that touch because they are one net. The check
is never relaxed for that part; the edit changes the table it checks. Each command prints the result of the check on
the edited table, so `show` -> `rename` -> `show` is the loop.

Edits live in <plan dir>/footprint_edits.json ({part: {rename: {old: new}, same_net: [[..]], by, at}}) and are
applied every time the librarian builds the part from its datasheet answer (librarian.put_datasheet), so a re-run
of the librarian on the plan builds it. The edit is noted in the part's review notes, with who made it.
API: load(base), apply(edits, res) -> (res, notes), table(plan, part, edits) -> {pads, problems, ...}."""
import argparse, copy, datetime, getpass, json, re, sys
from pathlib import Path

from .fsutil import write_text_atomic

FILE = "footprint_edits.json"
_safe = lambda s: re.sub(r"[^A-Za-z0-9_.+-]", "_", s)


def _base(plan):
    p = Path(plan)
    return p.parent if p.suffix.lower() == ".json" else p


def load(base):
    f = Path(base) / FILE
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        return {}


def save(base, edits):
    write_text_atomic(Path(base) / FILE, json.dumps(edits, indent=2, sort_keys=True) + "\n")


def for_part(edits, *names):
    return next((edits[n] for n in names if n and n in edits), None)


_ARRAY_KEYS = ("count", "dx", "dy", "first_number", "number_step", "prefix", "x", "y")


def _array_pads(a):
    """One pad_table array as explicit pad rows (the numbers and positions footprint_adv._expand_table gives it)."""
    step, first = a.get("number_step", 1), a.get("first_number", 1)
    keep = {k: v for k, v in a.items() if k not in _ARRAY_KEYS}
    return [dict(keep, number=f"{a.get('prefix', '')}{first + k * step}", x=a["x"] + k * a.get("dx", 0),
                 y=a["y"] + k * a.get("dy", 0)) for k in range(a["count"])]


def _array_numbers(t):
    return {str(p["number"]) for a in t.get("arrays") or [] for p in _array_pads(a)}


def _expand_arrays(t, numbers):
    """Turn every array that makes a pad in `numbers` into explicit pads, so a rename reaches those pads too."""
    keep = []
    for a in t.get("arrays") or []:
        rows = _array_pads(a)
        if any(str(p["number"]) in numbers for p in rows):
            t.setdefault("pads", []).extend(rows)
        else:
            keep.append(a)
    if "arrays" in t:
        t["arrays"] = keep


def apply(edit, res):
    """A datasheet result {spec, symbol, ...} with one part's edit applied (a copy) -> (res, notes)."""
    if not edit:
        return res, []
    res = copy.deepcopy(res)
    ren = {str(k): str(v) for k, v in (edit.get("rename") or {}).items()}
    notes = []
    spec, sym = res["spec"], res.get("symbol") or {}
    if ren:
        t = spec.get("pad_table") or {}
        _expand_arrays(t, set(ren))
        have = {str(p.get("number")) for p in t.get("pads") or []} | _array_numbers(t)
        gone = [k for k in ren if k not in have]
        if gone and t:                                    # renaming only the symbol pin would cross pin and pad
            notes.append("rename skipped, no such pad: " + ", ".join(f"{k} -> {ren[k]}" for k in gone))
            ren = {k: v for k, v in ren.items() if k in have}
        for p in t.get("pads") or []:
            if str(p.get("number")) in ren:
                p["number"] = ren[str(p["number"])]
        for p in sym.get("pins") or []:
            if str(p.get("num")) in ren:
                p["num"] = ren[str(p["num"])]
        if spec.get("same_net"):
            spec["same_net"] = [[ren.get(str(n), str(n)) for n in g] for g in spec["same_net"]]
        if ren:
            notes.append("pads renamed by hand: " + ", ".join(f"{a} -> {b}" for a, b in ren.items()))
    if edit.get("same_net"):
        from .datasheet import same_net_from_pins
        spec["same_net"] = same_net_from_pins([], (spec.get("same_net") or []) + edit["same_net"])
        notes.append("pads on one net by hand: " + "; ".join(",".join(g) for g in edit["same_net"]))
    who = f" ({edit.get('by')}, {edit.get('at')})" if edit.get("by") else ""
    return res, [f"footprint edited: {n}{who}" for n in notes]


def read(plan, part):
    """The part's last datasheet answer in <plan dir>/datasheet -> {spec, symbol} (raises FileNotFoundError)."""
    d = _base(plan) / "datasheet"
    sym = None
    for f in (d / f"{_safe(part)}.json", d / f"{_safe(part)}.symbol.json"):
        try:
            v = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(v, dict) and "pins" in v and "package_type" not in v:
            sym = v
            break
    if sym is None:
        raise FileNotFoundError(f"no datasheet answer for {part} in {d} (only parts read from a datasheet can be edited)")
    fp = _safe(sym["footprint"])
    for f in (d / f"{fp}.{_safe(sym['name'])}.json", d / f"{fp}.json", d / f"{fp}.package.json"):
        try:
            v = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(v, dict) and "package_type" in v:
            return {"spec": v, "symbol": sym}
    raise FileNotFoundError(f"no package spec {sym['footprint']} for {part} in {d}")


def table(plan, part, edits=None):
    """The edited pad table and what the check says about it."""
    from . import footprint_adv as FA
    edits = load(_base(plan)) if edits is None else edits
    res, notes = apply(for_part(edits, part), read(plan, part))
    spec = res["spec"]
    out = {"part": part, "footprint": spec["name"], "edit": for_part(edits, part), "notes": notes, "pads": [],
           "same_net": spec.get("same_net") or [], "pins": {str(p["num"]): p.get("name", "") for p in res["symbol"]["pins"]}}
    if not spec.get("pad_table"):
        out["problems"] = [{"level": "error", "rule": "table", "message": "this footprint was not read as a pad table: "
                                                                           "nothing to edit here (see ecadlib resolve)"}]
        return out
    try:
        pads = FA.table_layout(spec)[0]
        FA.tht_pad_size(pads, spec.get("pitch"))
        out["pads"] = [{k: p.get(k) for k in ("num", "kind", "x", "y", "w", "h", "role", "_src")} for p in pads]
        out["problems"] = FA.check_pad_table(spec, pads)
    except Exception as e:                            # a table the layout itself rejects
        out["problems"] = [{"level": "error", "rule": "layout", "message": str(e)}]
    out["ok"] = not any(p["level"] == "error" for p in out["problems"])
    return out


def _print(t):
    print(f"{t['part']}: {t['footprint']}")
    for n in t["notes"]:
        print(f"  {n}")
    if t["pads"]:
        print(f"  {'pad':<6} {'pin name':<12} {'kind':<10} {'x':>8} {'y':>8} {'w':>6} {'h':>6}  from")
        for p in t["pads"]:
            print(f"  {p['num'] or '(hole)':<6} {t['pins'].get(p['num'], '')[:12]:<12} {p['kind']:<10} {p['x']:>8.3f} "
                  f"{p['y']:>8.3f} {p['w']:>6.3f} {p['h']:>6.3f}  {p['_src']}")
    if t["same_net"]:
        print("  same net: " + "; ".join(",".join(g) for g in t["same_net"]))
    errs = [p for p in t["problems"] if p["level"] == "error"]
    for p in t["problems"]:
        print(f"  {p['level']}: {p['message']}")
    print("  the pad table passes the check: run the librarian on the plan again to build the part" if not errs else
          f"  {len(errs)} error(s): fix with `rename OLD=NEW` (e.g. the datasheet's pin 1 -> 1) or `same-net A,B`")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib fp-edit", description=__doc__.split("\n")[0])
    ap.add_argument("plan", help="the librarian's plan.json (or its folder)")
    ap.add_argument("--part", required=True, help="plan item / MPN")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="the pad table and the check's problems")
    r = sub.add_parser("rename", help="renumber pads: OLD=NEW ...")
    r.add_argument("pairs", nargs="+", metavar="OLD=NEW")
    s = sub.add_parser("same-net", help="pads on one net: A,B[,C] ...")
    s.add_argument("groups", nargs="+", metavar="A,B")
    sub.add_parser("undo", help="drop this part's edits")
    a = ap.parse_args(argv)
    base = _base(a.plan)
    edits = load(base)
    try:
        read(a.plan, a.part)
    except FileNotFoundError as e:
        print(f"ecadlib fp-edit: {e}", file=sys.stderr)
        return 2
    if a.cmd != "show":
        e = dict(edits.get(a.part) or {})
        if a.cmd == "undo":
            edits.pop(a.part, None)
        else:
            if a.cmd == "rename":
                ren = dict(e.get("rename") or {})
                for pr in a.pairs:
                    old, eq, new = pr.partition("=")
                    if not eq or not old.strip() or not new.strip():
                        ap.error(f"rename {pr!r}: use OLD=NEW, e.g. T=1")
                    ren[old.strip()] = new.strip()
                e["rename"] = ren
            else:
                gs = [[x.strip() for x in g.split(",") if x.strip()] for g in a.groups]
                if any(len(g) < 2 for g in gs):
                    ap.error("same-net: give at least two pads per group, e.g. 1,26")
                e["same_net"] = (e.get("same_net") or []) + gs
            try:
                user = getpass.getuser()
            except Exception:
                user = "unknown"
            e.update(by=user, at=datetime.datetime.now().isoformat(timespec="seconds"))
            edits[a.part] = e
        save(base, edits)
    t = table(a.plan, a.part, edits)
    if a.json:
        print(json.dumps(t, indent=1))
    else:
        _print(t)
    return 0 if t.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
