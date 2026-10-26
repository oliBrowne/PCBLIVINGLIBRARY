"""Fix 2026-10-26 (altwriters): Altium project-variant BOM path (R-AW-1..4)."""
import contextlib, io, pathlib, struct
from pathlib import Path

from tests.fixtures_lite import lite


def _blk(s):
    b = s.encode("cp1252") + b"\0"
    return struct.pack("<I", len(b)) + b


def _schdoc(path, params):
    from ecadlib import cfb
    recs = ["|HEADER=Protel for Windows - Schematic Capture Binary File Version 5.0|WEIGHT=4",
            "|RECORD=1|LIBREFERENCE=OPAMP|DESIGNITEMID=OPAMP|UNIQUEID=ABCDEFGH|CURRENTPARTID=1|PARTCOUNT=2",
            "|RECORD=34|OWNERINDEX=0|TEXT=U1|NAME=Designator"]
    recs += [f"|RECORD=41|OWNERINDEX=0|TEXT={v}|NAME={k}" for k, v in params.items()]
    Path(path).write_bytes(cfb.to_bytes({"FileHeader": b"".join(_blk(r) for r in recs)}))


BASE = {"Manufacturer Part Number": "LM358DR", "Manufacturer": "TI", "LCSC": "C7950", "Supplier 1": "LCSC",
        "Supplier Part Number 1": "C7950", "Datasheet": "https://ti.com/lm358.pdf", "Lifecycle Status": "Active",
        "Comment": "LM358DR"}


def _project(d, variations="", params=(), sch=None):
    _schdoc(d / "Sheet1.SchDoc", sch or BASE)
    pv = "".join(f"ParamVariation{i}=ParameterName={k}|VariantValue={v}\r\nParamDesignator{i}=U1\r\n"
                 for i, (k, v) in enumerate(params, 1))
    vs = [v for v in [variations] if v]
    (d / "B.PrjPcb").write_text("[Design]\r\n[Document1]\r\nDocumentPath=Sheet1.SchDoc\r\n[ProjectVariant1]\r\nDescription=V2\r\n"
                                f"VariationCount={len(vs)}\r\n" + "".join(f"Variation{i}={v}\r\n" for i, v in enumerate(vs, 1)) +
                                f"ParamVariationCount={len(params)}\r\n" + pv, encoding="utf-8")
    return d / "B.PrjPcb"


def _lines(prj, variant, rules=None):
    from ecadlib import altium_project as AP
    with contextlib.redirect_stderr(io.StringIO()):
        return AP.bom_lines(prj, variant, rules)[0]


@lite
def test_where_used_writes_utf8_on_a_narrow_locale(tmp_path, monkeypatch):
    """R-AW-4: the .md / .json were written in the locale encoding: 'RES 10kΩ' crashed on cp1252 after the CSVs."""
    from ecadlib import altium_project as AP
    real = pathlib.Path.write_text

    def narrow(self, data, encoding=None, errors=None, newline=None):
        return real(self, data, encoding=encoding or "ascii", errors=errors, newline=newline)
    monkeypatch.setattr(pathlib.Path, "write_text", narrow)
    res = {"projects": [{"name": "Board Ω", "components": 1, "variants": [], "path": "C:/p/Board.PrjPcb"}], "parts": {},
           "library": False, "warnings": ["µ warning"],
           "flags": [{"flag": "missing_from_library", "part": "RES 10kΩ", "mpn": "", "projects": "Board", "detail": "x"}]}
    out = AP.write_where_used(res, tmp_path / "wu")
    assert "RES 10kΩ" in out["md"].read_text(encoding="utf-8")
    assert out["json"].exists()
