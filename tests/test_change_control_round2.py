"""Change control, second #105 review round: house parameter names on the Altium 365 identity guard, publishers
reading files under the library lock, `part adopt` diffing against the stored snapshot, the symbol-export fallback
comparing as KiCad, value detail tokens, stored digests of an older schema, numbered second-source fields, refusal
messages, and DbLib-placed components in the Altium project check.

Each test is a regression for a finding of the round-2 critique (ids R2-n and the builders' known items a-d)."""
import contextlib, io, json, subprocess, sys, tempfile, threading
from pathlib import Path
from unittest import mock

from ecadlib import altium365 as A365, change_cli as CC, change_diff as CD, change_gate as CG, cliutil
from ecadlib import fingerprint as FP, ledger as LG
from tests.test_change_control import ANN, BOB, ROOT, demo, item, items_of, write_symbol

SGD = [("1", "S", "passive"), ("2", "G", "input"), ("3", "D", "passive")]
GSD = [("1", "G", "input"), ("2", "S", "passive"), ("3", "D", "passive")]


def _params(d):
    return [{"name": k, "value": v} for k, v in d.items()]


def test_a365_identity_guard_reads_house_names():
    """R2-1: with [parameters.names] the push sends "MfrPN"/"Maker"; an MPN or maker change under those names is still
    an identity change of the released component, and a cosmetic change is not."""
    rules = {"parameters": {"names": {"Manufacturer Part Number": "MfrPN", "Manufacturer": "Maker"}}}
    existing = {"details": {"parameters": _params({"Value": "10k", "MfrPN": "0603WAF1002T5E", "Maker": "UNI-ROYAL"})}}
    new = _params({"Value": "10k", "MfrPN": "RC0603FR-07100RL", "Maker": "Yageo"})
    ident = A365.identity_change(existing, new, rules)
    assert any("MPN" in t for t in ident) and any("anufacturer" in t for t in ident), ident
    same = _params({"Value": "10k", "MfrPN": "0603WAF1002T5E", "Maker": "UNI-ROYAL", "Supplier 1": "LCSC"})
    assert A365.identity_change(existing, same, rules) == []
