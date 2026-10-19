"""`ecadlib part`: see and decide changes to released parts (change control, #105).

    ecadlib part status   [PLAN|DIR] [--json]                  held changes waiting for a person, and recent releases
    ecadlib part show     PLAN PART [--json]                    one held change: every difference, its class, where used
    ecadlib part diff     A B [--plan PLAN] [--json]            classified difference of any two versions of a part
    ecadlib part history  PLAN PART [--json]                    every ledger line of a part: revisions, holds, publishes
    ecadlib part approve  PLAN --part P [--part ...] [-m WHY] [--user U]   release the held change (new revision)
    ecadlib part reject   PLAN --part P -m WHY [--user U]       keep the released version; the same change stays held
    ecadlib part revert   PLAN PART@REV [-m WHY] [--user U]     release an older revision again (as a new revision)
    ecadlib part fingerprint SPEC [SPEC ...] [--json]           the canonical fingerprint and its hashes

A or B in `part diff`: a file (X.PcbLib[:NAME], X.SchLib[:NAME], X.kicad_mod, Lib.kicad_sym[:NAME], several joined
with +), PLAN:PART (the part as it is now), PLAN:PART@3 (revision 3), PLAN:PART@head, PLAN:PART@proposed (the held
change); with --plan, PART@3 and PART@proposed are enough. The answer: each change with its class (identity,
electrical, geometry-major, geometry-minor, cosmetic) and what the [change] policy does with it.

approve: with [change] approvers = 2 a change needs two different people; the first approval is recorded and the
change stays held. The approved files go through the release check first; a version that fails it is not applied,
and that approval is not counted (`part status` shows "approval refused: release check").
Parts that share a file with the approved part (two resistors on one Resistor.SchLib) are approved with it.
Who you are: --user, else $ECADLIB_USER, else git config user.name / user.email. When the sign-off policy
(approvals.json / [release.approvals]) lists users, only they may approve. Exit codes: 0 done / nothing held,
1 held changes waiting (status), 2 refused or an error."""
import argparse, json, sys
from pathlib import Path

from . import change_diff as CD, change_gate as CG, fingerprint as FP, ledger as LG, term as TM

CLASS_MARK = {"identity": "ID", "electrical": "EL", "geometry-major": "G+", "geometry-minor": "g-", "cosmetic": "c "}


def _plan_path(p):
    p = Path(p or ".")
    if p.is_dir():
        p = p / "plan.json"
    return p


def _is_library(plan):
    """A plan.json, or the plan.json path of a kicad_import folder (its manifest.json lists the parts)."""
    return plan.is_file() or (plan.name == "plan.json" and (plan.parent / "manifest.json").is_file())


def _load(plan):
    """-> (plan.json path, the plan's items + the kicad_import parts no plan item uses: CG.library_items)"""
    plan = _plan_path(plan)
    if _is_library(plan):
        items, extra = CG.library_items(plan.parent)
        return plan, items + extra
    raise CG.ChangeError(f"{plan}: no plan.json (give the library's plan.json or its folder, or a kicad_import "
                         f"folder with a manifest.json)")


def _save(plan, items):
    if plan.is_file():                         # a kicad_import folder has no plan to write
        CG.write_plan(plan, items)


def _item(items, part):
    it = next((i for i in items if i["name"] == part), None)
    if it is None:
        raise CG.ChangeError(f"{part}: not a part of this plan")
    return it


# ---------------------------------------------------------------- resolving versions
def version(spec, plan=None):
    """A version spec -> (label, fingerprint)."""
    s = str(spec)
    if "+" in s and not Path(s).exists():
        parts = s.split("+")
        return s, FP.of_files(*parts)
    if FP.re.match(r".+\.(?:PcbLib|SchLib|kicad_mod|kicad_sym)(?::.+)?$", s, FP.re.I) and Path(s.split(":")[0] if not
                                                                                                   FP.re.match(r"^[A-Za-z]:\\", s) else s.rsplit(":", 1)[0]).exists():
        return s, FP.of_files(s)
    m = FP.re.match(r"(?:(.+\.json|.+?)[:])?([^:@]+)(?:@(\w+))?$", s)
    if not m:
        raise CG.ChangeError(f"{spec}: not a file, PLAN:PART, PART@REV or PLAN:PART@REV")
    pl, part, rev = m.group(1), m.group(2), m.group(3)
    plan_f = _plan_path(pl or plan) if (pl or plan) else None
    if not plan_f or not _is_library(plan_f):
        raise CG.ChangeError(f"{spec}: which library? PLAN:PART or --plan PLAN")
    lg = LG.Ledger(plan_f.parent)
    if rev is None:
        _, items = _load(plan_f)
        return f"{part} now", FP.of_item(_item(items, part), plan_f.parent)
    if rev == "proposed":
        prop = lg.proposal(part)
        if not prop:
            raise CG.ChangeError(f"{part}: no held change")
        return f"{part} proposed", lg.get_json(prop["fingerprint"])
    e = lg.head(part) if rev == "head" else lg.revision(part, rev)
    if not e:
        raise CG.ChangeError(f"{part}: no revision {rev} (see `ecadlib part history {plan_f} {part}`)")
    return f"{part}@{e['revision']}", lg.fingerprint(e)


# ---------------------------------------------------------------- printing
def print_changes(changes, pol=None, indent="  "):
    pol = pol or CG.POLICY
    for c in changes:
        act = pol[CG.CLASS_KEY[c["class"]]]
        print(f"{indent}{c['class']:<15} {act:<5} {c['text']}")


def _counts(c):
    return ", ".join(f"{n} {k}" for k, n in c.items()) or "no change"


# ---------------------------------------------------------------- commands
def cmd_status(a):
    folder = CG.lib_folder(_plan_path(a.plan))
    lg = LG.Ledger(folder)
    props = lg.proposals()
    pol = CG.policy(None, folder)
    if a.json:
        print(json.dumps({"library": str(folder), "ledger": str(lg.path) if lg.exists() else None, "parts": len(lg.parts()),
                          "held": [{"part": p["part"], "base": p["base"], "classes": p["classes"], "writer": p["writer"],
                                    "at": p["at"], "approvals": [x["who"] for x in p.get("approvals") or []],
                                    "summary": CD.summary(p["changes"]), "used_in": p.get("used_in") or [],
                                    "rejected_by": (p.get("rejected") or {}).get("by")} for p in props],
                          "policy": pol}, indent=1))
        return 1 if props else 0
    if not lg.exists():
        print(f"{folder}: no ledger yet; parts are recorded the first time they pass the release check")
        return 0
    print(f"{len(lg.parts())} released parts in {lg.path}")
    if not props:
        print("no held changes")
        return 0
    print(f"{len(props)} held change(s) waiting for a person (approvers needed: {pol['approvers']}):")
    for p in props:
        rej = f"  (rejected before by {p['rejected'].get('by')})" if p.get("rejected") else ""
        if p.get("refused"):
            rej += f"  (approval refused: release check, {p['refused'].get('by')})"
        got = len(p.get("approvals") or [])
        print(f"  {p['part']:<28} r{p['base']} -> proposed by {p['writer']}: {_counts(p['classes'])}{rej}"
              + (f"  [{got}/{pol['approvers']} approvals]" if got else ""))
        print(f"      {CD.summary(p['changes'])}")
        if p.get("used_in"):
            print(f"      used in {len(p['used_in'])}: {', '.join(p['used_in'][:4])}")
    plan = _plan_path(a.plan)
    print(f"see one: ecadlib part show {plan} PART    decide: ecadlib part approve|reject {plan} --part PART")
    return 1


def cmd_show(a):
    plan, items = _load(a.plan)
    lg = LG.Ledger(plan.parent)
    prop, head = lg.proposal(a.part), lg.head(a.part)
    pol = CG.policy(None, plan.parent)
    if a.json:
        print(json.dumps({"part": a.part, "head": head and {k: head.get(k) for k in ("revision", "at", "by", "decision", "eid",
                                                                                     "gid", "digest")},
                          "proposal": prop and {k: prop.get(k) for k in ("base", "writer", "by", "at", "changes", "classes",
                                                                         "why", "approvals", "used_in", "rejected", "refused",
                                                                         "digest")},
                          "used_in": [{"project": p, "revision": r} for p, r, _ in LG.used_in(plan.parent, a.part)]},
                         indent=1, default=str))
        return 0
    if not head:
        print(f"{a.part}: not released yet (no ledger revision)")
        return 0
    print(f"{a.part}: released revision {head['revision']} ({head.get('decision')}, {head.get('at')}, by {head.get('by')})")
    if not prop:
        print("no held change")
        return 0
    print(f"held change proposed by {prop['writer']} ({prop.get('by')}, {prop['at']}): {_counts(prop['classes'])}")
    for w in prop.get("why") or []:
        print(f"  policy: {w}")
    print_changes(prop["changes"], pol)
    used = LG.used_in(plan.parent, a.part)
    if used:
        print(f"used in {len(used)} project(s)/BOM(s): " + ", ".join(f"{p} (r{r})" if r else p for p, r, _ in used))
    if prop.get("approvals"):
        print("approved so far by: " + ", ".join(x["who"] for x in prop["approvals"]))
    if prop.get("rejected"):
        print(f"rejected before by {prop['rejected'].get('by')}: {prop['rejected'].get('reason') or ''}")
    if prop.get("refused"):
        print(f"approval by {prop['refused'].get('by')} refused: {prop['refused'].get('reason')}")
    print(f"diff again: ecadlib part diff {plan}:{a.part}@head {plan}:{a.part}@proposed")
    return 0


def cmd_diff(a):
    la, fa = version(a.a, a.plan)
    lb, fb = version(a.b, a.plan)
    changes = CD.diff(fa, fb)
    folder = CG.lib_folder(_plan_path(a.plan)) if a.plan else Path(".")
    pol = CG.policy(None, folder)
    dec = CG.decide(changes, pol)
    if a.json:
        print(json.dumps({"a": la, "b": lb, "hashes": {"a": fa["hashes"], "b": fb["hashes"]}, "changes": changes,
                          "classes": CD.counts(changes), "decision": dec}, indent=1, default=str))
        return 0
    same = [k for k in ("eid", "gid", "mid", "cid") if fa["hashes"][k] == fb["hashes"][k]]
    print(f"{la}  ->  {lb}")
    print(f"  same: {', '.join(same) or 'nothing'}" + (f"   differ: {', '.join(k for k in ('eid', 'gid', 'mid', 'cid') if k not in same)}"
                                                       if len(same) < 4 else ""))
    if not changes:
        print("  no change")
        return 0
    print_changes(changes, pol)
    print(f"  -> {dec['action']} under the [change] policy ({_counts(CD.counts(changes))})")
    return 0


def cmd_history(a):
    plan, _ = _load(a.plan)
    lg = LG.Ledger(plan.parent)
    lines = lg.history(a.part)
    if a.json:
        print(json.dumps([{k: v for k, v in e.items() if k not in ("item", "files", "fingerprint")} for e in lines], indent=1))
        return 0
    if not lines:
        print(f"{a.part}: no ledger lines")
        return 0
    for e in lines:
        ev = e.get("event")
        if ev in LG.RELEASES:
            what = f"r{e['revision']} {e.get('decision')}"
            detail = "; ".join(x for x in (_counts(e.get("classes") or {}) if e.get("changes") else "", e.get("reason")) if x)
        elif ev == "publish":
            what, detail = f"publish r{e.get('revision')}", f"to {e.get('target')}"
        else:
            what = ev
            detail = "; ".join(x for x in (_counts(e.get("classes") or {}) if ev == "hold" else "", e.get("reason")) if x)
        print(f"  {e.get('at', ''):<26} {what:<18} {e.get('writer') or '':<15} {e.get('by') or '':<30} {detail}")
        if ev in LG.RELEASES and e.get("changes") and (a.verbose or TM.VERBOSITY > 0):
            for c in e["changes"]:
                print(f"      {c['class']:<15} {c['text']}")
    return 0


def _group(lg, part):
    """The held proposal of `part` and every other proposal that shares one of its files (decided together)."""
    props = {p["part"]: p for p in lg.proposals()}
    if part not in props:
        raise CG.ChangeError(f"{part}: no held change (see `ecadlib part status`)")
    files = lambda p: {r["path"] for r in (p.get("files") or {}).values()} | {r["path"] for r in
                                                                               ((lg.head(p["part"]) or {}).get("files") or {}).values()}
    group, seen = [part], files(props[part])
    grew = True
    while grew:
        grew = False
        for n, p in props.items():
            if n not in group and files(p) & seen:
                group.append(n)
                seen |= files(p)
                grew = True
    return [props[n] for n in group]


def approve(plan, parts, who=None, comment="", role=None):
    """Record one approval of each held change (and the ones sharing its files); apply them when enough people
    approved. -> [{part, result, revision?, approvals, needed}]"""
    from . import approvals as AP, release_check as RC
    plan, items = _load(plan)
    folder = plan.parent
    lg = LG.Ledger(folder)
    pol = CG.policy(None, folder)
    who = who or AP.identity(None, cwd=folder)
    key = AP.user_key(who)
    sign = AP.load_policy(folder)
    users = (sign or {}).get("users") or {}
    if users and key not in users:
        raise CG.ChangeError(f"{who} is not in the sign-off policy's users ({AP.policy_source(folder, sign)})")
    groups, seen = [], set()
    for p in parts:
        if p in seen:
            continue
        g = _group(lg, p)
        seen |= {x["part"] for x in g}
        groups.append(g)
    out = []
    for g in groups:
        for prop in g:
            head = lg.head(prop["part"])
            if not head or head["revision"] != prop["base"]:
                lg.drop_proposal(prop["part"])
                raise CG.ChangeError(f"{prop['part']}: the released revision moved on since this change was proposed "
                                     f"(r{prop['base']} -> r{head and head['revision']}); run the writer again")
            if any(AP.user_key(x["who"]) == key for x in prop.get("approvals") or []):
                raise CG.ChangeError(f"{prop['part']}: {who} approved this change already"
                                     + (f"; it needs {pol['approvers']} different people" if pol["approvers"] > 1 else ""))
        final = len(g[0].get("approvals") or []) + 1 >= pol["approvers"]
        if final:                              # the last approval: the proposed files must pass the release check first
            try:
                _put_proposed(plan, items, g, lg)
            except CG.ChangeError as e:        # not recorded as an approval: the part stays held, and says why
                for prop in g:
                    prop["refused"] = {"by": who, "at": LG.now(), "reason": str(e)}
                    lg.append({"event": "refused", "part": prop["part"], "digest": prop["digest"], "by": who,
                               "base": prop["base"], "reason": str(e)})
                    lg.save_proposal(prop)
                    it = next((i for i in items if i["name"] == prop["part"]), None)
                    if it is not None:
                        it["change"] = CG.held_block(prop)
                _save(plan, items)
                raise
        for prop in g:
            prop.pop("refused", None)
            prop.setdefault("approvals", []).append({"who": who, "key": key, "when": LG.now(), "comment": comment or "",
                                                     **({"role": role} if role else {})})
            lg.append({"event": "approve", "part": prop["part"], "digest": prop["digest"], "by": who, "base": prop["base"],
                       **({"reason": comment} if comment else {})})
            lg.save_proposal(prop)
        if not final:
            for prop in g:
                it = next((i for i in items if i["name"] == prop["part"]), None)
                if it is not None:
                    it["change"] = CG.held_block(prop)
                out.append({"part": prop["part"], "result": "approval recorded", "approvals": len(prop["approvals"]),
                            "needed": pol["approvers"]})
            continue
        out += _apply_group(plan, items, g, who, comment, lg)
    _save(plan, items)
    return out


def _put_proposed(plan, items, group, lg):
    """Put the proposed files and fields in place and run the release check on them. A version that fails it is
    replaced by the released one again and raises ChangeError. -> the checked plan items."""
    from . import release_check as RC
    folder = plan.parent
    byname = {i["name"]: i for i in items}
    heads = {p["part"]: lg.head(p["part"]) for p in group}
    for p in group:
        lg.restore_files(p["files"], folder)
        if p["part"] in byname:
            LG.apply_content(byname[p["part"]], p["item"])
        if p.get("manifest"):                  # a held upstream reconversion: the manifest now records it
            from . import upstream as U
            mp = folder / p["manifest"]["path"]
            man = U.load_manifest(mp)
            man["entries"].update(p["manifest"]["entries"])
            U.save_manifest(mp, man)
    sel = [byname[p["part"]] for p in group if p["part"] in byname]
    checked = [i for i in sel if not i.get("_manifest")]   # kicad_import parts have no plan fields to check
    RC.apply(checked, folder)
    bad = [i for i in checked if i.get("status") == "held"]
    reasons = "; ".join(f"{i['name']}: {', '.join(i.get('hold_reasons') or [])}" for i in bad)
    if bad:                                    # the approved version fails the release check: put the released one back
        for p in group:
            lg.restore_files(heads[p["part"]].get("files"), folder)
            if p["part"] in byname:
                LG.apply_content(byname[p["part"]], heads[p["part"]].get("item") or {})
        RC.apply(checked, folder)
        for p in group:
            if p["part"] in byname:
                byname[p["part"]]["change"] = CG.held_block(p)
        raise CG.ChangeError(f"not applied: the proposed version fails the release check ({reasons}); the released "
                             f"version stays")
    return checked


def _apply_group(plan, items, group, who, comment, lg):
    folder = plan.parent
    byname = {i["name"]: i for i in items}         # the proposed version is in place: approve() put it there
    g = CG.Guard(folder, "approve", user=who)
    out = []
    for p in group:
        it = byname.get(p["part"])
        if it is None:
            continue
        fp = FP.of_item(it, folder)
        changes = p["changes"]
        row = g._release(it, fp, changes, {"action": "pass", "why": p.get("why") or []}, decision="approved",
                         reason=comment or "", approved_by=[x["who"] for x in p.get("approvals") or []], by=who)
        lg.drop_proposal(p["part"])
        it.pop("change", None)
        out.append({"part": p["part"], "result": "released", "revision": row["revision"], "approvals": len(p["approvals"]),
                    "needed": len(p["approvals"])})
    return out


def reject(plan, parts, who=None, comment=""):
    from . import approvals as AP
    if not (comment or "").strip():
        raise CG.ChangeError("a reject needs a reason (-m) saying what is wrong with the change")
    plan, items = _load(plan)
    lg = LG.Ledger(plan.parent)
    who = who or AP.identity(None, cwd=plan.parent)
    out, seen = [], set()
    for part in parts:
        for prop in _group(lg, part):
            if prop["part"] in seen:
                continue
            seen.add(prop["part"])
            lg.append({"event": "reject", "part": prop["part"], "digest": prop["digest"], "base": prop["base"], "by": who,
                       "reason": comment, "classes": prop.get("classes")})
            lg.drop_proposal(prop["part"])
            it = next((i for i in items if i["name"] == prop["part"]), None)
            if it is not None:
                it.pop("change", None)
            out.append({"part": prop["part"], "result": "rejected"})
    _save(plan, items)
    return out


def revert(plan, spec, who=None, comment=""):
    """Release revision REV of PART again as a new revision; parts sharing its files go back with it."""
    from . import approvals as AP, release_check as RC
    plan, items = _load(plan)
    folder = plan.parent
    if "@" not in spec:
        raise CG.ChangeError(f"{spec}: give PART@REV (see `ecadlib part history {plan} PART`)")
    part, rev = spec.rsplit("@", 1)
    lg = LG.Ledger(folder)
    target = lg.revision(part, rev)
    if not target:
        raise CG.ChangeError(f"{part}: no revision {rev}")
    head = lg.head(part)
    if head["digest"] == target["digest"]:
        raise CG.ChangeError(f"{part}: revision {target['revision']} is what is released now (r{head['revision']})")
    it = _item(items, part)
    who = who or AP.identity(None, cwd=folder)
    old_fp = lg.fingerprint(head)
    lg.restore_files(target["files"], folder)
    LG.apply_content(it, target.get("item") or {})
    written = {str((folder / r["path"]).resolve()) for r in (target.get("files") or {}).values()}
    others = [i for i in items if i is not it and {str((folder / p).resolve()) for p in LG.item_files(i).values()} & written]
    RC.apply([x for x in [it] + others if not x.get("_manifest")], folder)
    g = CG.Guard(folder, "revert", user=who)
    out = []
    for x in [it] + others:
        h = lg.head(x["name"])
        fp = FP.of_item(x, folder)
        if h and h.get("digest") == fp["hashes"]["digest"]:
            continue
        changes = CD.diff(old_fp if x is it else lg.fingerprint(h), fp) if h else []
        row = g._release(x, fp, changes, {"action": "pass", "why": []}, decision="revert",
                         reason=comment or f"revert to r{target['revision']}" + ("" if x is it else f" of {part}"),
                         approved_by=[who], event="revert", by=who)
        lg.drop_proposal(x["name"])
        x.pop("change", None)
        out.append({"part": x["name"], "result": "reverted", "revision": row["revision"], "to": target["revision"] if x is it else None})
    _save(plan, items)
    return out


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib part", description="Change control: held changes, diffs, history, approve / reject / revert")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="held changes waiting for a person")
    s.add_argument("plan", nargs="?", default=".")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("show", help="one part: its released revision and its held change")
    s.add_argument("plan"); s.add_argument("part")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("diff", help="classified difference of two versions of a part")
    s.add_argument("a"); s.add_argument("b")
    s.add_argument("--plan", help="the library for PART@REV specs")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("history", help="every ledger line of a part")
    s.add_argument("plan"); s.add_argument("part")
    s.add_argument("-v", "--verbose", action="store_true", help="list each change of each revision")
    s.add_argument("--json", action="store_true")
    for name in ("approve", "reject"):
        s = sub.add_parser(name, help=f"{name} a held change")
        s.add_argument("plan")
        s.add_argument("--part", action="append", default=[], required=True)
        s.add_argument("-m", "--comment", default="", help="why (required for reject)")
        s.add_argument("--user", help="who you are (default: $ECADLIB_USER, then git config)")
        if name == "approve":
            s.add_argument("--role", help="the role you sign as (sign-off policy)")
        s.add_argument("--json", action="store_true")
    s = sub.add_parser("revert", help="release an older revision again")
    s.add_argument("plan"); s.add_argument("spec", metavar="PART@REV")
    s.add_argument("-m", "--comment", default="")
    s.add_argument("--user")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("fingerprint", help="the canonical fingerprint of files")
    s.add_argument("specs", nargs="+", help="X.PcbLib[:NAME] X.SchLib[:NAME] X.kicad_mod Lib.kicad_sym[:NAME]")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "status":
            return cmd_status(a)
        if a.cmd == "show":
            return cmd_show(a)
        if a.cmd == "diff":
            return cmd_diff(a)
        if a.cmd == "history":
            return cmd_history(a)
        if a.cmd == "fingerprint":
            fp = FP.of_files(*a.specs)
            if a.json:
                print(json.dumps(fp, indent=1, default=str))
            else:
                print(f"{fp['part']} ({fp['tool']})")
                for k, v in fp["hashes"].items():
                    print(f"  {k:<7} {v}")
            return 0
        if a.cmd == "approve":
            rows = approve(a.plan, a.part, a.user, a.comment, a.role)
        elif a.cmd == "reject":
            rows = reject(a.plan, a.part, a.user, a.comment)
        else:
            rows = revert(a.plan, a.spec, a.user, a.comment)
    except (CG.ChangeError, ValueError, FileNotFoundError) as e:
        print(f"ecadlib part {a.cmd}: {e}", file=sys.stderr)
        return 2
    if getattr(a, "json", False):
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        extra = (f" -> r{r['revision']}" if r.get("revision") else "") + (
            f" ({r['approvals']}/{r['needed']} approvals)" if r.get("result") == "approval recorded" else "")
        print(f"  {r['result']:<18} {r['part']}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
