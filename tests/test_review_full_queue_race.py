"""One open queue item per part key when items are made within the same second (agent_queue.Queue.find).

`created` is to the second and queue ids sort at random within one: the soak (tests/test_agent_soak.py) once picked a
part's pushed item over its newer failed / rejected one made in the same second, so upsert made a new item beside the
rejected one, and a person's retry of that one left two open items for the part.
    python3 -m tests.test_review_full_queue_race"""
import tempfile
from unittest import mock

from ecadlib import agent_queue as AQ
from ecadlib.app import Workspace

T = "2026-10-16T00:00:00Z"
ANN, BOB = "Ann Lee <ann@example.com>", "Bob Roe <bob@example.com>"


def change(n):
    return {"kind": "changed", "source": {"type": "bom_file", "ref": "boms/main.csv"}, "mpn": "P78",
            "manufacturer": "ACME", "footprint": n}


def same_second(ids):
    """Every item made in one second, with these ids in turn (the pushed one sorting above the newer ones)."""
    it = iter(ids)
    return [mock.patch.object(AQ, "now", lambda: T), mock.patch.object(AQ.Queue, "_new_id", lambda self, d: next(it))]


def pushed_then(q, end):
    a = q.upsert(change("SOIC-8"))
    for s in ("building", "proving", "review"):
        q.transition(a["id"], s, user="ecadlib-agent")
    q.transition(a["id"], "approved", user=ANN)
    q.transition(a["id"], "pushed", user="ecadlib-agent")
    b = q.upsert(change("QFN-16"))                       # re-detected after the push: a new item
    assert b["id"] != a["id"] and b["status"] == "detected", b
    q.transition(b["id"], end, user=ANN if end == "rejected" else "ecadlib-agent", note="pad size")
    return a, b


def test_a_failed_or_rejected_item_made_in_the_pushed_items_second_is_still_the_current_one():
    for end in ("rejected", "failed"):
        with tempfile.TemporaryDirectory() as d:
            p = same_second(["20261016-000000-ffffff", "20261016-000000-000000", "20261016-000000-888888"])
            with p[0], p[1]:
                q = AQ.Queue(d)
                a, b = pushed_then(q, end)
                assert q.find(a["key"])["id"] == b["id"], (end, q.find(a["key"]))
                c = q.upsert(change("TSSOP-14"))             # another change: the rejected / failed item is re-opened
                assert c["id"] == b["id"] and c["status"] == "detected", (end, c)
                assert len(q.list()) == 2, (end, [(i["id"], i["status"]) for i in q.list()])


def test_retry_after_a_change_in_the_same_second_leaves_one_open_item():
    with tempfile.TemporaryDirectory() as d:
        p = same_second(["20261016-000000-ffffff", "20261016-000000-000000", "20261016-000000-888888"])
        with p[0], p[1]:
            q = AQ.Queue(d)
            a, b = pushed_then(q, "rejected")
            q.upsert(change("TSSOP-14"))
            for it in q.list(status=("failed", "rejected")):   # a person retries what is rejected (as in the soak)
                AQ.decide(Workspace(d), it["id"], "retry", BOB, note="again")
            open_ = [i["id"] for i in q.list() if i["status"] in AQ.OPEN]
            assert len(open_) == 1, [(i["id"], i["status"]) for i in q.list()]


if __name__ == "__main__":
    import sys
    from tests.run_all import main
    sys.exit(main(["test_review_full_queue_race"]))
