"""Finding an outside program (git, svn, gh, pdftoppm, tasklist, powershell) so that the one started is never a file
in the current folder.

ecadlib is run in project folders, and a project or BOM folder can come from someone else. On Windows a program
started by its bare name is looked for in the current folder before PATH (CreateProcess, and shutil.which too), so a
`git.exe` or `git.bat` that came with a project would be run in place of the real one (review 2026-11-23, 2.6: the
KiCad window had the same hole for `ecadlib.bat`; ecadlib/assets/kicad_action_plugin.py has its own copy of find(),
because it runs in KiCad's Python without this package).

find(name) looks through PATH only: entries that are empty, relative, the current folder or a folder in `avoid` are
skipped, and the answer is an absolute path. resolved(cmd) puts that path in a command's first place."""
import os


def _same_dir(a, b):
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def find(name, environ=None, avoid=(), nt=None, cwd=None):
    """The program `name` on PATH as an absolute path, or None. nt / cwd: stand-ins for the tests."""
    environ = os.environ if environ is None else environ
    nt = os.name == "nt" if nt is None else nt
    name = str(name or "")
    if not name or os.path.basename(name) != name or name in (".", ".."):
        return None
    try:
        skip = [cwd or os.getcwd()]
    except OSError:
        skip = []
    skip += [str(d) for d in avoid if d]
    exts = [""]
    if nt:
        pathext = [e for e in (environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(";") if e.startswith(".")]
        exts = [""] if any(name.lower().endswith(e.lower()) for e in pathext) else pathext
    for d in (environ.get("PATH") or "").split(";" if nt else os.pathsep):
        d = d.strip('"') if nt else d
        if not d or not os.path.isabs(d) or any(_same_dir(d, s) for s in skip):
            continue
        for e in exts:
            p = os.path.join(d, name + e)
            if os.path.isfile(p) and (nt or os.access(p, os.X_OK)):
                return p
    return None


def resolved(cmd, **kw):
    """cmd (an argument list) with its program as find() gives it. A program given with a folder is left as it is.
    Raises FileNotFoundError (an OSError, which callers of subprocess already handle) when PATH does not have it."""
    cmd = [str(c) for c in cmd]
    if not cmd or os.path.dirname(cmd[0]):
        return cmd
    exe = find(cmd[0], **kw)
    if not exe:
        raise FileNotFoundError(f"{cmd[0]} was not found on PATH (a copy in the current folder is never used)")
    return [exe] + cmd[1:]
