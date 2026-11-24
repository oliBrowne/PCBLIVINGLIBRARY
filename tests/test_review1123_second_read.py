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
    run.store, run.second = None, {}                # the shared store (main, #289): off
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
    run.store, run.second = None, {}                # the shared store (main, #289): off
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


# ---------------------------------------------------------------- a PDF the assistant saved is checked to name the part
ASSIST = {"datasheet": {"reader": "assistant"}}
DEAD = "https://maker.example/LM358DR.pdf"


def _saved(d, lines, part="LM358DR"):
    """The job of `part` whose only PDF is one the assistant saved as <MPN>.pdf (nothing here can download the link)."""
    rd = TC.reader(d, ASSIST)
    rd.offline = True
    DA.pdf_folder(rd.assistant).mkdir(parents=True, exist_ok=True)
    (DA.pdf_folder(rd.assistant) / DA.pdf_name(part)).write_bytes(TC.TR.pdf_lines(lines))
    try:
        return rd, rd.job(part, [(DEAD, "BOM")], "SOIC-8", mpn=part)
    except D.DatasheetError as e:
        return rd, e


def test_a_pdf_the_assistant_saved_that_does_not_name_the_part_keeps_the_part_with_a_person():
    api = TA._api()
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd, job = _saved(d, [["ACME ZX9000 quad op-amp datasheet", "Package outline SOIC-14"]])
        assert "saved by your assistant" in job["prov"]["where"], job["prov"]
        assert "doesn't mention LM358DR" in job["prov"]["unnamed"], job["prov"]
        try:
            rd.read_now(job)
            raise AssertionError("no AssistantPending")
        except D.AssistantPending:
            pass
        (DA.folder(rd.assistant, job) / "answer.json").write_text(json.dumps(TD.GOOD), encoding="utf-8")
        rd, job = _saved(d, [["ACME ZX9000 quad op-amp datasheet", "Package outline SOIC-14"]])
        res = rd.read_now(job)
        line = [r for r in res["needs_review"] if "doesn't mention LM358DR" in r]
        assert line and "check it is this part's datasheet" in line[0] and "taken on its file name" in line[0], res["needs_review"]
        assert L.UNSURE_READ.search(line[0])          # no release with no person, whatever agrees with that PDF
        res["cross_check"] = {"status": "agreed", "mode": "auto", "checks": [{"status": "agreed", "against": "second read (m)",
                                                                              "source": "second read"}]}
        assert L.read_confirmed(res, job, ["footprint"]) is None
    assert api.calls == []


def test_a_pdf_the_assistant_saved_that_names_the_part_is_read_as_before_and_a_notice_is_not_read():
    api = TA._api()
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd, job = _saved(d, [["LM358DR dual operational amplifier", "Package outline SOIC-8"]])
        assert "saved by your assistant" in job["prov"]["where"] and "unnamed" not in job["prov"], job["prov"]
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd, got = _saved(d, [["Product Change Notification", "LM358DR assembly site change"]])
        text = str(got) if isinstance(got, Exception) else json.dumps(got["prov"].get("attempts"))
        assert "not a datasheet" in text and "replace it with the datasheet" in text, text
        assert isinstance(got, Exception) or got["prov"].get("sent_as") == "url", got   # the file itself is never read


# ---------------------------------------------------------------- one assistant folder per request
def test_another_request_of_the_same_pdf_is_not_answered_by_the_first_answer():
    """More pages or another package of one PDF: the key's first 10 characters (the PDF's hash) are the same."""
    api = TA._api()
    with tempfile.TemporaryDirectory() as d, TB.patched(api):
        rd = TC.reader(d, ASSIST)
        j1 = rd.job("TESTPART", [(TC.URL, "BOM")], TC.QFN)
        try:
            rd.read_now(j1)
        except D.AssistantPending:
            pass
        f1 = DA.folder(rd.assistant, j1)
        (f1 / "answer.json").write_text(json.dumps(TD.GOOD), encoding="utf-8")
        rd = TC.reader(d, ASSIST)
        j2 = rd.job("TESTPART", [(TC.URL, "BOM")], "SOIC-8_3.9x4.9mm_P1.27mm")
        assert j1["key"] != j2["key"] and j1["key"][:10] == j2["key"][:10]
        f2 = DA.folder(rd.assistant, j2)
        assert f1 != f2 and DA.answer(rd.assistant, j2) is None
        try:
            rd.read_now(j2)
            raise AssertionError("the other package's request was answered by the first answer")
        except D.AssistantPending:
            pass
        assert (f2 / "request.md").is_file() and "SOIC-8" in (f2 / "request.md").read_text(encoding="utf-8")
        assert DA.answer(rd.assistant, j1) is not None  # the first request still has its answer
    assert api.calls == []


def test_an_answer_is_taken_only_for_the_request_its_folder_was_written_for():
    with tempfile.TemporaryDirectory() as d:
        k1, k2 = "0123456789abcdef-m-v1-aaaaaaaaaaaa", "0123456789abcdef-m-v1-bbbbbbbbbbbb"
        j1, j2 = {"part": "P", "name": "P", "key": k1}, {"part": "P", "name": "P", "key": k2}
        # a folder under the name used before 2026-11-23 (the key's first 10 characters), answered for request 1
        old = Path(d) / "P-0123456789"
        old.mkdir()
        (old / "job.json").write_text(json.dumps({"part": "P", "name": "P", "key": k1}), encoding="utf-8")
        (old / "answer.json").write_text("{}", encoding="utf-8")
        assert DA.folder(d, j1) == old and DA.answer(d, j1) is not None      # its own request: still usable
        assert DA.folder(d, j2) != old and DA.answer(d, j2) is None          # another request of that PDF: not
        assert DA.folder(d, dict(j1, part="Q")) != old                       # nor another part's
        # an answer.json in a folder whose job.json is another request's (or gone) is no answer, and is set aside
        new = DA.folder(d, j2)
        new.mkdir()
        (new / "answer.json").write_text("{}", encoding="utf-8")
        assert DA.answer(d, j2) is None
        (new / "job.json").write_text(json.dumps({"part": "P", "key": k1}), encoding="utf-8")
        assert DA.answer(d, j2) is None
        DA.request(d, dict(j2, data=None))
        assert DA.answer(d, j2) is None and (new / "answer.other-request.json").is_file()
        (new / "answer.json").write_text("{}", encoding="utf-8")            # answered after the request was written
        assert DA.answer(d, j2) is not None
        # the kinds keep their mark, and the whole key is in the name
        c = DA.folder(d, {"name": "P", "key": "check-0123456789abcdef-m-v1-cccccccccccc"}).name
        assert c.startswith("P-check-0123456789-") and DA.folder(d, {"name": "P", "key": "pins-ab12cd34ef567890"}).name.startswith("P-pins-")
        assert DA.folder(d, {"name": "P", "key": "check-0123456789abcdef-m1-v1-cccccccccccc"}).name != c


# ---------------------------------------------------------------- a schema error does not switch the build checks off
def _overlapping_land(name=None):
    """A SOIC-8 read with a maker land whose pads overlap by 0.73 mm (a1 r1_schema_skips_checks.py)."""
    e = copy.deepcopy(TD.INCH)
    e["package"]["land"] = {"pad_length": 1.5, "pad_width": 2.0, "pad_center": 2.7, "pad_center_y": None}
    if name:
        e["package"]["name"] = name
    return D.process(e, "TESTPART", "https://example.com/t.pdf")


def test_a_name_the_schema_does_not_take_is_reported_and_the_build_checks_still_hold():
    """The name is said, never altered (datasheet.py: a value is not changed to make a check pass); the land's copper
    check runs all the same, and the part is held."""
    for name in ("SOIC-8 (D) 3.9x4.9mm", "SOIC-8/D 3.9\u00d74.9mm", "SOIC 8 [D]"):
        res = _overlapping_land(name)
        assert res["spec"]["name"] == name
        assert any(r.startswith("schema: name") for r in res["needs_review"]), res["needs_review"]
        assert any(r.startswith("land pattern:") and "overlap" in r for r in res["needs_review"]), res["needs_review"]
        out, st, got = put(res, symbol=LIB_SYMBOL)
        assert out.startswith("held: ") and not got, (name, out)


def test_any_other_schema_error_still_runs_the_build_checks_when_the_read_builds():
    """A schema error the builder does not stop on (here a field the schema does not know): the librarian builds that
    spec without the schema, so the land's copper check must have run."""
    e = copy.deepcopy(TD.INCH)
    e["package"]["land"] = {"pad_length": 1.5, "pad_width": 2.0, "pad_center": 2.7, "pad_center_y": None}
    real = D.schema_check

    def failing(spec, review):
        real(spec, review)
        review.append("schema: (root): a test's schema error")
        return False
    D.schema_check = failing
    try:
        res = D.process(e, "TESTPART", "https://example.com/t.pdf")
    finally:
        D.schema_check = real
    assert any(r.startswith("schema:") for r in res["needs_review"])
    assert any(r.startswith("land pattern:") and "overlap" in r for r in res["needs_review"]), res["needs_review"]
    assert put(res, symbol=LIB_SYMBOL)[0].startswith("held: ")


# ---------------------------------------------------------------- unresolved, however it is worded
WORDINGS = ("lead_span (digits blurred; E could be 0.236 or 0.286)", "E (lead span): digits blurred, could be 0.236 or 0.286",
            "lead span blurred", "package.lead_span", "Lead_span unreadable", "dimension E / lead_span not legible",
            "the lead span")


def test_an_unresolved_entry_holds_however_the_field_is_written():
    for u in WORDINGS:
        assert DR.named_field(u) == "lead_span", u
        e = copy.deepcopy(TD.INCH)
        e["package"]["unresolved"] = [u]
        res = D.process(e, "TESTPART", "https://example.com/t.pdf")
        hold = DR.hold_reasons(res, True)
        assert any(h.startswith("unresolved: ") and "guessed" in h for h in hold), (u, hold)
        out, st, got = put(res, symbol=LIB_SYMBOL)    # (a SOIC-8: the checked standard land may stand in for the read's
        assert out.startswith("held: ") or st["footprint"]["source"].startswith("standard:"), (u, out)   # own pads)
    # an exposed pad the read could not size, in the words a model uses: the QFN is not built without its pad
    for u in ("EP size not printed", "thermal pad dimensions (D2, E2) not legible", "exposed pad"):
        assert DR.named_field(u) == "exposed_pad", u
        e = copy.deepcopy(TD.GOOD)
        e["package"]["exposed_pad"], e["package"]["unresolved"], e["pins"] = None, [u], e["pins"][:16]
        res = D.process(e, "TESTPART", "https://example.com/t.pdf")
        assert any("would be built without it" in h for h in DR.hold_reasons(res, True)), (u, DR.hold_reasons(res, True))
    # what stays advice: the height, a limit of a dimension whose value was read, text that names nothing
    assert DR.named_field("height_max (not in the lead span table)") == "height_max"
    assert DR.named_field("no table found") == "" and DR.named_field("seated height") == "height"
    for u in ("height_max", "body_length min/max (not printed)", "seated height not given"):
        e = copy.deepcopy(TD.INCH)
        e["package"]["unresolved"] = [u]
        res = D.process(e, "TESTPART", "https://example.com/t.pdf")
        assert not DR.hold_reasons(res, True) and not DR.open_unresolved(res["spec"]), (u, DR.hold_reasons(res, True))


def test_an_unresolved_entry_that_is_not_advisory_blocks_a_release_with_no_person():
    """An entry that names no field ("could not read the drawing's table clearly") holds nothing by itself, but the
    read says it guessed: a second read that agrees does not release the read's own pads."""
    t = T.SMA
    for u, waits in (("could not read the dimension table clearly", True), ("height_max", False),
                     ("body_length min/max (not printed)", False)):
        res = T.read(dict(copy.deepcopy(t), unresolved=[u]))
        assert not DR.hold_reasons(res, True), (u, DR.hold_reasons(res, True))
        cc, job = checked(res, t)
        out, st, got = put(res, job, LIB_SYMBOL)
        if waits:
            assert cc["status"] == "self-checked" and cc["unresolved"] == [u], (u, cc["status"], cc.get("unresolved"))
            assert out == "verify" and "lists as unresolved" in CC.summary_line(cc), (out, CC.summary_line(cc))
        else:
            assert cc["status"] == "agreed" and not cc.get("unresolved") and out == "released", (u, cc["status"], out)
