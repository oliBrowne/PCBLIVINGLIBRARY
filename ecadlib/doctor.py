"""`ecadlib doctor`: one screen that says whether this machine is ready, each line OK / WARN / FAIL with a fix.

    ecadlib doctor [--workspace W] [--lib DIR ...] [--rules FILE] [--json]

Checks: the Python version; the packages ecadlib needs (FAIL when missing) and the optional ones (pillow, pypdf) and
tools (pdftoppm, kicad-cli, git) (WARN); which API keys are set (names only, never the values); the rules file
(ecadlib.toml: which one is used and whether it is valid - errors name file:line where the line can be found); the
workspace (exists, writable); the library folders (--lib / $ECADLIB_LIB, [agent] libs and library_repo) and the watched
sources in [agent.watch]. Exit code 1 when any line is FAIL, else 0. --json prints {"ok", "checks": [{"check",
"level", "detail", "hint"}]}. Nothing is written except a temporary probe file in the workspace, removed at once."""
import argparse, importlib.util, json, os, re, shutil, sys, tempfile
from pathlib import Path

LEVELS = ("OK", "WARN", "FAIL")
CORE = (("requests", "requests"), ("pandas", "pandas"), ("openpyxl", "openpyxl"), ("jsonschema", "jsonschema"),
        ("olefile", "olefile"))
OPTIONAL = (("PIL", "pillow", "visual-check pictures"), ("pypdf", "pypdf", "datasheet page trimming"))
TOOLS = (("pdftoppm", "datasheet pages for visual-check", "install poppler-utils"),
         ("kicad-cli", "KiCad checks and SVG previews", "install KiCad 7 or newer"),
         ("git", "the library agent's batch PRs", "install git"))


def _c(check, level, detail, hint=""):
    return {"check": check, "level": level, "detail": detail, "hint": hint}


def _toml_ok():
    try:
        from .rules import toml_module
        return toml_module() is not None
    except ImportError:
        return False


def check_python(v=None):
    v = tuple(v or sys.version_info[:3])
    s = ".".join(map(str, v))
    if v < (3, 10):
        return _c("python", "FAIL", f"Python {s}", "ecadlib needs Python 3.10 or newer")
    if v < (3, 11) and not (v == tuple(sys.version_info[:3]) and _toml_ok()):     # 3.10 reads TOML with tomli
        return _c("python", "WARN", f"Python {s}", "3.10 cannot read TOML without tomli: pip install tomli, "
                  "use ecadlib_rules.json, or Python 3.11+")
    return _c("python", "OK", f"Python {s}")


def check_packages(find_spec=importlib.util.find_spec, which=shutil.which):
    out = []
    missing = [pip for mod, pip in CORE if find_spec(mod) is None]
    out.append(_c("packages", "FAIL", f"missing: {', '.join(missing)}", f"pip install {' '.join(missing)}") if missing
               else _c("packages", "OK", ", ".join(pip for _, pip in CORE)))
    for mod, pip, why in OPTIONAL:
        out.append(_c(f"optional {pip}", "OK", "installed") if find_spec(mod) is not None
                   else _c(f"optional {pip}", "WARN", f"not installed ({why})", f"pip install {pip}"))
    for tool, why, fix in TOOLS:
        p = which(tool)
        out.append(_c(f"tool {tool}", "OK", p) if p else _c(f"tool {tool}", "WARN", f"not found ({why})", fix))
    return out


def key_services(rules=None):
    """[(service, [env var names], what it unlocks)] - the names come from the rules where the rules name them."""
    ds = ((rules or {}).get("datasheet") or {}).get("api_key_env") or "ANTHROPIC_API_KEY"
    gh = ((rules or {}).get("agent") or {}).get("github_token_env") or "GITHUB_TOKEN"
    out = [("anthropic", [ds], "datasheet reader and visual check")] if _ai_provider(rules) == "anthropic" else []
    out += [("altium365", ["NEXAR_TOKEN"], "Altium 365 push / pull")]
    try:
        from . import providers as P
        out += [(c.name, list(c.env), f"{c.name} part lookups") for c in P.ALL if c.env]
    except Exception:                                  # a missing package is reported by check_packages
        pass
    out.append(("github", [gh], "library agent PRs, upkeep issues"))
    return out


def _ai_provider(rules):
    try:
        from . import llm as LL
        return LL.endpoint(rules if isinstance(rules, dict) else None, "datasheet")["provider"]
    except Exception:
        return "anthropic"


def check_ai(rules=None, environ=None):
    """The model every AI feature uses ([ai], ecadlib.llm): provider, model, tier and rails, and whether its key
    variable is set (names only). Nothing is sent: the profile comes from the built-in table and ecadlib.toml."""
    from . import llm as LL
    from .llm import profiles as PR, rails as R
    environ = os.environ if environ is None else environ
    try:
        ep = LL.endpoint(rules if isinstance(rules, dict) else None, "datasheet")
    except LL.AIConfigError as e:
        return [_c("ai model", "FAIL", str(e), "fix [ai] in ecadlib.toml (README: Bring your own model)")]
    prof = PR.profile(ep["model"], ep["provider"], rules)
    plan = R.plan(prof, ep["assist"], ep["feedback_retry"])
    guide = [k for k in plan["rails"] if R.RAILS[k][0] != "support"]
    where = "Anthropic API" if ep["provider"] == "anthropic" else f"{ep['base_url']}" + (" (local)" if ep["local"] else "")
    out = [_c("ai model", "OK", f"{ep['model']} via {ep['name']} ({where}); {plan['tier']} tier, rails: "
                                f"{', '.join(guide) or 'support only'}")]
    if ep["provider"] == "anthropic":                  # its key is the `key anthropic` line below
        return out
    key = LL.key_for(ep, environ=environ)
    if key == LL.NO_KEY:
        out.append(_c("ai key", "OK", "none needed (a server on this machine or your network)"))
    elif key:
        out.append(_c("ai key", "OK", f"{ep['key_env'] or 'ANTHROPIC_API_KEY'} set"))
    else:
        out.append(_c("ai key", "WARN", f"{ep['key_env']} not set: datasheets cannot be read and the visual check is skipped",
                      f"to add it: export {ep['key_env']}=... (ecadlib.toml keeps only the name, never the key)"))
    return out


def check_keys(rules=None, environ=None):
    environ = os.environ if environ is None else environ
    out = []
    for name, env, why in key_services(rules):
        ok = all(environ.get(e) for e in env)
        if name == "altium365" and not ok:
            ok = bool(environ.get("NEXAR_CLIENT_ID") and environ.get("NEXAR_CLIENT_SECRET"))
            env = env if not ok else ["NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET"]
        out.append(_c(f"key {name}", "OK", f"{' + '.join(env)} set") if ok
                   else _c(f"key {name}", "WARN", f"{' + '.join(env)} not set: {why} skipped",
                           f"to add it: export {env[0]}=... (ecadlib.toml keeps only the name, never the key)"
                           + ("; NEXAR_CLIENT_ID + NEXAR_CLIENT_SECRET also work" if name == "altium365" else "")))
    return out


def locate(err, path):
    """Line number in `path` that a RulesError message points at (TOML syntax errors carry it; validation errors name
    a key such as footprint.density or [section]), or None."""
    m = re.search(r"at line (\d+)", err)
    if m:
        return int(m.group(1))
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    m = re.search(re.escape(str(path)) + r": (\S+)", err)
    if not m:
        return None
    key = m.group(1)
    if key.startswith("[") and key.endswith("]"):
        want = key.strip("[]")
        return next((n for n, l in enumerate(lines, 1) if re.match(r"\s*\[+\s*" + re.escape(want) + r"\s*\]", l)), None)
    parts = [p for p in re.split(r"[.\[\]]", key) if p and not p.isdigit()]
    if len(parts) < 2:
        return None
    leaf, header = parts[-1], ""
    kre = re.compile(r'\s*["\']?' + re.escape(leaf) + r'["\']?\s*=')
    fallback = None
    for n, l in enumerate(lines, 1):
        h = re.match(r"\s*\[+\s*([^\]]+?)\s*\]", l)
        if h:
            header = h.group(1).replace('"', "")
            continue
        if kre.match(l):
            if header == ".".join(parts[:-1]):
                return n
            if header.split(".")[0] == parts[0] and fallback is None:
                fallback = n
    if fallback:
        return fallback
    want = ".".join(parts[:-1])                      # the key is in a sub-table header: [naming.by_category]
    return next((n for n, l in enumerate(lines, 1) if re.match(r"\s*\[+\s*" + re.escape(want) + r"\s*\]", l)), None)


def check_rules(path=None, workspace=None):
    """(check, rules dict or None)."""
    from . import rules as RU
    try:
        f = Path(path) if path else None
        if f is None and workspace is not None:
            f = next((Path(workspace) / n for n in RU.FILES if (Path(workspace) / n).is_file()), None)
        if f is None:
            f = RU.find(workspace if workspace is not None else ".")
    except RU.RulesError as e:                         # ECADLIB_CONFIG names a missing file
        return _c("rules", "FAIL", str(e), "fix or unset ECADLIB_CONFIG"), None
    if f is None:
        return _c("rules", "OK", "no ecadlib.toml found: ecadlib's defaults",
                  "`ecadlib init` writes one from your existing libraries"), RU.load({})
    if not f.is_file():
        return _c("rules", "FAIL", f"{f}: not found", "check the --rules path"), None
    try:
        r = RU.load(f)
    except RU.RulesError as e:
        n = locate(str(e), f)
        where = f"{f}:{n}" if n else str(f)
        msg = str(e)
        msg = msg[len(str(f)) + 2:] if msg.startswith(str(f) + ": ") else msg
        return _c("rules", "FAIL", f"{where}: {msg}", "fix that line; `ecadlib rules show` lists every key and its default"), None
    src = " (from ECADLIB_CONFIG)" if os.environ.get("ECADLIB_CONFIG") and not path else ""
    return _c("rules", "OK", f"{f}{src}: valid"), r


def check_workspace(ws):
    p = Path(ws).expanduser()
    if not p.exists():
        return _c("workspace", "WARN", f"{p.resolve()}: does not exist yet", "created on first use by `ecadlib ui` / `ecadlib mcp`")
    if not p.is_dir():
        return _c("workspace", "FAIL", f"{p.resolve()}: not a folder", "point --workspace (or ECADLIB_WORKSPACE) at a folder")
    try:
        with tempfile.NamedTemporaryFile(dir=p, prefix=".ecadlib-doctor-"):
            pass
    except OSError as e:
        return _c("workspace", "FAIL", f"{p.resolve()}: not writable ({e.strerror or e})", "fix the folder's permissions")
    return _c("workspace", "OK", f"{p.resolve()}: writable")


def check_formats(ws):
    """Files written by another ecadlib version (ecadlib/migrate.py): older -> `ecadlib migrate`; newer -> upgrade."""
    from . import migrate as MG
    try:
        rows = MG.scan(ws, max_depth=4)
    except OSError as e:
        return _c("file formats", "WARN", f"could not scan {ws} ({e})")
    new = [r for r in rows if r["state"] in ("new", "error")]
    old = [r for r in rows if r["state"] == "old"]
    if new:
        r = new[0]
        return _c("file formats", "FAIL", r.get("error") or f"{r['path']}: made by ecadlib {r['made_by'] or '?'} "
                  f"({r['kind']} schema {r['version']})", "upgrade ecadlib (pip install -U ecadlib)")
    if old:
        return _c("file formats", "WARN", f"{len(old)} file(s) from an older ecadlib ({', '.join(sorted({r['kind'] for r in old}))})",
                  "they are migrated when used; `ecadlib migrate` does it now (backups in .ecadlib_backup/)")
    return _c("file formats", "OK", f"{len(rows)} versioned file(s), all current")


def _lib_summary(p):
    try:
        sym = sum(1 for _ in p.glob("*.kicad_sym")) + sum(1 for _ in p.glob("*/*.kicad_sym"))
        fp = sum(1 for _ in p.glob("*.pretty")) + sum(1 for _ in p.glob("*/*.pretty"))
        al = sum(1 for _ in p.glob("*.SchLib")) + sum(1 for _ in p.glob("*.PcbLib")) + sum(1 for _ in p.glob("*.DbLib"))
    except OSError:
        return ""
    return ", ".join(f"{n} {k}" for n, k in ((sym, ".kicad_sym"), (fp, ".pretty"), (al, "Altium libs")) if n) or "no library files at the top"


def check_paths(libs=(), rules=None):
    out = []
    ag = (rules or {}).get("agent") or {}
    named = [("lib", l, "FAIL") for l in libs] + [("agent.libs", l, "FAIL") for l in ag.get("libs") or []]
    if ag.get("library_repo"):
        named.append(("agent.library_repo", ag["library_repo"], "FAIL"))
    for s in ((ag.get("watch") or {}).get("sources") or []):
        if isinstance(s, dict) and s.get("type") in ("bom_file", "folder", "altium_project") and s.get("ref"):
            named.append((f"watch {s['type']}", s["ref"], "WARN"))
    for what, l, bad in named:
        p = Path(str(l)).expanduser()
        if p.exists():
            out.append(_c(what, "OK", f"{p}" + (f" ({_lib_summary(p)})" if p.is_dir() and what != "agent.library_repo" else "")))
        else:
            out.append(_c(what, bad, f"{p}: not found", "fix the path (relative paths start at the current folder)"))
    if not libs:
        out.append(_c("lib", "WARN", "no --lib / ECADLIB_LIB library folder given",
                      "export ECADLIB_LIB=/path/to/kicad/libs (several: separate with " + repr(os.pathsep) + ")"))
    return out


def run(workspace="ecadlib_workspace", libs=(), rules_path=None, environ=None):
    checks = [check_python()] + check_packages()
    rc, rules = check_rules(rules_path, workspace if Path(workspace).is_dir() else None)
    checks.append(rc)
    checks += check_ai(rules, environ)
    checks += check_keys(rules, environ)
    checks.append(check_workspace(workspace))
    if Path(workspace).is_dir():
        checks.append(check_formats(workspace))
    checks += check_paths(libs, rules)
    return {"ok": not any(c["level"] == "FAIL" for c in checks), "checks": checks}


def render(rep, styled=False, width=None, verbosity=0):
    """Outcome first, then what must be fixed, then what is optional, then what is fine (each with its fix on the next
    line). styled: glyphs + colour (a terminal); -q: only the outcome and the problems."""
    import io
    from . import term as TM
    buf = io.StringIO()
    t = TM.Term(buf, styled=styled, width=width, verbosity=verbosity)
    n = {lv: sum(c["level"] == lv for c in rep["checks"]) for lv in LEVELS}
    st = {"FAIL": "failed", "WARN": "warn", "OK": "ok"}
    head = ("Ready to use." if not n["FAIL"] else f"Not ready: {n['FAIL']} problem{'s' if n['FAIL'] != 1 else ''} to fix first.") \
        + (f" {n['WARN']} optional thing{'s' if n['WARN'] != 1 else ''} not set up." if n["WARN"] else "")
    t.write(t.bold(head))
    for lv, title in (("FAIL", "Fix these:"), ("WARN", "Optional (works without them):"), ("OK", "Fine:")):
        group = [c for c in rep["checks"] if c["level"] == lv]
        if not group or (lv == "OK" and t.quiet):
            continue
        t.write("\n" + title)
        rows = []
        for c in group:
            rows.append([t.status(st.get(lv, "info"), lv) if styled else lv, c["check"], c["detail"]])
            if c["hint"] and lv != "OK":
                rows.append(["", "", f"-> {c['hint']}"])
        t.table(rows, indent=0)
    t.write(f"\n{n['OK']} ok, {n['WARN']} warning(s), {n['FAIL']} failure(s)")
    return buf.getvalue().rstrip("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib doctor", description="check this machine's ecadlib setup (OK / WARN / FAIL)")
    ap.add_argument("--workspace", default=os.environ.get("ECADLIB_WORKSPACE") or "ecadlib_workspace")
    ap.add_argument("--lib", action="append", default=[], help="KiCad library folder to check (repeatable; default $ECADLIB_LIB)")
    ap.add_argument("--rules", help="rules file to check (default: the workspace's, then ECADLIB_CONFIG, then the search)")
    ap.add_argument("--json", action="store_true", help='print {"ok", "checks": [{"check", "level", "detail", "hint"}]}')
    a = ap.parse_args(argv)
    rep = run(a.workspace, a.lib, a.rules)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        from . import term as TM
        print(render(rep, TM.wants_style(sys.stdout), TM.term_width(sys.stdout), TM.VERBOSITY))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
