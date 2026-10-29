"""Refresh ecadlib/assets/kicad_library_index.json.gz, the KiCad library name index shipped with ecadlib, from
gitlab.com (the same tree pages a run without one reads; about 4 minutes). Run it before a release:
    python scripts/build_kicad_index.py [--cache DIR]
A stopped run (timeout, HTTP 429) resumes from the pages cached in --cache (default: a temp folder)."""
import argparse, gzip, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ECADLIB_KICAD_SNAPSHOT"] = "0"            # build from gitlab.com, never from the copy being replaced
from ecadlib import librarian as L  # noqa: E402
from ecadlib.httpcache import CachedHTTP  # noqa: E402


def snapshot_bytes(index):
    """The .json.gz for an index dict: sorted, compact, no gzip time stamp, so the same index gives the same file."""
    data = json.dumps(index, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return gzip.compress(data, compresslevel=9, mtime=0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=None, help="HTTP cache for the tree pages (default: a temp folder)")
    a = ap.parse_args(argv)
    cache = Path(a.cache or tempfile.mkdtemp(prefix="kicad-index-"))
    lib = L.KiCadLibrary(CachedHTTP(cache, min_interval=0.2, timeout=60), cache / "kicad_library_index.json", refresh=True)
    if lib.error or not lib.symbols or not lib.footprints:
        print(f"not written: {lib.error or 'the index came back empty'}", file=sys.stderr)
        return 1
    built = json.loads((cache / "kicad_library_index.json").read_text(encoding="utf-8"))["built"]
    out = ROOT / "ecadlib" / "assets" / "kicad_library_index.json.gz"
    out.write_bytes(snapshot_bytes({"built": built, "symbols": lib.symbols, "footprints": lib.footprints}))
    print(f"wrote {out.relative_to(ROOT)}: {len(lib.symbols)} symbols, {len(lib.footprints)} footprints, built {built}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
