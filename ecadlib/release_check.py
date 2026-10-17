"""Check every part of an altium365 plan before it goes to Altium 365; hold back what fails.

    python3 -m ecadlib.release_check out/librarian/plan.json [--allow PART ...] [--unallow PART ...] [-o REPORT_STEM]

Rules, per plan item:
  files     the .SchLib and .PcbLib exist and read back with altium_lib's reader (cannot be overridden)
  pins      every symbol pin has a pad with the same number and every pad a pin; unnumbered, NPTH and
            mounting pads (MP, MH, SH, MTG, ...) are allowed without a pin
  footprint footprint_check and the rule book (footprint_rules, docs/footprint-rulebook.md) on the item's .kicad_mod:
            every footprint_check error fails, and rule-book errors in FR.RELEASE_HOLDS (name, layers, type, annular
            ring, thermal vias, silkscreen text size / over pads, also on the PcbLib); a reversed diode / LED / polarized-capacitor pin map fails for every part. Parts taken from the official KiCad
            library are held only for overlapping pads, a missing courtyard and a missing pin 1 (their other
            findings are reported as warnings). Without a .kicad_mod the same three are checked on the PcbLib.
            Overlapping pads whose symbol pins share a name (USB-C A1/B12 = GND) are stacked on purpose: a warning.
  height    a 3D height is present and inside the plausible range for the package family (HEIGHTS)
  fields    Manufacturer, Manufacturer Part Number, a datasheet link and Description are filled in
  parity    the .kicad_mod and the .PcbLib / .SchLib read back the same (ecadlib.parity: pads, drills, paste, courtyard,
            3D, pin -> pad); `[release] parity = "warn"` reports instead of holding, "off" skips it
  verify    the item carries `verify` notes (e.g. a datasheet-reader footprint): a person has to look first
Failing items get status `held` and `hold_reasons`; others `ready`. `--allow PART` records an override in the
item (`allow.failures` = the failures it covers); an allowed item is `ready` while its failures stay within
that list. altium365 push releases only `ready` items. Items `ecadlib a365 pull` brought in from the workspace
(status `existing`) are already released there and are not checked. Exit code 0 when nothing is held, 2 otherwise."""
import argparse, csv, datetime, re, sys
from pathlib import Path

from . import altium_lib as AL, footprint_check as FC, footprint_rules as FR
from . import sheetsafe as SS
from .fsutil import write_text_atomic

# plausible overall height (mm) per package family: first regex (on footprint name) that matches wins
HEIGHTS = [
    (r"^LED_(0201|0402|0603|0805|1206)", 0.2, 1.4), (r"(^|[_-])01005(?!\d)", 0.1, 0.3), (r"(^|[_-])0201(?!\d)", 0.15, 0.45), (r"(^|[_-])0402(?!\d)", 0.2, 0.8),
    (r"(^|[_-])0603(?!\d)", 0.3, 1.1), (r"(^|[_-])0805(?!\d)", 0.35, 1.6), (r"(^|[_-])1206(?!\d)", 0.4, 2.0),
    (r"(^|[_-])(1210|1812|2010|2512)(?!\d)", 0.4, 3.0), (r"^CP_EIA", 0.8, 3.5),
    (r"^SOT-223", 1.4, 1.95), (r"^SOT-23", 0.8, 1.45), (r"^SOT-89", 1.3, 1.7), (r"^SOT-?(323|363|353|563|666)", 0.45, 1.1),
    (r"^(D_)?SOD-?(123|323|523|923)", 0.4, 1.4), (r"^(D_)?(SMA|SMB|SMC)\b", 1.9, 2.7),
    (r"^(TSSOP|MSOP|VSSOP|HTSSOP|TSOP)", 0.75, 1.25), (r"^SSOP", 1.2, 2.05), (r"^(SOIC|SOP|SO-)", 1.2, 1.85),
    (r"(QFN|DFN|[^S]SON|^SON|WSON|USON)", 0.3, 1.05), (r"^LQFP", 1.35, 1.7), (r"^TQFP", 1.0, 1.25), (r"QFP", 1.0, 4.1),
    (r"BGA", 0.4, 2.6), (r"^Crystal_SMD_(1612|2016|2520|3225|5032)", 0.35, 1.4), (r"^Crystal_SMD_HC49", 3.0, 5.0),
    (r"^USB_C_Receptacle", 2.0, 3.6), (r"^JST_SH", 1.8, 3.5), (r"^PinHeader_.*P1\.27mm", 2.0, 9.0),
    (r"^PinHeader_.*P2\.54mm", 2.5, 12.0), (r"^(ESP32|RF_Module)", 1.5, 4.0), (r"^LED_", 0.2, 6.0),
]
GENERIC_HEIGHT = (0.05, 30.0)
MECH_PAD = re.compile(r"(MP|MH|SH|MTG|MOUNT|MECH|NPTH|H)\d*", re.I)
REQUIRED = ["Manufacturer", "Manufacturer Part Number"]
UNFIXABLE = ("files",)                         # rules --allow cannot override


def height_range(fp_name):
    for rx, lo, hi in HEIGHTS:
        if re.search(rx, fp_name or "", re.I):
            return lo, hi, rx
    return (*GENERIC_HEIGHT, None)


def _mil(s):
    m = re.match(r"\s*(-?[\d.]+)\s*mil", str(s or ""))
    return float(m.group(1)) * 0.0254 if m else None


def _path(base, p):
    return (Path(base) / p) if p and not Path(p).is_absolute() else (Path(p) if p else None)


def _kicad_mod(item, base, fp_file):
    p = _path(base, item["footprint"].get("kicad_mod"))
    if p and p.exists():
        return p
    if fp_file:                                # <models>/KiCad/<name>.kicad_mod next to <models>/PcbLib
        q = fp_file.parent.parent / "KiCad" / f"{AL.file_stem(item['footprint']['ref'])}.kicad_mod"
        return q if q.exists() else None
    return None


def _pad_rects(pads):
    out = []
    for r in pads:
        w, h = AL.to_mm(r["w"]), AL.to_mm(r["h"])
        if round(r["rot"]) % 180 == 90:
            w, h = h, w
        out.append((r["designator"], AL.to_mm(r["x"]), AL.to_mm(r["y"]), w, h, r["rot"] % 90 != 0))
    return out


OVERLAP = re.compile(r"pads (\S+) and (\S+) overlap")


def _same_net(msg, pin_names):
    """True when an overlap message names two pads whose symbol pins share a name (e.g. USB-C A1/B12 = GND):
    such pads are stacked on purpose and are connected anyway."""
    m = OVERLAP.search(msg)
    if not m:
        return False
    a, b = (pin_names.get(n, "").strip() for n in m.groups())
    return bool(a) and a == b and a not in ("~", m.group(1), m.group(2))


def _pcblib_minimal(fp, pin_names=None, courtyard=AL.MECH15, pin1=None):
    """Overlap, courtyard and pin 1 from the PcbLib records (used when there is no .kicad_mod)."""
    fails, warns, pads = [], [], [r for r in fp["records"] if r["type"] == "pad"]
    rects = _pad_rects([p for p in pads if p["hole"] == 0 or p["plated"]])
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if a[0] and a[0] == b[0] or a[5] or b[5]:
                continue
            if abs(a[1] - b[1]) < (a[3] + b[3]) / 2 - 0.005 and abs(a[2] - b[2]) < (a[4] + b[4]) / 2 - 0.005:
                msg = f"pads {a[0]} and {b[0]} overlap"
                (warns if _same_net(msg, pin_names or {}) else fails).append(msg)
    if not any(r["layer"] == courtyard for r in fp["records"] if r["type"] in ("track", "arc", "region")):
        fails.append(f"no courtyard (Mechanical {courtyard - 56})")
    if not _has_pin1({p["designator"] for p in pads}, pin1):
        fails.append("no pin 1 pad")
    return fails, warns


def _has_pin1(nums, pin1=None):
    return bool(set(pin1 or ("1", "A1")) & set(nums))


def check_item(item, base=".", rules=None):
    """-> (failures [(rule, message)], warnings [str], info {height, ...}). rules (ecadlib.rules): required fields,
    accepted pin-1 names and the courtyard layer; None = ecadlib's own."""
    from . import rules as RU
    rules = rules or RU.DEFAULTS
    pin1 = rules["footprint"]["pin1"]
    fails, warns, info = [], [], {}
    add = lambda rule, msg: fails.append((rule, msg))
    sym_f, fp_f = _path(base, item["symbol"].get("file")), _path(base, item["footprint"].get("file"))
    syms = fps = None
    pin_names = {}
    for kind, f, reader in (("symbol", sym_f, AL.read_schlib), ("footprint", fp_f, AL.read_pcblib)):
        if not f:
            add("files", f"no {kind} model (.{'SchLib' if kind == 'symbol' else 'PcbLib'})")
        elif not f.exists():
            add("files", f"{kind} file missing: {f}")
        else:
            try:
                r = reader(f)
                r = r[1] if kind == "symbol" else r
                if not r:
                    raise ValueError("library is empty")
                if kind == "symbol":
                    syms = r
                else:
                    fps = r
            except Exception as e:             # unreadable: altium_lib's reader is the gate
                add("files", f"{kind} file does not read back ({type(e).__name__}: {e})")
    # ---- pins vs pads
    if syms and fps:
        pins = {r["num"] for s in syms for r in s["records"] if r.get("RECORD") == "2"}
        pin_names = {r["num"]: r.get("name", "") for s in syms for r in s["records"] if r.get("RECORD") == "2"}
        pads = [r for r in fps[0]["records"] if r["type"] == "pad"]
        if not pins:
            add("pins", "symbol has no pins")
        if not pads:
            add("pins", "footprint has no pads")
        grid = FC.mount_rx([p["designator"] for p in pads]) is FC.MOUNT_GRID     # BGA: H1, H2 .. are balls, not holes
        mech = {p["designator"] for p in pads if not p["designator"] or (p["hole"] and not p["plated"])
                or MECH_PAD.fullmatch(p["designator"]) and not (grid and re.fullmatch(r"H\d+", p["designator"]))}
        numbered = {p["designator"] for p in pads} - mech
        if pins - {p["designator"] for p in pads}:
            add("pins", f"symbol pins without a pad: {FC._ranges(pins - {p['designator'] for p in pads})}")
        if numbered - pins:
            add("pins", f"pads without a symbol pin: {FC._ranges(numbered - pins)}")
        if mech - {""}:
            warns.append(f"mechanical pads without a pin (allowed): {FC._ranges(mech - {''})}")
    # ---- footprint rules
    src = str((item.get("source") or {}).get("footprint") or "")
    kicad_lib = src.startswith("kicad:")
    kmod = _kicad_mod(item, base, fp_f) if fp_f else None
    if kmod:
        info["kicad_mod"] = str(kmod)
        text = kmod.read_text(encoding="utf-8")
        try:
            issues, nums = FC.check(text), {p["num"] for p in FC._pad_rects(FC.sexpr.parse(text))}
        except Exception as e:                 # checker crash: fall back to the PcbLib minimum below
            issues, nums, kmod = [], set(), None
            warns.append(f"footprint_check failed on {info.pop('kicad_mod')} ({type(e).__name__}: {e})")
        for lvl, rule, msg in issues:
            if rule == "pin1" and msg == "no pad numbered 1" and _has_pin1(nums, pin1):
                continue                       # A1 counts as pin 1 (connectors, BGAs)
            if rule == "copper" and "overlap" in msg and _same_net(msg, pin_names):
                warns.append(f"{lvl} {rule}: {msg} (same pin name '{pin_names[OVERLAP.search(msg).group(1)]}': stacked on purpose)")
                continue
            hard = lvl == "error" and (not kicad_lib or (rule == "copper" and "overlap" in msg) or
                                       (rule == "courtyard" and msg.startswith("no courtyard")) or (rule == "pin1" and "no pad" in msg))
            (add("footprint", f"{rule}: {msg}") if hard else warns.append(f"{lvl} {rule}: {msg}"))
        if kmod:                               # the rule book (footprint_rules): naming, layers, type, pin map ...
            try:
                book = FR.check(text, filename=kmod.name, symbol_pins=pin_names if syms else None, geometry=False)
            except Exception as e:             # a rule-book crash must not hide the other rules
                book = [("warning", "rulebook", f"footprint_rules failed ({type(e).__name__}: {e})")]
            for lvl, rule, msg in book:
                if rule == "connect":
                    continue                   # the pins rule above already compares the Altium symbol and footprint
                if fps and msg.startswith("in the Altium export"):
                    continue                   # the PcbLib itself is checked below
                hard = lvl == "error" and (rule == "pinmap" or (not kicad_lib and rule in FR.RELEASE_HOLDS))
                (add("footprint", f"{rule}: {msg}") if hard else warns.append(f"{lvl} {rule}: {msg}"))
    if not kmod and fps:
        mf, mw = _pcblib_minimal(fps[0], pin_names, RU.layer_id(rules["layers"]["courtyard"]), pin1)
        for m in mf:
            add("footprint", m)
        warns += [f"{m} (same pin name: stacked on purpose)" for m in mw]
        warns.append("no .kicad_mod: only overlap, courtyard and pin 1 checked (on the PcbLib)")
    if fps:                                    # silkscreen text as Altium shows it: size, over pads, over the body
        for lvl, msg in FR.pcblib_text_issues(fps[0]):
            hard = lvl == "error" and not kicad_lib
            (add("footprint", f"silktext: {msg}") if hard else warns.append(f"{lvl} silktext: {msg}"))
    # ---- KiCad and Altium read back the same (ecadlib.parity)
    mode = (rules.get("release") or {}).get("parity", "hold")
    if mode != "off" and kmod and fps:
        from . import parity as PY
        try:
            diffs = PY.release_diffs(text, fps[0], syms, rules)
        except Exception as e:                 # a parity crash must not hide the other rules
            diffs = [{"level": "warn", "msg": f"parity check failed ({type(e).__name__}: {e})"}]
        for d in diffs:
            (add("parity", d["msg"]) if d["level"] == "error" and mode == "hold" else warns.append(f"parity: {d['msg']}"))
        info["parity"] = "mismatch" if any(d["level"] == "error" for d in diffs) else "match"
    # ---- 3D height
    if fps:
        bodies = [AL.parse_params(r["params"]) for r in fps[0]["records"] if r["type"] == "body"]
        hs = [h for h in (_mil(b.get("OVERALLHEIGHT")) for b in bodies) if h]
        h = max(hs) if hs else _mil(fps[0]["params"].get("HEIGHT"))
        if not bodies and h:
            warns.append("height only as the footprint HEIGHT parameter; no 3D body")
        lo, hi, fam = height_range(item["footprint"].get("kicad") or item["footprint"].get("ref") or fps[0]["name"])
        info["height"] = h
        if not h:
            add("height", "no 3D height (no 3D body and no HEIGHT)")
        elif not lo - 0.01 <= h <= hi + 0.01:
            add("height", f"3D height {h:.2f} mm outside {lo}-{hi} mm for {fam or 'any package'}")
        elif not fam:
            warns.append(f"no height range for package '{item['footprint'].get('ref')}'; only checked {lo}-{hi} mm")
    # ---- fields
    par = {p["name"]: p["value"] for p in item.get("parameters", [])}
    for k in rules["parameters"]["required"]:         # default: REQUIRED, then the datasheet link and Description
        if k == "Datasheet":
            if not any(str(par.get(x, "")).strip() for x in ("ComponentLink2URL", "Datasheet", RU.canonical_param("Datasheet", rules))):
                add("fields", "no datasheet link")
        elif k == "Description":
            if not str(item.get("description") or "").strip():
                add("fields", "Description is empty")
        elif not str(par.get(RU.canonical_param(k, rules), par.get(k, ""))).strip():
            add("fields", f"{k} is empty")
    for v in item.get("verify") or []:
        add("verify", v)
    return fails, warns, info


def apply(items, base=".", allow=(), unallow=(), who="--allow", rules=None):
    """Run check_item on every item, set status ready / held, record --allow overrides. Returns report rows.
    rules: a loaded rules dict, a path, or None to use the ecadlib.toml found from `base` (defaults when none)."""
    from . import rules as RU
    rules = RU.resolve(rules, base)
    rows, today = [], datetime.date.today().isoformat()
    for it in items:
        if it.get("a365") and it.get("status") in ("existing", "deleted_in_workspace"):
            # pulled from Altium 365 (ecadlib a365 pull): already released there, nothing to release or hold back
            it["hold_reasons"], it["release_check"] = [], {"result": it["status"], "failures": [], "warnings": []}
            rows.append({"name": it["name"], "status": it["status"], "result": it["status"], "symbol_source": "a365",
                         "footprint_source": "a365", "height_mm": "", "failures": "", "warnings": "", "allowed": ""})
            continue
        fails, warns, info = check_item(it, base, rules)
        msgs = [f"{r}: {m}" for r, m in fails]
        if it["name"] in unallow:
            it.pop("allow", None)
        if it["name"] in allow:
            it["allow"] = {"by": who, "date": today, "failures": [m for m in msgs if not m.startswith(UNFIXABLE)]}
            for k in ("hold_reasons", "release_check"):     # the key order a librarian re-run writes: no plan.json churn
                if k in it:
                    it[k] = it.pop(k)
        covered = set((it.get("allow") or {}).get("failures", []))
        hard = [m for m in msgs if m.startswith(UNFIXABLE)]
        open_ = [m for m in msgs if m not in covered]
        if not msgs:
            it["status"], result = "ready", "pass"
        elif it.get("allow") and not open_:
            it["status"], result = "ready", "allowed"
        else:
            it["status"], result = "held", "held"
        it["hold_reasons"] = open_ if result == "held" else []
        if result == "held" and it.get("allow"):
            it["hold_reasons"] = [m + (" (cannot be overridden)" if m in hard else " (not covered by --allow)") for m in open_]
        it["release_check"] = {"result": result, "failures": msgs, "warnings": warns, **{k: v for k, v in info.items() if k in ("height", "parity")}}
        rows.append({"name": it["name"], "status": it["status"], "result": result,
                     "symbol_source": (it.get("source") or {}).get("symbol") or "", "footprint_source": (it.get("source") or {}).get("footprint") or "",
                     "height_mm": f"{info['height']:.2f}" if info.get("height") else "", "failures": "; ".join(msgs),
                     "warnings": "; ".join(warns), "allowed": "yes" if it.get("allow") else ""})
    from .approvals import post_check          # sign-off policy (approvals.json next to the plan) and dedupe status
    post_check(items, base, rows)
    return rows


def write_report(rows, stem, plan=""):
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    with open(stem.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        w = SS.DictWriter(f, fieldnames=list(rows[0]) if rows else ["name", "status"])
        w.writeheader(); w.writerows(rows)
    held = [r for r in rows if r["status"] == "held"]
    md = [f"# Release check{f' - `{plan}`' if plan else ''}", "",
          f"{len(rows)} parts: {len(rows) - len(held)} ready ({sum(r['result'] == 'allowed' for r in rows)} by --allow), {len(held)} held.", "",
          "| Part | Status | Symbol from | Footprint from | Height | Failures |", "|---|---|---|---|---|---|"]
    md += [f"| {r['name']} | {r['result']} | {r['symbol_source']} | {r['footprint_source']} | {r['height_mm']} | "
           f"{r['failures'].replace('|', '/') or '-'} |" for r in rows]
    warned = [r for r in rows if r["warnings"]]
    if warned:
        md += ["", "## Warnings (not held)", ""] + [f"- **{r['name']}**: {r['warnings']}" for r in warned]
    stem.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return stem


def main(argv=None):
    import json
    ap = argparse.ArgumentParser(description="Check every plan item before it goes to Altium 365; hold back what fails")
    ap.add_argument("plan")
    ap.add_argument("--allow", action="append", default=[], help="part name to release despite its current failures (repeatable)")
    ap.add_argument("--unallow", action="append", default=[], help="remove an earlier --allow")
    ap.add_argument("-o", "--out", help="report stem (.md and .csv); default <plan dir>/release_check")
    ap.add_argument("--user", help="who gives the --allow (default: git config user.name/email); see approvals")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml found from the plan's folder or here)")
    ap.add_argument("--json", action="store_true", help="print the result as JSON (plan, report, counts, rows)")
    a = ap.parse_args(argv)
    plan = Path(a.plan)
    from .approvals import cli_plan_lock
    with cli_plan_lock(plan):                  # the library agent's librarian may be rewriting this plan right now
        items = json.loads(plan.read_text(encoding="utf-8"))
        unknown = set(a.allow + a.unallow) - {i["name"] for i in items}
        if unknown:
            sys.exit(f"not in the plan: {', '.join(sorted(unknown))}")
        rows = apply(items, plan.parent, a.allow, a.unallow, rules=a.rules)
        if a.allow:                            # record the person, so approvals can refuse self-approval
            from .approvals import identity, post_check, stamp_allow
            stamp_allow(items, a.allow, identity(a.user, cwd=plan.parent))
            post_check(items, plan.parent, rows)
        write_text_atomic(plan, json.dumps(items, indent=2))
        stem = write_report(rows, a.out or plan.parent / "release_check", plan)
    held = sum(r["status"] == "held" for r in rows)
    if a.json:                                 # stable shape: README "Machine-readable output (--json)"
        st = ("ready", "held", "awaiting_approval", "duplicate")
        print(json.dumps({"plan": str(plan), "report": str(stem.with_suffix(".md")),
                          "counts": {s: sum(r["status"] == s for r in rows) for s in st}, "rows": rows}, indent=2, default=str))
        return 2 if held else 0
    for r in rows:
        print(f"  {r['result']:<8} {r['name']:<28} {r['failures'][:110]}")
    other = {s: sum(r["status"] == s for r in rows) for s in ("awaiting_approval", "duplicate")}   # approvals / dedupe
    print(f"{sum(r['status'] == 'ready' for r in rows)} ready, {held} held"
          + "".join(f", {n} {s.replace('_', ' ')}" for s, n in other.items() if n) + f" -> {plan}; report {stem.with_suffix('.md')}")
    return 2 if held else 0


if __name__ == "__main__":
    sys.exit(main())
