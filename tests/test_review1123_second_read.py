"""Review 2026-11-23, the second read and what it releases (1.7, 1.8 and the lower Group 1 items): a footprint built
from a datasheet read is released with no person's look when a second source agrees, so the agreement must cover every
number the pads stand on, and the read must be this part's. Offline: no key, no network, no model.
    python3 -m tests.run_all test_review1123_second_read"""
import copy, json, re, tempfile, types
from pathlib import Path

from ecadlib import crosscheck as CC, datasheet as D, ds_assistant as DA, ds_route as DR, footprint_gen as F, librarian as L
from tests import test_crosscheck as TC, test_datasheet as TD, test_datasheet_batch as TB, test_ds_assistant as TA
from tests import test_fix1026_rd as T
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- helpers
def put(res, job=None, symbol=None):
    """librarian.Run.put_datasheet for real (only put_fp / put_sym stand in, so nothing is written) -> (outcome, st,
    got). outcome: "released" (built, nothing left for a person), "verify" (built, waits for a person), or "held: ..."."""
    run = object.__new__(L.Run)
    run.out, run.rules = Path(tempfile.mkdtemp()), {}
    run.reader = types.SimpleNamespace(assistant=None, cfg=D.settings({}), out=run.out)
    got = {}
    run.put_fp = lambda st, text, fp_name, src, *a, **k: (got.__setitem__("fp", text), st.__setitem__("footprint", {"source": src}))
    run.put_sym = lambda st, sym, src, *a, **k: (got.__setitem__("sym", sym), st.__setitem__("symbol", {"source": src}))
    st = {"name": "TESTPART", "footprint": None, "symbol": symbol, "review": [], "tried": [], "verify": [], "found": {}}
    j = dict({"url": "https://example.com/t.pdf", "where": "BOM", "part": "TESTPART"}, **(job or {}))
    try:
        run.put_datasheet(st, res, j, "fp", "sym")
    except D.DatasheetError as e:
        return "held: " + str(e), st, got
    return ("verify" if st["verify"] else "released"), st, got


def second_answer(t, true, terminals=True, rows=True, asked_rows=False):
    """A perfect second read of the true package t, in the second read's own schema. terminals / rows: False leaves
    them out, as every second read did before 2026-11-23."""
    dims = []
    for k in ("pitch", "body_length", "body_width", "lead_span") + (("terminal_length", "terminal_width") if terminals else ()):
        v = t.get(k)
        if v is None:
            continue
        lo, nom, hi = (None, v, None) if isinstance(v, (int, float)) else v
        dims.append({"name": k, "label": "X", "min": "" if lo is None else str(lo), "nom": "" if nom is None else str(nom),
                     "max": "" if hi is None else str(hi), "unit": "mm"})
    for k in ("length", "width"):
        v = (t.get("exposed_pad") or {}).get(k)
        if v:
            dims.append({"name": f"exposed_pad.{k}", "label": "X", "min": str(v[0]), "nom": str(v[1]), "max": str(v[2]), "unit": "mm"})
    pkg = {"name": t["name"], "package_type": t["package_type"], "pin_count": t.get("pin_count") or 0, "dims": dims, "unresolved": []}
    if asked_rows:
        c = CC.critical(true["spec"])
        pkg["pin_rows"] = [{"pins": list(r)} for r in ((c["sides"] or c["built_sides"] or []) if rows else [])]
    pins = [{"num": str(p["num"]), "name": p["name"]} for p in true["symbol"]["pins"]]
    return {"content": [{"type": "text", "text": json.dumps({"package": pkg, "pins": pins, "embedded_instructions": []})}],
            "stop_reason": "end_turn"}


def checked(res, t, true=None, seen=None, **kw):
    """crosscheck.run for real, the second read answered by second_answer(t, true, **kw) -> (record, job)."""
    true = true or T.read(copy.deepcopy(t))

    class Reader:
        cfg, effort, out = D.settings({}), "high", tempfile.mkdtemp()

        def check_message(self, job, cm, headers, body, key, n=None):
            asked = "pin_rows" in json.dumps(body["output_config"])
            if seen is not None:
                seen.update(asked_rows=asked, body=body, key=key)
            return second_answer(t, true, asked_rows=asked, **kw), "sync"
    job = {"data": None, "part": "TESTPART", "fp_needed": True, "url": "https://example.com/t.pdf", "where": "BOM"}
    cc = CC.run(Reader(), job, res, "model")
    res["cross_check"] = cc
    return cc, job


def scaled(t, key, f):
    v = t[key]
    return dict(copy.deepcopy(t), **{key: tuple(round(x * f, 4) for x in v) if isinstance(v, tuple) else round(v * f, 4)})


LIB_SYMBOL = {"source": "library symbol"}             # the symbol comes from the library: only the footprint is the read's
# the nine one-number misreads the review's sweep found released with wrong copper (a1 r4_single_misread_release.py)
MISREADS = (("TANT_B", "terminal_length", 0.5), ("SMA", "terminal_length", 2.0), ("MELF", "terminal_length", 2.0),
            ("MELF", "terminal_length", 0.5), ("DFN8", "terminal_length", 0.5), ("VQFN16", "terminal_length", 0.5),
            ("SOD123", "terminal_width", 2.0), ("SOD123", "terminal_width", 0.5), ("SOD123", "terminal_length", 0.5))
READ_BUILT = ("TANT_B", "SMA", "MELF", "DFN8", "VQFN16", "SOD123")


# ---------------------------------------------------------------- 1.7: the terminal's size is compared
def test_second_read_is_asked_for_the_terminal_and_its_cache_key_changed():
    dims = CC.output_schema()["properties"]["package"]["properties"]["dims"]["items"]["properties"]["name"]["enum"]
    assert {"terminal_length", "terminal_width"} <= set(dims)
    assert "terminal length" in CC.SYSTEM and "terminal width" in CC.SYSTEM
    # the versions every second read's cache key carried before 2026-11-23: an answer cached then never gave the
    # terminal, so it must not be found under the new key
    assert CC.version(False) != "9ecaae641981" and CC.version(True) != "055804ffd6be"


def test_a_misread_terminal_is_never_agreed_by_a_second_read_that_does_not_give_it():
    """The second read every cached answer and every stub gave until now: pitch, body, span, exposed pad. It cannot
    see a terminal read twice or half, so the read's own pads are not confirmed: the part waits for a person."""
    for name, key, f in MISREADS:
        t = getattr(T, name)
        res = T.read(scaled(t, key, f))
        if DR.hold_reasons(res, True):                # (SMA x2: its pads would short, held outright)
            assert put(res, symbol=LIB_SYMBOL)[0].startswith("held"), name
            continue
        cc, job = checked(res, t, terminals=False)
        assert cc["status"] == "self-checked", (name, key, f, cc["status"])
        assert key in cc["uncompared"], (name, cc["uncompared"])
        out, st, got = put(res, job, LIB_SYMBOL)
        assert out == "verify" and got.get("fp"), (name, key, f, out)
        assert not any("confirmed" in x for x in st["tried"]), st["tried"]
        assert "no second source gave" in CC.summary_line(cc), CC.summary_line(cc)


def test_a_misread_terminal_disagrees_with_a_second_read_that_gives_it():
    for name, key, f in MISREADS:
        t = getattr(T, name)
        res = T.read(scaled(t, key, f))
        if DR.hold_reasons(res, True):
            continue
        cc, _ = checked(res, t)
        assert cc["status"] == "disagreed", (name, key, f, cc["status"])
        assert key in [d["field"] for c in cc["checks"] for d in c["disagreed"]], (name, key, cc["checks"])


def test_true_reads_are_still_released_when_the_second_read_gives_every_number():
    """Not too blunt: the right read of each of those packages, a second read that gives the terminal (and a no-lead
    package's sides) too, is agreed and released with no person as before."""
    for name in READ_BUILT:
        t = getattr(T, name)
        res = T.read(copy.deepcopy(t))
        assert not DR.hold_reasons(res, True), name
        seen = {}
        cc, job = checked(res, t, seen=seen)
        assert cc["status"] == "agreed" and not cc.get("uncompared"), (name, cc["status"], cc.get("uncompared"), CC.summary_line(cc))
        out, st, got = put(res, job, LIB_SYMBOL)
        assert out == "released" and got.get("fp"), (name, out, st["verify"])
        assert seen["asked_rows"] == (t["package_type"] == "qfn" or bool(t.get("pin_positions"))), name


def test_a_standard_land_is_still_released_on_the_numbers_a_table_checks():
    """A SOT-89 / SOT-223 / DPAK read names a checked tab land, which the librarian builds in place of the read's
    pads: the terminal's size moves no copper there, so the agreement stands for that land (and only for it)."""
    for name in ("SOT89", "SOT223", "DPAK"):
        t = getattr(T, name)
        for f in (1.0, 2.0, 0.5):
            res = T.read(scaled(t, "terminal_width", f))
            if DR.hold_reasons(res, True):
                continue
            cc, job = checked(res, t, terminals=False)
            assert cc["status"] == "agreed" and "terminal_width" in cc["uncompared"], (name, f, cc["status"], cc.get("uncompared"))
            out, st, got = put(res, job, LIB_SYMBOL)
            assert out == "released" and st["footprint"]["source"].startswith("standard:"), (name, f, out, st["footprint"])
            # the same record for a footprint built from the read's own numbers is no agreement
            assert CC.built_from_read(cc)["status"] == "self-checked"


def test_build_fields_are_the_numbers_the_pads_stand_on():
    spec = lambda t: T.read(copy.deepcopy(t))["spec"]
    assert CC.build_fields(spec(T.R1206)) == ["body_length", "terminal_length", "body_width"]
    assert CC.build_fields(spec(T.SMA)) == ["body_length", "terminal_length", "terminal_width"]
    assert {"pin_positions", "terminal_length", "terminal_width", "ep_length", "ep_width", "pitch"} <= set(CC.build_fields(spec(T.DFN8)))
    assert {"lead_span", "terminal_length", "terminal_width", "pitch", "pin_count"} <= set(CC.build_fields(spec(T.SOIC8)))
    land = dict(spec(T.SMA), land={"pad_length": 2.0, "pad_width": 1.7, "pad_center": 2.1})
    assert CC.build_fields(land) == []                # the maker's land is the pads: the terminal moves none
    assert CC.build_fields({"package_type": "custom", "pad_table": {"pads": []}}) == []


def test_a_table_never_compares_the_terminal():
    first = CC.critical(T.read(copy.deepcopy(T.SOIC8))["spec"])
    rec = CC.compare(first, dict(first, terminal_length=(9.0, 9.0, 9.0), terminal_width=(9.0, 9.0, 9.0)), "table")
    assert rec["status"] == "agreed" and not {"terminal_length", "terminal_width"} & set(rec["compared"] + rec["not_compared"])
    rec = CC.compare(first, dict(first, terminal_length=(9.0, 9.0, 9.0)), "read")
    assert rec["status"] == "disagreed" and [d["field"] for d in rec["disagreed"]] == ["terminal_length"]


def test_pads_that_would_short_hold_the_read():
    res = T.read(scaled(T.SMA, "terminal_length", 2.0))
    hold = DR.hold_reasons(res, True)
    assert any("the pads would short" in h for h in hold), (DR.assess(res), hold)
    out, st, got = put(res, symbol=LIB_SYMBOL)
    assert out.startswith("held: ") and "the pads would short" in out and not got, out
    # a build that failed outright builds nothing, so it stays a soft line (the standard table may still give the land)
    assert DR._soft("footprint_gen.build failed: ValueError('x')") and not DR._soft(
        "footprint_gen.build failed: the pads would short: pad pairs 1-2 are 0.000 mm apart")
    # a pad table is the maker's land copied pad by pad: pads that meet there stay advice (the release check holds them)
    assert DR._soft("footprint_gen.build failed: the pads would short: pads 5-8 are -0.000 mm from pad 9", {"pad_table": {"pads": [{}]}})


# ---------------------------------------------------------------- 1.8: a no-lead part's rows are never a default
def no_rows(t, name=None, ep=True):
    return {k: v for k, v in dict(t, name=name or t["name"]).items()
            if k not in ("pins_left_right", "pins_top_bottom") and (ep or k != "exposed_pad")}


def test_a_no_lead_read_with_no_rows_is_held_not_built_a_quarter_per_side():
    for nm in ("MLP-8_3x3mm_P0.65mm", "LLP-8_3x3mm_P0.65mm", "QFN-8_3x3mm_P0.65mm", "VSSOP-8"):
        t = no_rows(T.DFN8, nm, ep=False)
        res = T.read(copy.deepcopy(t))
        hold = DR.hold_reasons(res, True)
        assert any(h.startswith(D.ROWS_UNREAD) for h in hold), (nm, DR.assess(res), hold)
        out, st, got = put(res, symbol=LIB_SYMBOL)
        assert out.startswith("held: ") and D.ROWS_UNREAD in out and "pin_sides" in out and not got, (nm, out)
    # 6 pins on a square body (the 2026-11-10 case): the same hold, with a message, not a bare ValueError
    res = T.read(no_rows(T.WSON6, "QFN-6_2x2mm_P0.65mm", ep=False))
    out, st, got = put(res, symbol=LIB_SYMBOL)
    assert out.startswith("held: ") and D.ROWS_UNREAD in out and not got, out


@lite
def test_the_rows_hold_ends_as_a_message_on_the_batch_path_too(tmp_path):
    res = T.read(no_rows(T.DFN8, "MLP-8_3x3mm_P0.65mm", ep=False))
    res["sidecar"]["via"] = "batch"
    run = object.__new__(L.Run)
    run.out, run.rules = tmp_path, {}
    job = {"url": "https://example.com/t.pdf", "where": "BOM", "part": "TESTPART", "key": "k1"}
    run.reader = types.SimpleNamespace(assistant=None, cfg=D.settings({}), out=tmp_path, flush=lambda: {"k1": res},
                                       answered=lambda *a: True)
    wrote = []
    run.put_fp = lambda *a, **k: wrote.append("fp")
    run.put_sym = lambda *a, **k: wrote.append("sym")
    st = {"name": "TESTPART", "footprint": None, "symbol": dict(LIB_SYMBOL), "review": [], "tried": [], "verify": [], "found": {}}
    run.waiting = [(st, job, "fp", "sym")]
    run.flush_datasheets()                            # must not raise
    assert not wrote and st["footprint"] is None
    assert any("DatasheetError" in x and D.ROWS_UNREAD in x for x in st["tried"]), st["tried"]


def test_a_two_sided_name_or_a_checked_standard_package_still_gives_the_rows():
    res = T.read(no_rows(T.DFN8))                      # "DFN" in the name: two sides (datasheet.two_sided, as before)
    assert (res["spec"]["pins_left_right"], res["spec"]["pins_top_bottom"]) == (4, 0) and not DR.hold_reasons(res, True)
    from ecadlib import stdparts as SP
    name = next(n for n, p in sorted(SP.packages().items()) if p.get("package_type") == "qfn" and SP.footprint_for([n])[0] == n
                and p["pins_left_right"] != p["pins_top_bottom"] and p["pins_top_bottom"] > 0 and not re.search(r"DFN|SON", n))
    p = SP.spec(name)
    tup = lambda d: (d["min"], d["nom"], d["max"]) if isinstance(d, dict) else d
    t = dict(name=name, package_type="qfn", pin_count=p["pin_count"], pitch=p["pitch"],
             **{k: tup(p[k]) for k in ("body_length", "body_width", "terminal_length", "terminal_width")})
    if p.get("exposed_pad"):
        t["exposed_pad"] = {k: tup(p["exposed_pad"][k]) for k in ("length", "width")}
    res = T.read(t)
    assert (res["spec"]["pins_left_right"], res["spec"]["pins_top_bottom"]) == (p["pins_left_right"], p["pins_top_bottom"]), name
    assert any("were left blank" in r and name in r for r in res["needs_review"]), res["needs_review"]
    assert not any(r.startswith(D.ROWS_UNREAD) for r in res["needs_review"])


def test_the_second_read_is_asked_for_a_no_lead_packages_sides_and_they_are_compared():
    # a DFN-8 read as a quad (2 pins on each of four sides, the split given): the pin count, pitch, body and terminal
    # all agree, only the sides tell
    wrong = dict(T.DFN8, pins_left_right=2, pins_top_bottom=2, name="QFN-8_3x3mm_P0.65mm")
    wrong.pop("exposed_pad")
    true = {k: v for k, v in T.DFN8.items() if k != "exposed_pad"}
    res = T.read(copy.deepcopy(wrong))
    if not DR.hold_reasons(res, True):
        seen = {}
        cc, job = checked(res, true, seen=seen)
        assert seen["asked_rows"]
        assert cc["status"] == "disagreed", (cc["status"], CC.summary_line(cc))
        assert "pin_positions" in [d["field"] for c in cc["checks"] for d in c["disagreed"]]
    # a right read, and a second read that gives no sides: they were not compared, so it is not agreed
    res = T.read(copy.deepcopy(true))
    cc, job = checked(res, true, rows=False)
    assert cc["status"] == "self-checked" and "pin_positions" in cc["uncompared"], (cc["status"], cc.get("uncompared"))
    assert put(res, job, LIB_SYMBOL)[0] == "verify"
    cc, job = checked(T.read(copy.deepcopy(true)), true)
    assert cc["status"] == "agreed" and "pin_positions" in cc["checks"][-1]["agreed"]
