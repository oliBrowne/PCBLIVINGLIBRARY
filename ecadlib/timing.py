"""How long a BOM takes to become a released library (#44): wall time per step, kept beside the plan.

Each librarian run starts a new run in <plan dir>/timing.jsonl; later steps on that plan join it:

    triage         looking the BOM lines up in the libraries and part sources   (librarian)
    build          finding / converting symbols, footprints and 3D models       (librarian)
    release_check  the release check over the plan                              (librarian)
    review         from the end of the release check to the last sign-off, --allow or review decision (people's time)
    push           the Altium 365 push (not a dry run)

`ecadlib status` shows the last run of each plan: per-step seconds, `work_seconds` (every step but review: what
ecadlib itself took), `total_seconds` (from the first step's start to the last step's end, review included).
Writing the ledger never fails a run: a file that cannot be written is skipped."""
import datetime, json, time
from contextlib import contextmanager
from pathlib import Path

FILE = "timing.jsonl"
STEPS = ("triage", "build", "release_check", "review", "push")


def _iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(folder, step, started, finished=None, new_run=False, **extra):
    """Append one step (started / finished: epoch seconds). new_run: this step starts a new BOM run."""
    finished = time.time() if finished is None else finished
    rec = {"step": step, "started": _iso(started), "finished": _iso(finished),
           "seconds": round(max(0.0, finished - started), 3), "t0": round(started, 3), "t1": round(finished, 3)}
    if new_run:
        rec["new_run"] = True
    rec.update({k: v for k, v in extra.items() if v is not None})
    try:
        f = Path(folder) / FILE
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    return rec


@contextmanager
def step(folder, name, new_run=False, **extra):
    """Time a block as one step; a step that raises is recorded with ok=false."""
    t0, ok = time.time(), False
    try:
        yield
        ok = True
    finally:
        record(folder, name, t0, new_run=new_run, **({} if ok else {"ok": False}), **extra)


def mark_review(folder, what=None):
    """A person's decision on the plan (approve / reject / --allow / a resolve choice): the review step runs from
    the end of the last run's release check to the latest such decision."""
    recs = read(folder)
    run = _last_run(recs)
    rc = next((r for r in reversed(run) if r["step"] == "release_check"), None)
    if rc is None:
        return None
    return record(folder, "review", rc["t1"], what=what)


def read(folder, problems=None):
    f = Path(folder) / FILE
    out = []
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for n, ln in enumerate(lines, 1):
        try:
            r = json.loads(ln)
            if isinstance(r, dict) and r.get("step") and isinstance(r.get("t0"), (int, float)) \
                    and isinstance(r.get("t1"), (int, float)):
                out.append(r)
        except ValueError:
            if problems is not None:
                problems.append(f"{f.as_posix()} line {n}: not JSON (skipped)")
    return out


def _last_run(recs):
    """The newest run that reached its release check (an interrupted run left no plan of its own), with the
    review / push records written after it; the newest run as is when none did."""
    starts = [i for i, r in enumerate(recs) if r.get("new_run")] or [0]
    for a, b in reversed(list(zip(starts, starts[1:] + [len(recs)]))):
        if any(r["step"] == "release_check" for r in recs[a:b]):
            return recs[a:b] + [r for r in recs[b:] if r["step"] in ("review", "push")]
    return recs[starts[-1]:]


def last_run(folder, problems=None):
    """The latest run of the plan in `folder` -> {"started", "finished", "steps": {step: seconds}, "work_seconds",
    "total_seconds"} or None. A step run more than once in a run (two pushes) adds up; review is its latest span."""
    run = _last_run(read(folder, problems))
    if not run:
        return None
    steps = {}
    for r in run:
        if r["step"] == "review":
            steps["review"] = r["seconds"]          # one span, from the release check to the latest decision
        else:
            steps[r["step"]] = round(steps.get(r["step"], 0) + r["seconds"], 3)
    t0, t1 = min(r["t0"] for r in run), max(r["t1"] for r in run)
    order = {s: i for i, s in enumerate(STEPS)}
    return {"started": _iso(t0), "finished": _iso(t1),
            "steps": dict(sorted(steps.items(), key=lambda kv: order.get(kv[0], len(STEPS)))),
            "work_seconds": round(sum(v for k, v in steps.items() if k != "review"), 3),
            "total_seconds": round(max(0.0, t1 - t0), 3),
            "push_errors": next((r.get("errors", 0) for r in reversed(run) if r["step"] == "push"), None)}


def human(seconds):
    """3s, 2m 05s, 1h 04m."""
    s = int(round(seconds or 0))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"
