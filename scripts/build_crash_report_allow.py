"""Writes api/_crash-report-allow.json: what the site's crash-report endpoint (api/crash-report.js) accepts. Each
ecadlib source file with the functions in it, the command names, and the error classes: Python's own, ecadlib's, and
those of the standard-library modules and libraries ecadlib's code imports (requests' ReadTimeout, json's
JSONDecodeError: read from the modules installed where this runs). The endpoint refuses any other file or function
name and files any other error name as "OtherError", so a made-up name can never reach a public issue (review
2026-11-23, 2.5: the error name used to be any word ending in Error). Entries are only ever added (older releases
still report the functions they had). tests/test_crash_report_endpoint.py fails when this file is behind.
Run:  python3 scripts/build_crash_report_allow.py"""
import ast, builtins, importlib, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "api" / "_crash-report-allow.json"
SPECIAL = ["<module>", "<lambda>", "<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>"]


def _names(tree):
    """Every function name in a module, plain (Python 3.11-3.13 tracebacks) and qualified (Class.method)."""
    out = set()

    def walk(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = f"{prefix}{ch.name}"
                if not isinstance(ch, ast.ClassDef):
                    out.update((ch.name, q))
                walk(ch, q + "." if isinstance(ch, ast.ClassDef) else q + ".<locals>.")
            else:
                walk(ch, prefix)
    walk(tree, "")
    return out


def _errors(tree):
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
            and any(isinstance(b, (ast.Name, ast.Attribute)) and (getattr(b, "id", None) or b.attr).endswith(
                ("Error", "Exception", "Warning", "Exit")) for b in n.bases)}


ERROR_NAME = re.compile(r"[A-Z][A-Za-z0-9]{1,40}")       # the shape api/crash-report.js takes for an error name


def _imported(tree):
    """The top-level modules a file imports from outside ecadlib."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
            out.add(n.module.split(".")[0])
    return out - {"ecadlib"}


def library_errors(modules):
    """Error class names of the modules ecadlib imports (those installed here; one that does not import is skipped):
    every exception class loaded once they are, that lives in one of them or in the standard library."""
    tops = set(modules)
    for m in sorted(tops):
        try:
            importlib.import_module(m)
        except BaseException:                        # not installed here (pcbnew, wx), or it stops on import
            continue
    ours = tops | set(getattr(sys, "stdlib_module_names", ()))
    out, todo, seen = set(), [BaseException], set()
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        try:
            todo.extend(c.__subclasses__())
        except TypeError:
            continue
        if str(getattr(c, "__module__", "")).split(".")[0] in ours and ERROR_NAME.fullmatch(c.__name__):
            out.add(c.__name__)
    return out


def current(libraries=False):
    """libraries: also the error classes of the modules ecadlib imports (the write; missing() does not need them)."""
    files, errors = {}, {n for n in dir(builtins) if isinstance(getattr(builtins, n), type)
                         and issubclass(getattr(builtins, n), BaseException)}
    imported = set()
    for p in sorted((ROOT / "ecadlib").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        files[rel] = _names(tree) | set(SPECIAL)
        errors |= _errors(tree)
        imported |= _imported(tree)
    sys.path.insert(0, str(ROOT))
    from ecadlib.cli import COMMANDS
    if libraries:
        errors |= library_errors(imported)
    return {"files": files, "commands": set(COMMANDS), "errors": errors}


def merged(old=None):
    cur = current(libraries=True)
    old = old or {}
    files = {k: set(v) for k, v in (old.get("files") or {}).items()}
    for k, v in cur["files"].items():
        files[k] = files.get(k, set()) | v
    return {"note": "built by scripts/build_crash_report_allow.py; entries are only added",
            "files": {k: sorted(files[k]) for k in sorted(files)},
            "commands": sorted(set(old.get("commands") or []) | cur["commands"]),
            "errors": sorted(set(old.get("errors") or []) | cur["errors"])}


def missing(table=None):
    """What the code has and the table lacks (Python's own error classes aside: they vary by Python version)."""
    t, cur = table if table is not None else load(), current()
    files = t.get("files") or {}
    out = [f"{k}: {sorted(set(v) - set(files.get(k, [])))}" for k, v in cur["files"].items() if set(v) - set(files.get(k, []))]
    out += [f"command {c}" for c in cur["commands"] - set(t.get("commands") or [])]
    builtin = {n for n in dir(builtins) if isinstance(getattr(builtins, n), type)}
    out += [f"error {e}" for e in cur["errors"] - set(t.get("errors") or []) - builtin]
    return out


def load():
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    OUT.write_text(json.dumps(merged(load()), indent=0, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
