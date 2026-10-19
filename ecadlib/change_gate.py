"""Change control: no library update can silently change a released part (#105).

Every writer that can replace a released part goes through one gate:

    with change_gate.Guard(lib_dir, "librarian", rules=rules) as g:     begin(): adopt parts released before change control
        ... the writer rewrites model files / plan fields ...
        g.settle(items)            each released part: fingerprint now vs its ledger head -> classified changes -> policy
        g.baseline(items)          parts released for the first time (status ready) get revision 1

    reasons = change_gate.publish_guard(plan, items, "kicad")   publishers (kicad-export, dblib-sync, altium365 push):
                                                                 settle hand edits, baseline, then publish only heads

Per released part (a ledger head exists) settle compares the fingerprint of what the writer left (ecadlib.fingerprint)
with the head's and classifies every difference (ecadlib.change_diff). The [change] policy turns classes into actions:

    [change]                   # ecadlib.toml
    identity = "hold"          # hold | warn            MPN, manufacturer, value
    electrical = "hold"        # hold | warn            pins, pin types, pin -> pad
    geometry_major = "hold"    # hold | warn | pass     pads moved / resized beyond tolerance, drills, 3D origin
    geometry_minor = "warn"    # hold | warn | pass     pad size within tolerance, margins, courtyard, 3D height
    cosmetic = "pass"          # hold | warn | pass     silk, fab, text, graphics, pin length, other fields
    approvers = 1              # people who must approve a held change; 2 = the two-person rule

pass / warn: a new ledger revision is recorded (warn adds the changes to the part's review notes). hold: the proposed
version (files + fields + fingerprint) goes to .ecadlib/proposed/<part>.json, the released files and fields are put
back, and the plan item carries `change` = {state: held, base, summary, changes}, so the library keeps the released part
until a person approves (`ecadlib part approve`, the web UI, the review list) or rejects it. A part whose file is
shared with a held part (two resistors on one Resistor.SchLib) is held with it, so a restored file never undoes a
recorded release. A proposal rejected before is held again without asking.

Writers (WRITERS) have a mode: `gate` (the rules above), `restyle` (style apply: may only produce cosmetic changes;
anything else is held as a bug), `observe` (a365 pull: the workspace released it; recorded, never held) and `publish`.
tests/test_change_control.py fails when a module that writes library files is not in WRITERS or does not use the gate.

Released means: a ledger head exists. The first revision is recorded when a part passes the release check (status
ready) or comes from the workspace (status existing); a part released before change control existed is adopted as it
is on disk when a writer first opens the folder (begin), before anything is rewritten."""
import json, os
from pathlib import Path

from . import change_diff as CD, fingerprint as FP, ledger as LG
from . import __version__

ACTIONS = ("pass", "warn", "hold")
POLICY = {"identity": "hold", "electrical": "hold", "geometry_major": "hold", "geometry_minor": "warn", "cosmetic": "pass",
          "approvers": 1, "a365_transition": "", "unlisted": "hold"}
ALLOWED = {"identity": ("hold", "warn"), "electrical": ("hold", "warn")}
CLASS_KEY = {c: c.replace("-", "_") for c in CD.CLASSES}
RELEASED_STATUSES = ("ready", "existing")

# Every module that writes a library part (a .SchLib / .PcbLib / .kicad_mod or the plan fields a release is made of),
# and how it goes through the gate. tests/test_change_control.py checks this list against the code.
WRITERS = {
    "librarian": {"mode": "gate", "module": "ecadlib.librarian", "what": "librarian runs and re-runs (also rebuild in "
                  "place, the library agent, fp-edit and ecadlib resolve's re-runs, which run the librarian)"},
    "resolve": {"mode": "gate", "module": "ecadlib.resolve", "what": "ecadlib resolve / review: a person picks a symbol or footprint"},
    "style-apply": {"mode": "restyle", "module": "ecadlib.house_style", "what": "ecadlib style apply (descriptions, value formats)"},
    "upstream-update": {"mode": "gate", "module": "ecadlib.upstream", "what": "upstream check-kicad --update / upkeep --apply"},
    "dedupe-merge": {"mode": "gate", "module": "ecadlib.dedupe", "what": "ecadlib dedupe --merge points a part at its keeper's files"},
    "sim-models": {"mode": "gate", "module": "ecadlib.sim_models", "what": "ecadlib sim-models --attach (simulation link in the symbol)"},
    "datasheet": {"mode": "gate", "module": "ecadlib.datasheet", "what": "ecadlib datasheet batch: parts read from datasheets"},
    "sourcing-plan": {"mode": "gate", "module": "ecadlib.sourcing", "what": "ecadlib sourcing --write-plan fills supplier fields"},
    "a365-pull": {"mode": "observe", "module": "ecadlib.a365_sync", "what": "ecadlib a365 pull: workspace revisions come in"},
    "approve": {"mode": "gate", "module": "ecadlib.change_cli", "what": "ecadlib part approve: a person releases a held change"},
    "revert": {"mode": "gate", "module": "ecadlib.change_cli", "what": "ecadlib part revert PART@REV: an older revision again"},
    "adopt": {"mode": "gate", "module": "ecadlib.change_cli", "what": "ecadlib part adopt: a person takes the files on disk "
              "as the release of a part whose ledger history was lost"},
    "hand-edit": {"mode": "gate", "module": "", "what": "a file changed outside ecadlib, found when a publisher opens the library"},
    "kicad-export": {"mode": "publish", "module": "ecadlib.kicad_export", "what": "KiCad library export"},
    "dblib-sync": {"mode": "publish", "module": "ecadlib.altium_dblib_sync", "what": "Altium DbLib / SVNDbLib sync"},
    "altium365-push": {"mode": "publish", "module": "ecadlib.altium365", "what": "Altium 365 release and updates"},
}
# Modules that call the Altium / KiCad file writers but never replace a released part: they write new files, the
# workspace model pool, test material or files a person names on the command line. The coverage test accepts these.
NOT_LIBRARY_WRITERS = {
    "ecadlib.altium_lib": "the writer itself (and its CLI, which writes the file a person names)",
    "ecadlib.kicad_import": "the converter: writes where it is told; librarian / upstream / resolve call it inside their gate",
    "ecadlib.footprint_gen": "the generator CLI writes the file a person names",
    "ecadlib.app": "the desktop / web app's model pool (<workspace>/altium_models): new files, an overwrite only when asked; "
                   "publishers gate these files per part (publish_guard)",
    "ecadlib.hub_copy": "the hub's own copy library (<workspace>/hub/library), never a released library",
    "ecadlib.stdparts": "standard-part CLI output",
    "ecadlib.symbol_layout": "symbol CLI output",
    "ecadlib.testpack": "the test pack folder",
    "ecadlib.eval.golden": "eval golden files",
    "ecadlib.altium_native": "Altium's own rebuild of files ecadlib already wrote (same content, Altium's bytes)",
    "ecadlib.easy_setup": "setup copies",
    "ecadlib.firstrun": "the demo folder",
    "ecadlib.agent": "copies of pictures for the review queue",
    "ecadlib.migrate": "backups of files it upgrades",
    "ecadlib.parity": "survey work folder",
    "ecadlib.rebuild": "rebuild --check copies the library to a temporary folder (in-place rebuild runs the librarian)",
    "ecadlib.altium_pkg": "the hand-off folder it creates from the plan's (gated) files",
    "ecadlib.release_check": "status, hold reasons and --allow overrides only (not fingerprinted)",
    "ecadlib.approvals": "sign-off records only (not fingerprinted)",
    "ecadlib.review_cli": "release anyway (status only); its choices go through ecadlib.resolve (gated)",
    "ecadlib.change_cli": "ecadlib part: the gate's own approve / reject / revert",
    "ecadlib.change_gate": "the gate itself",
    "ecadlib.upkeep": "upkeep --apply reconverts through change_gate.guarded_rewrite (writer upstream-update)",
}


class ChangeError(Exception):
    """A change-control action refused (said in plain words; the CLI exits 2 without a traceback)."""
    plain_error = True
    hint = "`ecadlib part status` lists held changes; docs/change-control.md explains each step"

    def __init__(self, msg, hint=None):
        super().__init__(msg)
        if hint:
            self.hint = hint


# ---------------------------------------------------------------- policy
def policy(rules=None, start="."):
    from . import rules as RU
    try:
        r = RU.resolve(rules, start)
    except Exception:
        r = {}
    raw = dict((r or {}).get("change") or {})
    return normalize_policy(raw)


def normalize_policy(raw, where="[change]"):
    p = dict(POLICY)
    for k, v in (raw or {}).items():
        if k not in POLICY:
            raise ValueError(f"{where}: unknown key {k!r}; known: {', '.join(POLICY)}")
        p[k] = v
    for k in CLASS_KEY.values():
        ok = ALLOWED.get(k, ACTIONS)
        if p[k] not in ok:
            raise ValueError(f"{where} {k} = {p[k]!r}: must be {' or '.join(ok)}")
    try:
        p["approvers"] = int(p["approvers"])
    except (TypeError, ValueError):
        raise ValueError(f"{where} approvers = {p['approvers']!r}: a whole number (1, or 2 for the two-person rule)") from None
    if p["approvers"] < 1:
        raise ValueError(f"{where} approvers must be 1 or more")
    if p["unlisted"] not in ACTIONS:
        raise ValueError(f"{where} unlisted = {p['unlisted']!r}: must be {' or '.join(ACTIONS)}")
    if not isinstance(p["a365_transition"], str):
        raise ValueError(f"{where} a365_transition: the name of an Altium 365 life-cycle transition or state, as text")
    return p


def decide(changes, pol, mode="gate"):
    """-> {action: pass | warn | hold, why: [...], by_class: {class: action}}"""
    pol = pol or POLICY
    by = {c["class"]: pol[CLASS_KEY[c["class"]]] for c in changes}
    action = max(by.values(), key=ACTIONS.index, default="pass")
    why = [f"{cls}: {act}" for cls, act in sorted(by.items(), key=lambda x: CD.RANK[x[0]])]
    if mode == "restyle":
        bad = [c for c in changes if c["class"] != "cosmetic"]
        if bad:
            action = "hold"
            why.insert(0, f"a restyle may only change cosmetics; this one changed {bad[0]['class']} ({bad[0]['text']}): "
                          "held (that is an ecadlib bug, please report it)")
    elif mode == "observe":
        action = "warn" if any(c["class"] != "cosmetic" for c in changes) else "pass"
        why.insert(0, "released in the workspace: recorded, not held")
    return {"action": action, "why": why, "by_class": by}


def released(item):
    return item.get("status") in RELEASED_STATUSES


def lib_folder(plan_or_folder):
    p = Path(plan_or_folder)
    return p.parent if p.suffix.lower() == ".json" else p


def _user_key(who):
    from .approvals import user_key
    return user_key(who)


# ---------------------------------------------------------------- the guard
def _locked_step(fn):
    """A step that records into the ledger runs under the library lock and lets it go at its end (a lock begin() took
    is held through the writer's work to here)."""
    import functools

    @functools.wraps(fn)
    def step(self, *a, **k):
        self.hold_lock()
        try:
            return fn(self, *a, **k)
        finally:
            self.close()
    return step


class Guard:
    """One writer's pass over a library folder. Use as a context manager (begin on enter) or call begin() yourself."""

    def __init__(self, folder, writer, rules=None, user=None, log=None, base=None, chosen_by=None, adopt=None):
        """chosen_by: the person whose explicit choice this write is (ecadlib resolve / review): their choice counts as
        one approval, so with approvers = 1 it is released (decision "approved") once the chosen version passes the
        release check, with 2 it waits for one more. adopt: {part: reason} a person confirmed (`ecadlib part adopt`):
        those parts are recorded as they are on disk although the ledger shows they had a history."""
        if writer not in WRITERS:
            raise ValueError(f"writer {writer!r} is not in change_gate.WRITERS")
        self.folder = lib_folder(folder)
        self.base = Path(base) if base else self.folder
        self.writer, self.mode = writer, WRITERS[writer]["mode"]
        self.ledger = LG.Ledger(self.folder)
        self.rules = rules
        self.policy = policy(rules, self.folder)
        self.user = user
        self.log = log or (lambda *a, **k: None)
        self.report = []
        self.chosen_by = chosen_by
        self.adopt = dict(adopt or {})
        self.adopt_changes = {}                # part -> the changes a person accepted when adopting (part adopt)
        self.pre = {}                          # path -> bytes on disk at begin() that are the released part
        self.pre_steps = {}                    # PcbLib path -> {STEP path: bytes} of a head recorded without its STEPs
        self._lock = None

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def hold_lock(self):
        """Take the library lock (once) and read the ledger again: what another run recorded counts from here."""
        if self._lock is None:
            self._lock = LG.lock(self.folder)
            self.ledger.reload()

    def close(self):
        """Let the library lock go (also on leaving a `with`, and when the guard is dropped)."""
        if self._lock is not None:
            key, self._lock = self._lock, None
            LG.unlock(key)

    def who(self):
        from .approvals import identity
        return identity(self.user, cwd=self.folder)

    # ------------------------------------------------------------ before the writer
    def begin(self, items=None):
        """Adopt released parts that have no ledger head yet (a library from before change control), as they are on disk
        now. items: the plan as it is on disk (default: <folder>/plan.json). A part whose history the ledger lost (the
        blob store, the usage index or other ledger lines show it was released) is never adopted: LedgerError."""
        self.hold_lock()
        if items is None:
            items = read_plan(self.folder)
        n, lost = 0, []
        for it in items or []:
            head = self.ledger.head(it["name"]) if released(it) else None
            if released(it) and not head and self._has_files(it):
                why = self.lost_history(it["name"])
                if why and it["name"] not in self.adopt:
                    lost.append((it["name"], why))
                    continue
                fp = FP.of_item(it, self.base)
                if fp.get("notes"):            # an unreadable file is no released revision to keep
                    continue
                self._release(it, fp, self.adopt_changes.get(it["name"], []) if why else [],
                              {"action": "pass", "why": []}, decision="adopted" if why else "baseline",
                              reason=self.adopt.get(it["name"]) or "adopted: released before change control recorded it",
                              by=self.who() if why else None)
                n += 1
            elif head:
                self._keep_bytes(it, head)
        self._refuse_lost(lost)
        if n:
            self.log(f"  change control: {n} released part(s) recorded as revision 1 (they were released before the ledger)")
        return n

    def lost_history(self, part):
        """Why the ledger must have known `part` although it has no release line for it (the ledger file was deleted,
        emptied or cut), or None. Adopting the disk then would hide whatever changed since: fail closed."""
        lg = self.ledger
        if not lg.lines():
            blobs = lg.store / "blobs"
            if blobs.is_dir() and any(blobs.iterdir()):
                return f"{LG.LEDGER} is missing or empty, but {LG.STORE}/blobs holds released files"
        for e in lg.history(part):
            if e.get("event") in ("publish", "retired", "superseded", "overwritten") or e.get("base") is not None:
                return f"{LG.LEDGER} has {e.get('event')} lines for it but no release line"
        try:
            used = [(p, r) for p, r, _ in LG.used_in(self.folder, part) if r]
        except (OSError, ValueError):
            used = []
        if used:
            return f"{LG.USAGE} records revision r{used[0][1]} in use by {used[0][0]}"
        prop = lg.proposal(part)
        if prop and prop.get("base") is not None:
            return f"a held change to its revision r{prop['base']} waits in {LG.STORE}/proposed"
        return None

    def stored_fingerprint(self, part):
        """The newest fingerprint of `part` the library still holds after its ledger history was lost -> (fingerprint,
        where) or (None, None). Fingerprints stay in the blob store (they carry the part's name); one whose digest a
        surviving ledger line names as published or retired is preferred, the newest file otherwise. Held changes keep
        their fingerprints in the store too: one a surviving line or a proposal names as held is never taken."""
        hist = self.ledger.history(part)
        named = [e["digest"] for e in reversed(hist) if e.get("digest") and e.get("event") in ("publish", "retired")]
        held = {e["digest"] for e in hist if e.get("digest") and e.get("event") not in ("publish", "retired", "release")}
        held |= {p.get("digest") for p in self.ledger.proposals() if p.get("part") == part}
        found = self.ledger.stored_fingerprints(part)
        if not found:
            return None, None
        for d in named:
            hit = next((x for x in found if x[2]["hashes"].get("digest") == d), None)
            if hit:
                return hit[2], f"the fingerprint of digest {d[:12]} that {LG.LEDGER} still names"
        found = [x for x in found if x[2]["hashes"].get("digest") not in held]
        if not found:
            return None, None
        _, sha, fp = max(found, key=lambda x: (x[0], x[1]))
        return fp, f"the newest fingerprint of {part} in {LG.STORE}/blobs ({sha[:12]})"

    def _refuse_lost(self, lost):
        if not lost:
            return
        names = ", ".join(n for n, _ in lost[:8]) + (f" and {len(lost) - 8} more" if len(lost) > 8 else "")
        raise LG.LedgerError(
            f"{self.folder}: the ledger has no release of {names}, but the library shows it was released before "
            f"({lost[0][1]}); the files on disk are not taken as revision 1 without a person, so nothing was changed",
            f"restore {LG.LEDGER} from git (`git checkout -- {LG.LEDGER}` in the library folder); or, after checking the "
            f"files on disk by hand: ecadlib part adopt {self.folder} --part {lost[0][0]} -m \"why\"")

    def _keep_bytes(self, it, head):
        """The files on disk now, when their bytes differ from the head's snapshot but they still are the released
        part (same fingerprint: rewritten with new timestamps, a simulation model attached). A hold puts these bytes
        back rather than the older snapshot, so nothing outside the fingerprint is lost."""
        recs = {str((self.base / r["path"]).resolve()): r for r in (head.get("files") or {}).values()}
        now = {}
        for p, r in recs.items():
            f = Path(p)
            side = f.with_name(f.name + ".ecadbuild")
            if r.get("ecadbuild") and "steps" not in r and side.is_file() and LG.sha_bytes(side.read_bytes()) == r["ecadbuild"]:
                # a head recorded before the ledger kept 3D models: keep the STEP files its (unchanged) recipe names
                self.pre_steps[p] = {str(f.parent / s): (f.parent / s).read_bytes() for s in LG.step_files(f)
                                     if (f.parent / s).is_file()}
            if f.is_file():
                b = f.read_bytes()
                if LG.sha_bytes(b) != r["sha256"]:
                    now[p] = b
                    side = f.with_name(f.name + ".ecadbuild")
                    if side.is_file():
                        now[str(side)] = side.read_bytes()
        if now and self._is_head(FP.of_item(it, self.base), head):
            self.pre.update(now)

    def _is_head(self, fp, head):
        """fp is the released revision: the same digest as the head's, or (a head an older ecadlib fingerprinted) as
        the head's snapshot fingerprinted again now."""
        d = fp["hashes"]["digest"]
        return d == head.get("digest") or d == ((self.ledger.fingerprint(head) or {}).get("hashes") or {}).get("digest")

    def _restore(self, files):
        """restore_files, preferring the bytes begin() kept for the same paths."""
        rest = {}
        for k, r in (files or {}).items():
            p = str((self.base / r["path"]).resolve()) if not Path(r["path"]).is_absolute() else str(Path(r["path"]).resolve())
            if p in self.pre:
                for f in (Path(p), Path(p + ".ecadbuild")):
                    data = self.pre.get(str(f))
                    if data is not None and (not f.is_file() or f.read_bytes() != data):
                        tmp = f.with_name(f.name + ".restore.tmp")
                        tmp.write_bytes(data)
                        os.replace(tmp, f)
            else:
                rest[k] = r
        self.ledger.restore_files(rest, self.base)
        for r in (files or {}).values():           # STEP files of a head recorded without them: as they were at begin()
            p = str((self.base / r["path"]).resolve()) if not Path(r["path"]).is_absolute() else str(Path(r["path"]).resolve())
            if "steps" in r:
                continue
            for s, data in self.pre_steps.get(p, {}).items():
                f = Path(s)
                if not f.is_file() or f.read_bytes() != data:
                    f.parent.mkdir(parents=True, exist_ok=True)
                    tmp = f.with_name(f.name + ".restore.tmp")
                    tmp.write_bytes(data)
                    os.replace(tmp, f)

    def _has_files(self, it):
        return any((self.base / p if not Path(p).is_absolute() else Path(p)).is_file() for p in LG.item_files(it).values())

    # ------------------------------------------------------------ after the writer
    @_locked_step
    def settle(self, items):
        """Compare every released part with its ledger head and apply the policy. -> report rows."""
        rows, todo = [], {}
        for it in items:
            head = self.ledger.head(it["name"])
            if not head:
                continue
            new = FP.of_item(it, self.base)
            if new["hashes"]["digest"] == head.get("digest"):
                self._mark_unchanged(it, head)
                continue
            old = self.ledger.fingerprint(head)
            changes = CD.diff(old, new) if old else [{"class": "electrical", "area": "ledger", "what": "fingerprint",
                                                       "old": None, "new": None, "text": "the released fingerprint is missing"}]
            if not changes:
                self._mark_unchanged(it, head)
                continue
            dec = decide(changes, self.policy, self.mode)
            rej = self.ledger.rejected(it["name"], new["hashes"]["digest"], new) if dec["action"] == "hold" else None
            todo[it["name"]] = {"item": it, "head": head, "fp": new, "changes": changes, "dec": dec, "rejected": rej}
        held_files = {}
        for name, t in todo.items():
            if t["dec"]["action"] == "hold":
                for p in self._paths(t["item"]) | self._paths_of(t["head"]):
                    held_files.setdefault(p, name)
        grew = True
        while grew:                            # a part sharing a file with a held part is held with it
            grew = False
            for name, t in todo.items():
                if t["dec"]["action"] == "hold":
                    continue
                hit = next((p for p in self._paths(t["item"]) | self._paths_of(t["head"]) if p in held_files), None)
                if hit:
                    t["dec"] = dict(t["dec"], action="hold", why=[f"shares {Path(hit).name} with {held_files[hit]}, whose "
                                                                  f"change is held"] + t["dec"]["why"])
                    for p in self._paths(t["item"]) | self._paths_of(t["head"]):
                        held_files.setdefault(p, name)
                    grew = True
        if self.chosen_by and self.mode == "gate" and self.policy["approvers"] <= 1:
            for t in todo.values():            # a person's own choice is the approval
                if t["dec"]["action"] == "hold":
                    t["approved"] = True
            bad = self._release_check([t for t in todo.values() if t.get("approved")])
            if bad:                            # like `part approve`: a version that fails the release check is not
                for t in todo.values():        # released; it waits as a held change and the choice is not counted
                    if t.pop("approved", None):
                        t["refused"] = {"by": self.chosen_by, "at": LG.now(), "reason": f"not applied: the chosen version "
                                        f"fails the release check ({bad}); the released version stays"}
        held = [t for t in todo.values() if t["dec"]["action"] == "hold" and not t.get("approved")]
        for t in held:                         # every proposal is copied before any shared file goes back to its head
            t["prop"] = self._propose(t)
        for t in held:                         # then the held files go back, before anything is released
            rows.append(self._hold(t))
        for name, t in todo.items():
            if t["dec"]["action"] != "hold":
                rows.append(self._release(t["item"], t["fp"], t["changes"], t["dec"], base=t["head"]))
            elif t.get("approved"):
                rows.append(self._release(t["item"], t["fp"], t["changes"], dict(t["dec"], action="pass"), base=t["head"],
                                          decision="approved", approved_by=[self.chosen_by], by=self.chosen_by,
                                          reason=f"chosen by {self.chosen_by}"))
        self.report += rows
        for r in rows:
            self.log(f"  change {r['action']:<5} {r['part']:<28} {r['summary']}")
        return rows

    def _release_check(self, todo):
        """The release check on copies of these items as the writer left them -> "" when all pass, else the reasons."""
        from . import release_check as RC
        probe = [json.loads(json.dumps(t["item"], default=str)) for t in todo if not t["item"].get("_manifest")]
        if not probe:
            return ""
        RC.apply(probe, self.base, rules=self.rules)
        return "; ".join(f"{i['name']}: {', '.join(i.get('hold_reasons') or [])}" for i in probe if i.get("status") == "held")

    @_locked_step
    def restyle(self, before, after):
        """A restyle (ecadlib style apply, fp_tidy) may only change cosmetics. Each part's before -> after difference
        is classified; anything else is an ecadlib bug, so that part keeps its fields (action "refused"). The parts
        that passed then settle against their released revision like any write (mode gate: an earlier hand edit
        found there is held as a change, not blamed on the restyle). -> report rows"""
        rows, keep = [], []
        bmap = {i["name"]: i for i in before}
        for it in after:
            b = bmap.get(it["name"])
            if b is None or LG.content_of(b) == LG.content_of(it):
                continue
            ch = CD.diff(FP.of_item(b, self.base), FP.of_item(it, self.base))
            bad = [c for c in ch if c["class"] != "cosmetic"]
            if bad:
                LG.apply_content(it, LG.content_of(b))
                head = self.ledger.head(it["name"]) or {}
                rows.append({"part": it["name"], "action": "refused", "revision": head.get("revision"),
                             "summary": f"a restyle may only change cosmetics; this one changed {bad[0]['class']} "
                                        f"({bad[0]['text']}): not applied (an ecadlib bug, please report it)",
                             "changes": [{"class": c["class"], "text": c["text"]} for c in ch]})
            else:
                keep.append(it)
        mode, self.mode = self.mode, "gate"
        try:
            rows += self.settle(keep)
        finally:
            self.mode = mode
        for r in rows:
            if r["action"] == "refused":
                self.report.append(r)
                self.log(f"  change refused {r['part']:<28} {r['summary']}")
        return rows

    @_locked_step
    def baseline(self, items):
        """Revision 1 for every part that is released now (status ready / existing) and has no ledger head. A new part
        carrying the MPN of a released part that left the plan (the librarian renamed RES-0603-10k to RES-0402-10k
        after a BOM change) is compared with that part first: a difference the policy holds is held for a person
        (`ecadlib part approve` records its revision 1), so a rename never slips a changed part out unreviewed."""
        todo, lost = [], []
        for it in items:
            if released(it) and not self.ledger.head(it["name"]) and self._has_files(it):
                why = self.lost_history(it["name"])
                if why and it["name"] not in self.adopt:
                    lost.append((it["name"], why))
                else:
                    todo.append((it, why))
        self._refuse_lost(lost)
        rows, heads = [], None
        names = {i["name"] for i in items}
        for it, why in todo:
            fp = FP.of_item(it, self.base)
            if fp.get("notes"):                # an unreadable file is no released revision to keep
                continue
            if self.mode == "gate" and not why and not it.get("_manifest"):
                if heads is None:              # released parts no longer in the plan, by MPN
                    heads = self._retired_by_mpn(names)
                row = self._twin_hold(it, fp, heads)
                if row:
                    rows.append(row)
                    continue
            rows.append(self._release(it, fp, self.adopt_changes.get(it["name"], []) if why else [],
                                      {"action": "pass", "why": []}, decision="adopted" if why else "baseline",
                                      reason=self.adopt.get(it["name"]) or "first release", by=self.who() if why else None))
        self.report += rows
        for r in rows:
            if r["action"] == "hold":
                self.log(f"  change hold  {r['part']:<28} {r['summary']}")
        return rows

    def _retired_by_mpn(self, names):
        out = {}
        for part in self.ledger.parts():
            head = self.ledger.head(part)
            if part in names or not head:
                continue
            m = mpn_of((head.get("item") or {}).get("parameters"))
            if m:
                out.setdefault(m, []).append(head)
        return out

    def _twin_hold(self, it, fp, heads):
        """Hold a new part whose MPN a released part (not in the plan any more) has, when the policy holds the
        difference between them -> the report row, or None to release it as usual."""
        best = None
        for head in heads.get(mpn_of(it.get("parameters")), []):
            old = self.ledger.fingerprint(head)
            changes = CD.diff(old, fp) if old else []
            dec = decide(changes, self.policy, self.mode)
            if best is None or len(changes) < len(best[1]):
                best = (head, changes, dec)
        if not best or best[2]["action"] != "hold":
            return None
        head, changes, dec = best
        twin = {"part": head["part"], "revision": head["revision"]}
        dec = dict(dec, why=[f"a new part with the MPN of released {twin['part']} r{twin['revision']}, which left the "
                             f"plan: compared with it"] + dec["why"])
        t = {"item": it, "head": None, "fp": fp, "changes": changes, "dec": dec, "twin": twin,
             "rejected": self.ledger.rejected(it["name"], fp["hashes"]["digest"], fp)}
        prop = self._propose(t)
        it["change"] = held_block(prop, self.ledger)
        it["status"] = "held"                  # not released (no revision yet): publishers leave it out
        why = (f"change control: new part with the MPN of released {twin['part']} r{twin['revision']} "
               f"({CD.summary(changes)}); a person approves it: ecadlib part show")
        it["hold_reasons"] = [h for h in it.get("hold_reasons") or [] if not h.startswith("change control:")] + [why]
        return {"part": it["name"], "action": "hold", "revision": None, "twin": twin,
                "summary": f"new part with the MPN of released {twin['part']} r{twin['revision']}: {CD.summary(changes)}",
                "changes": [{"class": c["class"], "text": c["text"]} for c in changes], "rejected": bool(t["rejected"]),
                "why": dec["why"]}

    @_locked_step
    def retire(self, names, reason="no longer in the plan"):
        """A `retired` ledger line for each released part that left the plan (once; it is listed again if it comes back
        and leaves again). -> the parts recorded."""
        done = []
        for n in names:
            head = self.ledger.head(n)
            hist = self.ledger.history(n)
            if not head or (hist and hist[-1].get("event") == "retired"):
                continue
            self.ledger.append({"event": "retired", "part": n, "revision": head["revision"], "digest": head.get("digest"),
                                "writer": self.writer, "by": self.who(), "reason": reason})
            self.log(f"  change retired {n:<28} r{head['revision']} {reason}")
            done.append(n)
        return done

    def finish(self, items):
        return self.settle(items) + self.baseline(items)

    # ------------------------------------------------------------ helpers
    def _paths(self, item):
        return {str((self.base / p).resolve()) if not Path(p).is_absolute() else str(Path(p).resolve())
                for p in LG.item_files(item).values()}

    def _paths_of(self, entry):
        return {str((self.base / r["path"]).resolve()) if not Path(r["path"]).is_absolute() else str(Path(r["path"]).resolve())
                for r in (entry.get("files") or {}).values()}

    def _mark_unchanged(self, it, head):
        prop = self.ledger.proposal(it["name"])
        if prop and prop.get("base") == head.get("revision"):
            it["change"] = held_block(prop, self.ledger)
        elif (it.get("change") or {}).get("state") == "held":
            it.pop("change", None)

    def _release(self, it, fp, changes, dec, base=None, decision=None, reason="", approved_by=None, event="release", by=None):
        prev = self.ledger.head(it["name"])
        rev = (prev or {}).get("revision", 0) + 1
        entry = {"event": event, "part": it["name"], "revision": rev, "base": (prev or {}).get("revision"),
                 "writer": self.writer, "by": by or self.who(), "decision": decision or ("warn" if dec["action"] == "warn" else
                                                                                            "observed" if self.mode == "observe" else "pass"),
                 **{k: fp["hashes"][k] for k in ("eid", "gid", "mid", "cid", "digest")},
                 "fingerprint": self.ledger.put_json(fp), "files": self.ledger.snapshot_files(it, self.base),
                 "item": LG.content_of(it), "changes": [{"class": c["class"], "text": c["text"]} for c in changes],
                 "classes": CD.counts(changes), "source": dict(it.get("source") or {}, ecadlib=__version__),
                 **({"approved_by": approved_by} if approved_by else {}), **({"reason": reason} if reason else {}),
                 **({"why": dec["why"]} if dec.get("why") else {})}
        rid = (it.get("a365") or {}).get("revision") if self.mode == "observe" else None
        if rid:                                # the workspace revision these files are (project check maps it back)
            entry["a365_revision"] = rid
        self.ledger.append(entry)
        old = self.ledger.proposal(it["name"])
        if old and event == "release" and decision != "approved":
            self.ledger.append({"event": "superseded", "part": it["name"], "digest": old.get("digest"), "revision": rev,
                                "writer": self.writer, "by": entry["by"]})
            self.ledger.drop_proposal(it["name"])
        if dec["action"] == "warn" and changes:
            it["change"] = {"state": "released", "revision": rev, "decision": "warn", "summary": CD.summary(changes),
                            "changes": entry["changes"]}
        elif (it.get("change") or {}).get("state") == "held":
            it.pop("change", None)
        return {"part": it["name"], "action": "baseline" if entry["decision"] == "baseline" else dec["action"],
                "revision": rev, "summary": CD.summary(changes) if changes else reason or "released", "changes": entry["changes"]}

    def _propose(self, t):
        """Save the held version as the part's proposal (its files go to the blob store) -> the proposal."""
        it, head, fp, changes, dec = t["item"], t["head"], t["fp"], t["changes"], t["dec"]
        name, digest = it["name"], fp["hashes"]["digest"]
        base = head["revision"] if head else None          # None: a new part held against its MPN twin (baseline)
        prev = self.ledger.proposal(name)
        if prev and digest in self.ledger.digests(prev) and prev.get("base") == base:
            if t.get("refused"):
                self._refused(prev, t["refused"])
            return prev
        chosen = self.chosen_by and not t.get("refused")    # a refused choice is not counted as an approval
        prop = {"part": name, "base": base, "digest": digest, "writer": self.writer, "by": self.who(),
                "at": LG.now(), "fingerprint": self.ledger.put_json(fp), "files": self.ledger.snapshot_files(it, self.base),
                "item": LG.content_of(it), "changes": changes, "classes": CD.counts(changes), "why": dec["why"],
                "approvals": [{"who": self.chosen_by, "key": _user_key(self.chosen_by), "when": LG.now(),
                               "comment": f"chose it ({self.writer})"}] if chosen else [],
                "rejected": t["rejected"] or None, "source": dict(it.get("source") or {}),
                "used_in": [p for p, *_ in LG.used_in(self.folder, name)], **({"twin": t["twin"]} if t.get("twin") else {})}
        self.ledger.save_proposal(prop)
        if not t["rejected"]:
            self.ledger.append({"event": "hold", "part": name, "base": base, "digest": digest,
                                "writer": self.writer, "by": prop["by"], "classes": prop["classes"],
                                "changes": [{"class": c["class"], "text": c["text"]} for c in changes], "why": dec["why"],
                                **({"twin": t["twin"]} if t.get("twin") else {})})
        if t.get("refused"):
            self._refused(prop, t["refused"])
        return prop

    def _refused(self, prop, refused):
        prop["refused"] = refused
        self.ledger.save_proposal(prop)
        self.ledger.append({"event": "refused", "part": prop["part"], "digest": prop["digest"], "by": refused["by"],
                            "base": prop["base"], "reason": refused["reason"], "writer": self.writer})

    def _hold(self, t):
        """Put the released version back (files and plan fields) and mark the item held."""
        it, head, changes, prop = t["item"], t["head"], t["changes"], t["prop"]
        name = it["name"]
        self._restore(head.get("files"))
        LG.apply_content(it, head.get("item") or {})
        it["change"] = held_block(prop, self.ledger)
        return {"part": name, "action": "hold", "revision": head["revision"], "summary": CD.summary(changes),
                "changes": [{"class": c["class"], "text": c["text"]} for c in changes],
                "rejected": bool(t["rejected"]), "why": t["dec"]["why"],
                **({"refused": t["refused"]["reason"]} if t.get("refused") else {})}


def mpn_of(params):
    """The MPN among a plan item's parameters ([{name, value}] or {name: value}), compared without case or spaces."""
    if isinstance(params, list):
        params = {p.get("name"): p.get("value") for p in params if isinstance(p, dict)}
    return "".join(FP.identity(params or {})["mpn"].split()).upper()


# ---------------------------------------------------------------- held changes: what a person decides on
def proposal_group(lg, part):
    """The held proposal of `part` and every other proposal that shares one of its files (decided together)."""
    props = {p["part"]: p for p in lg.proposals()}
    if part not in props:
        raise ChangeError(f"{part}: no held change (see `ecadlib part status`)")
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


def review_digest(lg, part, group=None):
    """What a person reviews and approves: the proposal's digest, or for parts decided together (a shared file) one
    digest over all of their proposals. `part show` prints it, `part approve --digest` and the web UI send it back,
    so an approval never applies to a proposal that changed after it was read."""
    group = group or proposal_group(lg, part)
    if len(group) == 1:
        return group[0]["digest"]
    return LG.sha_bytes("\n".join(sorted(f"{p['part']}:{p.get('base')}:{p['digest']}" for p in group)).encode())


def held_block(prop, lg=None):
    """The `change` block a plan item carries while a change to it is held (with lg: the review digest too)."""
    ch = prop.get("changes") or []
    digest = prop.get("digest")
    if lg is not None:
        try:
            digest = review_digest(lg, prop["part"])
        except ChangeError:
            pass
    return {"state": "held", "base": prop.get("base"), "summary": CD.summary(ch), "classes": prop.get("classes") or CD.counts(ch),
            "changes": [{"class": c["class"], "text": c["text"]} for c in ch], "writer": prop.get("writer"),
            "proposed_at": prop.get("at"), "approvals": [a.get("who") for a in prop.get("approvals") or []],
            **({"digest": digest} if digest else {}),
            **({"twin": prop["twin"]} if prop.get("twin") else {}),
            **({"rejected_by": prop["rejected"].get("by")} if prop.get("rejected") else {}),
            **({"refused": prop["refused"].get("reason")} if prop.get("refused") else {}),
            **({"used_in": prop["used_in"]} if prop.get("used_in") else {})}


# ---------------------------------------------------------------- plan helpers
def read_plan(folder):
    f = lib_folder(folder) / "plan.json"
    if not f.is_file():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return []


def write_plan(plan, items):
    from .fsutil import write_text_atomic
    write_text_atomic(Path(plan), json.dumps([i for i in items if not i.get("_manifest")], indent=2))


def publish_guard(plan, items, target, rules=None, user=None, log=None, base=None, save=True):
    """Before a publisher writes anything: settle changes made since the last writer (hand edits), record first
    releases, and say per item whether its head may be published. -> {name: None | reason}. The plan file is
    rewritten when settling changed an item (a held hand edit put the released version back)."""
    plan = Path(plan)
    folder = plan.parent if plan.suffix.lower() == ".json" else plan
    before = json.dumps(items, sort_keys=True, default=str)
    g = Guard(folder, "hand-edit", rules=rules, user=user, log=log, base=base or folder)
    try:                                       # under the library lock: parallel publishers settle one at a time
        g.finish(items)
        if save and plan.suffix.lower() == ".json" and json.dumps(items, sort_keys=True, default=str) != before:
            write_plan(plan, items)
        out = {}
        for it in items:
            head = g.ledger.head(it["name"])
            out[it["name"]] = None if head else ("not released yet" + (f" (status {it.get('status')})" if it.get("status") else ""))
    finally:
        g.close()
    return out, g


def publishing(folder):
    """The library lock for one publisher run (a context manager). A publisher holds it from publish_guard through
    reading or copying the part files to record_publish, so another writer's change can never be shipped (and
    recorded as published) between the settle step and the read; both steps take the same lock again inside."""
    return LG.locked(lib_folder(folder))


def record_publish(folder, items, target, ok=lambda it: True, by=None, detail=None):
    """A `publish` ledger line per item that went to `target` (its head revision)."""
    with LG.locked(lib_folder(folder)):
        return _record_publish(folder, items, target, ok, by, detail)


def _record_publish(folder, items, target, ok, by, detail):
    lg = LG.Ledger(lib_folder(folder))
    n = 0
    for it in items:
        head = lg.head(it["name"])
        if not head or not ok(it):
            continue
        last = lg.published(it["name"], target)
        d = detail(it) if detail else None
        if last and last.get("revision") == head["revision"] and last.get("digest") == head.get("digest") and not (
                (d or {}).get("ksym") and not (last.get("detail") or {}).get("ksym")):   # (a KiCad baseline not yet kept)
            continue
        lg.append({"event": "publish", "part": it["name"], "target": target, "revision": head["revision"],
                   "digest": head.get("digest"), "by": by or "", **({"detail": d} if detail else {})})
        n += 1
    return n


def manifest_items(mdir):
    """The parts of a kicad_import folder (no plan.json) as plan-like items: one per conversion spec, named by its MPN or
    its file name, with the SchLib / PcbLib the manifest lists (paths relative to the folder)."""
    mdir = Path(mdir)
    try:
        man = json.loads((mdir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    by = {}
    for key, e in sorted((man.get("entries") or {}).items()):
        spec = e.get("spec") or {}
        k = json.dumps(spec, sort_keys=True)
        it = by.setdefault(k, {"name": None, "status": "ready", "symbol": {"ref": None, "file": None},
                               "footprint": {"ref": None, "file": None}, "parameters": {}})
        half = "footprint" if e.get("kind") == "footprint" else "symbol"
        it[half]["file"] = e.get("output")
        it[half]["kicad_ref"] = e.get("kicad_ref")
        it["name"] = spec.get("mpn") or it["name"] or Path(e.get("output") or key).stem
    names, out = set(), []
    for it in by.values():                    # two specs with the same MPN stay two parts
        n, i = it["name"], 2
        while it["name"] in names:
            it["name"] = f"{n}~{i}"; i += 1
        names.add(it["name"])
        out.append(it)
    return out


def library_items(folder):
    """Every part change control guards in a library folder: the plan's items, then the parts of a kicad_import
    manifest there (<folder>/manifest.json or <folder>/models/manifest.json) whose files no plan item uses, marked
    "_manifest" (write_plan leaves them out). Their file paths are made relative to the folder."""
    import os
    folder = lib_folder(folder)
    plan = read_plan(folder)
    res = lambda p: str((folder / p).resolve())
    used = {res(p) for it in plan for p in LG.item_files(it).values()}
    names = {it["name"] for it in plan}
    extra = []
    for md in (folder, folder / "models"):
        if not (md / "manifest.json").is_file():
            continue
        for it in manifest_items(md):
            for h in ("symbol", "footprint"):
                if it[h].get("file"):
                    it[h]["file"] = os.path.relpath(md / it[h]["file"], folder).replace(os.sep, "/")
            if any(res(p) in used for p in LG.item_files(it).values()):
                continue
            n, k = it["name"], 2
            while it["name"] in names:
                it["name"] = f"{n}~{k}"; k += 1
            names.add(it["name"])
            it["_manifest"] = True
            extra.append(it)
    return plan, extra


def guarded_rewrite(manifest, writer, fn, rules=None, user=None, log=None):
    """Run fn() (it rewrites files a kicad_import manifest lists: upstream --update, upkeep --apply) under the gate.
    The library is the plan.json next to the manifest (or one folder up: <plan dir>/models/manifest.json), else the
    manifest's folder itself; library_items() lists what is guarded. A held part's files go back to the released
    revision and its manifest entries too, so the upstream check keeps reporting the change; the proposal carries the
    new entries for `ecadlib part approve`. -> (fn's result, {path: "held: ..."} for held files, gate rows)"""
    mpath = Path(manifest)
    mdir = mpath.parent
    plan = next((d / "plan.json" for d in (mdir, mdir.parent) if (d / "plan.json").is_file()), None)
    folder = plan.parent if plan else mdir
    items, extra = library_items(folder)
    old_man = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {"entries": {}}
    g = Guard(folder, writer, rules=rules, user=user, log=log)
    try:
        return _guarded_rewrite(g, fn, mpath, mdir, plan, folder, items, extra, old_man)
    finally:
        g.close()


def _guarded_rewrite(g, fn, mpath, mdir, plan, folder, items, extra, old_man):
    g.begin(items + extra)
    res = fn()
    _, fresh = library_items(folder)           # a reconversion may add entries; names are stable
    fresh = {i["name"]: i for i in fresh}
    extra = [fresh.pop(i["name"], i) for i in extra] + list(fresh.values())
    rows = g.settle(items + extra)
    g.baseline(items + extra)
    every = items + extra
    held = {}
    for r in rows:
        if r["action"] != "hold":
            continue
        it = next(i for i in every if i["name"] == r["part"])
        for p in g._paths(it):
            held[p] = (f"held: change to released {r['part']} r{r['revision']} ({r['summary']}); "
                       f"ecadlib part show {plan or folder} {r['part']}")
    if held and mpath.is_file():               # the manifest goes back for the held files: still "changed" upstream
        man = json.loads(mpath.read_text(encoding="utf-8"))
        for r in rows:                         # an approval writes the new entries (ecadlib part approve)
            prop = g.ledger.proposal(r["part"]) if r["action"] == "hold" else None
            if prop is not None:
                mine = g._paths(next(i for i in every if i["name"] == r["part"]))
                prop["manifest"] = {"path": os.path.relpath(mpath, folder).replace(os.sep, "/"),
                                    "entries": {k: e for k, e in man.get("entries", {}).items()
                                                if str((mdir / (e.get("output") or "")).resolve()) in mine}}
                g.ledger.save_proposal(prop)
        for key, e in list(man.get("entries", {}).items()):
            if str((mdir / (e.get("output") or "")).resolve()) in held:
                if key in old_man.get("entries", {}):
                    man["entries"][key] = old_man["entries"][key]
                else:
                    man["entries"].pop(key)
        from .upstream import save_manifest
        save_manifest(mpath, man)
    if plan:
        write_plan(plan, items)
    return res, held, rows


def project_key(folder, path):
    """How a project / BOM is named in library.usage.json: its path relative to the library folder (posix slashes),
    so the index stays the same when the library is cloned elsewhere; absolute only across Windows drives."""
    import os
    p = Path(path).resolve()
    try:
        rel = os.path.relpath(p, Path(folder).resolve())
    except ValueError:                         # another drive on Windows
        return p.as_posix()
    return Path(rel).as_posix()


def record_bom_usage(folder, bom, items):
    """The librarian's BOM is a user of every part it lists, at the revision released now."""
    lg = LG.Ledger(folder)
    if not lg.exists():
        return None
    parts = {it["name"]: {"revision": (lg.head(it["name"]) or {}).get("revision")} for it in items}
    return LG.record_usage(folder, project_key(folder, bom), parts, tool="bom")


def pending(folder):
    """Held changes waiting for a decision: [proposal]."""
    return LG.Ledger(lib_folder(folder)).proposals()
