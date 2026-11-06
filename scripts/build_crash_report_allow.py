"""Writes api/_crash-report-allow.json: what the site's crash-report endpoint (api/crash-report.js) accepts. Each
ecadlib source file with the functions in it, the command names, and ecadlib's own error classes. The endpoint refuses
anything else, so a made-up file or function name can never reach a public issue. Entries are only ever added (older
releases still report the functions they had). tests/test_crash_report_endpoint.py fails when this file is behind.
Run:  python3 scripts/build_crash_report_allow.py"""
import ast, builtins, json, sys
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


def current():
    files, errors = {}, {n for n in dir(builtins) if isinstance(getattr(builtins, n), type)
                         and issubclass(getattr(builtins, n), BaseException)}
    for p in sorted((ROOT / "ecadlib").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        files[rel] = _names(tree) | set(SPECIAL)
        errors |= _errors(tree)
    sys.path.insert(0, str(ROOT))
    from ecadlib.cli import COMMANDS
    return {"files": files, "commands": set(COMMANDS), "errors": errors}


def merged(old=None):
    cur = current()
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
