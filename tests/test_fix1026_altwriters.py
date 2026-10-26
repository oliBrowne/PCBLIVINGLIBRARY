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
def test_param_variation_rederives_every_field(tmp_path):
    """R-AW-2: a variant that sets MPN, Manufacturer, LCSC, supplier PN, datasheet and lifecycle: every one of them
    reaches the variant line (only the MPN / comment / value did; maker and LCSC stayed TI / C7950)."""
    prj = _project(tmp_path, params=[("Manufacturer Part Number", "MCP6002T-I/SN"), ("Manufacturer", "Microchip"),
                                     ("LCSC", "C7377"), ("Supplier Part Number 1", "C7377"),
                                     ("Datasheet", "https://microchip.com/mcp6002.pdf"), ("Lifecycle Status", "NRND")])
    ln = _lines(prj, "V2")[0]
    assert (ln["mpn"], ln["manufacturer"], ln["lcsc"]) == ("MCP6002T-I/SN", "Microchip", "C7377"), ln
    assert ln["datasheet"] == "https://microchip.com/mcp6002.pdf" and ln["altium_supplier_pn"] == "C7377", ln
    assert ln["altium_lifecycle"] == "NRND", ln
    # a variation of an unrelated parameter keeps every base field
    prj = _project(tmp_path, params=[("Tolerance", "1%")])
    ln = _lines(prj, "V2")[0]
    assert (ln["mpn"], ln["manufacturer"], ln["lcsc"]) == ("LM358DR", "TI", "C7950"), ln


@lite
def test_comment_variation_moves_a_comment_mpn(tmp_path):
    """R-AW-2: no MPN parameter, the Comment is the MPN (Content Vault parts); a variant that changes the Comment
    changes the MPN with it (it kept the base Comment's MPN)."""
    prj = _project(tmp_path, params=[("Comment", "MCP6002T-I/SN")], sch={"Comment": "LM358DR"})
    assert _lines(prj, None)[0]["mpn"] == "LM358DR"
    ln = _lines(prj, "V2")[0]
    assert ln["mpn"] == "MCP6002T-I/SN" and ln["value"] == "MCP6002T-I/SN", ln
    prj = _project(tmp_path, params=[("Comment", "10k")], sch={"Comment": "LM358DR"})
    assert not _lines(prj, "V2")[0].get("mpn")

@lite
def test_variant_on_house_named_mpn_parameter(tmp_path):
    """R-AW-3: rules [parameters] names map Manufacturer Part Number -> MfrPN; a variant that changes MfrPN must change
    the variant's MPN (apply_variant used the built-in names only)."""
    from ecadlib import rules as RU
    rules = RU.load({"parameters": {"names": {"Manufacturer Part Number": "MfrPN", "Manufacturer": "Maker"}}})
    sch = {"MfrPN": "LM358DR", "Maker": "TI"}
    prj = _project(tmp_path, params=[("MfrPN", "LM358BIDR"), ("Maker", "Texas Instruments")], sch=sch)
    assert _lines(prj, None, rules)[0]["mpn"] == "LM358DR"
    ln = _lines(prj, "V2", rules)[0]
    assert ln["mpn"] == "LM358BIDR" and ln["manufacturer"] == "Texas Instruments", ln


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
