"""2026-10-26 hunt (fsafe): file safety and portability (file names from part names, encodings, rules loading)."""
import hashlib, json, re
from pathlib import Path

from tests.fixtures_lite import lite


# ---------------------------------------------------------------- partview: a part's datasheet sidecar, never another's
def _sidecar_file(d, part, extra=""):
    """The file name datasheet.write gives a part's sidecar (its `safe` + the punctuation-twin hash suffix)."""
    safe = re.sub(r"[^A-Za-z0-9_.+-]", "_", part)
    if extra:
        safe = f"{safe}-{hashlib.sha1(part.encode()).hexdigest()[:6]}"
    f = Path(d) / f"{safe}.extraction.json"
    f.write_text(json.dumps({"part": part, "cross_check": {"checks": []}}), encoding="utf-8")
    return f


@lite
def test_partview_sidecar_is_the_parts_own(tmp_path):
    from ecadlib import partview as PV
    d = tmp_path / "datasheet"
    d.mkdir()
    _sidecar_file(d, "A_B")                 # written first: A_B.extraction.json
    _sidecar_file(d, "A/B", extra=True)     # its punctuation twin: A_B-<hash>.extraction.json
    assert PV._sidecar(tmp_path, "A_B")["part"] == "A_B"
    got = PV._sidecar(tmp_path, "A/B")
    assert got is not None and got["part"] == "A/B", got      # was A_B's sidecar: another part's cross-check rows


@lite
def test_partview_sidecar_keeps_plus_and_runs(tmp_path):
    from ecadlib import partview as PV
    d = tmp_path / "datasheet"
    d.mkdir()
    for name in ("AB+/C", "X//Y", "LT1086CM-3.3#PBF"):
        _sidecar_file(d, name)
        got = PV._sidecar(tmp_path, name)
        assert got is not None and got["part"] == name, (name, got)


@lite
def test_partview_sidecar_without_part_field_still_reads(tmp_path):
    from ecadlib import partview as PV
    d = tmp_path / "datasheet"
    d.mkdir()
    (d / "LM358.extraction.json").write_text(json.dumps({"cross_check": {}}), encoding="utf-8")   # older sidecar
    assert PV._sidecar(tmp_path, "LM358") == {"cross_check": {}}


# ---------------------------------------------------------------- rules: a file saved in another encoding
@lite
def test_rules_utf16_file_loads(tmp_path):
    from ecadlib import rules as RU
    f = tmp_path / "ecadlib.toml"
    f.write_bytes("[naming]\ncase = \"upper\"\n".encode("utf-16"))       # PowerShell 5 `>` writes UTF-16 LE + BOM
    assert RU.load(str(f))["naming"]["case"] == "upper"


@lite
def test_rules_ansi_file_is_a_rules_error(tmp_path):
    from ecadlib import rules as RU
    f = tmp_path / "ecadlib.toml"
    f.write_bytes("[naming]\ncomponent = \"M\xfcller-{mpn}\"\n".encode("cp1252"))   # Notepad "ANSI"
    try:
        RU.load(str(f))
    except RU.RulesError as e:
        assert "ecadlib.toml" in str(e) and "UTF-8" in str(e), e
    else:
        raise AssertionError("an ANSI rules file loaded")


@lite
def test_partview_sidecar_found_by_mpn_when_the_name_differs(tmp_path):
    """The librarian reads a datasheet under the part's MPN (librarian.src_datasheet: rd.job(mpn or name)), so a part
    named by [naming] rules ('IC-LM358-SOIC8') or with a space in its MPN has its sidecar under the MPN."""
    from ecadlib import partview as PV
    d = tmp_path / "datasheet"
    d.mkdir()
    _sidecar_file(d, "LM358DR")
    _sidecar_file(d, "FC-135 32.768KA-AG0")
    assert PV._sidecar(tmp_path, "IC-LM358-SOIC8", "LM358DR")["part"] == "LM358DR"
    assert PV._sidecar(tmp_path, "FC-135_32.768KA-AG0", "FC-135 32.768KA-AG0")["part"] == "FC-135 32.768KA-AG0"
    assert PV._sidecar(tmp_path, "IC-LM358-SOIC8", "NE555") is None


@lite
def test_migrate_scan_reads_rules_file_as_rules_load_does(tmp_path):
    from ecadlib import migrate as MG
    (tmp_path / "ecadlib.toml").write_bytes("schema_version = 1\n[naming]\ncase = \"upper\"\n".encode("utf-16"))
    rows = [r for r in MG.scan(tmp_path) if r["kind"] == "rules"]
    assert [r["state"] for r in rows] == ["ok"], rows        # was "error": the UTF-8 read refused what load() reads


@lite
def test_migrate_scan_survives_an_unreadable_rules_file(tmp_path, monkeypatch):
    from ecadlib import migrate as MG, rules as RU
    (tmp_path / "ecadlib.toml").write_text("[naming]\n", encoding="utf-8")

    def locked(f):
        raise PermissionError(13, "The process cannot access the file", str(f))
    monkeypatch.setattr(RU, "read_file", locked)              # Windows: another program holds it open
    rows = [r for r in MG.scan(tmp_path) if r["kind"] == "rules"]
    assert [r["state"] for r in rows] == ["error"], rows
    MG.guard_tree(tmp_path)                                   # the web UI opening the workspace does not crash


# ---------------------------------------------------------------- generated file names on Windows / macOS
DEVICES = {"AUX", "CON", "NUL", "PRN", "COM1", "LPT9", "COM¹"}


def test_file_names_never_a_windows_device_name():
    """A part / footprint named like a Windows device (AUX, CON, NUL, COM1, LPT9; any case; with an extension too)
    gave AUX.SchLib / CON.kicad_mod: Windows opens the device instead of a file, so the library cannot be written
    or opened there."""
    from ecadlib import altium_lib as AL, kicad_export as KX
    for name in ("AUX", "aux", "CON", "NUL", "PRN", "COM1", "lpt9", "COM¹", "NUL.x", "CON ", "AUX."):
        for stem in (AL.file_stem(name), KX.kname(name), KX.libname(name)):
            assert stem.split(".")[0].rstrip(" ").upper() not in DEVICES, (name, stem)
    for name in ("AUXILIARY", "CONN-2", "COM10", "LM358", "NULL", "R1"):
        assert AL.file_stem(name) == name and KX.kname(name) == name, name
    assert AL.file_stem("R1.") == "R1"


def test_kicad_names_are_nfc():
    """macOS file systems treat NFC u-umlaut and NFD u + U+0308 as one file name: two footprints spelled so got two
    names (the case-insensitive clash check missed them) and one .kicad_mod overwrote the other there."""
    import unicodedata
    from ecadlib import kicad_export as KX
    nfc = "Bürklin_1x02"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd and KX.kname(nfc) == KX.kname(nfd) == nfc


# ---------------------------------------------------------------- a library file another program holds open
def test_friendly_names_the_target_not_the_temp_file():
    """fsutil.write_*_atomic replaces the target with os.replace; on Windows, with the .SchLib open in Altium, that
    fails with PermissionError whose filename is the temp file ('.LM358.SchLib.4242.1.tmp'): the one-line error named
    a file the person has never seen and said to check its permissions."""
    from ecadlib import cliutil
    dst = "/w/models/SchLib/LM358.SchLib"
    e = PermissionError(13, "Access is denied", "/w/models/SchLib/.LM358.SchLib.4242.1.tmp", None, dst)
    msg, hint, code = cliutil.friendly(e)
    assert dst in msg and ".tmp" not in msg, msg
    assert "open" in hint and code == 2, hint


@lite
def test_atomic_write_of_a_long_file_name(tmp_path):
    """The temp file is '.<name>.<pid>.<thread>.tmp': for a name the file system takes (up to 255 characters) the temp
    name was ~30 characters longer and the write failed with 'File name too long'."""
    from ecadlib import fsutil
    f = tmp_path / ("P" * 240 + ".SchLib")
    fsutil.write_text_atomic(f, "x")
    fsutil.write_bytes_atomic(f, b"y")
    assert f.read_bytes() == b"y" and [p.name for p in tmp_path.iterdir()] == [f.name]
    assert fsutil.atomic_tmp(fsutil._tmp_for(f))                # still recognised as ecadlib's temp file


# ---------------------------------------------------------------- caches a crash can cut short
@lite
def test_kicad_index_cut_short_is_built_again(tmp_path, monkeypatch):
    """A librarian run killed while writing the KiCad name index left half a JSON file: every later run (the file is
    'fresh' for 30 days) stopped with a JSONDecodeError traceback."""
    from ecadlib.librarian import KiCadLibrary
    f = tmp_path / "kicad_library_index.json"
    f.write_text('{"built": "2026-10-26", "symbols": {"Device": ["R"', encoding="utf-8")

    class Offline:                            # httpcache's offline client: no cached page -> not ok
        offline = True

        def get(self, url, timeout=None):
            return type("R", (), {"ok": False, "error": "offline: not in the cache", "status": 0})()
    k = KiCadLibrary(Offline(), f)
    assert k.symbols == {} and k.footprints == {}             # no crash; offline: nothing to build it from

    calls = []

    def crawl(self, repo, dsuf, fsuf):
        calls.append(repo)
        return {"R": ["Device"]} if repo == "kicad-symbols" else {"R_0603_1608Metric": ["Resistor_SMD"]}

    class Online:
        offline = False
    monkeypatch.setattr(KiCadLibrary, "_crawl", crawl)
    k = KiCadLibrary(Online(), f)
    assert calls == ["kicad-symbols", "kicad-footprints"] and k.symbols == {"R": ["Device"]}
    assert json.loads(f.read_text(encoding="utf-8"))["symbols"] == {"R": ["Device"]}   # rebuilt whole
