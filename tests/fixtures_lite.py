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
