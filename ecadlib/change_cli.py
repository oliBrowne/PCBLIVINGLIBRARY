"""`ecadlib part`: see and decide changes to released parts (change control, #105).

    ecadlib part status   [PLAN|DIR] [--json]                  held changes waiting for a person, and recent releases
    ecadlib part show     PLAN PART [--json]                    one held change: every difference, its class, where used
    ecadlib part diff     A B [--plan PLAN] [--json]            classified difference of any two versions of a part
    ecadlib part history  PLAN PART [--json]                    every ledger line of a part: revisions, holds, publishes
    ecadlib part approve  PLAN --part P --digest D [--part ... --digest ...] [-m WHY] [--user U]
                                                                release the held change you reviewed (new revision)
    ecadlib part reject   PLAN --part P -m WHY [--user U]       keep the released version; the same change stays held
    ecadlib part revert   PLAN PART@REV [-m WHY] [--user U]     release an older revision again (as a new revision)
    ecadlib part adopt    PLAN --part P|--all -m WHY [--user U] take the files on disk as the release of a part whose
                                                                ledger history was lost (after checking them by hand);
                                                                the disk is diffed against the part's last stored
                                                                fingerprint: --accept-changes to take a real change
    ecadlib part fingerprint SPEC [SPEC ...] [--json]           the canonical fingerprint and its hashes

A or B in `part diff`: a file (X.PcbLib[:NAME], X.SchLib[:NAME], X.kicad_mod, Lib.kicad_sym[:NAME], several joined
with +), PLAN:PART (the part as it is now), PLAN:PART@3 (revision 3), PLAN:PART@head, PLAN:PART@proposed (the held
change); with --plan, PART@3 and PART@proposed are enough. The answer: each change with its class (identity,
electrical, geometry-major, geometry-minor, cosmetic) and what the [change] policy does with it.

approve: --digest is the digest `part show` and `part status` print for the held change (6 or more of its first
characters are enough): an approval applies only to the change you reviewed, and is refused when the change was
proposed again since. With [change] approvers = 2 a change needs two different people (one person under two spellings,
"Ann Lee <ann@x.com>" and "ann lee", is one person); the first approval is recorded and the change stays held. The
approved files go through the release check first; a version that fails it is not applied, and that approval is not
counted (`part status` shows "approval refused: release check").
Parts that share a file with the approved part (two resistors on one Resistor.SchLib) are approved with it (one digest
covers them all). revert: with approvers = 2 a revert is held like any change, with the reverting person's approval.
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


def _shown(plan):
    """How hints name the library: its plan.json, or the folder of a kicad_import library (it has no plan.json)."""
    return plan if plan.is_file() else plan.parent


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
    f = FP.re.match(r"(.+?\.(?:PcbLib|SchLib|kicad_mod|kicad_sym))(?::.+)?$", s, FP.re.I)   # the file, as FP.of_files reads it
    if f and Path(f.group(1)).exists():                 # (C:\lib\X.SchLib, C:/lib/X.SchLib:NAME: a drive letter is no :NAME)
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
                                    "digest": _review(lg, p),
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
        base = f"r{p['base']}" if p.get("base") is not None else f"new (MPN of {(p.get('twin') or {}).get('part')})"
        print(f"  {p['part']:<28} {base} -> proposed by {p['writer']}: {_counts(p['classes'])}{rej}"
              + (f"  [{got}/{pol['approvers']} approvals]" if got else "") + f"  digest {_review(lg, p)[:12]}")
        print(f"      {CD.summary(p['changes'])}")
        if p.get("used_in"):
            print(f"      used in {len(p['used_in'])}: {', '.join(p['used_in'][:4])}")
    plan = _shown(_plan_path(a.plan))
    print(f"see one: ecadlib part show {plan} PART    decide: ecadlib part approve {plan} --part PART --digest D | "
          f"reject {plan} --part PART -m WHY")
    return 1


def _review(lg, prop):
    try:
        return CG.review_digest(lg, prop["part"])
    except CG.ChangeError:
        return prop.get("digest") or ""


def cmd_show(a):
    plan, items = _load(a.plan)
    lg = LG.Ledger(plan.parent)
    prop, head = lg.proposal(a.part), lg.head(a.part)
    pol = CG.policy(None, plan.parent)
    if a.json:
        print(json.dumps({"part": a.part, "head": head and {k: head.get(k) for k in ("revision", "at", "by", "decision", "eid",
                                                                                     "gid", "digest")},
                          "proposal": prop and {**{k: prop.get(k) for k in ("base", "writer", "by", "at", "changes", "classes",
                                                                            "why", "approvals", "used_in", "rejected", "refused",
                                                                            "digest", "twin", "revert_to")},
                                                "review_digest": _review(lg, prop)},
                          "used_in": [{"project": p, "revision": r} for p, r, _ in LG.used_in(plan.parent, a.part)]},
                         indent=1, default=str))
        return 0
    if not head and not prop:
        print(f"{a.part}: not released yet (no ledger revision)")
        return 0
    if head:
        print(f"{a.part}: released revision {head['revision']} ({head.get('decision')}, {head.get('at')}, by {head.get('by')})")
    else:
        tw = prop.get("twin") or {}
        print(f"{a.part}: not released yet; a new part with the MPN of released {tw.get('part')} r{tw.get('revision')}, "
              f"which left the plan: the changes below are against that part")
    if not prop:
        print("no held change")
        return 0
    if prop.get("revert_to"):
        print(f"held revert to r{prop['revert_to']}, waiting for a second person")
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
    plan = _shown(plan)
    group = _group(lg, a.part)
    if len(group) > 1:
        print(f"decided together with {', '.join(p['part'] for p in group if p['part'] != a.part)} (they share a file)")
    digest = CG.review_digest(lg, a.part, group)
    print(f"digest {digest}")
    if head:
        print(f"diff again: ecadlib part diff {plan}:{a.part}@head {plan}:{a.part}@proposed")
    print(f"approve this change: ecadlib part approve {plan} --part {a.part} --digest {digest[:12]}")
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
    return CG.proposal_group(lg, part)


def _people(group):
    """The people who approved every proposal of a group as it is now (a proposal that changed starts with no
    approvals, so an approval of the old version of one part never counts for the new one), each person once."""
    from .approvals import same_person
    out = []
    for a in group[0].get("approvals") or []:
        w = a.get("who")
        if not w or any(same_person(w, x) for x in out):
            continue
        if all(any(same_person(w, b.get("who")) for b in p.get("approvals") or []) for p in group[1:]):
            out.append(w)
    return out


def check_digest(lg, group, part, digest):
    """Refuse an approval that is not of the held change as it is now. digest: the review digest (CG.review_digest)
    `part show` printed, or a prefix of 6+ characters."""
    cur = CG.review_digest(lg, part, group)
    d = str(digest or "").strip().lower()
    # the messages never print the current digest: it is handed out only with the diff (`part show`), so nobody can
    # approve a change they have not seen by copying it from an error
    show = f"`ecadlib part show {lg.folder} {part}`"
    if not d:
        raise CG.ChangeError(f"{part}: say which held change you approve: --digest D, the digest {show} prints with the "
                             f"diff, so a change made after you looked at it is never approved by mistake")
    if len(d) < 6:
        raise CG.ChangeError(f"{part}: --digest needs at least 6 characters of the digest {show} prints")
    if not cur.startswith(d):
        seen = {x for p in group for e in lg.history(p["part"]) if e.get("event") in ("hold", "approve", "reject",
                                                                                        "refused", "superseded")
                for x in (e.get("digest"), e.get("review")) if x}
        if not any(str(x).startswith(d) for x in seen):          # a typo, or a digest of something else
            raise CG.ChangeError(f"{part}: no held change has that digest ({d[:12]}); copy it from {show}")
        raise CG.ChangeError(f"{part}: the held change is not the one you reviewed (digest {d[:12]}): it was proposed "
                             f"again since you looked; read it again with {show} and approve that one")
    return cur


def approve(plan, parts, who=None, comment="", role=None, digests=None):
    """Record one approval of each held change (and the ones sharing its files); apply them when enough people
    approved. digests: {part: review digest or a 6+ character prefix} of what the person reviewed (`part show`, the web
    UI); a held change that no longer matches is refused. None: not checked (callers that showed the person nothing
    else than the proposal on disk now). -> [{part, result, revision?, approvals, needed, warning?}]"""
    folder = CG.lib_folder(_plan_path(plan))
    with LG.locked(folder):
        return _approve(plan, parts, who, comment, role, digests)


def _approve(plan, parts, who, comment, role, digests):
    from . import approvals as AP
    plan, items = _load(plan)
    folder = plan.parent
    lg = LG.Ledger(folder)
    gone = sorted({x["part"] for p in parts for x in _group(lg, p)} - {i["name"] for i in items})
    if gone:                                   # nothing to release them into: refuse before any file or approval moves
        raise CG.ChangeError(f"{', '.join(gone)}: not in the plan any more; run the librarian with a BOM that uses "
                             f"{'them' if len(gone) > 1 else 'it'}, or reject the change")
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
        groups.append((p, g))
    out = []
    for asked, g in groups:
        for prop in g:
            head = lg.head(prop["part"])
            moved = (head is not None) if prop.get("base") is None else (not head or head["revision"] != prop["base"])
            if moved:
                lg.drop_proposal(prop["part"])
                raise CG.ChangeError(f"{prop['part']}: the released revision moved on since this change was proposed "
                                     f"(r{prop['base']} -> r{head and head['revision']}); run the writer again")
        review = check_digest(lg, g, asked, (digests or {}).get(asked)) if digests is not None else None
        people = _people(g)
        first = next((x for x in people if AP.same_person(who, x)), None)
        if first is not None:                  # name the approval on record: "Ann" and "Аnn" are one person
            same = "" if CD.shown(first) == CD.shown(who) else f" ({CD.shown(who)} is the same person)"
            raise CG.ChangeError(f"{g[0]['part']}: {CD.shown(first)} approved this change already{same}"
                                 + (f"; it needs {pol['approvers']} different people" if pol["approvers"] > 1 else ""))
        final = len(people) + 1 >= pol["approvers"]
        warnings = []
        if final:                              # the last approval: the proposed files must pass the release check first
            try:
                warnings = _put_proposed(plan, items, g, lg, who)
            except CG.ChangeError as e:        # not recorded as an approval: the part stays held, and says why
                for prop in g:
                    prop["refused"] = {"by": who, "at": LG.now(), "reason": str(e)}
                    lg.append({"event": "refused", "part": prop["part"], "digest": prop["digest"], "by": who,
                               "base": prop["base"], "reason": str(e)})
                    lg.save_proposal(prop)
                for prop in g:
                    it = next((i for i in items if i["name"] == prop["part"]), None)
                    if it is not None:
                        it["change"] = CG.held_block(prop, lg)
                _save(plan, items)
                raise
        for prop in g:
            prop.pop("refused", None)
            if not any(AP.same_person(who, a.get("who")) for a in prop.get("approvals") or []):
                prop.setdefault("approvals", []).append({"who": who, "key": key, "when": LG.now(), "comment": comment or "",
                                                         "digest": prop["digest"], **({"review": review} if review else {}),
                                                         **({"role": role} if role else {})})
            lg.append({"event": "approve", "part": prop["part"], "digest": prop["digest"], "by": who, "base": prop["base"],
                       **({"review": review} if review else {}), **({"reason": comment} if comment else {})})
            lg.save_proposal(prop)
        if not final:
            for prop in g:
                it = next((i for i in items if i["name"] == prop["part"]), None)
                if it is not None:
                    it["change"] = CG.held_block(prop, lg)
                out.append({"part": prop["part"], "result": "approval recorded", "approvals": len(people) + 1,
                            "needed": pol["approvers"]})
            continue
        rows = _apply_group(plan, items, g, who, comment, lg, people + [who])
        for r in rows:
            w = [x for x in warnings if x["part"] == r["part"]]
            if w:
                r["warning"] = w[0]["text"]
        out += rows
    _save(plan, items)
    return out


def _step_backup(file_sets, folder):
    """The STEP files (3D models) restoring these snapshots will overwrite -> {path: bytes now}. A head recorded before
    the ledger kept STEP files cannot put them back itself: _step_restore does, when a change is not applied."""
    backup = {}
    for files in file_sets:
        for r in (files or {}).values():
            for s in r.get("steps") or {}:
                f = (folder / r["path"]).parent / s
                if f.is_file():
                    backup[f] = f.read_bytes()
    return backup


def _step_restore(backup, heads, folder):
    covered = {(folder / r["path"]).parent / s for h in heads if h for r in (h.get("files") or {}).values()
               for s in r.get("steps") or {}}
    for f, data in backup.items():
        if f not in covered and (not f.is_file() or f.read_bytes() != data):
            f.write_bytes(data)


def _mix_of(it, folder, versions):
    """Every file of `it` on disk is byte for byte one of these versions' (the released head, the proposal), and its
    fields are one version's: what an approval cut short (killed while it put the files in place) leaves behind. That
    is no newer edit, so it is not reported as overwritten."""
    vs = [v for v in versions if v]
    for kind, rel in LG.item_files(it).items():
        f = Path(rel) if Path(rel).is_absolute() else folder / rel
        if not f.is_file():
            continue
        sha = LG.sha_bytes(f.read_bytes())
        if not any(((v.get("files") or {}).get(kind) or {}).get("sha256") == sha for v in vs):
            return False
    now = json.loads(json.dumps(LG.content_of(it)))
    return any(now == json.loads(json.dumps(v.get("item") or {})) for v in vs)


def _put_proposed(plan, items, group, lg, who=""):
    """Put the proposed files and fields in place and run the release check on them. A version that fails it is
    replaced by the released one again and raises ChangeError. A newer edit on disk (neither the released version
    nor the proposal: made after the hold) is replaced too, but never silently: its bytes go to the blob store and an
    `overwritten` ledger line says so. -> [{part, text}] warnings about such edits."""
    from . import release_check as RC
    folder = plan.parent
    byname = {i["name"]: i for i in items}
    heads = {p["part"]: lg.head(p["part"]) for p in group}
    warnings = []
    for p in group:
        it = byname.get(p["part"])
        if it is None:
            continue
        try:
            now = FP.of_item(it, folder)["hashes"]["digest"]
        except Exception:                      # unreadable now: it is replaced, so keep it
            now = None
        if now not in lg.digests(heads[p["part"]]) | lg.digests(p) and not _mix_of(it, folder, (heads[p["part"]], p)):
            kept = lg.snapshot_files(it, folder)
            text = (f"{p['part']}: the files on disk had a newer edit than the held change (made after it was held); "
                    f"the approved change replaced it. Its bytes are kept (ledger `overwritten`; `ecadlib part history`)")
            lg.append({"event": "overwritten", "part": p["part"], "by": who, "digest": now, "files": kept,
                       "item": LG.content_of(it), "base": p["base"], "reason": "a newer edit on disk was replaced by "
                       f"the approved held change {p['digest'][:12]}"})
            warnings.append({"part": p["part"], "text": text})
    backup = _step_backup([p.get("files") for p in group], folder)
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
    RC.apply(checked, folder, change_holds=False)     # the version being approved
    bad = [i for i in checked if i.get("status") == "held"]
    reasons = "; ".join(f"{i['name']}: {', '.join(i.get('hold_reasons') or [])}" for i in bad)
    if bad:                                    # the approved version fails the release check: put the released one back
        for p in group:
            h = heads[p["part"]]
            if h is None:                      # a new part (no revision yet): its files stay, it stays held
                continue
            lg.restore_files(h.get("files"), folder)
            if p["part"] in byname:
                LG.apply_content(byname[p["part"]], h.get("item") or {})
        _step_restore(backup, heads.values(), folder)
        RC.apply(checked, folder)
        for p in group:
            if p["part"] in byname:
                byname[p["part"]]["change"] = CG.held_block(p, lg)
                if heads[p["part"]] is None:
                    byname[p["part"]]["status"] = "held"
        hint = None
        if "parity:" in reasons:               # e.g. only the .kicad_mod was edited: it no longer matches the .PcbLib
            moved = set()
            for p in group:                    # which of the part's files the held change touched
                old = (heads.get(p["part"]) or {}).get("files") or {}
                moved |= {k for k, r in (p.get("files") or {}).items() if (old.get(k) or {}).get("sha256") != r.get("sha256")}
            if moved == {"kicad_mod"}:
                first = ("only the .kicad_mod was edited, so it no longer matches the .PcbLib: edit the .PcbLib too (or "
                         "use `ecadlib fp-edit`, which writes both), then run the writer again so it proposes both")
            elif moved == {"footprint"}:
                first = ("only the .PcbLib was edited, so it no longer matches the .kicad_mod: edit the .kicad_mod too "
                         "(or use `ecadlib fp-edit`, which writes both), then run the writer again so it proposes both")
            else:
                first = ("the part's KiCad and Altium files must say the same (parity): make the same change to the "
                         "other file too (or use `ecadlib fp-edit`), then run the writer again so it proposes both")
            hint = (first + "; or reject it: `ecadlib part reject` this change. A library that accepts a difference "
                    "sets [release] parity = \"warn\" in ecadlib.toml")
        raise CG.ChangeError(f"not applied: the proposed version fails the release check ({reasons}); the released "
                             f"version stays", hint)
    return warnings


def _apply_group(plan, items, group, who, comment, lg, people=None):
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
        by = people or [x["who"] for x in p.get("approvals") or []]
        reason = comment or (f"revert to r{p['revert_to']}" if p.get("revert_to") else "")
        if p.get("twin"):
            reason = (reason + "; " if reason else "") + (f"new part with the MPN of released {p['twin']['part']} "
                                                          f"r{p['twin']['revision']}")
        row = g._release(it, fp, changes, {"action": "pass", "why": p.get("why") or []}, decision="approved",
                         reason=reason, approved_by=by, by=who, event="revert" if p.get("revert_to") else "release",
                         min_rev=p.get("min_revision"))
        lg.drop_proposal(p["part"])
        it.pop("change", None)
        out.append({"part": p["part"], "result": "released", "revision": row["revision"], "approvals": len(by),
                    "needed": len(by)})
    return out


def reject(plan, parts, who=None, comment=""):
    if not (comment or "").strip():
        raise CG.ChangeError("a reject needs a reason (-m) saying what is wrong with the change")
    with LG.locked(CG.lib_folder(_plan_path(plan))):
        return _reject(plan, parts, who, comment)


def _reject(plan, parts, who, comment):
    from . import approvals as AP
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
                       "reason": comment, "classes": prop.get("classes"),
                       **{k: prop[k] for k in ("fingerprint", "files", "item") if prop.get(k) is not None}})
            lg.drop_proposal(prop["part"])
            it = next((i for i in items if i["name"] == prop["part"]), None)
            if it is not None:
                it.pop("change", None)
            out.append({"part": prop["part"], "result": "rejected"})
    _save(plan, items)
    return out


def revert(plan, spec, who=None, comment=""):
    """Release revision REV of PART again as a new revision; parts sharing its files go back with it. The reverted
    version must pass the release check. With [change] approvers = 2 or more a revert is a release like any other: it
    becomes a held change carrying the reverting person's approval, and `part approve` by another person applies it."""
    with LG.locked(CG.lib_folder(_plan_path(plan))):
        return _revert(plan, spec, who, comment)


def _revert(plan, spec, who, comment):
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
    if lg.digests(head) & lg.digests(target):
        raise CG.ChangeError(f"{part}: revision {target['revision']} is what is released now (r{head['revision']})")
    it = _item(items, part)
    who = who or AP.identity(None, cwd=folder)
    pol = CG.policy(None, folder)
    old_fp = lg.fingerprint(head)
    backup = _step_backup([target.get("files")], folder)
    lg.restore_files(target["files"], folder)
    LG.apply_content(it, target.get("item") or {})
    written = {str((folder / r["path"]).resolve()) for r in (target.get("files") or {}).values()}
    others = [i for i in items if i is not it and {str((folder / p).resolve()) for p in LG.item_files(i).values()} & written]
    group = [it] + others
    checked = [x for x in group if not x.get("_manifest")]
    RC.apply(checked, folder)
    bad = [x for x in checked if x.get("status") == "held"]

    def put_back():
        for x in group:
            h = lg.head(x["name"])
            if h:
                lg.restore_files(h.get("files"), folder)
                LG.apply_content(x, h.get("item") or {})
        _step_restore(backup, [lg.head(x["name"]) for x in group], folder)
        RC.apply(checked, folder)

    if bad:                                    # like an approval: a version that fails the release check is not released
        put_back()
        _save(plan, items)
        raise CG.ChangeError(f"{part}: not reverted: r{target['revision']} fails the release check now ("
                             + "; ".join(f"{x['name']}: {', '.join(x.get('hold_reasons') or [])}" for x in bad)
                             + "); the released version stays")
    g = CG.Guard(folder, "revert", user=who, chosen_by=who)
    todo = []
    for x in group:
        h = lg.head(x["name"])
        fp = FP.of_item(x, folder)
        if h and fp["hashes"]["digest"] in lg.digests(h):
            continue
        changes = CD.diff(old_fp if x is it else lg.fingerprint(h), fp) if h else []
        todo.append((x, h, fp, changes))
    if pol["approvers"] >= 2:                  # the two-person rule: the revert waits for a second person
        ts = []
        for x, h, fp, changes in todo:
            if not h:
                continue
            why = [f"revert to r{target['revision']}" + ("" if x is it else f" of {part}") + f" by {who}: a release, so it "
                   f"needs {pol['approvers']} people ([change] approvers)"]
            ts.append({"item": x, "head": h, "fp": fp, "changes": changes, "rejected": None,
                       "dec": {"action": "hold", "why": why, "by_class": {}}})
        for t in ts:
            prev = lg.proposal(t["item"]["name"])
            if prev and t["fp"]["hashes"]["digest"] not in lg.digests(prev):     # a pending held change gives way
                lg.append({"event": "superseded", "part": prev["part"], "digest": prev.get("digest"), "writer": "revert",
                           "by": who, "reason": f"replaced by a revert to r{target['revision']}"})
                lg.drop_proposal(prev["part"])
            t["prop"] = g._propose(t)
            t["prop"]["revert_to"] = target["revision"]
            if comment:
                t["prop"]["comment"] = comment
            lg.save_proposal(t["prop"])
        out = []
        for t in ts:
            g._hold(t)
        _step_restore(backup, [t["head"] for t in ts], folder)
        for t in ts:
            out.append({"part": t["item"]["name"], "result": "approval recorded", "approvals": 1,
                        "needed": pol["approvers"], "to": target["revision"] if t["item"] is it else None})
        RC.apply(checked, folder)
        _save(plan, items)
        return out
    out = []
    for x, h, fp, changes in todo:
        row = g._release(x, fp, changes, {"action": "pass", "why": []}, decision="revert",
                         reason=comment or f"revert to r{target['revision']}" + ("" if x is it else f" of {part}"),
                         approved_by=[who], event="revert", by=who)
        lg.drop_proposal(x["name"])
        x.pop("change", None)
        out.append({"part": x["name"], "result": "reverted", "revision": row["revision"], "to": target["revision"] if x is it else None})
    _save(plan, items)
    return out


def adopt(plan, parts, who=None, comment="", all_lost=False, accept_changes=False):
    """`ecadlib part adopt`: after the ledger lost a part's history (a deleted or cut ledger: the gate then refuses to
    take the files on disk as revision 1), a person who checked those files records them as the part's release
    (decision "adopted", by them, with their reason), as the next revision after any it had (lost_revision).

    The files on disk are compared with the newest fingerprint the blob store still holds for the part. Only files
    with no difference beyond cosmetics are adopted directly. A real difference is refused unless accept_changes, and
    even then it is never released by the adopter alone: the stored version's files, found again in the blob store,
    are adopted, and the disk becomes a held change against them; without those files (or with nothing left to
    compare with) the disk becomes a held change with no released version. Either one needs `part approve` as the
    [change] policy says. -> [{part, result, revision, by, compared, changes}]"""
    if not (comment or "").strip():
        raise CG.ChangeError("adopt needs a reason (-m): what you checked on disk, and why the history is gone")
    with LG.locked(CG.lib_folder(_plan_path(plan))):
        from . import approvals as AP
        plan, items = _load(plan)
        who = who or AP.identity(None, cwd=plan.parent)
        g = CG.Guard(plan.parent, "adopt", user=who)
        lost = [i["name"] for i in items if CG.released(i) and not g.ledger.head(i["name"]) and g.lost_history(i["name"], i)]
        names = lost if all_lost else list(parts)
        bad = [n for n in names if n not in lost]
        if bad:
            raise CG.ChangeError(f"{', '.join(bad)}: nothing to adopt (released in the ledger, not released, or no sign "
                                 f"of a lost history: the next writer records it as usual)")
        if not names:
            raise CG.ChangeError("no part has a lost history" if all_lost else "name the parts: --part P (or --all)")
        compared, olds, refused = {}, {}, []
        for it in (i for i in items if i["name"] in names):
            old, where = g.stored_fingerprint(it["name"])
            lock = g.lock_check(it) if old is None else None
            if old is None and lock == "":                 # the files are the bytes the librarian built and recorded
                compared[it["name"]] = (f"compared with library.lock.json: the files on disk are the ones the librarian "
                                        f"built", [])
                continue
            if old is None:
                used = [(p, r) for p, r, _ in LG.used_in(plan.parent, it["name"]) if r]
                compared[it["name"]] = (
                    "not compared: no earlier fingerprint is left in .ecadlib/blobs" +
                    (f" ({LG.USAGE} records r{used[0][1]} in use by {used[0][0]}, but not its content)" if used else "") +
                    (f"; {lock}" if lock else ""),
                    None)
                refused.append(it["name"])
                continue
            if old.get("schema") != FP.SCHEMA:          # stored by an older ecadlib: what it did not record is not compared
                where += f" (fingerprint schema {old.get('schema')}: only what it recorded is compared)"
            changes = CD.diff_stored(old, FP.of_item(it, g.base))
            compared[it["name"]], olds[it["name"]] = (f"compared with {where}", changes), old
            if any(c["class"] != "cosmetic" for c in changes):
                refused.append(it["name"])
        skipped = []
        if refused and not accept_changes and all_lost and len(refused) < len(names):
            # --all: the parts that compare clean are adopted; the others are listed, refused, and left as they are
            skipped = [{"part": n, "result": "refused", "revision": None, "by": who, "compared": compared[n][0],
                        "held": None, "changes": [c["text"] for c in compared[n][1] or [] if c["class"] != "cosmetic"]}
                       for n in refused]
            names = [n for n in names if n not in refused]
            refused = []
        if refused and not accept_changes:
            lines = []
            for n in refused:
                where, changes = compared[n]
                lines.append(f"{n} ({where})" + (":" if changes else ""))
                lines += [f"    {c['class']:<15} {c['text']}" for c in changes or [] if c["class"] != "cosmetic"]
            raise CG.ChangeError(
                "the files on disk differ from the part's last stored fingerprint, or cannot be compared, so they may "
                "not be what was released:\n  " + "\n  ".join(lines) + "\n  restore the ledger and .ecadlib/ from git "
                "(`git checkout -- library.ledger.jsonl .ecadlib` in the library folder), or put the released files "
                "back; --accept-changes proposes these files as a held change that needs `ecadlib part approve`")
        revs = {n: g.lost_revision(n) for n in names}
        direct = [n for n in names if n not in refused]
        rows = []
        try:
            if direct:
                g.adopt = {n: f"adopted by {who}: {comment.strip()}" for n in direct}
                g.adopt_changes = {n: compared[n][1] for n in direct}
                g.adopt_revision = {n: revs[n] for n in direct}
                rows += [dict(r, result="adopted") for r in g.baseline([i for i in items if i["name"] in direct])]
            for n in refused:
                rows += _adopt_held(g, plan, items, _item(items, n), olds.get(n), compared[n], revs[n], who, comment)
        finally:
            g.close()
        _save(plan, items)
        return [{"part": r["part"], "result": r["result"], "revision": r.get("revision"), "by": who,
                 "compared": compared[r["part"]][0], "held": r.get("held"),
                 "changes": [c["text"] for c in compared[r["part"]][1] or []]} for r in rows] + skipped


def _adopt_held(g, plan, items, it, old, compared, rev, who, comment):
    """--accept-changes for a part whose disk differs from its last stored fingerprint (or cannot be compared): never
    a release by the adopter alone. -> report rows"""
    folder = plan.parent
    name = it["name"]
    files = g.files_from_blobs(it, old) if old is not None else None
    if files:                                  # the stored version is adopted; the disk is a held change against it
        disk = {}
        for rel, data in files.items():
            p = Path(rel) if Path(rel).is_absolute() else folder / rel
            disk[p] = p.read_bytes() if p.is_file() else None
            p.write_bytes(data)
        try:
            g.adopt = {name: f"adopted by {who} from the last stored version in .ecadlib/blobs: {comment.strip()}"}
            g.adopt_changes, g.adopt_revision = {}, {name: rev}
            base = g.baseline([it])
        finally:
            for p, data in disk.items():       # the files on disk as the person left them
                if data is None:
                    p.unlink(missing_ok=True)
                else:
                    p.write_bytes(data)
        h = CG.Guard(folder, "hand-edit", user=who)
        try:
            h.settle(items)
        finally:
            h.close()
        g.ledger.reload()
        held = g.ledger.proposal(name)
        return [dict(r, result="adopted (stored version)", held=bool(held)) for r in base]
    # no released version to put back: the disk waits as a change with no base, released only by `part approve`
    fp = FP.of_item(it, g.base)
    changes = compared[1] or [{"class": "electrical", "area": "ledger", "what": "history", "old": None, "new": None,
                               "text": "no earlier version is left to compare with"}]
    why = [f"adopted by {who} with --accept-changes after the ledger lost its history ({compared[0]}): a person "
           f"approves it (ecadlib part approve)"]
    t = {"item": it, "head": None, "fp": fp, "changes": changes, "rejected": None,
         "dec": {"action": "hold", "why": why, "by_class": {}}}
    prop = g._propose(t)
    prop["min_revision"], prop["comment"] = rev, comment
    g.ledger.save_proposal(prop)
    it["change"] = CG.held_block(prop, g.ledger)
    it["status"] = "held"                      # not released (no revision yet): publishers leave it out
    it["hold_reasons"] = [x for x in it.get("hold_reasons") or [] if not x.startswith("change control:")] + [
        f"change control: adopted files wait for approval (ecadlib part show {plan} {name})"]
    return [{"part": name, "result": "held for approval", "revision": None, "held": True}]


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
            s.add_argument("--digest", action="append", default=[], help="the digest of the held change you reviewed "
                           "(`part show` prints it; 6+ characters); one per --part, in the same order")
        s.add_argument("--json", action="store_true")
    s = sub.add_parser("revert", help="release an older revision again")
    s.add_argument("plan"); s.add_argument("spec", metavar="PART@REV")
    s.add_argument("-m", "--comment", default="")
    s.add_argument("--user")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("adopt", help="take the files on disk as the release of parts whose ledger history was lost")
    s.add_argument("plan")
    s.add_argument("--part", action="append", default=[])
    s.add_argument("--all", action="store_true", help="every part the gate refused to adopt")
    s.add_argument("-m", "--comment", default="", help="what you checked (required)")
    s.add_argument("--user", help="who you are (default: $ECADLIB_USER, then git config); printed with the result")
    s.add_argument("--accept-changes", action="store_true", help="adopt although the files differ from the part's "
                   "last stored fingerprint by more than cosmetic changes (they are recorded on the adopted line)")
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
            if len(a.digest) != len(a.part):
                raise CG.ChangeError("give one --digest per --part (the digest `ecadlib part show PLAN PART` prints for "
                                     "the change you reviewed)" if a.digest else
                                     "--digest is required: the digest of the held change you reviewed (`ecadlib part show "
                                     f"{a.plan} {a.part[0]}` prints it), so a change proposed after you looked is never "
                                     "approved by mistake")
            rows = approve(a.plan, a.part, a.user, a.comment, a.role, dict(zip(a.part, a.digest)))
        elif a.cmd == "adopt":
            rows = adopt(a.plan, a.part, a.user, a.comment, a.all, a.accept_changes)
        elif a.cmd == "reject":
            rows = reject(a.plan, a.part, a.user, a.comment)
        else:
            rows = revert(a.plan, a.spec, a.user, a.comment)
    except (CG.ChangeError, ValueError, FileNotFoundError) as e:
        print(f"ecadlib part {a.cmd}: {e}", file=sys.stderr)
        if getattr(e, "hint", None):
            print(f"  hint: {e.hint}", file=sys.stderr)
        return 2
    refused = [r for r in rows if r.get("result") == "refused"]
    if getattr(a, "json", False):
        print(json.dumps(rows, indent=1))
        return 1 if refused else 0
    for r in rows:
        extra = (f" -> r{r['revision']}" if r.get("revision") else "") + (
            f" ({r['approvals']}/{r['needed']} approvals)" if r.get("result") == "approval recorded" else "")
        print(f"  {r['result']:<18} {r['part']}{extra}")
        if r.get("warning"):
            print(f"  warning: {r['warning']}")
        if r.get("result") == "adopted":
            print(f"    signed as {r['by']}; {r['compared']}" + ("" if r["changes"] else ": no difference"))
        elif r.get("result") == "refused":            # adopt --all: left as it is
            print(f"    not adopted: {r['compared']}")
            for c in r["changes"]:
                print(f"      {c}")
        elif "compared" in r:                  # --accept-changes: the disk waits as a held change
            print(f"    signed as {r['by']}; {r['compared']}")
            for c in r["changes"]:
                print(f"      {c}")
            if r.get("held"):
                print(f"    the files on disk are held as a change: ecadlib part show {a.plan} {r['part']}, then a person "
                      f"approves it (ecadlib part approve)")
    if refused:
        print(f"  {len(refused)} part(s) not adopted: restore the ledger and .ecadlib/ from git, put the released files "
              f"back, or `ecadlib part adopt {a.plan} --part {refused[0]['part']} --accept-changes` to hold them as a "
              f"change for approval", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
