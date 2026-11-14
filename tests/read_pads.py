"""For tests of the datasheet read path itself: the read's own pads, with [datasheet] standard_land off. Since the
held-out test of 2026-11-13 the default is "auto" (a read that prints no land and names one checked standard package
gets the table's land), which these tests' fake reads (a VQFN-16, an SOIC-8 with no land) would otherwise take."""
import functools
from unittest import mock

from ecadlib import datasheet as D, rules as RU


def read_pads(fn):
    @functools.wraps(fn)
    def run(*a, **kw):
        with mock.patch.dict(D.SETTINGS, {"standard_land": "off"}), \
                mock.patch.dict(RU.DEFAULTS["datasheet"], {"standard_land": "off"}):
            return fn(*a, **kw)
    return run
