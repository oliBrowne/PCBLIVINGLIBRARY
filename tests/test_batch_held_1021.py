"""Live benchmark 2026-10-21 (Opus, batch): one part's cross-check hold reached every part of the batch. Reader.flush()
built an advanced job's final answer outside any try, so that part's CrossCheckHeld left flush(), and
librarian.flush_datasheets gave the same exception to all 71 waiting parts ("held by the cross-check" x 71 with one
part's message). A hold or a failed read belongs to its own part only."""
from ecadlib import crosscheck as CC, datasheet as D


def _reader(results, rounds, held_key):
    r = object.__new__(D.Reader)
    r.pending, r.estimate_only, r.batch, r.wait = {}, False, True, True
    r.results = results
    seq = iter(rounds)
    r._flush_round = lambda jobs: next(seq)

    def result(job, rec):
        if job["key"] == held_key:
            raise CC.held({"status": "disagreed", "checks": [], "fields": {}})
        return {"part": job["part"], "rec": rec}
    r._result = result
    return r


def test_a_hold_on_an_advanced_part_stays_with_that_part():
    a, b, c = ({"key": k, "part": k.upper(), "name": k.upper()} for k in ("a", "b", "c"))
    a2 = dict(a, key="a2", final=True)                # a's next step, already answered in the batch
    r = _reader({"a2": {"message": {}}}, [{"a": D._Advance(a2), "b": {"part": "B"}, "c": D.DatasheetError("no PDF")}],
                held_key="a2")
    r.pending = {j["key"]: j for j in (a, b, c)}
    out = r.flush()
    assert isinstance(out["a"], CC.Held) and isinstance(out["a"], D.DatasheetError), out["a"]
    assert out["b"] == {"part": "B"}, out["b"]
    assert isinstance(out["c"], D.DatasheetError) and not isinstance(out["c"], CC.Held), out["c"]


def test_a_failed_final_read_on_an_advanced_part_stays_with_that_part():
    a, b = ({"key": k, "part": k.upper(), "name": k.upper()} for k in ("a", "b"))
    a2 = dict(a, key="a2", final=True)
    r = _reader({}, [{"a": D._Advance(a2), "b": {"part": "B"}}], held_key=None)

    def result(job, rec):
        if job["key"] == "a2":
            raise D.DatasheetError("response is not JSON; rejected")
        return {"part": job["part"]}
    r._result = result
    r.pending = {j["key"]: j for j in (a, b)}
    out = r.flush()
    assert isinstance(out["a"], D.DatasheetError) and "not JSON" in str(out["a"]), out["a"]
    assert out["b"] == {"part": "B"}, out["b"]
