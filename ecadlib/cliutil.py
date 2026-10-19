"""Quality-of-life plumbing for the `ecadlib` command line (used by ecadlib/cli.py):

- environment defaults: ECADLIB_LIB (one or more KiCad library folders, separated by the OS path separator, ':' on
  Linux/macOS, ';' on Windows) and ECADLIB_WORKSPACE (workspace folder) fill in --lib / --workspace for the commands
  that take them when the flag is not given. A flag on the command line always wins. ECADLIB_CONFIG (the rules file)
  is handled by ecadlib.rules.find, so every command that searches for ecadlib.toml uses it.
- friendly errors: an expected error (missing file, bad JSON, bad table, missing package, no permission) becomes one
  line with a hint instead of a traceback; --debug or ECADLIB_DEBUG=1 shows the traceback."""
import json, os

# env var -> (flag, {command: None (any) | (subcommands the flag belongs to, added at the end of argv)}).
# Only commands where the flag means the same thing: --workspace is a folder here (a365 / upkeep / lifecycle /
# upstream use --workspace for an Altium 365 URL, so they are not listed).
ENV_FLAGS = {
    "ECADLIB_LIB": ("--lib", {"triage": None, "librarian": None, "resolve": None, "kicad-export": None, "mcp": None,
                              "ui": None, "doctor": None}),
    "ECADLIB_WORKSPACE": ("--workspace", {"mcp": None, "ui": None, "visual-check": None, "doctor": None, "status": None,
                                          "review": None}),
}
DEBUG_ENV = "ECADLIB_DEBUG"


def _has_flag(argv, flag):
    for a in argv:
        if a == "--":
            return False
        if a == flag or a.startswith(flag + "="):
            return True
    return False


def _subcommand(argv):
    """The first positional word: the subcommand. Options before it (agent's --workspace / --rules) take a value."""
    it = iter(argv)
    for a in it:
        if a == "--":
            return None
        if a.startswith("-"):
            if "=" not in a:
                next(it, None)
            continue
        return a
    return None


def env_defaults(cmd, argv, environ=None):
    """argv (the command's own arguments) with --lib / --workspace added from the environment where the command takes
    them and the user did not give them. Returns (new argv, [notes]) - notes name what was added (for --debug)."""
    environ = os.environ if environ is None else environ
    argv, notes = list(argv), []
    if any(a in ("-h", "--help") for a in argv):
        return argv, notes
    for var, (flag, cmds) in ENV_FLAGS.items():
        val = (environ.get(var) or "").strip()
        if not val or cmd not in cmds or _has_flag(argv, flag):
            continue
        values = [v for v in val.split(os.pathsep) if v.strip()] if flag == "--lib" else [val]
        extra = [x for v in values for x in (flag, v.strip())]
        subs = cmds[cmd]
        if subs is None:
            argv = extra + argv                       # top-level option: before any subcommand
        elif _subcommand(argv) in subs:
            argv = argv + extra                       # a subcommand's option: after it
        else:
            continue
        notes.append(f"{flag} from ${var}")
    return argv, notes


def debug_wanted(argv, environ=None):
    """(debug?, argv without --debug). --debug may go anywhere before a bare '--'."""
    environ = os.environ if environ is None else environ
    out, debug, rest = [], False, False
    for a in argv:
        if a == "--":
            rest = True
        if a == "--debug" and not rest:
            debug = True
            continue
        out.append(a)
    return debug or (environ.get(DEBUG_ENV, "").strip().lower() not in ("", "0", "false", "no", "off")), out


def friendly(e):
    """(one-line message, hint, exit code) for an exception, or None when it is not one we expected (a bug)."""
    name = getattr(e, "filename", None)
    if getattr(type(e), "plain_error", False):    # change control / ledger refusals: their own words and hint
        return (str(e), getattr(e, "hint", "") or "see the docs", 2)
    if isinstance(e, FileNotFoundError):
        return (f"file not found: {name or e}", f"check the path (relative paths start at {os.getcwd()})", 2)
    if isinstance(e, PermissionError):
        return (f"permission denied: {name or e}", "check the file's owner and permissions, or pick another output folder", 2)
    if isinstance(e, IsADirectoryError):
        return (f"expected a file but got a folder: {name or e}", "name the file inside that folder", 2)
    if isinstance(e, NotADirectoryError):
        return (f"expected a folder but got a file: {name or e}", "check the path", 2)
    if isinstance(e, json.JSONDecodeError):
        return (f"not valid JSON: {e.msg} at line {e.lineno} column {e.colno}",
                "is this the right file? plan.json is written by `ecadlib librarian`", 2)
    if isinstance(e, UnicodeDecodeError):
        return (f"file is not readable text ({e.encoding}: {e.reason})", "save the file as UTF-8 (CSV UTF-8 in Excel)", 2)
    if isinstance(e, ModuleNotFoundError) and e.name and not e.name.startswith("ecadlib"):
        pkg = {"PIL": "pillow", "yaml": "pyyaml"}.get(e.name.split(".")[0], e.name.split(".")[0])
        return (f"missing Python package {e.name!r}", f"pip install {pkg}  (or run `ecadlib doctor`)", 2)
    if type(e).__module__.startswith("pandas") and type(e).__name__ in ("EmptyDataError", "ParserError"):
        return (f"cannot read the table: {e}", "a BOM needs a header row (CSV, TSV or XLSX)", 2)
    if isinstance(e, OSError):
        return (f"{e.strerror or e}" + (f": {name}" if name else ""), "check the path and the disk", 2)
    return None


def report(cmd, e, out):
    """Print one line (and a hint) for e to `out`; returns the exit code."""
    f = friendly(e)
    if f is None:
        msg, hint, code = (f"internal error ({type(e).__name__}: {e})",
                           f"run again with --debug (or {DEBUG_ENV}=1) for the traceback, and please report it", 1)
    else:
        msg, hint, code = f[0], f[1] + "; --debug shows the traceback", f[2]
    print(f"ecadlib {cmd}: {msg}\n  hint: {hint}", file=out)
    return code
