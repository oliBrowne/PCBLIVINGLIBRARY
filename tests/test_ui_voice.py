"""The reports people open, in the shared voice (docs/ui-voice.md): snapshots on fixed fixtures, Markdown and HTML say
the same, and no internal module names or absolute paths in anything a person reads. Run:
    python3 -m tests.run_all test_ui_voice
Snapshots live in tests/fixtures/voice/; after a deliberate wording change: UPDATE_VOICE=1 python3 -m tests.run_all test_ui_voice"""
import html, json, os, re, tempfile
from pathlib import Path

from ecadlib import messages as M, reports as RP

GOLD = Path(__file__).resolve().parent / "fixtures" / "voice"
DS = [{"name": "ComponentLink2URL", "value": "https://example.com/ds.pdf"}]


def _item(name, status, sym="kicad:Device:R", fp="kicad:Resistor_SMD:R_0603_1608Metric", hold=(), review=(), tried=(), **kw):
    it = {"name": name, "status": status, "source": {"symbol": sym, "footprint": fp}, "parameters": DS,
          "hold_reasons": list(hold), "librarian": {"tried": list(tried), "review": list(review), "notes": []},
          "release_check": {"result": "held" if status == "held" else "pass", "parity": "match", "height": 0.45}}
    it.update(kw)
    return it


ITEMS = [
    _item("RES-0603-10k", "ready"),
    _item("AO3401A", "ready", sym="standard:Q_PMOS_GSD", fp="standard:Package_TO_SOT_SMD:SOT-23",
          review=["symbol pinout Q_PMOS_GSD from the AO3401A family table (ecadlib.stdparts): check pins 1-3 against the datasheet"]),
    _item("CH340C", "held", sym=None, fp="standard:Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
          hold=["files: no symbol model (.SchLib)"], tried=["standard: footprint Package_SO:SOIC-16; symbol: no standard pinout for this part"]),
    _item("QFN-PART", "held", sym=None, fp=None,
          hold=["files: no symbol model (.SchLib)", "files: no footprint model (.PcbLib)"],
          review=["cross-check disagrees, nothing written: second read (model-x): pitch read 0.5 mm, second read 0.65 mm"],
          cross_check={"status": "disagreed", "summary": "second read (model-x): pitch read 0.5 mm, second read 0.65 mm"},
          tried=["datasheet reader: held, the cross-check disagrees (see datasheet/*.review.md)"]),
    _item("LM358DR", "held", fp="kicad:Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
          hold=["pins: symbol pins without a pad: 9", "height: 3D height 2.10 mm outside 1.2-1.85 mm for ^(SOIC|SOP|SO-)",
                "parity: pad 1 size/rotation: KiCad (1.95, 0.6, 0) / Altium (1.95, 0.65, 0) (w, h mm, rotation deg) (not covered by --allow)"],
          allow={"by": "Ann", "date": "2026-10-16", "failures": []}),
    _item("NE555DR", "awaiting_approval", approval={"missing": ["1 approval from role librarian"]}),
    _item("R-DUP", "duplicate", duplicate_of="RES-0603-10k"),
]


def _snap(name, text):
    f = GOLD / name
    if os.environ.get("UPDATE_VOICE") or not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    assert text == f.read_text(encoding="utf-8"), f"{name} changed; if on purpose: UPDATE_VOICE=1 and review the diff"


def _text_of_html(h):
    h = re.sub(r"<code>.*?</code>", "", re.sub(r"<style>.*?</style>", "", h, flags=re.S))
    return html.unescape(re.sub(r"<[^>]+>", " ", h))


def _no_leaks(text, where):
    """Commands and paths the reader is told to type (`code spans`) are allowed; everything else must be clean."""
    bad = M.leaks(re.sub(r"`[^`\n]+`", "", text))
    assert not bad, f"{where}: internal names or absolute paths in user-facing text: {bad}"


# ---------------------------------------------------------------- the library review
def test_review_snapshot_md_and_html_say_the_same():
    with tempfile.TemporaryDirectory() as d:
        doc = RP.write_review_files(ITEMS, d, "/srv/secret/uploads/board_bom.csv", ["### What was tried", "", "- x: y"])
        md, page = (Path(d) / "review.md").read_text(), (Path(d) / "review.html").read_text()
    _snap("review.md", md)
    lines = md.splitlines()
    assert lines[0] == "# Library review: board_bom.csv" and lines[2].startswith("**7 parts: 2 ready, 3 held, 1 waiting for sign-off, 1 duplicate.**"), lines[:3]
    held = md.split("## Held: fix these first (3)")[1].split("\n## ")[0]
    assert held.index("### CH340C") < held.index("### QFN-PART") < held.index("### LM358DR")
    q = held.split("### QFN-PART")[1].split("###")[0]
    assert q.index("The two datasheet reads disagree on pitch (0.5 mm vs 0.65 mm)") < q.index("There is no symbol"), q
    assert "Check the datasheet and pick one, or release anyway." in q
    assert "It is new since the part was released anyway." in held       # LM358DR: a reason the allow does not cover
    assert "Pad 1 size/rotation differs: KiCad (1.95, 0.6, 0), Altium (1.95, 0.65, 0)." in held
    assert "Waiting for sign-off (1 approval from role librarian)." in md and "This part is a duplicate of RES-0603-10k." in md
    assert "## Ready, but check before you release (1)" in md and "family table, not from this part's datasheet" in md
    # the HTML page carries the same sentences, chips and the web UI's colours in light and dark
    text = _text_of_html(page)
    for p in doc["sections"]:
        for part in p.get("parts") or []:
            assert part["name"] in text
            for ln in part["lines"]:
                assert ln["text"] in text and ln["fix"] in text, ln
    assert 'class="chip bad">held<' in page and "prefers-color-scheme:dark" in page and "--bad:#b3261e" in page
    assert '<meta name="viewport"' in page and "<script" not in page
    for t in (md, text):
        _no_leaks(t, "review")


def test_part_view_keeps_codes_for_machines():
    v = RP.part_view(ITEMS[3])
    assert [r["code"] for r in v["reasons"]] == ["crosscheck.disagree", "files.no_symbol", "files.no_footprint"], v["reasons"]
    assert v["word"] == "held" and v["meta"][0] == ("Symbol from", "none")
    assert RP.part_view(ITEMS[5])["word"] == "waiting for sign-off"
    assert M.source_word("kicad:Package_TO_SOT_SMD:SOT-23") == "KiCad library (SOT-23)"
    assert M.source_word("datasheet:https://x.example/a.pdf") == "datasheet reader" and M.source_word("") == "none"


def test_markdown_to_html_is_escaped():
    h = RP.md_to_html(["# T <b>", "", "| a | b |", "|---|---|", "| <script> | **x** |", "", "- `c` <https://x.example/a>"])
    assert "<script>" not in h and "&lt;script&gt;" in h and "<b>x</b>" in h and '<a href="https://x.example/a">' in h


# ---------------------------------------------------------------- release check, parity, PR body
def test_release_check_report_snapshot():
    from ecadlib import release_check as RC
    rows = [{"name": "A", "status": "ready", "result": "pass", "symbol_source": "kicad:Device:R",
             "footprint_source": "kicad:Resistor_SMD:R_0603_1608Metric", "height_mm": "0.45", "failures": "", "warnings": "", "allowed": ""},
            {"name": "B", "status": "held", "result": "held", "symbol_source": "", "footprint_source": "standard:Package_SO:SOIC-8",
             "height_mm": "", "warnings": "no .kicad_mod: only overlap, courtyard and pin 1 checked (on the PcbLib)", "allowed": "",
             "failures": "files: no symbol model (.SchLib); footprint: pin1: pads are numbered clockwise; KiCad and IPC number "
                         "counter-clockwise from pin 1 (seen from the top); height: no 3D height (no 3D body and no HEIGHT)"},
            {"name": "C", "status": "ready", "result": "allowed", "symbol_source": "kicad:Device:C", "footprint_source": "kicad:C:C_0402",
             "height_mm": "0.50", "failures": "fields: no datasheet link", "warnings": "", "allowed": "yes"}]
    with tempfile.TemporaryDirectory() as d:
        stem = RC.write_report(rows, Path(d) / "release_check", "/srv/secret/ws/librarian/board/plan.json")
        md = stem.with_suffix(".md").read_text()
    _snap("release_check.md", md)
    assert md.startswith("# Release check: board\n\n**3 parts: 2 ready, 1 held.** 1 of the ready ones was released anyway.")
    assert "The pads are numbered clockwise; KiCad and IPC number them counter-clockwise." in md   # "; " inside one reason
    _no_leaks(md, "release_check.md")


def test_parity_report_snapshot():
    from ecadlib import parity as P
    rep = {"plan": "/srv/secret/ws/librarian/board/plan.json", "mode": "export",
           "counts": {"match": 1, "mismatch": 1, "skipped": 1, "database_errors": 0}, "database": [],
           "parts": [{"part": "OK1", "result": "match", "checked": ["footprint (2 pads)", "pyaltiumlib", "pin -> pad"], "diffs": []},
                     {"part": "BAD1", "result": "mismatch", "checked": ["footprint (8 pads)"],
                      "diffs": [{"level": "error", "msg": "footprint pad count: KiCad 8 / Altium 9"},
                                {"level": "error", "msg": "pin 3 name: KiCad IN_A / Altium +IN_A"},
                                {"level": "warn", "msg": "approximation shape: KiCad pad 1: curve / Altium as written (kicad_import)"}]},
                     {"part": "H1", "result": "skipped", "checked": [], "why": "held (kicad-export skips it)", "diffs": []}]}
    with tempfile.TemporaryDirectory() as d:
        md = P.write_report(rep, Path(d) / "parity").with_suffix(".md").read_text()
    _snap("parity.md", md)
    assert md.startswith("# KiCad and Altium match: board\n\n**1 part matches, 1 differs.** 1 not compared.")
    assert "- The KiCad footprint has 8 pads and the Altium one 9." in md
    _no_leaks(md, "parity.md")


def test_pr_body_snapshot():
    from ecadlib import agent as A
    it = {"id": "q1", "key": "acme|X1", "mpn": "X1", "manufacturer": "ACME", "origin": "watch",
          "change": {"kind": "new", "source": {"type": "bom_file", "name": "b.csv"}, "line": {"datasheet": "https://example.com/x1.pdf"}},
          "build": {"part": "X1", "status": "held", "source": {"symbol": "kicad:Device:R", "footprint": "datasheet:https://example.com/x1.pdf"},
                    "hold_reasons": ["verify: datasheet reader output (footprint): compare with https://example.com/x1.pdf; see datasheet/*.review.md"],
                    "cross_check": {"status": "disagreed", "summary": "SOT-23 table: pin count read 5, table 3"}},
          "proof": {"verdict": "doubt", "reason": "pin 1 mark on the other side", "model": "m"}, "pictures": {}}
    md = A.pr_body("b1", [it], lambda i, p: p, "2026-10-16")
    _snap("PR.md", md)
    assert "**1 part to review** (1 held)" in md and "**Check before you approve:**" in md
    assert "The datasheet read and the standard package table disagree on pin count \\(5 vs 3\\)" in md     # markdown-escaped
    _no_leaks(md, "PR.md")


# ---------------------------------------------------------------- real runs: demo, test pack, datasheet review, dialogs
def test_generated_reports_have_no_internal_names_or_absolute_paths():
    """`ecadlib demo` and `ecadlib testpack` end to end (offline): every report a person opens is clean, and every
    hold reason they produce has a specific sentence."""
    from ecadlib import firstrun as FR, testpack as TP
    with tempfile.TemporaryDirectory() as d:
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            assert FR.demo_main(["-o", str(Path(d) / "demo")]) == 0
        TP.build(Path(d) / "pack", log=lambda *a, **k: None)
        files = [f for f in Path(d).rglob("*") if f.suffix in (".md", ".html") and f.name != "CHANGELOG.md"]
        names = {f.name for f in files}
        assert {"review.md", "review.html", "release_check.md", "CHECKLIST.md", "parity.md"} <= names, names
        for f in files:
            if f.parent.name == "datasheet" or "triage" in f.name or f.name.startswith("dedupe"):
                continue                                   # bom triage / dedupe tables are other helpers' reports
            t = f.read_text(encoding="utf-8")
            _no_leaks(_text_of_html(t) if f.suffix == ".html" else t, str(f.relative_to(d)))
            assert d not in t, f
        for plan in Path(d).rglob("plan.json"):
            for it in json.loads(plan.read_text()):
                for r in it.get("hold_reasons") or []:
                    assert M.explain(r)["code"] != "other", r


def test_datasheet_review_leads_with_the_outcome():
    from tests import test_crosscheck as TC
    from ecadlib import crosscheck as CC
    api = TC.API(lambda k, m, b: TC.first() if k == "read" else TC.check_answer(pitch=0.65))
    with tempfile.TemporaryDirectory() as d:
        try:
            TC.read(d, api)
        except CC.Held:
            pass
        md = (Path(d) / "out" / "datasheet" / "TESTPART.review.md").read_text()
    head = md.splitlines()
    assert head[0] == "# TESTPART: what the datasheet reader found" and head[2].startswith("**The footprint "), head[:6]
    assert head[4] == "Held: The two datasheet reads disagree on pitch (0.5 mm vs 0.65 mm); nothing was written to the library."
    assert head[5] == "**Next:** Check the datasheet and pick one, or release anyway."
    assert md.index("## Check these first") < md.index("## Dimensions to compare") < md.index("## Details")
    _no_leaks(md.split("## Details")[0], "TESTPART.review.md")


def test_kicad_plugin_shows_the_plain_reason():
    """A held part in the KiCad window: the plain reason and the next step, as the Altium dialog shows them."""
    from ecadlib import kicad_menu as KM
    from tests.test_tool_packages import _plugin_module
    mod, _ = _plugin_module()
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "b.kicad_pcb").write_text("(kicad_pcb)")
        res = KM.run_job(Path(d) / "ecadlib-work" / "jobs" / "j", "add-part", str(Path(d) / "b.kicad_pcb"), "RC0603FR-0710KL",
                         offline=True)
        got = mod.read_ini(str(Path(d) / "ecadlib-work" / "jobs" / "j" / "result.ini"), "result")
    assert res["status"] == "held" and got["big"] == "Held", res
    text, fix = M.held("files.no_symbol")
    assert text in got["message"] and got["fix"].startswith(fix) and "the Open review queue button" in got["fix"], got
    assert "Traceback" not in got["message"], got


def test_menu_and_mcp_errors_are_two_plain_lines():
    from ecadlib import mcp_server as MS
    r = MS.handle(None, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "workspace_info", "arguments": {}}})
    text = r["result"]["content"][0]["text"]
    assert r["result"]["isError"] and len(text.splitlines()) == 2 and "Traceback" not in text and "--debug" in text, text
