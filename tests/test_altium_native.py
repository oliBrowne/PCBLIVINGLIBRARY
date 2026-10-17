"""Altium builds the libraries itself (ecadlib.altium_native): recipes, the DelphiScript, the package hookup and the
compare step (offline; the script itself only runs inside Altium). Run:  python3 -m tests.test_altium_native"""
import json, re, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, altium_menu as AM, altium_native as AN, altium_pkg as PKG

HERE = Path(__file__).resolve().parent.parent


def _soic8():
    spec = json.loads((HERE / "ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json").read_text())
    return AL.footprint_from_spec(spec)[0]


def _ne555(fp="SOIC-8_3.9x4.9mm_P1.27mm"):
    names = ["GND", "TRIG", "OUT", "~{RESET}", "CONT", "THR", "DIS", "VCC"]
    pins = [{"num": str(i), "name": n, "type": "io", "side": "left" if i < 5 else "right"} for i, n in enumerate(names, 1)]
    return AL.ic_symbol("NE555DR", pins, "Timer", footprint=fp, layout=False)


def _lib(d):
    (d / "PcbLib").mkdir(parents=True)
    (d / "SchLib").mkdir()
    AL.write_pcblib(d / "PcbLib" / "SOIC8.PcbLib", [_soic8()])
    AL.write_schlib(d / "SchLib" / "NE555DR.SchLib", [_ne555()])


def test_every_library_write_leaves_a_recipe():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _lib(d)
        pr = (d / "PcbLib" / "SOIC8.PcbLib.ecadbuild").read_text(encoding="utf-8").splitlines()
        assert pr[0] == "ECADLIB-BUILD\t1" and pr[1] == "PCBLIB\tSOIC8.PcbLib" and pr[-1] == "END", pr[:2]
        pads = [ln.split("\t") for ln in pr if ln.startswith("PAD\t")]
        assert [p[1] for p in pads] == [str(i) for i in range(1, 9)]
        fp = _soic8()
        assert int(pads[0][2]) == AL.mm(fp["pads"][0]["x"]) and int(pads[0][4]) == AL.mm(fp["pads"][0]["w"])
        assert pads[0][7] == "roundrect" and pads[0][9] == str(AL.TOP)
        sr = (d / "SchLib" / "NE555DR.SchLib.ecadbuild").read_text(encoding="utf-8").splitlines()
        pins = [ln.split("\t") for ln in sr if ln.startswith("PIN\t")]
        assert len(pins) == 8 and pins[3][4] == "R\\E\\S\\E\\T\\", pins[3]       # overline as Altium writes it
        assert "FOOTPRINTLINK\tSOIC-8_3.9x4.9mm_P1.27mm" in sr and sr[1] == "SCHLIB\tNE555DR.SchLib"
        # rewriting the same library leaves the recipe alone (no churn in git)
        m = (d / "PcbLib" / "SOIC8.PcbLib.ecadbuild").stat().st_mtime_ns
        AL.write_pcblib(d / "PcbLib" / "SOIC8.PcbLib", [_soic8()])
        assert (d / "PcbLib" / "SOIC8.PcbLib.ecadbuild").stat().st_mtime_ns == m


def test_text_fields_cannot_break_the_record():
    fp = dict(_soic8(), description="tab\there\nnew line")
    line = AN.footprint_lines(fp)[0]
    assert line.count("\t") == 3 and "\n" not in line, line


def test_write_collects_recipes_with_folder_relative_paths():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _lib(d)
        rep = AN.write(d)
        assert sorted(rep["libraries"]) == ["PcbLib/SOIC8.PcbLib", "SchLib/NE555DR.SchLib"], rep
        text = (d / AN.BUILD).read_text(encoding="utf-8-sig")
        assert (d / AN.BUILD).read_bytes().startswith(b"\xef\xbb\xbf")
        assert "PCBLIB\tPcbLib\\SOIC8.PcbLib" in text and "SCHLIB\tSchLib\\NE555DR.SchLib" in text
        assert text.count("ECADLIB-BUILD") == 1 and text.count("\nEND") == 2
        assert (d / AN.PROJECT).read_bytes().decode() == "[Design]\r\nVersion=1.0\r\n\r\n[Document1]\r\nDocumentPath=ecadlib_build.pas\r\n"
        assert b"\r\n" in (d / AN.UNIT).read_bytes()
        out = d / "elsewhere"
        AN.write(d, out)
        assert "PCBLIB\t..\\PcbLib\\SOIC8.PcbLib" in (out / AN.BUILD).read_text(encoding="utf-8-sig")


def test_script_parses_and_calls_only_documented_api():
    text = AN.PAS
    a, b = text.index("Var\n    EbF"), text.index("Procedure EbSplit")
    routines = AM.pas_routines(AM.pas_tokens(text[:a] + text[b:]))        # the checker has no global Var section
    assert routines["BuildLibrariesInAltium"]["kind"] == "procedure" and not routines["BuildLibrariesInAltium"]["params"]
    assert routines["BuildLibraries"]["params"] == {"folder"}
    allowed = {k.lower() for k in AN.ALTIUM_API} | {w.lower() for ws in AN.PROPERTIES.values() for w in ws}
    known = {n.lower() for n in routines} | {"ebf", "eblines", "ebreport", "ebidx", "eberr"}
    bad = set()
    for name, r in routines.items():
        local = r["params"] | r["vars"]
        for kind, val, line in r["body"]:
            w = val.lower()
            if kind == "word" and not (w in AM.PAS_KEYWORDS or w in AM.PAS_TYPES or w in known or w in local or w in allowed):
                bad.add(f"{name}:{val}")
        if r["kind"] == "function":
            assert any(t[0] == "word" and t[1].lower() == "result" for t in r["body"]), name
    assert not bad, sorted(bad)
    for name, where in AN.ALTIUM_API.items():
        assert re.search(r"TR01\d\d|API|used in|TStringList|constructors|destructor", where), name
    # every recipe keyword the Python side writes is one the script handles, and the other way round
    written = {"FOOTPRINT", "PAD", "TRACK", "ARC", "FILL", "REGION", "TEXT", "BODY", "SYMBOL", "PIN", "RECT", "LINE",
               "POLYLINE", "POLYGON", "SARC", "CIRCLE", "LABEL", "PARAM", "DESIGNATOR", "COMMENT", "FOOTPRINTLINK"}
    handled = set(re.findall(r"Kind = '([A-Z]+)'", text)) - {"END", "PCBLIB", "SCHLIB"}
    assert handled == written, handled ^ written
    # unit globals carry the Eb prefix: the install script shares this project and has its own Report
    assert not re.search(r"^\s+(Report|Lines|F|Idx)\s*:", text, re.M)


def test_step_models_keep_their_name():
    """Altium embeds a STEP model under its file name, so the file beside the recipe carries the model's own name."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        fp = dict(_soic8(), bodies=[{"pts": [(-1, -1), (1, -1), (1, 1), (-1, 1)], "height": 1.5,
                                     "step": {"name": "SOIC-8.step", "data": b"ISO-10303-21;", "rot": (0.0, 0.0, 90.0)}}])
        AL.write_pcblib(d / "SOIC8.PcbLib", [fp])
        rel = AN.step_file(fp, 0, fp["bodies"][0])
        assert rel == "ecadlib_3d/SOIC-8_3.9x4.9mm_P1.27mm_0/SOIC-8.step", rel
        assert (d / rel).read_bytes() == b"ISO-10303-21;"
        body = [ln.split("\t") for ln in (d / "SOIC8.PcbLib.ecadbuild").read_text(encoding="utf-8").splitlines() if ln.startswith("BODY\t")]
        assert body[0][4] == rel and body[0][7] == "90000", body
        AN.write(d)
        assert r"ecadlib_3d\SOIC-8_3.9x4.9mm_P1.27mm_0\SOIC-8.step" in (d / AN.BUILD).read_text(encoding="utf-8-sig")


def test_compare_finds_a_moved_pad_and_a_renamed_pin():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _lib(d)
        assert AN.compare(d) == {"checked": 2, "problems": [], "unreadable": []}
        fp = _soic8()
        fp["pads"][2] = dict(fp["pads"][2], x=fp["pads"][2]["x"] + 0.1)        # "Altium" saved pad 3 0.1 mm off
        (d / "PcbLib" / "SOIC8.PcbLib").write_bytes(AL.pcblib_bytes([fp], "SOIC8.PcbLib"))
        sym = _ne555()
        for pr in sym["prims"]:
            if pr[0] == "pin" and pr[1]["num"] == "8":
                pr[1]["name"] = "VDD"
        (d / "SchLib" / "NE555DR.SchLib").write_bytes(AL.schlib_bytes([sym]))
        rep = AN.compare(d)
        assert rep["checked"] == 2 and len(rep["problems"]) == 2, rep
        assert any("pad 3: x" in p for p in rep["problems"]) and any("pin 8" in p and "VDD" in p for p in rep["problems"]), rep
        # a symbol saved with the wrong part count (Altium's factory default) or without its parameters shows up too
        rp = d / "SchLib" / "NE555DR.SchLib.ecadbuild"
        lines = rp.read_text(encoding="utf-8").splitlines()
        lines = [("\t".join(ln.split("\t")[:5] + ["2"] + ln.split("\t")[6:]) if ln.startswith("SYMBOL\t") else ln) for ln in lines]
        rp.write_text("\n".join(lines + ["PARAM\tManufacturer\tTI"]) + "\n", encoding="utf-8")
        probs = AN.compare(d)["problems"]
        assert any("PARTCOUNT 2 != 3" in p for p in probs) and any("parameter Manufacturer" in p for p in probs), probs
        (d / "PcbLib" / "SOIC8.PcbLib").write_bytes(b"not an ole file")
        assert AN.compare(d)["unreadable"], "a file Altium could not save shows up"


def test_package_lets_altium_rebuild_before_installing():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        _lib(d / "src")
        rep = PKG.build(d / "src", d / "pkg", "mylib", log=lambda *a: None)
        assert rep["native_build"] and not rep["problems"], rep
        pkg = d / "pkg"
        assert (pkg / "PcbLib" / "SOIC8.PcbLib.ecadbuild").is_file() and (pkg / AN.BUILD).is_file()
        prj = (pkg / "mylib_Install.PrjScr").read_bytes().decode()
        assert "DocumentPath=mylib_Install.pas" in prj and "[Document2]\r\nDocumentPath=ecadlib_build.pas" in prj
        pas = (pkg / "mylib_Install.pas").read_bytes().decode()
        assert "Procedure RebuildWithAltium;" in pas and "BuildLibraries(Folder);" in pas
        for proc in ("InstallDatabaseLibrary", "InstallFileLibraries", "BuildIntegratedLibrary"):
            body = pas[pas.index(f"Procedure {proc};"):]
            assert body.index("NativeBuild;") < body.index("End;\r\n"), proc
        assert pas.index("Procedure Finish(") < pas.index("Procedure RebuildWithAltium;")   # defined before use
        rep = PKG.build(d / "src", d / "plain", "mylib", log=lambda *a: None, native=False)
        assert not rep["native_build"] and "ecadlib_build.pas" not in (d / "plain" / "mylib_Install.PrjScr").read_text()
        assert "BuildLibraries(" not in (d / "plain" / "mylib_Install.pas").read_text()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
