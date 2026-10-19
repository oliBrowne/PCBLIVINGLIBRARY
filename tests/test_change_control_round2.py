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


def test_export_holds_the_library_lock_until_the_publish_is_recorded():
    """R2-4: a gated writer that starts right after the export's settle step waits for the export to finish, so the
    export ships the released symbol (not the writer's unsettled one) and records that as its publish."""
    from ecadlib import kicad_export as KX
    plan, lib = demo()
    real, started, wrote = CG.publish_guard, threading.Event(), threading.Event()

    def writer():
        g = CG.Guard(lib, "librarian")
        started.set()
        g.begin()                              # takes the library lock: blocks while the export runs
        write_symbol(lib, item(items_of(plan), "AO3401A"), SGD)
        wrote.set()
        items = items_of(plan)
        g.settle(items)
        CG.write_plan(plan, items)

    th = threading.Thread(target=writer)

    def guard_then_writer(*a, **k):
        r = real(*a, **k)
        th.start()
        started.wait(10)
        assert not wrote.wait(1.0), "the writer got the lock while the export was running"
        return r

    with mock.patch.object(CG, "publish_guard", guard_then_writer):
        KX.export(plan, lib.parent / "kx", "x", log=lambda *a, **k: None)
    th.join(60)
    shipped = FP.kicad_sym_file(lib.parent / "kx" / "x.kicad_sym", "AO3401A")[0]
    assert [shipped["pins"][n]["raw"] for n in ("1", "2", "3")] == ["G", "S", "D"], shipped["pins"]
    ev = [(e["event"], e.get("revision")) for e in LG.Ledger(lib).lines() if e.get("part") == "AO3401A"]
    assert ev[-1][0] == "hold" and ("publish", 1) in ev, ev


def _lose_ledger(lib):
    (lib / LG.LEDGER).unlink()


def test_adopt_diffs_the_disk_against_the_last_stored_fingerprint():
    """R2-3: after the ledger is lost, a pin swap made on disk is shown and refused by `part adopt`; --accept-changes
    takes it and records the changes; an untouched part adopts with no difference, signed by the named person."""
    plan, lib = demo()
    _lose_ledger(lib)
    write_symbol(lib, item(items_of(plan), "AO3401A"), SGD)
    e = None
    try:
        CC.adopt(plan, ["AO3401A"], ANN, "checked")
    except CG.ChangeError as x:
        e = x
    assert e and "pins swapped" in str(e) and "--accept-changes" in str(e), e
    assert LG.Ledger(lib).head("AO3401A") is None
    rows = CC.adopt(plan, ["RES-0603-10k"], ANN, "checked")
    assert rows[0]["by"] == ANN and rows[0]["changes"] == [] and "blobs" in rows[0]["compared"], rows
    rows = CC.adopt(plan, ["AO3401A"], ANN, "checked the swap", accept_changes=True)
    head = LG.Ledger(lib).head("AO3401A")
    assert head["decision"] == "adopted" and any("swapped" in c["text"] for c in head["changes"]), head


def test_adopt_never_compares_with_a_held_change():
    """R2-3: a held change keeps its fingerprint in the blob store (newer than the release). After the ledger is lost,
    the held change written to disk again is still compared with the release, not with itself."""
    plan, lib = demo()
    items = items_of(plan)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    CG.publish_guard(plan, items, "kicad")
    assert LG.Ledger(lib).proposal("AO3401A")
    _lose_ledger(lib)
    write_symbol(lib, item(items_of(plan), "AO3401A"), SGD)
    e = None
    try:
        CC.adopt(plan, ["AO3401A"], ANN, "checked")
    except CG.ChangeError as x:
        e = x
    assert e and "pins swapped" in str(e), e


def _qp_symbol(t1="input", t2="passive", t3="passive", alt=""):
    pin = '(pin {} line (at {}) (length 2.54) (name "{}" (effects (font (size 1.27 1.27)))) (number "{}" (effects (font (size 1.27 1.27)))){})'
    return ('(kicad_symbol_lib (version 20231120) (generator test)\n  (symbol "QP_TEST" (pin_names (offset 0)) (in_bom yes) (on_board yes)\n'
            '    (property "Reference" "Q" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
            '    (property "Value" "QP" (at 0 0 0) (effects (font (size 1.27 1.27))))\n    (symbol "QP_TEST_1_1"\n      '
            + "\n      ".join([pin.format(t1, "-5.08 0 0", "G", "1", alt), pin.format(t2, "2.54 -5.08 90", "S", "2", ""),
                               pin.format(t3, "2.54 5.08 270", "D", "3", "")]) + ")))")


class _OneSymbolHTTP:
    def __init__(self, text):
        self.text = text

    def get(self, url, **kw):
        from ecadlib.httpcache import Response
        return Response(200, self.text) if "QP_TEST" in url else Response(404, "")


def test_reused_kicad_symbol_is_compared_as_kicad_with_the_first_publish():
    """R2-2: KiCad-only pin details (passive -> no_connect / free, an added alternate) do not survive the Altium round
    trip. The first KiCad publish of a revision records them; a later export whose source changed one of them exports
    the released symbol with a warning."""
    from ecadlib import kicad_export as KX
    plan, lib = demo()
    items = items_of(plan)
    item(items, "AO3401A")["source"]["symbol"] = "kicad:Transistor_FET:QP_TEST"
    CG.write_plan(plan, items)

    def run(text, tag):
        rep = KX.export(plan, lib.parent / tag, "x", http=_OneSymbolHTTP(text), log=lambda *a, **k: None)
        row = next(r for r in rep["parts"] if r["part"] == "AO3401A")
        return row["symbol"], [w for w in rep["warnings"] if w.startswith("AO3401A") and "not the released" in w]

    how, warn = run(_qp_symbol(), "k0")
    assert how.startswith("KiCad Transistor_FET:QP_TEST (reused") and not warn, (how, warn)
    pub = [e for e in LG.Ledger(lib).history("AO3401A") if e.get("event") == "publish" and (e.get("detail") or {}).get("ksym")]
    assert pub and pub[-1]["detail"]["ksym"]["3"]["ktype"] == "passive", pub
    for k, text, says in (("k1", _qp_symbol(t3="no_connect"), "KiCad type passive -> no_connect"),
                          ("k2", _qp_symbol(t2="free"), "KiCad type passive -> free"),
                          ("k3", _qp_symbol(alt=' (alternate "SWAP" output line)'), "alternate functions")):
        how, warn = run(text, k)
        assert "read back" in how and warn and says in warn[0], (k, how, warn)
    how, warn = run(_qp_symbol(), "k4")                   # the source as released: reused again
    assert "reused" in how and not warn, (how, warn)


def _value_class(a, b, kind=""):
    def fp(v):
        return FP.build("", params={"Value": v, "Manufacturer Part Number": "X1", **({"Description": kind} if kind else {})})
    ch = [c for c in CD.diff(fp(a), fp(b)) if c["area"] == "identity"]
    return CD.worst(ch)


def test_value_detail_accepts_only_recognised_tokens():
    """R2-5: only a tolerance, rating, temperature coefficient, dielectric or package size after the value is "more
    detail"; DNF, NC, a parallel or another value after it, or a unit that is not the part's, is an identity change.
    A rating written with a space is the same rating."""
    for a, b in (("10k", "10k DNF"), ("10k", "10k NC"), ("10k", "10k (do not fit)"), ("4.7k", "4.7k||1k"),
                 ("10k", "10k 0R"), ("100n", "100nH"), ("10", "10V"), ("100nF 50V", "100nF 6.3V")):
        assert _value_class(a, b) == "identity", (a, b)
        assert _value_class(a, b, "Resistor") == "identity", (a, b)
    assert _value_class("100n", "100nH", "Ceramic capacitor") == "identity"
    for a, b, kind in (("10uF 25V", "10uF 25 V", ""), ("10k", "10k 1%", ""), ("10k", "10k 1% 0603", ""),
                       ("100nF", "100nF 50V X7R", ""), ("10k", "10k 1/4W", ""), ("10k 1%", "10k", ""),
                       ("100n", "100nF", "Ceramic capacitor"), ("10k", "10kΩ", "Resistor"), ("4k7", "4.7k", "")):
        assert _value_class(a, b, kind) == "cosmetic", (a, b, kind)


# ---------------------------------------------------------------- stored digests of an older schema (R2-6, a, b)
def test_approve_after_upgrade_does_not_report_an_overwritten_edit():
    """R2-6: on a library released by an older ecadlib, approving a held change compared the disk's new digest with
    the head's stored old one, so every approval warned of a newer edit and wrote an `overwritten` line."""
    from tests.test_change_control_classifier import _age_ledger
    plan, lib = demo()
    _age_ledger(lib)
    items = items_of(plan)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    CG.publish_guard(plan, items, "kicad")
    rows = CC.approve(plan, ["AO3401A"], ANN, "ok", digests={"AO3401A": CG.review_digest(LG.Ledger(lib), "AO3401A")})
    assert rows[0]["result"] == "released" and not any(r.get("warning") for r in rows), rows
    assert not [e for e in LG.Ledger(lib).lines() if e.get("event") == "overwritten"]


def test_revert_after_upgrade_writes_no_line_for_an_unchanged_sibling():
    """Known item (b): reverting RES-0603-10k after an upgrade also wrote a revert line (and a new revision) for
    RES-0603-4.7k, which shares its .SchLib but did not change."""
    from tests.test_change_control_classifier import _age_ledger
    plan, lib = demo()
    _age_ledger(lib)
    items = items_of(plan)
    for p in item(items, "RES-0603-10k")["parameters"]:
        if p["name"] == "Value":
            p["value"] = "10k 1%"                          # more detail: released as r2 without asking
    CG.write_plan(plan, items)
    CG.publish_guard(plan, items_of(plan), "kicad")
    assert LG.Ledger(lib).head("RES-0603-10k")["revision"] == 2
    before = LG.Ledger(lib).head("RES-0603-4.7k")["revision"]
    CC.revert(plan, "RES-0603-10k@1", ANN, "undo")
    lg = LG.Ledger(lib)
    assert lg.head("RES-0603-10k")["revision"] == 3 and lg.head("RES-0603-4.7k")["revision"] == before
    assert not [e for e in lg.history("RES-0603-4.7k") if e.get("event") == "revert"]


def test_a_change_rejected_before_an_upgrade_is_still_known_as_rejected():
    """Known item (a): a reject line keeps the digest of its schema. After an upgrade the same change came back as a
    fresh hold with no "rejected before" note. A reject line now keeps the proposal's snapshot (read again by a later
    schema); an older line without one is matched on what its stored fingerprint recorded."""
    plan, lib = demo()
    items = items_of(plan)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    CG.publish_guard(plan, items, "kicad")
    CC.reject(plan, ["AO3401A"], BOB, "the old pinout is right")
    lg = LG.Ledger(lib)
    rej = [e for e in lg.lines() if e.get("event") == "reject"][-1]
    assert rej.get("files") and rej.get("fingerprint"), rej
    # as an older ecadlib wrote it: no snapshot, and the digest of its own fingerprint schema
    fp = lg.get_json(rej["fingerprint"])
    fp["schema"] = 1
    for p in ((fp.get("symbol") or {}).get("pins") or {}).values():
        p.pop("inst", None)
    fp["hashes"] = FP.hashes(fp)
    lg.put_json(fp)
    raw = []
    for line in lg.path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("event") == "reject":
            e = {k: v for k, v in e.items() if k not in ("files", "fingerprint", "item")}
            e["digest"] = fp["hashes"]["digest"]
        raw.append(json.dumps(e, sort_keys=True))
    lg.path.write_text("\n".join(raw) + "\n", encoding="utf-8")
    items = items_of(plan)
    write_symbol(lib, item(items, "AO3401A"), SGD)        # the same change comes again
    CG.publish_guard(plan, items, "kicad")
    prop = LG.Ledger(lib).proposal("AO3401A")
    assert prop and (prop.get("rejected") or {}).get("by") == BOB, prop and prop.get("rejected")


def test_numbered_second_sources_and_a_house_part_number():
    """R2-7: "Manufacturer Part Number 2" / "Manufacturer 2" (a second source that may be fitted) changing or being
    added is an identity change; a house "Part Number" added next to the MPN is the library's own number (cosmetic).
    Supplier part numbers stay cosmetic."""
    base = {"Value": "10k", "Manufacturer Part Number": "RC0603FR-0710KL", "Manufacturer": "Yageo"}

    def worst(old, new):
        return CD.worst(CD.diff(FP.build("", params={**base, **old}), FP.build("", params={**base, **new})))

    assert worst({}, {"Part Number": "ACME-00123"}) == "cosmetic"
    assert worst({"Manufacturer Part Number 2": "0603WAF1002T5E"}, {"Manufacturer Part Number 2": "ERJ-3EKF1002V"}) == "identity"
    assert worst({}, {"Manufacturer Part Number 2": "ERJ-3EKF1002V"}) == "identity"
    assert worst({"Manufacturer 2": "UNI-ROYAL"}, {"Manufacturer 2": "Panasonic"}) == "identity"
    assert worst({}, {"Supplier Part Number 2": "C1234"}) == "cosmetic"


def test_refusals_do_not_hand_out_the_current_digest_and_a_held_choice_is_not_ready():
    """R2-8: a refused approval printed the digest of the change as it is now, so it could be approved without being
    read; `resolve choose` printed "now ready" with rc 0 when change control held the choice."""
    plan, lib = demo()
    items = items_of(plan)
    write_symbol(lib, item(items, "AO3401A"), SGD)
    CG.publish_guard(plan, items, "kicad")
    cur = CG.review_digest(LG.Ledger(lib), "AO3401A")
    for d in ("ffffffffffff", "fffff", ""):
        try:
            CC.approve(plan, ["AO3401A"], ANN, digests={"AO3401A": d})
            raise AssertionError("approved")
        except CG.ChangeError as e:
            assert cur[:6] not in str(e) and "part show" in str(e), e
    plan, lib = demo()
    env = {**__import__("os").environ, "ECADLIB_USER": ANN}
    r = subprocess.run([sys.executable, "-m", "ecadlib", "resolve", str(plan), "--part", "AO3401A", "--offline",
                        "--cache", str(lib.parent / "cache"), "choose", "footprint", "spec:SOT-23-5"],
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=600)
    assert r.returncode == 2 and "held, not applied" in r.stdout and "now ready" not in r.stdout, r.stdout + r.stderr


def test_altium_project_check_lists_dblib_placed_components_as_not_checked():
    """Known item (d): a component placed from a DbLib carries no workspace revision. The Altium project check
    skipped it without a word; it is now a "not checked" row that says why (and does not fail the check)."""
    from ecadlib import altium_project as AP, project_check as PJ

    class Nexar:
        def find(self, ws, name):
            return {"id": "CMP-1", "name": name, "revision": {"revisionId": "R1"}} if name == "WS-PMOS" else None

    comps = [{"designator": "Q1", "design_item_id": "WS-PMOS", "vault_guid": "v", "item_guid": "i", "revision_guid": "R1",
              "libref": "WS-PMOS"},
             {"designator": "R9", "design_item_id": "RES-0603-10k", "vault_guid": "", "item_guid": "", "revision_guid": "",
              "libref": "Resistor", "source_library": "ecadlib.DbLib", "database_table": "Resistors"},
             {"designator": "R10", "design_item_id": "RES-0603-10k", "libref": "Resistor", "source_library": "ecadlib.DbLib",
              "database_table": "Resistors"}]
    with mock.patch.object(AP, "load_project", lambda p, rules=None: {"path": p, "components": comps}):
        res = PJ.check_altium("board.PrjPcb", Nexar(), "https://acme.365.altium.com")
    rows = {r["id"]: r for r in res["rows"]}
    assert rows["WS-PMOS"]["state"] == "same"
    r = rows["RES-0603-10k"]
    assert r["state"] == "not checked" and r["refs"] == ["R10", "R9"] and "placed from DbLib ecadlib.DbLib" in r["why"], r
    assert r["action"] == "pass" and res["held"] == 0
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        PJ.print_check(res)
    assert "not checked" in buf.getvalue() and "RES-0603-10k" in buf.getvalue(), buf.getvalue()


def test_an_ohm_sign_is_the_resistor_unit():
    """R3-2: "10k" -> "10k Ohm" / "10kΩ" / "10k Ω" only writes the unit (Ω is unambiguous, so also on a part of unknown
    kind); on a part known to be a capacitor it is another value. "100n" -> "100nF" stays held for an unknown kind."""
    for b in ("10k Ohm", "10kΩ", "10k Ω", "10k ohms"):
        assert _value_class("10k", b) == "cosmetic", b
        assert _value_class("10k", b, "Resistor") == "cosmetic", b
        assert _value_class("10k", b, "Ceramic capacitor") == "identity", b
    assert _value_class("10k Ohm", "10k Ohm 1%") == "cosmetic"
    assert _value_class("100n", "100nF") == "identity"
