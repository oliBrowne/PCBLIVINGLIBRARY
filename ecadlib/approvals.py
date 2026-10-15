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
  reject_blocks     a current reject holds the item until that person approves again (default true)
Each decision is stored in the item's `approvals` list: who, key, when, role, decision, comment and `hash`, a sha256
over the item's .SchLib/.PcbLib bytes and its key fields (name, folder, description, comment, parameters, cleared,
symbol/footprint refs). Any later change to them makes the approval stale; it then no longer counts. The latest
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
    """Comparable key for a person: the e-mail when there is one, else the lower-cased name."""
    who = str(who or "").strip()
    m = re.search(r"<([^<>]+)>", who)
    return (m.group(1) if m and m.group(1).strip() else who).strip().lower()


# ---------------------------------------------------------------- policy
def policy_file(base):
    return next((Path(base) / n for n in POLICY_FILES if (Path(base) / n).exists()), None)


def load_policy(base):
    """The policy next to the plan, with defaults filled in, or None when there is none. Without an approvals file the
    [release] approvals table of the library rules (ecadlib.toml in the plan's folder or a parent) is used."""
    f = policy_file(base)
    if not f:
        from . import rules as RU
        raw = RU.load(None, start=base)["release"]["approvals"]
        return normalize_policy(dict(raw), "ecadlib.toml [release.approvals]") if raw else None
    if f.suffix == ".toml":
        import tomllib
        raw = tomllib.loads(f.read_text(encoding="utf-8"))
        raw = raw.get("approvals", raw)            # either top level or an [approvals] table
    else:
        raw = json.loads(f.read_text(encoding="utf-8") or "{}")
    return normalize_policy(raw, f)


def policy_source(base, policy):
    """Where the policy in force comes from, for people: the approvals file, the rules' [release.approvals], or none."""
    f = policy_file(base)
    if f:
        return str(f)
    if policy:
        from . import rules as RU
        return f"{RU.find(base) or 'ecadlib.toml'} [release.approvals]"
    return "none (approvals are recorded, not enforced)"


def normalize_policy(raw, where="policy"):
    bad = set(raw) - set(DEFAULTS)
    if bad:
        raise ValueError(f"{where}: unknown keys {sorted(bad)}; known: {sorted(DEFAULTS)}")
    p = {**DEFAULTS, **raw}
    p["required"] = int(p["required"])
    if p["required"] < 0 or p["applies_to"] not in ("all", "allowed"):
        raise ValueError(f"{where}: required must be >= 0 and applies_to 'all' or 'allowed'")
    p["roles"] = [str(r) for r in p["roles"]]
    p["users"] = {user_key(k): [str(r) for r in (v if isinstance(v, list) else [v])] for k, v in (p["users"] or {}).items()}
    p["parts"] = [str(x) for x in p["parts"]]
    return p


def applies(item, policy):
    if not policy or (policy["required"] == 0 and not policy["roles"]):
        return False
    if policy["applies_to"] == "allowed" and not item.get("allow"):
        return False
    return not policy["parts"] or any(fnmatch.fnmatchcase(item["name"], g) for g in policy["parts"])


# ---------------------------------------------------------------- content hash
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
    fields["parameters"] = sorted((p["name"], str(p["value"])) for p in item.get("parameters") or [])
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
    latest = {}
    for r in item.get("approvals") or []:
        latest[r.get("key") or user_key(r["who"])] = r
    valid, stale, rejected, ignored = [], [], [], []
    self_key = user_key(_allow_user(item)) if _allow_user(item) else None
    users = (policy or {}).get("users") or {}
    for k, r in latest.items():
        if r.get("hash") != h:
            stale.append(r["who"])
        elif r["decision"] == "reject":
            rejected.append(f"{r['who']}" + (f" ({r['comment']})" if r.get("comment") else ""))
        elif policy and policy["no_self_approval"] and self_key and k == self_key:
            ignored.append(f"{r['who']}: released it with --allow")
        elif users and (k not in users or (r.get("role") and r["role"] not in users[k])):
            ignored.append(f"{r['who']}: not authorised" + (f" as {r['role']}" if r.get("role") else ""))
        else:
            valid.append(r)
    need = applies(item, policy)
    missing = []
    if need:
        if len(valid) < policy["required"]:
            missing.append(f"{policy['required'] - len(valid)} more approval(s) ({len(valid)}/{policy['required']})")
        for role in policy["roles"]:
            if not any(r.get("role") == role for r in valid):
                missing.append(f"an approval as {role}")
        if rejected and policy["reject_blocks"]:
            missing.append("rejected by " + "; ".join(rejected))
    return {"required": need, "met": not missing, "hash": h, "approved_by": [r["who"] for r in valid],
            "missing": missing, "stale": stale, "rejected_by": rejected, "ignored": ignored}


def post_check(items, base=".", rows=None):
    """Hook at the end of release_check.apply: keep dedupe's `duplicate` status, and with a policy turn `ready`
    into `awaiting_approval` until the item is signed off. Records `approval` in each item."""
    policy = load_policy(base)
    for i, it in enumerate(items):
        row = rows[i] if rows and i < len(rows) else None
        if it.get("duplicate_of"):
            it["status"] = "duplicate"
        if not policy:
            it.pop("approval", None)
        else:
            ev = evaluate(it, policy, base)
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
    if not pol:
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


def decide(item, decision, who, role=None, comment="", base=".", policy=None):
    """Validate and append one approval record; returns it. Raises Refused when the policy forbids it."""
    if decision not in ("approve", "reject"):
        raise ValueError(decision)
    k = user_key(who)
    if decision == "reject" and not (comment or "").strip():
        raise Refused("a reject needs a comment (-m) saying what is wrong")
    if policy:
        users = policy["users"]
        if users:
            if k not in users:
                raise Refused(f"{who} is not in the policy's users")
            if role and role not in users[k]:
                raise Refused(f"{who} may sign as {', '.join(users[k])}, not {role}")
            if not role and len(users[k]) == 1:
                role = users[k][0]
        if decision == "approve" and policy["no_self_approval"] and _allow_user(item) and user_key(_allow_user(item)) == k:
            raise Refused(f"{who} released {item['name']} with --allow and cannot approve it (no_self_approval)")
    rec = {"who": who, "key": k, "when": now(), "role": role or "", "decision": decision,
           "comment": comment or "", "hash": content_hash(item, base)}
    item.setdefault("approvals", []).append(rec)
    return rec


def refresh_status(item, policy, base="."):
    """After a decision: flip between ready and awaiting_approval (never touches held items). An item whose
    content changed since the release check stays awaiting_approval until release-check runs again."""
    if item.get("status") not in ("ready", "awaiting_approval") or not policy:
        return item.get("status")
    ev = evaluate(item, policy, base)
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
    ws = agent_workspace(a.plan) if a.action != "status" else None
    if ws is None:
        return _run(a)
    from .agent_queue import plan_lock                 # the library agent's librarian may be rewriting this plan
    with plan_lock(ws, wait=False) as free:
        if not free:
            sys.exit("the library agent is rebuilding the library right now; decide again in a minute")
        return _run(a)


def agent_workspace(plan):
    """The workspace whose library agent may rewrite this plan (<ws>/librarian/<name>/plan.json with <ws>/agent/), or
    None."""
    p = Path(plan).resolve()
    ws = p.parent.parent.parent
    return ws if p.parent.parent.name == "librarian" and (ws / "agent").is_dir() else None


@contextlib.contextmanager
def cli_plan_lock(plan):
    """For CLI commands that rewrite a plan the library agent owns (<ws>/librarian/<name>/plan.json): wait for the
    agent's librarian to finish before reading the plan, so neither write is lost. Other plans: no lock."""
    ws = agent_workspace(plan)
    if ws is None:
        yield
        return
    from .agent_queue import plan_lock
    with plan_lock(ws, wait=False) as free:
        if free:
            yield
            return
    print("the library agent is rebuilding this library; waiting for it to finish ...", file=sys.stderr)
    with plan_lock(ws, wait=True):
        yield


def _run(a):
    plan = Path(a.plan)
    base = plan.parent
    items = json.loads(plan.read_text(encoding="utf-8"))
    try:
        policy = load_policy(base)
    except (ValueError, OSError) as e:
        sys.exit(f"approval policy: {e}")
    by = {i["name"]: i for i in items}
    unknown = set(a.part) - set(by)
    if unknown:
        sys.exit(f"not in the plan: {', '.join(sorted(unknown))}")
    if a.action == "status":
        sel = [by[n] for n in a.part] if a.part else items
        if getattr(a, "json", False):              # stable shape: README "Machine-readable output (--json)"
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
            note = "" if it.get("status") != "held" else " (still held by the release check)"
            print(f"  {a.action.rstrip('e')}ed  {it['name']:<28} -> {it.get('status')}{note}")
        except Refused as e:
            refused += 1
            entry.update(result=f"refused: {e}")
            print(f"  refused  {it['name']:<28} {e}")
        audit(base, entry)
    write_text_atomic(plan, json.dumps(items, indent=2))
    if not policy:
        print("no approval policy (approvals.json/.toml next to the plan, or [release.approvals] in ecadlib.toml): "
              "recorded, but push does not require them")
    print(f"{len(sel) - refused} recorded as {who}, {refused} refused -> {plan}; log {base / AUDIT}")
    return 2 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
