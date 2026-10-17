"""Regression tests from the 2026-10-18 review of the team server (ecadlib ui --team), the library agent's review queue
and approvals. Offline; a real HTTP server on an ephemeral port. Run:
    python3 -m tests.run_all test_review_1018_team"""
import json

from ecadlib import agent_queue as AQ, team as TM


def test_plan_approval_route_keeps_queue_no_self_approval():
    """The person who started (or asked for) a queue item's build could not approve it in the queue, but could approve
    the same plan part through /api/approvals/decide (or ws.approve_part / the MCP tool): self-approval by another route."""
    from tests.test_agent_queue import _review_item, raises, walk
    from tests.test_resolve_ui import workspace, ANN, BOB
    from tests.test_team import Client, server
    with workspace() as (ws, bom, plan), server(ws) as (_, team, port):
        (plan.parent / "approvals.json").write_text('{"required": 1}')
        _review_item(ws, bom, "USBC-FAKE", builder="Apr Prover <apr@example.com>")
        apr, ap2 = Client(port), Client(port)
        apr.login("apr"), ap2.login("ap2")
        st, e = apr.call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve"})
        assert st == 403 and "self-approval" in e["error"], (st, e)
        item = next(x for x in json.loads(plan.read_text()) if x["name"] == "USBC-FAKE")
        assert not any(a["decision"] == "approve" for a in item.get("approvals") or []), item.get("approvals")
        st, r = apr.call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "reject",
                                                           "comment": "pin 9 wrong"})
        assert st == 200, (st, r)                                        # rejecting your own build is fine
        st, r = ap2.call("POST", "/api/approvals/decide", {"bom": bom, "part": "USBC-FAKE", "decision": "approve"})
        assert st == 200 and r["decision"]["who"] == "Two Prover <ap2@example.com>", (st, r)
        # a person who asked for a rebuild (requested_by), through the Workspace API (MCP, local UI); "./" spelling too
        k = _review_item(ws, bom, "FTSH-105-TEST")
        ws.agent_queue_decide(k, "rebuild", BOB, None, "use the 2x5 header")
        walk(AQ.Queue(ws.root), k, "proving", "review")
        raises(PermissionError, ws.approve_part, "./" + bom, "FTSH-105-TEST", "approve", "Bob <bob@example.com>",
               contains="self-approval")
        assert ws.approve_part(bom, "FTSH-105-TEST", "approve", ANN)["decision"]["who"] == ANN
        # once the queue item is closed (rejected), its old requester is no longer blocked on the plan part
        AQ.Queue(ws.root).transition(k, "rejected", user=ANN, note="superseded")
        assert ws.approve_part(bom, "FTSH-105-TEST", "approve", "Bob <bob@example.com>")["decision"]["decision"] == "approve"


def test_team_users_sign_as_odd_types_are_a_400():
    """sign_as of 5 or {} from the admin page's JSON raised TypeError: a 500 with a traceback in the terminal."""
    from tests.test_team import Client, server
    with server() as (ws, team, port):
        ada = Client(port)
        ada.login("ada")
        for bad in (5, {"a": 1}, True):
            st, e = ada.call("POST", "/api/team/users", {"action": "add", "username": "zed", "role": "viewer",
                                                          "password": "zed-password-1", "sign_as": bad})
            assert st == 400 and "sign-as" in e["error"], (bad, st, e)
            st, e = ada.call("POST", "/api/team/users", {"action": "set-role", "username": "vic", "role": "viewer",
                                                          "sign_as": bad})
            assert st == 400 and "sign-as" in e["error"], (bad, st, e)
        assert TM.UserStore(ws.root).get("zed") is None
        st, r = ada.call("POST", "/api/team/users", {"action": "set-role", "username": "vic", "role": "viewer",
                                                      "sign_as": "viewer, hardware"})
        assert st == 200 and next(u for u in r["users"] if u["username"] == "vic")["sign_as"] == ["viewer", "hardware"], r
