"""Run every tests/test_*.py module (plain functions, no pytest needed). Exit 1 if any test fails.
    python3 -m tests.run_all [name-filter]"""
import importlib, os, sys, time, traceback
from pathlib import Path


def main(argv=None):
    flt = (argv if argv is not None else sys.argv[1:])
    failed, ran, t0 = [], 0, time.time()
    for f in sorted(Path(__file__).parent.glob("test_*.py")):
        mod = importlib.import_module(f"tests.{f.stem}")
        for name, fn in list(vars(mod).items()):
            if not name.startswith("test_") or not callable(fn) or (flt and not any(s in f"{f.stem}.{name}" for s in flt)):
                continue
            ran += 1
            try:
                fn()
                print(f"ok    {f.stem}.{name}")
            except (Exception, SystemExit):
                failed.append(f"{f.stem}.{name}")
                tb = traceback.format_exc()
                print(f"FAIL  {f.stem}.{name}\n{tb}")
                if os.environ.get("GITHUB_ACTIONS"):       # an annotation, readable without the job log
                    msg = tb[-3000:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                    print(f"::error title=FAIL {f.stem}.{name}::{msg}")
    print(f"\n{ran - len(failed)}/{ran} passed in {time.time() - t0:.1f}s")
    if failed:
        print("failed:\n  " + "\n  ".join(failed))
    return 1 if failed or not ran else 0


if __name__ == "__main__":
    sys.exit(main())
