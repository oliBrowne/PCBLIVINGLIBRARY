"""Offline tests for ecadlib.approvals: policy, content hash, release_check hook, push gate, CLI and audit log.
Run:  python3 -m tests.test_approvals

Plan items are built with tests.test_release_check.make (real .SchLib/.PcbLib from altium_lib), so the release
check passes and only the sign-off decides whether an item is released."""
import contextlib, io, json, os, tempfile
from pathlib import Path

from ecadlib import altium365 as A, approvals as AP, cli, release_check as RC
from tests.test_release_check import make

ANN, BOB, CAT = "Ann Lee <ann@example.com>", "Bob Roe <bob@example.com>", "Cat <cat@example.com>"


def _plan(d, items, policy=None, toml=None):
    if policy is not None:
        Path(d, "approvals.json").write_text(json.dumps(policy))
    if toml is not None:
        Path(d, "approvals.toml").write_text(toml)
    p = Path(d, "plan.json")
    p.write_text(json.dumps(items, indent=2))
    return p


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def _push(items, d, policy=None):
    """Dry-run push the way `altium365 push` does it: model paths resolved against the plan folder first."""
    items = json.loads(json.dumps(items))
    for i in items:
        for k in ("symbol", "footprint"):
            i[k]["file"] = str((Path(d) / i[k]["file"]).resolve())
    return {r["name"]: r for r in A.push(items, None, "https://ws", dry_run=True, policy=policy)}


def test_identity_and_keys():
    assert AP.identity("  Ann <ann@x.com> ") == "Ann <ann@x.com>"
    old = os.environ.get("ECADLIB_USER")
    try:
        os.environ["ECADLIB_USER"] = "ci-bot"
        assert AP.identity() == "ci-bot" and AP.identity("me") == "me"
        del os.environ["ECADLIB_USER"]
        assert AP.identity()                       # git config or the login name, never empty
    finally:
        if old is not None:
            os.environ["ECADLIB_USER"] = old
    assert AP.user_key("Ann Lee <Ann@Example.com>") == "ann@example.com" == AP.user_key("ann@example.com")
    assert AP.user_key("Bob") == "bob"


def test_no_policy_keeps_current_behaviour():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        RC.apply([it], d)
        assert it["status"] == "ready" and "approval" not in it
        assert _push([it], d)["PART1"]["action"] == "release"
        AP.decide(it, "approve", ANN, base=d)      # recorded, not enforced
        assert AP.push_block(it) is None and _push([it], d)["PART1"]["action"] == "release"


def test_policy_n_approvals_and_roles():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        _plan(d, [], {"required": 2, "roles": ["librarian", "hardware"]})
        pol = AP.load_policy(d)
        rows = RC.apply([it], d)
        assert it["status"] == "awaiting_approval" and rows[0]["status"] == "awaiting_approval"
        assert "2 more approval(s)" in rows[0]["approval"] and it["approval"]["checked_hash"] == AP.content_hash(it, d)
        r = _push([it], d, pol)["PART1"]
        assert r["action"] == "skip" and r["detail"].startswith("awaiting approval")
        assert _push([it], d)["PART1"]["action"] == "skip"           # snapshot in the item is enough (UI / MCP path)
        AP.decide(it, "approve", ANN, "librarian", base=d, policy=pol)
        assert AP.refresh_status(it, pol, d) == "awaiting_approval"
        AP.decide(it, "approve", ANN, "hardware", base=d, policy=pol)  # same person twice still counts once
        assert AP.refresh_status(it, pol, d) == "awaiting_approval"
        assert AP.evaluate(it, pol, d)["missing"] == ["1 more approval(s) (1/2)", "an approval as librarian"]
        AP.decide(it, "approve", BOB, "librarian", base=d, policy=pol)
        assert AP.refresh_status(it, pol, d) == "ready"
        assert _push([it], d, pol)["PART1"]["action"] == "release"
        RC.apply([it], d)                          # a re-check keeps a signed-off item ready
        assert it["status"] == "ready" and it["approval"]["met"]


def test_change_invalidates_approval():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        _plan(d, [], {"required": 1})
        pol = AP.load_policy(d)
        RC.apply([it], d)
        AP.decide(it, "approve", ANN, base=d, policy=pol)
        assert AP.refresh_status(it, pol, d) == "ready" and AP.push_block(it, pol, d) is None
        h = AP.content_hash(it, d)
        it["parameters"][0]["value"] = "OTHER"                     # key field changed after sign-off
        assert AP.content_hash(it, d) != h
        assert "changed since the release check" in AP.push_block(it, pol, d)
        assert AP.refresh_status(it, pol, d) == "awaiting_approval"
        RC.apply([it], d)
        assert it["status"] == "awaiting_approval" and AP.evaluate(it, pol, d)["stale"] == [ANN]
        it["parameters"][0]["value"] = "ACME"; RC.apply([it], d)
        assert it["status"] == "ready"                             # back to the approved content
        f = Path(d, it["footprint"]["file"])                       # model bytes count, the path does not
        f.write_bytes(f.read_bytes() + b"\0")
        RC.apply([it], d)
        assert it["status"] in ("awaiting_approval", "held")
        moved = Path(d, "elsewhere.SchLib"); moved.write_bytes(Path(d, it["symbol"]["file"]).read_bytes())
        it2 = json.loads(json.dumps(it)); it2["symbol"]["file"] = str(moved.resolve())
        assert AP.content_hash(it2, d) == AP.content_hash(it, d)


def test_no_self_approval_of_allow_and_audit():
    with tempfile.TemporaryDirectory() as d:
        bad = make(d, name="PART2", height=9.0)                    # implausible height -> held
        plan = _plan(d, [make(d), bad], {"required": 1})
        assert _quiet(RC.main, [str(plan), "--allow", "PART2", "--user", ANN, "-o", str(Path(d, "rc"))]) == 0
        items = json.loads(plan.read_text())
        assert items[1]["allow"]["user"] == ANN and items[1]["allow"]["by"] == "--allow"
        assert [i["status"] for i in items] == ["awaiting_approval", "awaiting_approval"]
        assert _quiet(cli.main, ["approve", str(plan), "approve", "--part", "PART2", "--user", "Ann <ANN@example.com>"]) == 2
        log = [json.loads(x) for x in Path(d, AP.AUDIT).read_text().splitlines()]
        assert log[-1]["result"].startswith("refused") and "no_self_approval" in log[-1]["result"]
        assert _quiet(cli.main, ["approve", str(plan), "approve", "--part", "PART2", "--user", BOB, "-m", "height ok"]) == 0
        items = json.loads(plan.read_text())
        assert items[1]["status"] == "ready" and items[1]["approvals"][0]["comment"] == "height ok"
        it = items[1]                                              # an approval recorded before the --allow does not count either
        it["approvals"].insert(0, {**it["approvals"][0], "who": ANN, "key": "ann@example.com"})
        assert AP.evaluate(it, AP.load_policy(d), d)["ignored"] == [f"{ANN}: released it with --allow"]
        log = [json.loads(x) for x in Path(d, AP.AUDIT).read_text().splitlines()]
        assert log[-1]["result"] == "recorded" and log[-1]["status"] == "ready" and len(log) == 2


def test_reject_blocks_until_reapproved():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        _plan(d, [], {"required": 1})
        pol = AP.load_policy(d)
        RC.apply([it], d)
        try:
            AP.decide(it, "reject", BOB, base=d, policy=pol)
            assert False, "reject without comment"
        except AP.Refused:
            pass
        AP.decide(it, "approve", ANN, base=d, policy=pol)
        AP.decide(it, "reject", BOB, comment="pin 3 wrong", base=d, policy=pol)
        assert AP.refresh_status(it, pol, d) == "awaiting_approval"
        assert "rejected by Bob Roe <bob@example.com> (pin 3 wrong)" in AP.push_block(it, pol, d)
        AP.decide(it, "approve", BOB, comment="fixed", base=d, policy=pol)
        assert AP.refresh_status(it, pol, d) == "ready" and len(it["approvals"]) == 3


def test_users_registry_and_roles():
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        _plan(d, [], toml='[approvals]\nrequired = 1\nroles = ["hardware"]\nusers = {"ann@example.com" = ["hardware"], "bob@example.com" = ["librarian", "hardware"]}\n')
        pol = AP.load_policy(d)
        assert pol["users"]["ann@example.com"] == ["hardware"]
        RC.apply([it], d)
        for who, role in ((CAT, None), (ANN, "librarian")):
            try:
                AP.decide(it, "approve", who, role, base=d, policy=pol)
                assert False, (who, role)
            except AP.Refused:
                pass
        assert AP.decide(it, "approve", ANN, base=d, policy=pol)["role"] == "hardware"   # single role: implied
        assert AP.refresh_status(it, pol, d) == "ready"
        it["approvals"].append({**it["approvals"][0], "who": CAT, "key": "cat@example.com"})   # hand-edited record
        assert AP.evaluate(it, pol, d)["ignored"] == [f"{CAT}: not authorised as hardware"]
        Path(d, "approvals.toml").write_text("required = 1\nbogus = 2\n")
        try:
            AP.load_policy(d)
            assert False
        except ValueError as e:
            assert "bogus" in str(e)


def test_scope_allowed_only_and_parts():
    with tempfile.TemporaryDirectory() as d:
        a, b = make(d), make(d, name="PART2")
        b["allow"] = {"by": "--allow", "failures": []}
        pol = AP.normalize_policy({"applies_to": "allowed"})
        assert not AP.applies(a, pol) and AP.applies(b, pol)
        pol = AP.normalize_policy({"parts": ["PART2*"]})
        assert not AP.applies(a, pol) and AP.applies(b, pol)
        _plan(d, [], {"parts": ["PART2*"]})
        RC.apply([a, b], d)
        assert (a["status"], b["status"]) == ("ready", "awaiting_approval")
        assert not AP.applies(a, AP.normalize_policy({"required": 0}))


def test_cli_status_and_all():
    with tempfile.TemporaryDirectory() as d:
        plan = _plan(d, [make(d), make(d, name="PART2")], {"required": 1})
        _quiet(RC.main, [str(plan), "-o", str(Path(d, "rc"))])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert cli.main(["approve", str(plan)]) == 0
        assert "awaiting_approval" in out.getvalue() and "1 more approval" in out.getvalue()
        try:
            _quiet(cli.main, ["approve", str(plan), "approve"])
            assert False, "needs --part or --all"
        except SystemExit:
            pass
        assert _quiet(cli.main, ["approve", str(plan), "approve", "--all", "--user", ANN]) == 0
        items = json.loads(plan.read_text())
        assert [i["status"] for i in items] == ["ready", "ready"]
        assert all(i["approvals"][0]["hash"] == AP.content_hash(i, d) for i in items)
        assert "approval" in Path(d, "rc.csv").read_text().splitlines()[0]


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_approvals"]))
