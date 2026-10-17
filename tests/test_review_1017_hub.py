"""Review 2026-10-17, library hub and setup page: regressions for the bugs fixed in that review."""
import json, tempfile
from pathlib import Path

from ecadlib import easy_setup as ES, hub as H, hub_copy as HC
from ecadlib.app import Workspace
from tests.test_hub import _demo, _fresh, _row

INDEX = Path(__file__).resolve().parents[1] / "ecadlib" / "ui" / "static" / "index.html"


def test_hub_part_drops_a_javascript_datasheet_link():
    """A vendor's datasheet field went straight into the hub panel's <a href>: javascript: ran on click."""
    t = _demo()
    plan_f = next((t / "ws" / "librarian").glob("*/plan.json"))
    items = json.loads(plan_f.read_text(encoding="utf-8"))
    it = next(i for i in items if i["name"] == "AO3401A")
    it["parameters"] = [p for p in it.get("parameters") or [] if not str(p.get("name", "")).startswith("ComponentLink")
                        and p.get("name") not in ("Datasheet", "datasheet")]
    it["parameters"].append({"name": "Datasheet", "value": "javascript:alert(document.cookie)"})
    plan_f.write_text(json.dumps(items), encoding="utf-8")
    ws = Workspace(t / "ws", offline=True)
    _fresh()
    p = ws.hub_part(_row(ws.hub(), "AO3401A")["key"])
    assert p["plan"] is not None and p["plan"]["datasheet"] is None
    assert H._web_url("https://example.com/ds.pdf") == "https://example.com/ds.pdf"
    assert H._web_url("http://example.com/ds.pdf") == "http://example.com/ds.pdf"
    assert H._web_url(" JavaScript:alert(1)") is None and H._web_url('https://x/"onmouseover=1') is None
    js = INDEX.read_text(encoding="utf-8")
    assert 'href="${esc(pl.datasheet)}"' in js and "test(pl.datasheet" in js     # the page checks it as well


def _prj(d, data):
    prj = Path(d) / "Board.PrjPcb"
    prj.write_bytes(data)
    lib = Path(d) / "hub" / "x.SchLib"
    lib.parent.mkdir(parents=True)
    lib.write_bytes(b"")
    return prj, lib


def test_add_to_prjpcb_keeps_an_ansi_project_file_intact():
    """An ANSI (cp1252) .PrjPcb was read as UTF-8 with errors replaced and written back: every non-ASCII character in
    the user's project (a path, a parameter) became U+FFFD."""
    with tempfile.TemporaryDirectory() as d:
        text = "[Design]\r\nVersion=1.0\r\n\r\n[Document1]\r\nDocumentPath=C:\\Users\\Müller\\Board.SchDoc\r\n\r\n" \
               "[Parameter1]\r\nName=Désigner\r\nValue=Jürgen µ\r\n"
        prj, lib = _prj(d, text.encode("cp1252"))
        HC.add_to_prjpcb(prj, [lib])
        out = prj.read_bytes().decode("cp1252")
        assert "Müller" in out and "Jürgen µ" in out and "\ufffd" not in out
        assert "[Document2]\r\nDocumentPath=hub\\x.SchLib\r\n" in out
        assert "\r\n" in out and "\n" not in out.replace("\r\n", "")       # CRLF stays CRLF everywhere


def test_add_to_prjpcb_reads_a_utf16_project_file():
    with tempfile.TemporaryDirectory() as d:
        text = "[Design]\r\nVersion=1.0\r\n\r\n[Document1]\r\nDocumentPath=Board.SchDoc\r\n"
        prj, lib = _prj(d, text.encode("utf-16"))
        HC.add_to_prjpcb(prj, [lib])
        out = prj.read_bytes().decode("utf-16")
        assert "DocumentPath=Board.SchDoc" in out and "[Document2]\r\nDocumentPath=hub\\x.SchLib" in out


def test_kicad_plugin_series_cannot_name_another_folder():
    """series from the setup page's request body became a folder name under KiCad's user folder: ../ walked out."""
    for bad in ("../../../tmp/x", "8.0/../../x", ["8.0"], 8):
        try:
            ES.install_kicad_plugin(bad)
            raise AssertionError(f"{bad!r} accepted")
        except ValueError as e:
            assert "series" in str(e)
