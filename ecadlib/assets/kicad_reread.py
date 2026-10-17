"""Footprints of another tool's library as KiCad's own importer reads them, written back as .kicad_mod text.

Run by a Python that can `import pcbnew` (KiCad's own, often /usr/bin/python3); no ecadlib imports, so any such
Python can run it from the installed package:

    python3 ecadlib/kicad_reread.py LIBRARY OUT_DIR      LIBRARY: Eagle / Fusion .lbr, EasyEDA Pro .elibz / .epro
                                                         -> one line of JSON {"kicad": version, "footprints": {name: file}}

eagle_export and easyeda_pro_export use it as an independent second reader: KiCad's Eagle importer (KiCad 7+) and
EasyEDA Pro importer (KiCad 8+) read the written library, and ecadlib.parity compares what KiCad got with the source."""
import json, sys, tempfile
from pathlib import Path

import pcbnew


def _mgr():
    return getattr(pcbnew, "PCB_IO_MGR", None) or pcbnew.IO_MGR


def plugin_for(path):
    m = _mgr()
    kind = {".lbr": "EAGLE", ".elibz": "EASYEDAPRO", ".epro": "EASYEDAPRO"}.get(Path(path).suffix.lower())
    if not kind or not hasattr(m, kind):
        return None
    return m.PluginFind(getattr(m, kind))


def saved_file(pretty, before, fp=None):
    """The .kicad_mod FootprintSave just wrote: the new file in the folder, else the one named after the footprint
    (KiCad names it <footprint>.kicad_mod; a second footprint of the same name overwrites the first). Not the newest
    mtime: several saves in one tick of a coarse clock (FAT, HFS+, 1-2 s) would all point at one file."""
    new = sorted(set(pretty.glob("*.kicad_mod")) - set(before))
    if new:
        return new[0]
    try:
        f = pretty / f"{fp.GetFPID().GetLibItemName()}.kicad_mod"
        if f.is_file():
            return f
    except Exception:
        pass
    files = sorted(pretty.glob("*.kicad_mod"), key=lambda f: f.stat().st_mtime)
    return files[-1] if files else None


def main(argv):
    lib, out = Path(argv[0]), Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    p = plugin_for(lib)
    if p is None:
        print(json.dumps({"kicad": pcbnew.Version(), "error": f"this KiCad cannot read {lib.suffix} libraries"}))
        return 0
    m = _mgr()
    k = m.PluginFind(m.KICAD_SEXP)
    pretty = Path(tempfile.mkdtemp(dir=out)) / "reread.pretty"
    try:
        k.FootprintLibCreate(str(pretty))
    except AttributeError:                    # KiCad 8.0's Python shim calls a CreateLibrary its plugin has not got;
        pretty.mkdir()                        # a .kicad_mod library is only a folder
    got, errors = {}, {}
    for name in p.FootprintEnumerate(str(lib)):
        try:
            fp = p.FootprintLoad(str(lib), name)
            if fp is None:
                errors[name] = "not loaded"
                continue
            before = set(pretty.glob("*.kicad_mod"))
            k.FootprintSave(str(pretty), fp)
            f = saved_file(pretty, before, fp)
            got[name] = str(f) if f else None
        except Exception as e:                # one bad footprint must not stop the others
            errors[name] = f"{type(e).__name__}: {e}"
    print(json.dumps({"kicad": pcbnew.Version(), "footprints": got, "errors": errors}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
