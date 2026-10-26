"""Sign-off before release: people approve or reject plan items; altium365 push releases only what the policy allows.

    python3 -m ecadlib.approvals out/librarian/plan.json status
    python3 -m ecadlib.approvals out/librarian/plan.json approve --part CH340C [--role librarian] [-m "pinout checked"]
    python3 -m ecadlib.approvals out/librarian/plan.json reject  --part CH340C -m "pin 9 is V3, not VCC"
    python3 -m ecadlib.approvals out/librarian/plan.json approve --all [--user "Ann <ann@x.com>"]
    (or: ecadlib approve PLAN ...)

Policy: approvals.json (or approvals.toml) next to the plan. Without one nothing changes: push behaves as before and
approvals are only recorded. Keys (all optional):
  required          number of distinct people whose approval counts (default 1)
  roles             roles that each need at least one approval, e.g. ["librarian", "hardware"]
  users             {"ann@x.com": ["librarian"], ...}: when given, only these people may sign, and only with their roles
  no_self_approval  the person who released the item with --allow cannot approve it (default true)
  applies_to        "all" (default) or "allowed": only items released with an --allow override need sign-off
  parts             fnmatch patterns limiting which parts need sign-off (default: every part)
  reject_blocks     a current reject holds the item until that person approves again (default true). Without any
                    policy a current reject holds the item too; only approvals need a policy to be required
Each decision is stored in the item's `approvals` list: who, key, when, role, decision, comment and `hash`, a sha256
over the item's .SchLib/.PcbLib bytes and its key fields (name, folder, description, comment, parameters, cleared,
symbol/footprint refs). Any later change to them makes an approval stale; it then no longer counts. A reject keeps
holding whatever changed since, until that person approves (a restyle, a revert or a re-run never lifts it). That approve
is always recorded, even when the person may not sign (no longer in users, or released it with --allow): it takes the
reject back without counting as a sign-off. A reject by someone no longer in the policy's users is also lifted by an
approve that counts, recorded after it. The parameters
the JLCPCB assembly check wrote until it was removed (Assembly *, LCSC Part) do not count, so a plan built while it
existed keeps its sign-offs. The latest
decision per person counts. Every decision (and every refused one) is appended to approvals_audit.jsonl next to
the plan.
release_check calls post_check() at the end: with a policy, an item that passes but is not signed off gets status
`awaiting_approval`, and `approval` records the policy, the content hash at check time and what is missing.
altium365 push calls push_block(): it skips items awaiting approval, items whose approvals went stale, items changed
since the release check, and items dedupe marked as duplicates.
Identity: --user, else $ECADLIB_USER, else `git config user.name` / `user.email`, else the login name."""
import argparse, contextlib, datetime, fnmatch, getpass, hashlib, json, os, re, subprocess, sys
from pathlib import Path

from .fsutil import write_text_atomic

POLICY_FILES = ("approvals.json", "approvals.toml")
AUDIT = "approvals_audit.jsonl"
DEFAULTS = {"required": 1, "roles": [], "users": {}, "no_self_approval": True, "applies_to": "all", "parts": [],
            "reject_blocks": True}
KEY_FIELDS = ("name", "folder", "description", "comment")


class Refused(Exception):
    pass


# ---------------------------------------------------------------- identity
def _git(key, cwd=None):
    try:
        r = subprocess.run(["git", "config", key], capture_output=True, text=True, timeout=5, cwd=cwd)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def identity(user=None, cwd=None):
    """'Name <email>' of whoever runs this: --user, $ECADLIB_USER, git config, the login name."""
    if user and user.strip():
        return user.strip()
    if os.environ.get("ECADLIB_USER", "").strip():
        return os.environ["ECADLIB_USER"].strip()
    name, email = _git("user.name", cwd), _git("user.email", cwd)
    if name or email:
        return f"{name} <{email}>" if name and email else name or email
    try:
        return getpass.getuser()
    except Exception:                              # no login name (containers)
        return "unknown"


def user_key(who):
    """Comparable key for a person: the e-mail when there is one, else the name (case and spacing folded)."""
    who = str(who or "").strip()
    m = re.search(r"<([^<>]+)>", who)
    if m and m.group(1).strip():
        return m.group(1).strip().lower()
    return _fold(who)


_EMAIL = re.compile(r"[^\s<>()\[\],;]+@[^\s<>()\[\],;]+")


def _fold(s):
    import unicodedata
    return " ".join(unicodedata.normalize("NFKC", str(s or "")).casefold().split())


# letters of other scripts that look like Latin ones (after casefold), as Unicode's confusables list maps them: a
# Cyrillic 'а' in 'Аnn' is not a second person
_CONFUSABLE = str.maketrans({
    "а": "a", "в": "b", "е": "e", "ё": "e", "з": "3", "і": "i", "ї": "i", "ј": "j", "к": "k", "м": "m", "н": "h",
    "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l", "ԛ": "q",
    "ԝ": "w", "ɡ": "g", "α": "a", "β": "b", "ε": "e", "η": "n", "ι": "i", "κ": "k", "μ": "m", "ν": "v", "ο": "o",
    "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "ζ": "z", "ω": "w", "ϲ": "c", "ı": "i", "ł": "l", "ø": "o", "đ": "d",
    "ħ": "h", "ŀ": "l"})
_TRAIL = ".,;:!?-_'\"`"


def skeleton(s):
    """One spelling for text a person types as their name or e-mail: Unicode forms (NFKC) and case folded, zero-width
    and other invisible format characters dropped, accents removed (Zoë = Zoe), and letters of other scripts that
    look Latin read as Latin (a Cyrillic or Greek 'а' / 'ο' is an 'a' / 'o')."""
    import unicodedata
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cf", "Cc", "Mn", "Me") or ch in "\t\n")
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if unicodedata.category(ch) not in ("Mn", "Me"))
    return " ".join(unicodedata.normalize("NFKC", s).casefold().translate(_CONFUSABLE).split())


def person(who):
    """'Ann Lee <ann@x.com>', 'ann lee', 'ann@x.com', 'Ann Lee (ann@x.com)' -> (folded name, folded e-mail): spacing,
    case, Unicode forms, accents, invisible characters and look-alike letters folded (skeleton); a +tag in the e-mail's
    local part dropped (ann+2@x.com is ann@x.com's mailbox), and a mailto: or trailing punctuation ('Ann.',
    'ann@x.com.') left out."""
    s = re.sub(r"mailto:", " ", skeleton(who))
    m = _EMAIL.search(s)
    email = ""
    if m:
        local, _, dom = m.group(0).partition("@")
        email = f"{local.split('+', 1)[0].strip(_TRAIL)}@{dom.strip(_TRAIL)}"
        s = s[:m.start()] + s[m.end():]
    name = " ".join(re.sub(r"[<>()\[\]\"']", " ", s).split()).strip(_TRAIL + " ")
    return name, email


def same_person(a, b):
    """Two identities name one person when their e-mails match. When both give an e-mail the e-mail decides (two
    people may share a name: 'Ann Lee <ann@x.com>' and 'Ann Lee <ann.lee@y.com>' are two); when one of them gives none,
    their names decide. The two-person rule counts people with this, so a second spelling of one name or mailbox is
    never a second approver."""
    (na, ea), (nb, eb) = person(a), person(b)
    if ea and eb:
        return ea == eb
    return bool(na and na == nb)


# ---------------------------------------------------------------- policy
def policy_file(base):
    return next((Path(base) / n for n in POLICY_FILES if (Path(base) / n).exists()), None)


def policy_error(cmd, e):
    """A policy that does not load: one line + hint, exit 2. A broken ecadlib.toml (RulesError) is raised on, to
    cli.py's message with the doctor hint."""
    from .rules import RulesError
    if isinstance(e, RulesError):
        raise e
    print(f"ecadlib {cmd}: approval policy: {e}\n  hint: fix the approvals file next to the plan "
          f"(`ecadlib doctor` checks it)", file=sys.stderr)
    return 2


def load_policy(base):
    """The policy next to the plan, with defaults filled in, or None when there is none. Without an approvals file the
    [release] approvals table of the library rules (ecadlib.toml in the plan's folder or a parent) is used."""
    f = policy_file(base)
    if not f:
        from . import rules as RU
        raw = RU.load(None, start=base)["release"]["approvals"]
        return normalize_policy(dict(raw), "ecadlib.toml [release.approvals]") if raw else None
    if f.suffix == ".toml":
        from .rules import parse_text
        raw = parse_text(f.read_text(encoding="utf-8"), f.name)
        raw = raw.get("approvals", raw)            # either top level or an [approvals] table
    else:
        raw = json.loads(f.read_text(encoding="utf-8") or "{}")
    from . import migrate as MG                    # a policy from a newer ecadlib is refused, schema_version is not a key
    return normalize_policy(MG.check_key("approvals_policy", raw, str(f)), f)


def policy_source(base, policy):
    """Where the policy in force comes from, for people: the approvals file, the rules' [release.approvals], or none."""
    f = policy_file(base)
    if f:
        return str(f)
    if policy:
        from . import rules as RU
        return f"{RU.find(base) or 'ecadlib.toml'} [release.approvals]"
    return "none (approvals are recorded, not enforced)"


def _names(v, where, key):
    """A list of names; one name on its own is a one-item list (never split into its letters)."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if not isinstance(v, (list, tuple)) or not all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in v):
        raise ValueError(f"{where}: {key} must be a list of names, e.g. [\"librarian\"] (got {v!r})")
    return [str(x) for x in v]


def normalize_policy(raw, where="policy"):
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: the approval policy must be an object / table of keys "
                         f"({', '.join(sorted(DEFAULTS))}), not {type(raw).__name__}")
    bad = set(raw) - set(DEFAULTS)
    if bad:
        raise ValueError(f"{where}: unknown keys {sorted(bad)}; known: {sorted(DEFAULTS)}")
    p = {**DEFAULTS, **raw}
    req = p["required"]
    whole = not isinstance(req, bool) and (isinstance(req, int) or (isinstance(req, float) and req.is_integer())
                                           or (isinstance(req, str) and req.strip().isdigit()))
    if not whole:
        raise ValueError(f"{where}: required must be a whole number of approvals, e.g. 1 (got {req!r})")
    p["required"] = int(req)
    if p["required"] < 0 or p["applies_to"] not in ("all", "allowed"):
        raise ValueError(f"{where}: required must be >= 0 and applies_to 'all' or 'allowed'")
    p["roles"] = _names(p["roles"], where, "roles")
    if not isinstance(p["users"] or {}, dict):
        raise ValueError(f"{where}: users must be a table / object {{email: [roles]}}, e.g. "
                         f"{{\"ann@example.com\": [\"librarian\"]}} (got a {type(p['users']).__name__})")
    p["users"] = {user_key(k): _names(v, where, f"users.{k}") for k, v in (p["users"] or {}).items()}
    p["parts"] = _names(p["parts"], where, "parts")
    return p


def applies(item, policy):
    if not policy or (policy["required"] == 0 and not policy["roles"]):
        return False
    if policy["applies_to"] == "allowed" and not item.get("allow"):
        return False
    return not policy["parts"] or any(fnmatch.fnmatchcase(item["name"], g) for g in policy["parts"])


# ---------------------------------------------------------------- content hash
DERIVED_PARAMS = frozenset(("Assembly Profile", "LCSC Part", "Assembly Rotation", "Assembly Offset", "Assembly Check",
                            "Assembly Evidence"))          # the removed assembly check's PARAMS: not signed
def _resolve(base, p):
    return None if not p else Path(p) if Path(p).is_absolute() else Path(base) / p


def content_hash(item, base="."):
    """sha256 over the released model files' bytes and the item's key fields. Paths do not count, contents do."""
    h = hashlib.sha256(b"ecadlib-approval-v1\0")
    for kind in ("symbol", "footprint"):
        f = _resolve(base, (item.get(kind) or {}).get("file"))
        h.update(f"{kind}:{(item.get(kind) or {}).get('ref') or ''}\0".encode())
        h.update(hashlib.sha256(f.read_bytes()).digest() if f and f.is_file() else b"missing")
    fields = {k: item.get(k) or "" for k in KEY_FIELDS}
    fields["parameters"] = sorted((p["name"], str(p["value"])) for p in item.get("parameters") or []
                                  if p["name"] not in DERIVED_PARAMS)
    fields["cleared"] = sorted(item.get("cleared") or [])
    h.update(json.dumps(fields, sort_keys=True).encode())
    return h.hexdigest()


# ---------------------------------------------------------------- evaluation
def _allow_user(item):
    a = item.get("allow") or {}
    return a.get("user") or None                  # `by` is '--allow' / 'ecadlib ui/mcp', not a person


def evaluate(item, policy, base=".", h=None):
    """-> {required, met, hash, approved_by, missing, stale, rejected_by, ignored}."""
    h = h or content_hash(item, base)
    latest, pos = {}, {}
    for i, r in enumerate(item.get("approvals") or []):   # one person, however they spelled their name (same_person)
        k = r.get("key") or user_key(r["who"])
        k = next((x for x, y in latest.items() if x == k or same_person(y["who"], r["who"])), k)
        latest[k], pos[k] = r, i
    valid, stale, rejected, ignored, rejects, counted = [], [], [], [], [], []
    self_user = _allow_user(item)
    users = (policy or {}).get("users") or {}
    for k, r in latest.items():
        if r["decision"] == "reject":          # a "no" holds until that person approves, whatever changed since (a
            rejects.append(k)                  # restyle, a revert, a re-run lifts none)
        elif r.get("hash") != h:
            stale.append(r["who"])
        elif policy and policy["no_self_approval"] and self_user and (k == user_key(self_user) or same_person(r["who"], self_user)):
            ignored.append(f"{r['who']}: released it with --allow")
        elif users and (k not in users or (r.get("role") and r["role"] not in users[k])):
            ignored.append(f"{r['who']}: not authorised" + (f" as {r['role']}" if r.get("role") else ""))
        else:
            valid.append(r)
            counted.append(k)
    for k in rejects:
        r = latest[k]
        gone = bool(users) and k not in users  # the rejecter is no longer one of the policy's signers: an approve
        by = next((latest[v]["who"] for v in counted if pos[v] > pos[k]), None) if gone else None   # that counts,
        if by:                                 # recorded after the reject, lifts it (else nobody could)
            ignored.append(f"{r['who']}: rejected it, but is no longer in the policy's users; {by} approved after")
            continue
        rejected.append(f"{r['who']}" + (f" ({r['comment']})" if r.get("comment") else "")
                        + ("; the part changed since" if r.get("hash") != h else "")
                        + ("; no longer in the policy's users: an approve by a current one lifts it" if gone else ""))
    need = applies(item, policy)
    missing = []
    if need:
        if len(valid) < policy["required"]:
            missing.append(f"{policy['required'] - len(valid)} more approval(s) ({len(valid)}/{policy['required']})")
        for role in policy["roles"]:
            if not any(r.get("role") == role for r in valid):
                missing.append(f"an approval as {role}")
    # a current reject is an explicit "no": it holds the part with or without a policy (a policy may turn that off
    # with reject_blocks = false); only approvals need a policy to be required
    blocked = bool(rejected) and (not policy or policy["reject_blocks"])
    if blocked:
        missing.append("rejected by " + "; ".join(rejected))
    return {"required": need or blocked, "met": not missing, "hash": h, "approved_by": [r["who"] for r in valid],
            "missing": missing, "stale": stale, "rejected_by": rejected, "ignored": ignored}


def post_check(items, base=".", rows=None):
    """Hook at the end of release_check.apply: keep dedupe's `duplicate` status, and with a policy turn `ready`
    into `awaiting_approval` until the item is signed off. Records `approval` in each item."""
    policy = load_policy(base)
    for i, it in enumerate(items):
        row = rows[i] if rows and i < len(rows) else None
        if it.get("duplicate_of"):
            it["status"] = "duplicate"
        ev = evaluate(it, policy, base) if policy or it.get("approvals") else None
        if not policy and not (ev and ev["required"]):
            it.pop("approval", None)
        else:                                      # a policy, or a current reject (which holds the part without one)
            it["approval"] = {"policy": policy, "checked_hash": ev["hash"],
                              **{k: ev[k] for k in ("required", "met", "approved_by", "missing", "stale")}}
            if it["status"] in ("ready", "awaiting_approval"):    # awaiting_approval only ever comes from ready
                it["status"] = "awaiting_approval" if ev["required"] and not ev["met"] else "ready"
            if row is not None:
                row["approval"] = ("not required" if not ev["required"] else "approved" if ev["met"] else "; ".join(ev["missing"]))
        if row is not None:
            row["status"] = it["status"]
    return policy


def push_block(item, policy=None, base="."):
    """Reason altium365 push must not release / update this item, or None. policy: the plan's policy (None = use the
    snapshot release_check stored in the item; no snapshot and no policy = behave as without approvals)."""
    if item.get("status") == "duplicate" or item.get("duplicate_of"):
        return f"duplicate of {item.get('duplicate_of') or '?'} (dedupe)"
    snap = item.get("approval") or {}
    pol = policy if policy is not None else snap.get("policy")
    if not pol:                                    # no policy: only a current reject holds the part
        ev = evaluate(item, None, base) if item.get("approvals") else None
        if ev and ev["required"] and not ev["met"]:
            return "rejected: " + "; ".join(ev["missing"])
        return "awaiting approval" if item.get("status") == "awaiting_approval" else None
    ev = evaluate(item, pol, base)
    if snap.get("checked_hash") and snap["checked_hash"] != ev["hash"] and item.get("status") != "held":
        return "changed since the release check: run release-check again"
    if ev["required"] and not ev["met"]:
        return "awaiting approval: " + "; ".join(ev["missing"])
    return None


# ---------------------------------------------------------------- recording decisions
def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _takes_back(item, who):
    """who's latest decision on the item is a reject (so an approve now takes it back)."""
    k = user_key(who)
    mine = [r for r in item.get("approvals") or [] if (r.get("key") or user_key(r["who"])) == k or same_person(r["who"], who)]
    return bool(mine) and mine[-1]["decision"] == "reject"


def decide(item, decision, who, role=None, comment="", base=".", policy=None):
    """Validate and append one approval record; returns it. Raises Refused when the policy forbids it."""
    if decision not in ("approve", "reject"):
        raise ValueError(decision)
    k = user_key(who)
    if decision == "reject" and not (comment or "").strip():
        raise Refused("a reject needs a comment (-m) saying what is wrong")
    back = decision == "approve" and _takes_back(item, who)   # the person's own reject, taken back: always recorded
    if policy:                                                 # (it lifts their "no"; evaluate decides whether it
        users = policy["users"]                                # also counts as a sign-off)
        if users:
            if k not in users and not back:
                raise Refused(f"{who} is not in the policy's users")
            if role and role not in users.get(k, [role]):
                raise Refused(f"{who} may sign as {', '.join(users[k])}, not {role}")
            if not role and len(users.get(k, [])) == 1:
                role = users[k][0]
        if decision == "approve" and policy["no_self_approval"] and _allow_user(item) and not back and (
                user_key(_allow_user(item)) == k or same_person(_allow_user(item), who)):
            raise Refused(f"{who} released {item['name']} with --allow and cannot approve it (no_self_approval)")
    rec = {"who": who, "key": k, "when": now(), "role": role or "", "decision": decision,
           "comment": comment or "", "hash": content_hash(item, base), **({"takes_back": "reject"} if back else {})}
    item.setdefault("approvals", []).append(rec)
    return rec


def refresh_status(item, policy, base="."):
    """After a decision: flip between ready and awaiting_approval (never touches held items). An item whose
    content changed since the release check stays awaiting_approval until release-check runs again."""
    if item.get("status") not in ("ready", "awaiting_approval"):
        return item.get("status")
    ev = evaluate(item, policy, base)
    if not policy and not ev["required"]:          # no policy and no current reject: nothing to wait for
        snap = item.get("approval") or {}
        if snap and not snap.get("policy"):
            item.pop("approval", None)
            if snap.get("checked_hash") in (None, ev["hash"]):
                item["status"] = "ready"           # it waited only for the reject that is now taken back
        return item["status"]
    snap = item.setdefault("approval", {"policy": policy, "checked_hash": None})
    snap.update({"policy": policy, **{k: ev[k] for k in ("required", "met", "approved_by", "missing", "stale")}})
    changed = snap.get("checked_hash") not in (None, ev["hash"])
    if changed:
        snap["missing"] = snap["missing"] + ["changed since the release check: run release-check again"]
    item["status"] = "ready" if (not ev["required"] or ev["met"]) and not changed else "awaiting_approval"
    return item["status"]


def audit(base, entry):
    with open(Path(base) / AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def stamp_allow(items, names, who):
    """release_check --allow: record which person gave the override (no_self_approval uses it)."""
    for it in items:
        if it["name"] in names and it.get("allow"):
            it["allow"]["user"] = who


def status_rows(items, policy, base="."):
    out = []
    for it in items:
        ev = evaluate(it, policy, base)
        out.append({"name": it["name"], "status": it.get("status", ""), "needs_signoff": "yes" if ev["required"] else "",
                    "approved_by": "; ".join(ev["approved_by"]), "missing": "; ".join(ev["missing"]),
                    "stale": "; ".join(ev["stale"]), "ignored": "; ".join(ev["ignored"])})
    return out


DECISION_WAIT = 30                                     # seconds `ecadlib approve` waits for plan.lock (short writes)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Approve or reject plan items before altium365 push releases them")
    ap.add_argument("plan")
    ap.add_argument("action", nargs="?", default="status", choices=("approve", "reject", "status"))
    ap.add_argument("--part", action="append", default=[], help="part name (repeatable)")
    ap.add_argument("--all", action="store_true", help="every part that is ready or awaiting approval")
    ap.add_argument("--user", help="who you are (default: $ECADLIB_USER, then git config user.name/email)")
    ap.add_argument("--role", help="role you sign as (see the policy's roles/users)")
    ap.add_argument("-m", "--comment", default="", help="comment (required for reject)")
    ap.add_argument("--json", action="store_true", help="status: print the policy and one row per part as JSON")
    a = ap.parse_args(argv)
    ws = plan_workspace(a.plan) if a.action != "status" else None
    if ws is None:
        return _run(a)
    from .fsutil import plan_lock                      # the web UI may be recording into this plan: plan.lock is held
    with plan_lock(ws, wait=True, timeout=DECISION_WAIT) as free:   # only for short writes, so wait a little
        if not free:
            sys.exit("another ecadlib command is changing this plan right now; decide again in a minute")
        return _run(a)


def plan_workspace(plan):
    """The workspace of a workspace plan (<ws>/librarian/<name>/plan.json), whose plan lock the web UI takes, or None."""
    p = Path(plan).resolve()
    return p.parent.parent.parent if p.parent.parent.name == "librarian" else None


@contextlib.contextmanager
def cli_plan_lock(plan):
    """For CLI commands that rewrite a workspace plan (<ws>/librarian/<name>/plan.json): wait while the web UI records
    into it, so neither write is lost. Other plans: no lock. The plan's library lock is taken first (the lock order
    in fsutil): these commands go on to change-control steps (resolve, release anyway, dedupe merge) that take it, and
    a plan.lock holder never waits for the library lock."""
    ws = plan_workspace(plan)
    if ws is None:
        yield
        return
    from . import ledger as LG
    from .fsutil import plan_lock
    with LG.locked(Path(plan).resolve().parent):
        with plan_lock(ws, wait=False) as free:
            if free:
                yield
                return
        print("another ecadlib command is changing this plan; waiting for it to finish ...", file=sys.stderr)
        with plan_lock(ws, wait=True):
            yield


def _run(a):
    plan = Path(a.plan)
    base = plan.parent
    items = json.loads(plan.read_text(encoding="utf-8"))
    try:
        policy = load_policy(base)
    except (ValueError, OSError) as e:
        return policy_error("approve", e)
    by = {i["name"]: i for i in items}
    unknown = set(a.part) - set(by)
    if unknown:
        sys.exit(f"not in the plan: {', '.join(sorted(unknown))}")
    if a.action == "status":
        sel = [by[n] for n in a.part] if a.part else items
        if getattr(a, "json", False):              # stable shape: docs/command-line.md "Machine-readable output (--json)"
            pf = policy_file(base)
            print(json.dumps({"plan": str(plan), "policy_file": str(pf) if pf else None, "enforced": bool(policy),
                              "awaiting_approval": sum(i.get("status") == "awaiting_approval" for i in sel),
                              "rows": status_rows(sel, policy, base)}, indent=2, default=str))
            return 0
        print(f"policy: {policy_source(base, policy)}")
        for r in status_rows(sel, policy, base):
            print(f"  {r['status']:<18} {r['name']:<28} " + (f"approved by {r['approved_by']}" if r["approved_by"] else "")
                  + (f"  missing: {r['missing']}" if r["missing"] else "") + (f"  stale: {r['stale']}" if r["stale"] else "")
                  + (f"  ignored: {r['ignored']}" if r["ignored"] else ""))
        return 0
    if not a.part and not a.all:
        sys.exit("name the parts (--part X, repeatable) or use --all")
    sel = [by[n] for n in a.part] if a.part else [i for i in items if i.get("status") in ("ready", "awaiting_approval")]
    who, refused = identity(a.user, cwd=base), 0
    for it in sel:
        entry = {"ts": now(), "plan": str(plan), "part": it["name"], "who": who, "role": a.role or "", "decision": a.action,
                 "comment": a.comment}
        try:
            rec = decide(it, a.action, who, a.role, a.comment, base, policy)
            entry.update(role=rec["role"], hash=rec["hash"], result="recorded", status=refresh_status(it, policy, base))
            note = (" (still held by the release check)" if it.get("status") == "held" else
                    " (held: not pushed or exported until this rejection is taken back with an approve)"
                    if a.action == "reject" else "")
            print(f"  {a.action.rstrip('e')}ed  {it['name']:<28} -> {it.get('status')}{note}")
        except Refused as e:
            refused += 1
            entry.update(result=f"refused: {e}")
            print(f"  refused  {it['name']:<28} {e}")
        audit(base, entry)
    write_text_atomic(plan, json.dumps(items, indent=2))
    from .librarian import refresh_review
    refresh_review(plan, items)
    from . import timing                       # review time: from the release check to this decision
    timing.mark_review(plan.parent, a.action)
    if not policy:
        print("no approval policy (approvals.json/.toml next to the plan, or [release.approvals] in ecadlib.toml): "
              + ("a rejection still holds the part (an approve by the same person takes it back)" if a.action == "reject"
                 else "approvals are recorded, but push does not require them"))
    print(f"{len(sel) - refused} recorded as {who}, {refused} refused -> {plan}; log {base / AUDIT}")
    return 2 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
