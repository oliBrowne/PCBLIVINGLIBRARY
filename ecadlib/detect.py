"""Find Altium Designer and KiCad on this machine, for the setup page (and the doctor's kicad-cli check).

Nothing is run except `kicad-cli version` (to learn KiCad's version when the install folder does not say it), and
nothing is written. Every function takes the folders it looks in, so tests can point them at a fake tree.

Altium Designer (Windows only): the registry's Builds keys (HKLM\\SOFTWARE\\Altium\\Builds\\*: ProgramsInstallPath,
Version), then the default install folders <Program Files>\\Altium\\AD*\\X2.EXE.
KiCad: <Program Files>\\KiCad\\<version>\\bin (Windows), /Applications/KiCad (macOS), kicad / kicad-cli on PATH (all).
KiCad's per-user folders (where the setup page copies the action plugin), from KiCad's own paths code (paths.cpp,
GetDefaultUserDocumentsPath / GetUserScriptingPath): Windows and macOS Documents/KiCad/<major.minor>, Linux
$XDG_DATA_HOME/kicad/<major.minor> (~/.local/share/kicad/...); the plugins go in scripting/plugins below it."""
import os, re, shutil, subprocess, sys
from pathlib import Path


def _program_files(environ=None):
    environ = os.environ if environ is None else environ
    out = []
    for v in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        p = environ.get(v)
        if p and Path(p) not in out:
            out.append(Path(p))
    return out or [Path("C:/Program Files")]


def _version_key(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)


# ------------------------------------------------------------------ Altium Designer
def _altium_registry():
    try:
        import winreg
    except ImportError:
        return []
    out = []
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Altium\Builds", 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        vals = {}
                        for name in ("ProgramsInstallPath", "Version", "ProductName", "FullBuild"):
                            try:
                                vals[name] = str(winreg.QueryValueEx(k, name)[0])
                            except OSError:
                                pass
                except OSError:
                    continue
                if vals.get("ProgramsInstallPath"):
                    out.append(vals)
    return out


def altium(program_files=None, registry=None):
    """[{name, version, folder, exe}] newest first; [] on a machine without Altium (or not Windows)."""
    if program_files is None and os.name != "nt":
        return []
    found = {}
    for r in (_altium_registry() if registry is None else registry):
        folder = Path(r["ProgramsInstallPath"])
        exe = folder / "X2.EXE"
        if exe.is_file():
            found[str(folder).lower()] = {"name": r.get("ProductName") or "Altium Designer",
                                          "version": r.get("Version") or r.get("FullBuild") or "", "folder": str(folder),
                                          "exe": str(exe)}
    for pf in (_program_files() if program_files is None else program_files):
        for exe in sorted(Path(pf).glob("Altium/*/X2.EXE")) + sorted(Path(pf).glob("Altium/*/X2.exe")):
            key = str(exe.parent).lower()
            if key not in found:
                m = re.match(r"AD(\d+)", exe.parent.name, re.I)
                found[key] = {"name": "Altium Designer" + (f" {m.group(1)}" if m else ""),
                              "version": m.group(1) if m else "", "folder": str(exe.parent), "exe": str(exe)}
    return sorted(found.values(), key=lambda x: _version_key(x["version"]), reverse=True)


def altium_library_folders(public=None):
    """Altium's own example library folders (Users\\Public\\Documents\\Altium\\AD*\\Library), as suggestions."""
    if public is None:
        if os.name != "nt":
            return []
        public = Path(os.environ.get("PUBLIC") or "C:/Users/Public") / "Documents"
    return [str(p) for p in sorted(Path(public).glob("Altium/*/Library"), reverse=True) if p.is_dir()]


# ------------------------------------------------------------------ KiCad
def _cli_version(cli, run=subprocess.run):
    try:
        r = run([str(cli), "version"], capture_output=True, text=True, timeout=20)
        m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", r.stdout or "")
        return m.group(0) if m else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def kicad(program_files=None, applications=None, which=shutil.which, run=subprocess.run):
    """[{name, version, series, folder, cli}] newest first. series is the major.minor of KiCad's per-user folders."""
    found = {}

    def add(folder, cli, version):
        series = ".".join(re.findall(r"\d+", version)[:2]) if version else ""
        if series and "." not in series:
            series += ".0"
        found.setdefault(str(Path(cli).resolve()) if cli else str(folder), {
            "name": "KiCad" + (f" {series}" if series else ""), "version": version, "series": series,
            "folder": str(folder), "cli": str(cli) if cli else ""})

    if program_files is not None or os.name == "nt":
        for pf in (_program_files() if program_files is None else program_files):
            for d in sorted(Path(pf).glob("KiCad/*")):
                b = d / "bin"
                if (b / "kicad.exe").is_file() or (b / "kicad-cli.exe").is_file():
                    cli = b / "kicad-cli.exe"
                    add(d, cli if cli.is_file() else None, d.name if re.match(r"\d", d.name) else "")
    apps = applications if applications is not None else ([Path("/Applications")] if sys.platform == "darwin" else [])
    for a in apps:
        cli = Path(a) / "KiCad" / "KiCad.app" / "Contents" / "MacOS" / "kicad-cli"
        if cli.is_file():
            add(cli.parent.parent.parent, cli, _cli_version(cli, run))
    for name in ("kicad-cli",):
        p = which(name)
        if p and not any(Path(x["cli"] or "/").resolve() == Path(p).resolve() for x in found.values()):
            add(Path(p).parent, Path(p), _cli_version(p, run))
    return sorted(found.values(), key=lambda x: _version_key(x["version"] or x["series"]), reverse=True)


def kicad_cli(program_files=None, applications=None):
    """The newest kicad-cli found (also when it is not on PATH), or None."""
    for k in kicad(program_files, applications):
        if k["cli"]:
            return k["cli"]
    return None


def kicad_user_dir(series, home=None, environ=None, platform=None):
    """KiCad's per-user folder for a version series ("8.0"): where scripting/plugins and 3rdparty live."""
    environ = os.environ if environ is None else environ
    platform = platform or ("windows" if os.name == "nt" else "macos" if sys.platform == "darwin" else "linux")
    home = Path(home) if home else Path.home()
    if platform == "linux":
        base = Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "kicad"
    else:
        docs = home / "Documents"
        if platform == "windows" and home == Path.home():
            from .userconfig import documents_dir
            docs = documents_dir()
        base = docs / "KiCad"
    return base / series


def kicad_plugin_dir(series, **kw):
    return kicad_user_dir(series, **kw) / "scripting" / "plugins"


def summary():
    """Everything the setup page shows: {altium: [...], kicad: [...], altium_libraries: [...]}."""
    ks = kicad()
    for k in ks:
        k["plugin_dir"] = str(kicad_plugin_dir(k["series"])) if k["series"] else ""
    return {"altium": altium(), "kicad": ks, "altium_libraries": altium_library_folders()}
