"""tmp_path / monkeypatch for plain test functions: tests.run_all calls tests with no arguments and CI has no pytest.
    @lite
    def test_x(tmp_path, monkeypatch): ...
The wrapper takes no arguments, so pytest and tests.run_all both call it as is."""
import inspect, os, tempfile
from pathlib import Path

_MISSING = object()


class MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        old = getattr(obj, name, _MISSING)
        self._undo.append(lambda: delattr(obj, name) if old is _MISSING else setattr(obj, name, old))
        setattr(obj, name, value)

    def setenv(self, name, value):
        old = os.environ.get(name)
        self._undo.append(lambda: os.environ.pop(name, None) if old is None else os.environ.__setitem__(name, old))
        os.environ[name] = str(value)

    def chdir(self, path):
        old = os.getcwd()
        self._undo.append(lambda: os.chdir(old))
        os.chdir(path)

    def undo(self):
        while self._undo:
            self._undo.pop()()


def lite(fn):
    names = list(inspect.signature(fn).parameters)

    def run():
        mp = MonkeyPatch()
        with tempfile.TemporaryDirectory() as d:
            got = {"tmp_path": Path(d), "monkeypatch": mp}
            try:
                return fn(**{n: got[n] for n in names})
            finally:
                mp.undo()
    run.__name__, run.__doc__, run.__module__ = fn.__name__, fn.__doc__, fn.__module__
    return run


def released_anyway(items, base=".", who="--allow"):
    """Test parts a person released anyway (release-check --allow): each `ready` item's release-check failures, as the
    check finds them now, are covered, so the publishers (which run the check again) send it. A file that does not read
    back cannot be covered. For tests about something else than the release check. Returns items."""
    import datetime
    from ecadlib import release_check as RC, rules as RU
    rules = RU.resolve(None, base)              # the rules the publishers use: ecadlib.toml found from the plan
    for it in items:
        if it.get("status", "ready") == "ready":
            fails = [f"{r}: {m}" for r, m in RC.check_item(it, base, rules)[0] if r not in RC.UNFIXABLE]
            if fails:
                it["allow"] = {"by": who, "date": datetime.date.today().isoformat(), "failures": fails}
    return items


DEMO_PINOUTS = ("AMS1117-3.3", "AO3401A")      # the demo parts held for a family-table pinout until a person looks


def demo_released(out, cwd=None):
    """`ecadlib demo -o out` as a librarian leaves it after the review: the two family-table pinouts checked and
    released anyway (release-check --allow, by a reviewer), then the demo run again so its exports carry them. Every
    part but CH340C is then released, as in the demo before those pinouts were held. -> the plan's folder"""
    import subprocess, sys
    run = lambda *a: subprocess.run([sys.executable, "-m", *a], cwd=cwd, capture_output=True, text=True, timeout=300)
    assert run("ecadlib", "demo", "-o", str(out)).returncode == 0
    lib = Path(out) / "librarian" / "demo_bom_demo"
    r = run("ecadlib.release_check", str(lib / "plan.json"), *[x for n in DEMO_PINOUTS for x in ("--allow", n)],
            "--user", "Demo Reviewer <reviewer@example.com>")
    assert r.returncode == 2 and "1 held" in r.stdout, r     # 2: CH340C stays held (the demo has no symbol for it)
    assert run("ecadlib", "demo", "-o", str(out)).returncode == 0
    return lib
