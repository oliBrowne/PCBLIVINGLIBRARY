"""Install an exported KiCad library into KiCad itself, so every project sees it (no per-project copy).

    ecadlib kicad-install DIR [--name LIB] [--nick NICK] [--kicad 9.0] [--var ECADLIB_DIR_LIB] [--dry-run]
    ecadlib kicad-install DIR --uninstall

DIR is what `ecadlib kicad-export -o DIR --name LIB` wrote (LIB.kicad_sym, LIB.pretty, LIB.3dshapes). For each KiCad
version found on this computer (or the one --kicad names) this edits KiCad's own per-user files, the same ones
Preferences > Manage Symbol / Footprint Libraries and Configure Paths edit:

    sym-lib-table, fp-lib-table   one row LIB each (uri ${VAR}/LIB.kicad_sym, ${VAR}/LIB.pretty); every other row,
                                  their order and layout are kept; a copy of the old file is kept as *.ecadlib-bak
    kicad_common.json             the path variable VAR = DIR (environment > vars), other settings kept

The footprints' 3D models resolve through the same variable when they were exported with `--models-var VAR`
(`kicad-export --install` does both). Footprints that point at ${KIPRJMOD}/... find their models only inside the
export folder's own project; the result says so.

Where KiCad keeps those files: %APPDATA%\\kicad\\N.0 (Windows), ~/Library/Preferences/kicad/N.0 (macOS),
$XDG_CONFIG_HOME/kicad/N.0 or ~/.config/kicad/N.0 (Linux), the Flatpak's ~/.var/app/org.kicad.KiCad/config/kicad/N.0,
or $KICAD_CONFIG_HOME/N.0 when that is set. A version whose global tables do not exist yet (KiCad never started,
or its first-start "copy the default library table" step was skipped) is set up from that KiCad's own template
tables when they are found; otherwise it is skipped with a message (writing a table with only our row would hide
KiCad's own libraries). KiCad 7 cannot read the KiCad 8 symbol format: its row points at LIB.kicad7.kicad_sym, a
converted copy written next to the library.

KiCad reads these files when it starts: restart KiCad (all its windows) after installing. KiCad also writes
kicad_common.json when it closes, so close KiCad before installing, or the variable may be lost."""
import argparse, json, os, re, shutil, sys
from pathlib import Path

from . import fsutil, sexpr

TABLES = (("sym", "sym-lib-table", "sym_lib_table"), ("fp", "fp-lib-table", "fp_lib_table"))
MIN_MAJOR = 7


def var_name(lib):
    """The KiCad path variable for a library: ECADLIB_DIR_<LIB> in KiCad's variable alphabet (A-Z, 0-9, _). KiCad
    puts its path variables into the environment of what it starts (the ecadlib window runs ecadlib), so the name
    never matches one of ecadlib's own settings (ECADLIB_LIB, ECADLIB_HOME, ...)."""
    return "ECADLIB_DIR_" + (re.sub(r"[^A-Z0-9]+", "_", str(lib).upper()).strip("_") or "LIB")


def check_var(var):
    """ValueError unless `var` can name the library folder: a KiCad variable name that is not KiCad's own
    (KIPRJMOD, KICADn_...) nor one of ecadlib's settings (any other ECADLIB_...)."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", var or ""):
        raise ValueError(f"{var!r} is not a KiCad path variable name (letters, digits and _)")
    u = var.upper()
    if u == "KIPRJMOD" or re.match(r"KICAD\d*_", u) or (u.startswith("ECADLIB_") and not u.startswith("ECADLIB_DIR_")):
        raise ValueError(f"{var} is taken (KiCad's own, or one of ecadlib's settings): use e.g. {var_name('mylib')}")
    return var


def _platform(platform=None):
    return platform or ("windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux")


def config_bases(environ=None, home=None, platform=None):
    """KiCad's per-user configuration roots on this computer, most specific first (each holds N.0 folders)."""
    environ = os.environ if environ is None else environ
    home = Path(home) if home else Path.home()
    if environ.get("KICAD_CONFIG_HOME"):
        return [Path(environ["KICAD_CONFIG_HOME"])]
    p = _platform(platform)
    if p == "windows":
        bases = [Path(environ["APPDATA"]) / "kicad"] if environ.get("APPDATA") else [home / "AppData" / "Roaming" / "kicad"]
    elif p == "macos":
        bases = [home / "Library" / "Preferences" / "kicad"]
    else:
        bases = [Path(environ.get("XDG_CONFIG_HOME") or home / ".config") / "kicad",
                 home / ".var" / "app" / "org.kicad.KiCad" / "config" / "kicad"]
    return bases


def _major(series):
    m = re.match(r"(\d+)\.", str(series) + ".")
    return int(m.group(1)) if m else 0


def config_dirs(kicad=None, environ=None, home=None, platform=None):
    """[(series, folder)] of the KiCad versions to install into: every N.0 folder (N >= 7) that exists, newest
    first; with kicad="9.0" (or "9") only that one, created under the first root when it does not exist yet."""
    bases = config_bases(environ, home, platform)
    if kicad:
        series = str(kicad) if "." in str(kicad) else f"{kicad}.0"
        for b in bases:
            if (b / series).is_dir():
                return [(series, b / series)]
        return [(series, bases[0] / series)]
    found = {}
    for b in bases:
        for d in b.glob("*.*") if b.is_dir() else []:
            if d.is_dir() and re.fullmatch(r"\d+\.\d+", d.name) and _major(d.name) >= MIN_MAJOR:
                found.setdefault(d.name, d)
    return sorted(found.items(), key=lambda kv: tuple(map(int, kv[0].split("."))), reverse=True)


def template_dirs(series, environ=None, platform=None, program_files=None):
    """Folders that may hold KiCad `series`' template sym-lib-table / fp-lib-table (its installer's share folder)."""
    environ = os.environ if environ is None else environ
    major = _major(series)
    out = [Path(environ[k]) for k in (f"KICAD{major}_TEMPLATE_DIR",) if environ.get(k)]
    p = _platform(platform)
    if p == "windows":
        from .detect import _program_files
        for pf in (_program_files(environ) if program_files is None else program_files):
            out.append(Path(pf) / "KiCad" / series / "share" / "kicad" / "template")
    elif p == "macos":
        out.append(Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/template"))
    else:
        out += [Path("/usr/share/kicad/template"), Path("/usr/local/share/kicad/template"),
                Path("/var/lib/flatpak/app/org.kicad.KiCad/current/active/files/share/kicad/template")]
    return out


def _template(series, kind_file, dirs):
    """KiCad's own template table for `series`, or None. A template of another KiCad version (its rows name
    ${KICADn_SYMBOL_DIR} of another n) is not used: that KiCad would not find its libraries."""
    major = _major(series)
    for d in dirs:
        f = Path(d) / kind_file
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            other = {int(n) for n in re.findall(r"\$\{KICAD(\d+)_", text)}
            if not other or other == {major}:
                return text
    return None


def _rows(text):
    from .kicad_dblib import _row_spans
    out = []
    for s, e in _row_spans(text):
        node = sexpr.parse(text[s:e])
        if isinstance(node, list) and node and node[0] == "lib":
            val = lambda k: str(sexpr.first(node, k)[1]) if len(sexpr.first(node, k) or []) > 1 else ""
            out.append((s, e, val("name"), val("type"), val("uri")))
    return out


def remove_row(text, nick):
    """The table text without the row(s) named `nick` (each removed with its own line)."""
    for s, e, name, _, _ in reversed(_rows(text)):
        if name == nick:
            while s > 0 and text[s - 1] in " \t":
                s -= 1
            if text[e:e + 1] == "\n":
                e += 1
            text = text[:s] + text[e:]
    return text


def _common(folder):
    p = Path(folder) / "kicad_common.json"
    if not p.is_file():
        return p, {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ValueError(f"{p} is not valid JSON ({e}); fix or delete it in KiCad first") from e
    if not isinstance(data, dict):
        raise ValueError(f"{p} is not a KiCad settings file")
    return p, data


def _write(p, text, dry_run, backups):
    p = Path(p)
    if p.is_file() and p.read_text(encoding="utf-8") == text:
        return False
    if not dry_run:
        if p.is_file():
            bak = p.with_name(p.name + ".ecadlib-bak")
            shutil.copy2(p, bak)
            backups.append(str(bak))
        p.parent.mkdir(parents=True, exist_ok=True)
        fsutil.write_text_atomic(p, text)
    return True


def library(lib_dir, name=None):
    """(folder, LIB) of an exported KiCad library; LIB from --name or the folder's only .kicad_sym."""
    d = Path(lib_dir).resolve()
    if not d.is_dir():
        raise ValueError(f"{lib_dir}: no such folder (pass the -o folder of `ecadlib kicad-export`)")
    if name:
        lib = name
    else:
        syms = sorted(p.name[:-len(".kicad_sym")] for p in d.glob("*.kicad_sym") if not p.name.endswith(".kicad7.kicad_sym"))
        if len(syms) != 1:
            raise ValueError(f"{d}: {'no' if not syms else len(syms)} .kicad_sym libraries here; pass --name LIB")
        lib = syms[0]
    if not (d / f"{lib}.kicad_sym").is_file() and not (d / f"{lib}.pretty").is_dir():
        raise ValueError(f"{d}: neither {lib}.kicad_sym nor {lib}.pretty is here")
    return d, lib


def model_paths(folder, lib):
    """{footprint: [model paths]} of the library's footprints."""
    out = {}
    for f in sorted((Path(folder) / f"{lib}.pretty").glob("*.kicad_mod")):
        out[f.stem] = re.findall(r'\(model\s+"((?:[^"\\]|\\.)*)"', f.read_text(encoding="utf-8", errors="replace"))
    return out


def install(lib_dir, name=None, kicad=None, var=None, dry_run=False, uninstall=False, environ=None, home=None,
            platform=None, templates=None, log=print, nick=None):
    """Add (or with uninstall=True remove) the library's rows and path variable in each KiCad version's user files.
    nick: the name the rows give the library in KiCad (default LIB, the file name): the KiCad window's library is
    ecadlib.kicad_sym in every project, so each project's goes in as its own nickname (ecadlib_myboard).
    Returns {library, nick, folder, var, versions: {series: {status, changed, notes}}, warnings, backups}."""
    folder, lib = library(lib_dir, name) if not uninstall or Path(lib_dir).is_dir() else (Path(lib_dir), name)
    if not lib:
        raise ValueError("--uninstall of a folder that is gone needs --name LIB")
    nick = nick or lib
    if not re.fullmatch(r"[^\s:\"()]+", nick):
        raise ValueError(f"{nick!r} cannot name a KiCad library (no spaces, ':', quotes or brackets)")
    var = check_var(var or var_name(nick))
    rep = {"library": lib, "nick": nick, "folder": str(folder), "var": var, "versions": {}, "warnings": [], "backups": []}
    dirs = config_dirs(kicad, environ, home, platform)
    if not dirs:
        rep["warnings"].append("no KiCad settings folder found (KiCad 7 or newer, started at least once); "
                               "start KiCad once, or pass --kicad 9.0")
    root = folder.as_posix()
    for series, d in dirs:
        v = rep["versions"][series] = {"folder": str(d), "status": "", "changed": [], "notes": []}
        if _major(series) < MIN_MAJOR:
            v["status"] = f"skipped: KiCad {series} is older than KiCad {MIN_MAJOR}"
            continue
        try:
            _one(series, Path(d), lib, folder, root, var, v, rep, dry_run, uninstall, environ, platform, templates, nick)
        except ValueError as e:
            v["status"] = f"not changed: {e}"
    if not uninstall and folder.is_dir():
        paths = [m for ms in model_paths(folder, lib).values() for m in ms]
        if any(m.startswith("${KIPRJMOD}") for m in paths):
            rep["warnings"].append(f"the footprints' 3D models point at ${{KIPRJMOD}}/..., which only resolves inside the "
                                   f"export folder's own project: re-export with `ecadlib kicad-export ... --install` "
                                   f"(or --models-var {var}) so they use ${{{var}}}")
    for line in format_result(rep, dry_run):
        log(line)
    return rep


def _one(series, d, lib, folder, root, var, v, rep, dry_run, uninstall, environ, platform, templates, nick=None):
    nick = nick or lib
    major = _major(series)
    sym_file = f"{lib}.kicad_sym"
    if not uninstall and major < 8 and (folder / sym_file).is_file():
        from .kicad_pcm import symbol_kicad7
        k7 = folder / f"{lib}.kicad7.kicad_sym"
        text7 = symbol_kicad7((folder / sym_file).read_text(encoding="utf-8"))
        if not (k7.is_file() and k7.read_text(encoding="utf-8") == text7):
            if not dry_run:
                fsutil.write_text_atomic(k7, text7)
            v["changed"].append(str(k7))
        v["notes"].append(f"KiCad {series} reads the KiCad 7 copy {k7.name} (KiCad 7 cannot read the KiCad 8 format)")
        sym_file = k7.name
    uris = {"sym": f"${{{var}}}/{sym_file}", "fp": f"${{{var}}}/{lib}.pretty"}
    have = {"sym": (folder / sym_file).is_file() or uninstall, "fp": (folder / f"{lib}.pretty").is_dir() or uninstall}
    common_p, common = _common(d)
    texts = {}
    for kind, fname, table in TABLES:
        p = d / fname
        if p.is_file():
            text = p.read_text(encoding="utf-8").lstrip("﻿")
            t = sexpr.parse(text)
            if not t or t[0] != table:
                raise ValueError(f"{p} is not a {fname}")
        elif uninstall:
            continue
        else:
            text = _template(series, fname, template_dirs(series, environ, platform) if templates is None else templates)
            if text is None:
                raise ValueError(f"KiCad {series} has no {fname} yet: start KiCad {series} once (let it copy its "
                                 f"default library table), then run this again")
            v["notes"].append(f"{fname} set up from KiCad {series}'s template (KiCad's own libraries)")
        texts[kind] = (p, text, table)
    for kind, (p, text, table) in texts.items():
        if uninstall:
            new = remove_row(text, nick)
        elif not have[kind]:
            continue
        else:
            for _, _, name, typ, uri in _rows(text):
                if name == nick and typ.casefold() != "kicad":
                    raise ValueError(f"{p.name} already has a library {nick!r} of type {typ} ({uri}); pass another --name")
                if name == nick and uri != uris[kind]:
                    rep["warnings"].append(f"KiCad {series} {p.name}: the {nick!r} row pointed at {uri}; it now points "
                                           f"at {uris[kind]}")
            new = _set_row(text, table, nick, uris[kind], f"ecadlib library ({root})")
        if _write(p, new, dry_run, rep["backups"]):
            v["changed"].append(str(p))
    vars_ = common.setdefault("environment", {})
    if not isinstance(vars_, dict):
        raise ValueError(f"{common_p}: unexpected 'environment' setting; set {var} in Configure Paths by hand")
    vs = vars_.get("vars") if isinstance(vars_.get("vars"), dict) else {}
    if uninstall:
        changed = vs.pop(var, None) is not None
    else:
        changed = vs.get(var) != root
        vs[var] = root
    vars_["vars"] = vs
    if changed and (common_p.is_file() or not uninstall):
        _write(common_p, json.dumps(common, indent=2) + "\n", dry_run, rep["backups"])
        v["changed"].append(str(common_p))
    v["status"] = ("removed" if uninstall else "installed") if v["changed"] else ("not there" if uninstall else "already installed")


def _set_row(text, table, nick, uri, descr):
    """The table with exactly one KiCad row `nick` -> uri, in place of an existing one, else appended."""
    row = (f'(lib (name {sexpr.quote(nick)})(type "KiCad")(uri {sexpr.quote(uri)})(options "")'
           f'(descr {sexpr.quote(descr)}))')
    rows = [r for r in _rows(text) if r[2] == nick]
    if rows:
        s, e = rows[0][0], rows[0][1]
        old = text[s:e]
        same = (sexpr.first(sexpr.parse(old), "uri") or [None, None])[1] == uri
        text = remove_row(text[:s] + "\x00ECADLIB_ROW\x00" + text[e:], nick).replace("\x00ECADLIB_ROW\x00", old if same else row)
    else:
        body = text.rstrip()
        if not body.endswith(")"):
            raise ValueError(f"unexpected {table} layout; add this row by hand: {row}")
        text = body[:-1].rstrip() + "\n\t" + row + "\n)\n"
    t = sexpr.parse(text)
    names = [str(sexpr.first(l, "name")[1]) for l in sexpr.find(t, "lib") if sexpr.first(l, "name")]
    if names.count(nick) != 1:
        raise ValueError(f"could not set the {nick} row (unexpected layout); add it by hand: {row}")
    return text


def format_result(rep, dry_run=False):
    lines = []
    for series, v in rep["versions"].items():
        lines.append(f"  KiCad {series:<5} {v['status']}{'  (dry run: nothing written)' if dry_run and v['changed'] else ''}")
        for n in v["notes"]:
            lines.append(f"              {n}")
    for w in rep["warnings"]:
        lines.append(f"  warning: {w}")
    changed = [v for v in rep["versions"].values() if v["status"] in ("installed", "removed")]   # not a version
    if changed and not dry_run:                      # stopped part way ("not changed: ..." after the KiCad 7 copy)
        if all(v["status"] == "removed" for v in changed):
            lines.append(f"  {rep['var']} and the {rep.get('nick') or rep['library']} rows removed; restart KiCad (all its windows)")
        else:
            lines.append(f"  {rep['var']} = {rep['folder']}; restart KiCad (all its windows) to see {rep.get('nick') or rep['library']}")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib kicad-install", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("dir", help="the folder `ecadlib kicad-export -o` wrote")
    ap.add_argument("--name", help="the library name (default: the folder's only .kicad_sym)")
    ap.add_argument("--kicad", help="only this KiCad version (9.0, 10.0, ...); default every version found")
    ap.add_argument("--nick", help="the library's name in KiCad's tables (default: --name)")
    ap.add_argument("--var", help="the path variable (default ECADLIB_DIR_<NICK>)")
    ap.add_argument("--dry-run", action="store_true", help="say what would change, write nothing")
    ap.add_argument("--uninstall", action="store_true", help="remove the rows and the variable again")
    a = ap.parse_args(argv)
    try:
        rep = install(a.dir, a.name, a.kicad, a.var, a.dry_run, a.uninstall, nick=a.nick)
    except ValueError as e:
        print(f"ecadlib kicad-install: {e}", file=sys.stderr)
        return 2
    return 1 if rep["versions"] and all(v["status"].startswith("not changed") for v in rep["versions"].values()) \
        or not rep["versions"] else 0


if __name__ == "__main__":
    sys.exit(main())
