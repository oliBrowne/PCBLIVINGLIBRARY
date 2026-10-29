"""The KiCad library name index shipped with ecadlib (ecadlib/assets/kicad_library_index.json.gz): a workspace with no
index of its own starts from it instead of reading ~400 gitlab.com tree pages first (about 4 minutes on 2026-10-29,
and one run timed out at page 219 and built the part without KiCad); a build that fails falls back on it.
python3 -m tests.test_kicad_index_snapshot"""
import gzip, json, os, sys
from pathlib import Path

from ecadlib import librarian as L
from tests.fixtures_lite import lite
from tests.test_librarian import FakeHTTP, _Gitlab429

ROOT = Path(__file__).resolve().parent.parent


def _on(monkeypatch):
    monkeypatch.setenv("ECADLIB_KICAD_SNAPSHOT", "1")        # tests/__init__.py turns it off for the rest of the suite


@lite
def test_no_index_yet_starts_from_the_shipped_one_without_gitlab(tmp_path, monkeypatch):
    _on(monkeypatch)
    http = FakeHTTP()
    f = tmp_path / "cache" / "kicad_library_index.json"
    lib = L.KiCadLibrary(http, f)
    assert not [u for u in http.calls if "gitlab.com" in u], http.calls     # not one tree page read
    assert lib.error is None and len(lib.symbols) > 10000 and len(lib.footprints) > 10000
    assert lib.find_symbol("NE555DR")[0] == "Timer:NE555D"                   # the part that took 4 minutes
    kept = json.loads(f.read_text(encoding="utf-8"))                          # kept as this cache's own index
    assert kept["built"] and kept["symbols"] == lib.symbols
    again = FakeHTTP()
    L.KiCadLibrary(again, f)
    assert not again.calls


@lite
def test_an_index_of_its_own_and_refresh_still_win(tmp_path, monkeypatch):
    _on(monkeypatch)
    f = tmp_path / "idx.json"
    f.write_text(json.dumps({"built": "2026-10-01", "symbols": {"MYPART": ["Mine"]}, "footprints": {}}), encoding="utf-8")
    assert L.KiCadLibrary(FakeHTTP(), f).symbols == {"MYPART": ["Mine"]}
    http = FakeHTTP()
    lib = L.KiCadLibrary(http, tmp_path / "fresh.json", refresh=True)      # --refresh-index reads gitlab.com
    assert any("tree" in u for u in http.calls) and "MYPART" not in lib.symbols and len(lib.symbols) < 100


@lite
def test_a_failed_build_falls_back_on_the_shipped_index(tmp_path, monkeypatch):
    _on(monkeypatch)
    http = _Gitlab429("timeout")
    lib = L.KiCadLibrary(http, tmp_path / "idx.json", refresh=True)        # no index of its own, gitlab.com times out
    assert lib.error and "page 2" in lib.error
    assert lib.find_symbol("NE555DR")[0] == "Timer:NE555D"


@lite
def test_while_a_failed_build_waits_to_retry_a_broken_index_still_falls_back(tmp_path, monkeypatch):
    _on(monkeypatch)
    f = tmp_path / "idx.json"
    L.KiCadLibrary(_Gitlab429("timeout"), f, refresh=True)                  # leaves idx.json.retry.json behind
    assert f.with_name("idx.json.retry.json").exists()
    f.write_text("{not json", encoding="utf-8")                             # and an index that cannot be read
    http = FakeHTTP()
    lib = L.KiCadLibrary(http, f)
    assert not http.calls and lib.error                                     # still waiting, gitlab.com left alone
    assert lib.find_symbol("NE555DR")[0] == "Timer:NE555D"


@lite
def test_switched_off_it_builds_from_gitlab(tmp_path, monkeypatch):
    monkeypatch.setenv("ECADLIB_KICAD_SNAPSHOT", "0")
    http = FakeHTTP()
    lib = L.KiCadLibrary(http, tmp_path / "idx.json")
    assert any("tree" in u for u in http.calls) and len(lib.symbols) < 100


def test_shipped_file_is_packaged_and_rebuilds_byte_for_byte():
    snap = L.KiCadLibrary.SNAPSHOT
    assert snap.is_file() and snap.stat().st_size < 400_000
    data = gzip.decompress(snap.read_bytes())
    d = json.loads(data)
    assert set(d) == {"built", "symbols", "footprints"} and d["built"][:2] == "20"
    assert '"assets/*"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")       # in the wheel's package-data
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import build_kicad_index as B
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    assert os.environ.get("ECADLIB_KICAD_SNAPSHOT") == "0"                  # the script never builds from the old copy
    assert B.snapshot_bytes(d) == snap.read_bytes(), "write it with scripts/build_kicad_index.py (deterministic)"


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
