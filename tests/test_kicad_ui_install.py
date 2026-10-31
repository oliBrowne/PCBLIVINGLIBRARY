"""The web page's KiCad library (step 3): "Export KiCad library" and "Put it in KiCad" (Workspace.export_kicad,
POST /api/export/kicad), offline."""
import contextlib, io, json, re
from pathlib import Path

from tests.fixtures_lite import lite

PARTS = """Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,Description,Datasheet
"R1, R2",2,10k,Resistor_SMD:R_0603_1608Metric,Yageo,RC0603FR-0710KL,RES SMD 10K OHM 1% 1/10W 0603,https://example.com/rc.pdf
C1,1,1uF,Capacitor_SMD:C_0603_1608Metric,Murata,GRM188R71C105KA12D,CAP CER 1UF 16V X7R 0603,https://example.com/c.pdf
"""


def _tables(base, series="9.0"):
    c = Path(base) / series
    c.mkdir(parents=True)
    for kind, table in (("sym", "sym_lib_table"), ("fp", "fp_lib_table")):
        (c / f"{kind}-lib-table").write_text(f'({table}\n  (version 7)\n  (lib (name "Own")(type "KiCad")(uri "/x/Own")'
                                              f'(options "")(descr ""))\n)\n')
    return c


@lite
def test_export_then_put_it_in_kicad(tmp_path, monkeypatch):
    from ecadlib.app import Workspace
    c = _tables(tmp_path / "kicad-config")
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "kicad-config"))
    ws = Workspace(str(tmp_path / "ws"), [], providers=["digikey"], offline=True)
    bom = ws.save_upload("board.csv", PARTS.encode())
    try:
        ws.export_kicad(bom)
        raise AssertionError("exported before the librarian ran")
    except ValueError as e:
        assert "Fill the library first" in str(e)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        ws.librarian_run(bom, ai=False)
    r = ws.export_kicad(bom)
    assert r["written"] >= 2 and "install" not in r and not r["errors"], r
    lib = r["library"]
    assert re.fullmatch(r"ecadlib_[a-z0-9_]+", lib), lib
    folder = ws.root / r["folder"]
    assert (folder / f"{lib}.kicad_sym").is_file() and not (c / "kicad_common.json").exists()
    r = ws.export_kicad(bom, install=True)
    var, nick = r["install"]["var"], r["install"]["nick"]
    assert r["install"]["versions"] == {"9.0": "installed"} and nick == lib and var.startswith("ECADLIB_DIR_"), r
    assert f'(uri "${{{var}}}/{lib}.pretty")' in (c / "fp-lib-table").read_text()
    assert json.loads((c / "kicad_common.json").read_text())["environment"]["vars"][var] == folder.resolve().as_posix()
    models = lambda: [m for f in (folder / f"{lib}.pretty").glob("*.kicad_mod")
                      for m in re.findall(r'\(model "([^"]+)"', f.read_text())]
    assert models() and all(m.startswith(f"${{{var}}}/") for m in models()), models()
    r = ws.export_kicad(bom)                                     # exported again: the paths stay on the variable
    assert all(m.startswith(f"${{{var}}}/") for m in models()), models()
    assert (c / "fp-lib-table").read_text().count(f'(name "{lib}")') == 1


@lite
def test_put_it_in_kicad_without_kicad_says_so(tmp_path, monkeypatch):
    from ecadlib.app import Workspace
    monkeypatch.setenv("KICAD_CONFIG_HOME", str(tmp_path / "none"))
    ws = Workspace(str(tmp_path / "ws"), [], providers=["digikey"], offline=True)
    bom = ws.save_upload("board.csv", PARTS.encode())
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        ws.librarian_run(bom, ai=False)
    r = ws.export_kicad(bom, install=True)
    assert r["install"]["versions"] == {} and r["install"]["warnings"], r
