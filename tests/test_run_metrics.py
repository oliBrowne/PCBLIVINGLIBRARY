"""BOM-to-library time (#44) and the review rate (#45) in `ecadlib status`.
Run:  python3 -m tests.test_run_metrics"""
import contextlib, io, json, os, tempfile, time
from pathlib import Path
from unittest import mock

from ecadlib import altium365 as A, approvals as AP, timing as TI, ws_status as WS


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def test_a_librarian_run_its_review_and_push_are_timed_in_status():
    """The librarian writes triage / build / release_check to <plan dir>/timing.jsonl, a sign-off adds the review
    span, a real push adds push; status --json gives per-step and total seconds of the last run, and a new librarian
    run starts a new run (the old review does not leak into it)."""
    from tests import test_librarian as TL
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d, "ws")
        lib = ws / "librarian"
        lib.mkdir(parents=True)
        items, _ = TL.run(lib, sources=["kicad"])
        out = lib / "out"
        recs = TI.read(out)
        assert [r["step"] for r in recs] == ["triage", "build", "release_check"] and recs[0].get("new_run"), recs
        assert recs[0]["bom"] == "bom.csv" and recs[0]["lines"] >= 1 and all(r["seconds"] >= 0 for r in recs), recs
        s = WS.status(ws)
        lr = s["last_run"]
        assert lr["plan"] == "librarian/out/plan.json" and list(lr["steps"]) == ["triage", "build", "release_check"], lr
        assert 0 <= lr["work_seconds"] <= lr["total_seconds"] + 0.01, lr
        assert s["plans"][0]["timing"]["steps"] == lr["steps"]

        ready = [n for n, i in items.items() if i["status"] in ("ready", "awaiting_approval")]
        assert ready, items
        with mock.patch.dict(os.environ, {"ECADLIB_USER": "Ann <ann@example.com>"}):
            assert _quiet(AP.main, [str(out / "plan.json"), "approve", "--part", ready[0]]) == 0
        A.record_push(out, time.time() - 2, [{"action": "release"}, {"action": "up_to_date"}])
        lr = WS.status(ws)["last_run"]
        assert list(lr["steps"]) == ["triage", "build", "release_check", "review", "push"], lr
        assert lr["steps"]["push"] >= 2 and lr["work_seconds"] >= lr["steps"]["push"], lr
        assert TI.read(out)[-1]["released"] == 1
        text = WS.render(WS.status(ws))
        assert "last run" in text and "BOM to released" in text and "review " in text, text

        TL.run(lib, sources=["kicad"])                   # a new BOM run: review and push belong to the old one
        lr = WS.status(ws)["last_run"]
        assert list(lr["steps"]) == ["triage", "build", "release_check"], lr


def test_dry_runs_and_bad_ledgers_never_fail():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d, TI.FILE)
        TI.record(d, "triage", 100.0, 103.5, new_run=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("{half\n")
        TI.record(d, "build", 103.5, 110.0)
        problems = []
        lr = TI.last_run(d, problems)
        assert lr["steps"] == {"triage": 3.5, "build": 6.5} and lr["total_seconds"] == 10.0, lr
        assert problems and "line 2" in problems[0], problems
        assert TI.mark_review(Path(d, "nothing")) is None      # no release check yet: no review span
        assert TI.last_run(Path(d, "nothing")) is None
        blocker = Path(d, "file")
        blocker.write_text("x")
        TI.record(blocker / "sub", "push", 1.0, 2.0)         # cannot be written: skipped, no exception
        assert TI.human(4.4) == "4s" and TI.human(125) == "2m 05s" and TI.human(3840) == "1h 04m"


def test_review_stats_count_first_time_held_approved_rejected_changed():
    """#45: of the parts the librarian built, how many went through as built, are held (and why), were approved,
    rejected, or changed by a person (--allow, a resolve choice, a footprint edit)."""
    with tempfile.TemporaryDirectory() as d:
        ap = lambda dec: {"decision": dec, "who": "Ann"}
        items = [
            {"name": "OK1", "status": "ready"},                                               # first time
            {"name": "OK2", "status": "ready", "approvals": [ap("approve")]},                 # first time, approved
            {"name": "REJ", "status": "awaiting_approval", "approvals": [ap("reject"), ap("approve")]},
            {"name": "ALW", "status": "ready", "allow": {"user": "Ann"}},                     # released anyway
            {"name": "PICK", "status": "ready"},                                              # resolve choice
            {"name": "EDIT", "status": "ready"},                                              # fp-edit
            {"name": "H1", "status": "held", "hold_reasons": ["files: no symbol model (.SchLib)", "parity: pad 2 moved"]},
            {"name": "H2", "status": "held", "hold_reasons": ["files: no footprint model (.PcbLib)"]},
            {"name": "DUP", "status": "duplicate"},                                           # not built: not counted
        ]
        Path(d, "librarian_state.json").write_text(json.dumps({"PICK": {"footprint": {"chosen": True}}, "OK1": {}}))
        Path(d, "footprint_edits.json").write_text(json.dumps({"EDIT": {"rename": {"1+": "1"}}}))
        r = WS.review_stats(items, d)
        assert {k: r[k] for k in WS.REVIEW_KEYS} == {"built": 8, "held": 2, "passed_first_time": 2, "approved": 2,
                                                       "rejected": 1, "changed": 3}, r
        assert [(x["reason"], x["parts"]) for x in r["held_reasons"]] == [("files", 2), ("parity", 1)], r
        assert r["held_reasons"][0]["example"].startswith("files: no symbol model")


def test_status_sums_review_over_plans_and_renders_it():
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        for name, items in (("a", [{"name": "A", "status": "ready"}, {"name": "B", "status": "held", "hold_reasons": ["files: x"]}]),
                            ("b", [{"name": "C", "status": "held", "hold_reasons": ["files: y", "verify: z"]}])):
            (ws / "librarian" / name).mkdir(parents=True)
            (ws / "librarian" / name / "plan.json").write_text(json.dumps(items))
        s = WS.status(ws)
        rv = s["review"]
        assert (rv["built"], rv["held"], rv["passed_first_time"]) == (3, 2, 1), rv
        assert [(x["reason"], x["parts"]) for x in rv["held_reasons"]] == [("files", 2), ("verify", 1)], rv
        assert s["last_run"] is None and all(p["timing"] is None for p in s["plans"])
        text = WS.render(s)
        assert "3 built: 1 passed first time, 2 held" in text and "held for" in text and "files 2" in text, text
        json.dumps(s)


if __name__ == "__main__":
    from tests.run_all import main
    raise SystemExit(main(["test_run_metrics"]))
