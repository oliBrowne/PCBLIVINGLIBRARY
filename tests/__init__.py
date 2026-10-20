import os, tempfile

# Keys and settings stored by `ecadlib keys` / the setup page live in a per-user folder (ecadlib.userconfig); the tests
# use an empty one of their own, so a developer's stored keys never change a result.
os.environ["ECADLIB_HOME"] = tempfile.mkdtemp(prefix="ecadlib-test-home-")

# The undocumented EasyEDA / JLCPCB endpoints ([providers] unofficial) are on by default again since 2026-10-20 (they
# were opt-in from 2026-10-19). Most tests were written against fixtures from those sources, so they run with them on
# whatever ships; tests/test_unofficial_default.py checks the shipped default and its effect.
from ecadlib import rules as _RU                       # noqa: E402

SHIPPED_UNOFFICIAL = _RU.DEFAULTS["providers"]["unofficial"]
_RU.DEFAULTS["providers"]["unofficial"] = True
