"""Regression tests for the debug sweep's librarian-side reports (DS2-*): rebuild, upkeep, change_cli.
Plain functions (tests.run_all, no pytest)."""
import contextlib, hashlib, io
from pathlib import Path

from ecadlib import cli, librarian as L, rebuild as RB
from tests import upgrade_fixtures as UF
from tests.e2e_support import seed_cache
from tests.fixtures_lite import lite

QUIET = lambda *a, **k: None


def _tree(root):
    """{relative path: sha256} of every file under root."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def _library(d):
    """A real librarian output folder (offline, from the recorded cache) with its lock and change-control ledger."""
    d = Path(d).resolve()
    (d / "bom.csv").write_text(UF.BOM, encoding="utf-8")
    seed_cache(d / "cache")
    L.run(d / "bom.csv", out=d / "lib", offline=True, cache=d / "cache", rules={}, log=QUIET)
    return d / "lib"


@lite
def test_ds2_46_rebuild_check_keep_never_deletes_or_writes_into_the_library(tmp_path):
    lib = _library(tmp_path)
    before = _tree(lib)
    assert "plan.json" in before and "library.ledger.jsonl" in before and RB.LOCK in before
    # --keep the folder that holds the library (`--keep .` from its parent), and --keep the library itself
    for keep in (lib.parent, lib):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = cli.main(["rebuild", str(lib), "--check", "--keep", str(keep)])
        assert _tree(lib) == before, f"--check --keep {keep} changed the library"
        assert rc == 2 and "internal error" not in err.getvalue(), (rc, err.getvalue())
