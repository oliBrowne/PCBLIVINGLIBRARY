"""The AI hold reviewer (ecadlib.ai_review, [review] ai_reviewer): off by default; with "assistant" it writes a review
task for each built part held only for a person's look, and a "release" answer for the exact files it saw releases
that part, never one with any other hold, never an older build."""
import json
from pathlib import Path

from ecadlib import ai_review as AR, release_check as RC, rules as RU
from tests.fixtures_lite import lite

LOOK = "verify: footprint Foo chosen by footprint named after the MPN's series: compare with the datasheet land pattern"
PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _part(d, name="U1PART", msgs=(LOOK,), pdf=True):
    out = Path(d)
    (out / "models" / "SchLib").mkdir(parents=True, exist_ok=True)
    (out / "models" / "KiCad").mkdir(parents=True, exist_ok=True)
    (out / "models" / "PcbLib").mkdir(parents=True, exist_ok=True)
    (out / "models" / "PcbLib" / f"{name}.PcbLib").write_bytes(b"pcblib " + name.encode())
    (out / "models" / "SchLib" / f"{name}.SchLib").write_bytes(b"schlib " + name.encode())
    (out / "models" / "KiCad" / f"{name}.kicad_mod").write_text("(footprint x)", encoding="utf-8")
    if pdf:
        pdfs = out / "datasheet" / "assistant" / "pdfs"
        pdfs.mkdir(parents=True, exist_ok=True)
        (pdfs / f"{name}.pdf").write_bytes(PDF)
    return {"name": name, "status": "held", "hold_reasons": list(msgs),
            "parameters": [{"name": "Manufacturer Part Number", "value": name}],
            "symbol": {"ref": name, "file": f"models/SchLib/{name}.SchLib"},
            "footprint": {"ref": name, "file": f"models/PcbLib/{name}.PcbLib", "kicad_mod": f"models/KiCad/{name}.kicad_mod"},
            "release_check": {"result": "held", "failures": list(msgs), "warnings": []}}


def _quiet_files(monkeypatch):
    monkeypatch.setattr(AR, "_pins", lambda f, n: ["1:IN", "2:GND", "3:OUT"])
    monkeypatch.setattr(AR, "_pads", lambda f: ["1 smd at(-0.95,1.1) size 0.6x1.0"])


ON = {"review": {"ai_reviewer": "assistant"}}


def _answer(d, verdict="release", sym="match", fp="match"):
    (Path(d) / "answer.json").write_text(json.dumps({"verdict": verdict, "reason": "checked", "checked":
                                                     {"symbol": sym, "footprint": fp}}), encoding="utf-8")


def test_off_by_default():
    assert RU.DEFAULTS["review"]["ai_reviewer"] == "off"
    assert AR.mode({}) == "off" and AR.mode(None) == "off" and AR.mode({"review": {"ai_reviewer": "bogus"}}) == "off"
    assert AR.mode(ON) == "assistant"


@lite
def test_off_writes_nothing_and_releases_nothing(tmp_path, monkeypatch):
    _quiet_files(monkeypatch)
    it = _part(tmp_path)
    assert not AR.review([it], tmp_path, {}) and "ai_review" not in it
    assert not (tmp_path / "review").exists()


def test_only_a_part_held_for_looks_alone():
    it = {"status": "held", "release_check": {"result": "held", "failures": [LOOK]}}
    assert AR.looks_only(it) == [LOOK]
    for fails in ([LOOK, "files: no footprint model (.PcbLib)"], [LOOK, "footprint: pad 3 overlaps pad 4"],
                  ["fields: Description is empty"], [],
                  # "verify:" holds a pinout + land comparison cannot settle (review 2026-11-23)
                  ["verify: footprint X: copper not written to the .PcbLib (2 pads); draw it in Altium"],
                  ["verify: BOM lines C1, C2 name different footprints for this part"],
                  ["verify: datasheet from your folder: X.pdf is a family file; check it is this part's"],
                  [LOOK, "verify: value 4,700pF reads two ways"]):
        assert AR.looks_only({"status": "held", "release_check": {"result": "held", "failures": fails}}) is None, fails
    assert AR.looks_only({"status": "ready", "release_check": {"result": "pass", "failures": []}}) is None


@lite
def test_a_release_answer_releases_the_part_it_saw(tmp_path, monkeypatch):
    _quiet_files(monkeypatch)
    it = _part(tmp_path)
    assert not AR.review([it], tmp_path, ON)                         # first run: the task is written, the part waits
    [d] = [p for p in (tmp_path / "review" / "assistant").iterdir() if p.is_dir()]
    assert (d / "datasheet.pdf").read_bytes() == PDF and (d / "request.md").exists()
    case = json.loads((d / "case.json").read_text(encoding="utf-8"))
    assert case["symbol_pins"] == ["1:IN", "2:GND", "3:OUT"] and case["holds"] == [LOOK]
    assert "U1PART" in (tmp_path / "review" / "assistant" / "READ_ME.md").read_text(encoding="utf-8")
    _answer(d)
    assert AR.review([it], tmp_path, ON) == {"U1PART": [LOOK]}
    assert it["ai_review"]["failures"] == [LOOK] and it["ai_review"]["folder"].startswith("review/assistant/")


@lite
def test_a_hold_or_half_checked_answer_keeps_it_held(tmp_path, monkeypatch):
    _quiet_files(monkeypatch)
    for kw in ({"verdict": "hold"}, {"fp": "not found"}, {"sym": "mismatch"}):
        it = _part(tmp_path)
        AR.review([it], tmp_path, ON)
        [d] = [p for p in (tmp_path / "review" / "assistant").iterdir() if p.is_dir()]
        _answer(d, **kw)
        assert not AR.review([it], tmp_path, ON) and "ai_review" not in it, kw
    for bad in ("not json", "null", '"release"', "42", "[1, 2]"):     # never a crash of the whole run
        (d / "answer.json").write_text(bad, encoding="utf-8")
        assert not AR.review([it], tmp_path, ON), bad


@lite
def test_a_rebuilt_footprint_asks_again(tmp_path, monkeypatch):
    _quiet_files(monkeypatch)
    it = _part(tmp_path)
    AR.review([it], tmp_path, ON)
    [d] = [p for p in (tmp_path / "review" / "assistant").iterdir() if p.is_dir()]
    _answer(d)
    (tmp_path / "models" / "KiCad" / "U1PART.kicad_mod").write_text("(footprint y)", encoding="utf-8")
    assert not AR.review([it], tmp_path, ON) and "ai_review" not in it       # the answer was for the old pads
    assert len([p for p in (tmp_path / "review" / "assistant").iterdir() if p.is_dir()]) == 2


@lite
def test_no_pdf_or_no_built_files_no_task(tmp_path, monkeypatch):
    _quiet_files(monkeypatch)
    it = _part(tmp_path, pdf=False)
    assert not AR.review([it], tmp_path, ON) and not list((tmp_path / "review" / "assistant").glob("*-*"))
    it = _part(tmp_path, name="U2PART")
    (tmp_path / "models" / "KiCad" / "U2PART.kicad_mod").unlink()
    assert not AR.review([it], tmp_path, ON) and not list((tmp_path / "review" / "assistant").glob("U2PART-*"))


@lite
def test_release_check_counts_the_review_only_for_looks(tmp_path, monkeypatch):
    fails = {"A": [("verify", LOOK[len("verify: "):])],
             "B": [("verify", LOOK[len("verify: "):]), ("fields", "Description is empty")]}
    monkeypatch.setattr(RC, "check_item", lambda it, base, rules: (fails[it["name"]], [], {}))
    items = [{"name": n} for n in ("A", "B")]
    rows = RC.apply(items, tmp_path, rules={}, ai={"A": [LOOK], "B": [LOOK]})
    a, b = items
    assert a["status"] == "ready" and a["release_check"]["result"] == "ai_reviewed" and a["hold_reasons"] == []
    assert b["status"] == "held" and "fields: Description is empty" in b["hold_reasons"]
    assert {r["name"]: r["allowed"] for r in rows}["A"] == "ai reviewer"
    c = {"name": "A"}                                                # never covers another rule
    fails["A"] = [("fields", "Description is empty")]
    RC.apply([c], tmp_path, rules={}, ai={"A": ["fields: Description is empty"]})
    assert c["status"] == "held"


@lite
def test_an_old_ai_review_in_plan_json_releases_nothing(tmp_path, monkeypatch):
    """part approve, resolve, the app: a later check of plan.json never counts a marker an earlier run wrote."""
    monkeypatch.setattr(RC, "check_item", lambda it, base, rules: ([("verify", LOOK[len("verify: "):])], [], {}))
    it = {"name": "A", "ai_review": {"failures": [LOOK], "by": "ai reviewer (your assistant)"}}
    RC.apply([it], tmp_path, rules={"review": {"ai_reviewer": "assistant"}})
    assert it["status"] == "held"


@lite
def test_the_symbol_shown_is_the_parts_own_or_none(tmp_path, monkeypatch):
    from ecadlib import altium_lib as AL, symbol_layout as SL
    monkeypatch.setattr(AL, "read_schlib", lambda f: ({}, [{"name": "C"}, {"name": "C_Polarized"}]))
    monkeypatch.setattr(SL, "pins_from_schlib", lambda f, i: ("x", [{"num": "1", "name": f"sym{i}"}]))
    assert AR._pins("lib.SchLib", "c_polarized") == ["1:sym1"]
    try:
        AR._pins("lib.SchLib", "CP_Tantalum")
    except LookupError:
        pass
    else:
        raise AssertionError("a symbol not in the SchLib must not show the first symbol's pins")


@lite
def test_only_the_parts_own_pdf_from_a_folder(tmp_path, monkeypatch):
    it = _part(tmp_path, name="TPS7A20", pdf=False)
    pdfs = tmp_path / "datasheet" / "assistant" / "pdfs"
    pdfs.mkdir(parents=True, exist_ok=True)
    (pdfs / "TPS7A20-Q1.pdf").write_bytes(PDF)                       # another variant's datasheet
    assert AR.pdf_for(it, tmp_path) is None
    (pdfs / "TPS7A20.pdf").write_bytes(PDF)
    assert AR.pdf_for(it, tmp_path).name == "TPS7A20.pdf"


def test_rules_check_the_setting():
    try:
        RU.validate({"review": {"ai_reviewer": "api"}}, "x.toml")
    except RU.RulesError as e:
        assert "review.ai_reviewer" in str(e), e
    else:
        raise AssertionError("an unknown ai_reviewer value must be refused")
    RU.validate({"review": {"ai_reviewer": "assistant"}}, "x.toml")
