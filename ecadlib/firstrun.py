"""First-run conveniences for a new user (used by `ecadlib init`, `ecadlib demo` and ecadlib/cli.py):

- setup questions for `ecadlib init`: which tool (Altium / KiCad / both), your library folders, the workspace folder,
  which part sources to use, and which API keys are set (variable NAMES only, never values). The answers go in
  ecadlib.toml [setup] (and [sources] order); `--yes` takes the defaults without asking. Afterwards `init` runs the
  doctor and prints the next commands to run.
- `ecadlib demo`: a small library built from a bundled sample BOM, fully offline (standard parts only, no keys), in a
  real workspace folder, so `ecadlib status` and `ecadlib ui` can open it.
- checks the CLI runs before a command: --lib folders that do not exist and output folders that cannot be written
  give one line and a fix instead of a traceback or a silent empty result; [setup] libs / workspace fill in --lib /
  --workspace when neither the command line nor ECADLIB_LIB / ECADLIB_WORKSPACE give them.
- `blockers(rows)`: after a librarian run, one line per thing that held parts back for a reason outside the part
  (network blocked, no API key for the datasheet reader), with the fix."""
import argparse, json, os, re, shutil, sys, tempfile, time
from pathlib import Path

from . import rules as RU
from . import userconfig as UC

TOOLS = ("altium", "kicad", "both")
DEMO_BOM = Path(__file__).with_name("assets") / "demo_bom.csv"
DEMO_NAME = "ecadlib_demo"
DEMO_HEADER = "# ecadlib demo workspace (written by `ecadlib demo`)"


# ------------------------------------------------------------------ setup questions (ecadlib init)
def key_status(rules=None, environ=None):
    """[(env var name, set?, what it unlocks)] - names only; values are never read beyond 'is it non-empty'."""
    from .doctor import key_services
    environ = os.environ if environ is None else environ
    out = []
    for name, env, why in key_services(rules):
        ok = all(UC.secret(e, environ) for e in env)
        if name == "altium365" and not ok and UC.secret("NEXAR_CLIENT_ID", environ) and UC.secret("NEXAR_CLIENT_SECRET", environ):
            env, ok = ["NEXAR_CLIENT_ID", "NEXAR_CLIENT_SECRET"], True
        out.append((" + ".join(env), ok, why))
    return out


def reader_key(rules=None, environ=None, keys=None):
    """-> (what the datasheet reader's model needs, ready?) for the configured [ai] / [datasheet] model: a Claude key
    variable, another provider's key variable, or none at all (a local server such as Ollama)."""
    from . import llm as LL
    environ = os.environ if environ is None else environ
    try:
        ep = LL.endpoint(rules if isinstance(rules, dict) else None, "datasheet")
    except LL.AIConfigError as e:
        return f"[ai] needs fixing first: {e}", False
    if ep["provider"] == "anthropic":
        from .doctor import _ai_key_env
        env = _ai_key_env(rules)
        ok = next((ok for n, ok, _ in (keys if keys is not None else key_status(rules, environ)) if n == env), False)
        return f"needs {env}, which is {'set' if ok else 'not set yet'}", ok
    k = LL.key_for(ep, environ=environ)
    if k == LL.NO_KEY:
        return f"{ep['model']} at {ep['base_url']}: no key needed", True
    return f"needs {ep['key_env']}, which is {'set' if k else 'not set yet'}", bool(k)


def _split(text):
    return [p.strip() for p in re.split(r"[,;]", text or "") if p.strip()]


def _yes(ans, default=True):
    ans = (ans or "").strip().lower()
    return default if not ans else ans in ("y", "yes", "1", "true")


def ask_setup(existing=None, given=None, inp=input, out=print, interactive=True, rules=None, environ=None):
    """The [setup] answers {tool, libs, workspace, sources, unofficial}. existing: the current [setup]/[sources]/
    [providers] of ecadlib.toml (its values are the defaults); given: values from the command line (never asked).
    interactive=False: no questions, defaults only. sources is the [sources] order to write, or None to leave it as it
    is; unofficial is the [providers] unofficial answer (EasyEDA / JLCPCB search, off unless the person opts in), or
    None when not asked."""
    existing, given = existing or {}, {k: v for k, v in (given or {}).items() if v not in (None, [], "")}
    st = dict(existing.get("setup") or {})
    cur_sources = list((existing.get("sources") or {}).get("order") or RU.DEFAULTS["sources"]["order"])
    ans = {"tool": given.get("tool") or st.get("tool") or "both",
           "libs": list(given.get("libs") or st.get("libs") or []),
           "workspace": given.get("workspace") or st.get("workspace") or "ecadlib_workspace",
           "sources": list(given["sources"]) if given.get("sources") else None,
           "unofficial": given.get("unofficial")}
    keys = key_status(rules, environ)
    ds_need, ds_key = reader_key(rules, environ, keys)
    if interactive:
        out("\nSetup (Enter keeps the value in [brackets]):")
        if "tool" not in given:
            for _ in range(3):
                a = inp(f"  Which tool do you make libraries for? altium / kicad / both [{ans['tool']}]: ").strip().lower()
                if not a or a in TOOLS:
                    ans["tool"] = a or ans["tool"]
                    break
                out("    please type altium, kicad or both")
            else:
                out(f"    keeping {ans['tool']}")
        if "libs" not in given:
            a = inp("  Your existing library folders (KiCad .kicad_sym/.pretty or Altium .SchLib/.PcbLib), separated by commas"
                    f" [{', '.join(ans['libs']) or 'none'}]: ").strip()
            if a:
                ans["libs"] = [] if a.lower() == "none" else _split(a)
            for l in ans["libs"]:
                if not Path(l).expanduser().exists():
                    out(f"    note: {l} does not exist yet; check the path (the setup check will flag it too)")
        if "workspace" not in given:
            a = inp(f"  Folder where ecadlib keeps your BOMs and the libraries it builds [{ans['workspace']}]: ").strip()
            ans["workspace"] = a or ans["workspace"]
        if ans["sources"] is None:
            online = _yes(inp("  Look missing parts up online (KiCad library on gitlab.com)? [Y/n]: "))
            if online and "unofficial" not in given:
                was = bool((existing.get("providers") or {}).get("unofficial", RU.DEFAULTS["providers"]["unofficial"]))
                ans["unofficial"] = _yes(inp("  Also use EasyEDA/LCSC parts? They come from undocumented endpoints, not an "
                                             f"official API; review them before use [{'Y/n' if was else 'y/N'}]: "), default=was)
            ds = _yes(inp(f"  Let a model read the datasheet for parts no library has? ({ds_need}) "
                          f"[{'Y/n' if ds_key else 'y/N'}]: "), default=ds_key)
            order = [s for s in cur_sources if (online or s not in ("kicad", "easyeda")) and (ds or s != "datasheet")]
            if "standard" not in order:
                order.append("standard")
            ans["sources"] = order if order != cur_sources else None
    return ans


def setup_raw(ans):
    """The rules-file sections for the answers (added to what `init` infers)."""
    raw = {"setup": {"tool": ans["tool"], "libs": [str(l) for l in ans["libs"]], "workspace": str(ans["workspace"])}}
    if ans.get("sources"):
        raw["sources"] = {"order": list(ans["sources"])}
    if ans.get("unofficial") is not None:
        raw["providers"] = {"unofficial": bool(ans["unofficial"])}
    return raw


def keys_lines(rules=None, environ=None):
    """One line on the API keys that matter for a first run (names only); the doctor lists every one."""
    main = [k for k in key_status(rules, environ) if not k[2].endswith("part lookups")]
    have = [n for n, ok, _ in main if ok]
    miss = [f"{n} ({why.split(',')[0]})" for n, ok, why in main if not ok]
    return ["API keys (names only; `ecadlib keys set NAME`, the setup page or your environment, never ecadlib.toml): "
            + "; ".join(x for x in (f"set: {', '.join(have)}" if have else "", f"not set: {', '.join(miss)}" if miss else "") if x)
            + ". None is needed for KiCad-library and standard parts."]


def next_steps(ans, rules_file="ecadlib.toml"):
    """[(command, what it does)]: the 2-3 commands to run after `ecadlib init`."""
    ws = ans.get("workspace") or "ecadlib_workspace"
    lib = f"{ws}/librarian/YOUR_BOM"                 # <workspace>/librarian/*/plan.json: `ecadlib status` lists it
    steps = [("ecadlib demo", "see a real result first: a small library from a bundled BOM, offline, in under a minute"),
             (f"ecadlib librarian YOUR_BOM.csv -o {lib}", "fill every missing symbol and footprint for your BOM, then the "
                                                          "release check (`ecadlib status` shows the result)")]
    if ans.get("tool") in ("kicad", "both"):
        steps.append((f"ecadlib kicad-export {lib}/plan.json -o {ws}/kicad/YOUR_BOM --name YOUR_BOM",
                      "the library as KiCad files with fp-lib-table / sym-lib-table"))
    if ans.get("tool") in ("altium", "both"):
        steps.append(("ecadlib ui", f"or do it in the web UI on {ws}: drop your BOM there, review held parts, sign off, send "
                                    "to Altium (DbLib, .SchLib/.PcbLib, Altium 365); a plan made on the command line is "
                                    f"reviewed with `ecadlib review {lib}/plan.json`"))
    return steps


def finish_init(ans, rules_file, out=print, environ=None, run_doctor=True):
    """After `init` wrote the file: make the workspace folder, run the doctor, print the next commands. Returns the
    doctor report (None when not run)."""
    from . import doctor
    ws = Path(ans["workspace"]).expanduser()
    if not ws.is_absolute():
        ws = Path(rules_file).resolve().parent / ws
    try:
        ws.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        out(f"could not create the workspace {ws}: {e.strerror or e}")
    rules = RU.load(str(rules_file))
    for line in keys_lines(rules, environ):
        out(line)
    rep = None
    if run_doctor:
        libs = [str(_rel_to(l, rules_file)) for l in ans["libs"]]
        rep = doctor.run(str(ws), libs, str(rules_file), environ)
        out("\nChecking the setup (ecadlib doctor):")
        from . import term as TM               # styled on a terminal; plain when captured (web UI, tests)
        out(doctor.render(rep, TM.wants_style(sys.stdout), TM.term_width(sys.stdout), TM.VERBOSITY) if out is print
            else doctor.render(rep))
    out("\nNext:")
    for c, why in next_steps(ans):
        out(f"  {c}\n      {why}")
    return rep


def setup_workspace(start="."):
    """The [setup] workspace of the ecadlib.toml found from `start` (relative paths start at that file), or None."""
    try:
        f = RU.find(start)
        ws = (RU.parse_text(f.read_text(encoding="utf-8"), f.name).get("setup") or {}).get("workspace") if f else None
    except Exception:
        return None
    if not isinstance(ws, str) or not ws.strip():
        return None
    p = _rel_to(ws, f)
    try:
        return Path(os.path.relpath(p))       # short on the screen when it is below the current folder
    except ValueError:                        # another drive on Windows
        return p


def default_out(kind, bom, start="."):
    """Default -o of `librarian` / `triage` for this BOM: <[setup] workspace>/librarian/<BOM name> or
    <workspace>/reports/triage_<BOM name> (where `ecadlib status` looks), else out/librarian / out/triage."""
    ws = setup_workspace(start)
    stem = re.sub(r"[^\w.-]+", "_", Path(str(bom)).stem) or "bom"
    if kind == "librarian":
        return str(ws / "librarian" / stem) if ws else "out/librarian"
    return str(ws / "reports" / f"triage_{stem}") if ws else "out/triage"


def _rel_to(p, rules_file):
    p = Path(str(p)).expanduser()
    return p if p.is_absolute() else (Path(rules_file).resolve().parent / p)


# ------------------------------------------------------------------ [setup] defaults for --lib / --workspace
def config_defaults(cmd, argv, environ=None, start="."):
    """argv with --lib / --workspace from ecadlib.toml [setup] added where the command takes them (the same commands
    as ECADLIB_LIB / ECADLIB_WORKSPACE) and neither the command line nor the environment gives them. Relative paths
    in [setup] start at the rules file's folder. Returns (argv, notes). Never raises: a bad file is left to the command."""
    from .cliutil import ENV_FLAGS, _has_flag, _subcommand
    environ = os.environ if environ is None else environ
    argv, notes = list(argv), []
    if any(a in ("-h", "--help") for a in argv):
        return argv, notes
    wanted = [(var, flag, cmds[cmd]) for var, (flag, cmds) in ENV_FLAGS.items()
              if cmd in cmds and not (environ.get(var) or "").strip() and not _has_flag(argv, flag)]
    if not wanted:
        return argv, notes
    try:
        f = RU.find(start)
        raw = RU.parse_text(f.read_text(encoding="utf-8-sig"), f.name) if f else {}
        st = raw.get("setup") or {}
    except Exception:
        f, st = None, {}
    for var, flag, subs in wanted:
        vals = st.get("libs") if flag == "--lib" else [st.get("workspace")] if st.get("workspace") else []
        vals = [v for v in (vals or []) if isinstance(v, str) and v.strip()]
        where = f"[setup] in {f}"
        if vals:
            vals = [str(_rel_to(v, f)) for v in vals]
        elif flag == "--workspace":
            vals, where = _settings_workspace(start, environ), "the setup page (settings.json)"
        if not vals:
            continue
        extra = [x for v in vals for x in (flag, v)]
        if subs is None:
            argv = extra + argv
        elif _subcommand(argv) in subs:
            argv = argv + extra
        else:
            continue
        notes.append(f"{flag} from {where}")
    return argv, notes


def _settings_workspace(start=".", environ=None):
    """[the workspace chosen on the setup page / desktop app] (settings.json), the last default for --workspace, when
    it is a folder and there is no ./ecadlib_workspace here (that one keeps winning, as before); else []."""
    try:
        w = UC.load_settings(environ).get("workspace")
        if isinstance(w, str) and w.strip() and Path(w).expanduser().is_dir() \
                and not (Path(start) / "ecadlib_workspace").exists():
            return [str(Path(w).expanduser())]
    except Exception:
        pass
    return []


# ------------------------------------------------------------------ checks before a command runs
LIB_CHECKED = {"triage", "librarian", "resolve", "kicad-export", "mcp", "ui", "dedupe"}
OUT_CHECKED = {"init", "demo", "triage", "librarian", "kicad-export", "kicad-dblib", "dblib",
               "export-parts", "dblib-sync"}


def _values(argv, flags):
    out, it = [], iter(argv)
    for a in it:
        if a == "--":
            break
        for f in flags:
            if a == f:
                v = next(it, None)
                if v is not None:
                    out.append(v)
            elif f.startswith("--") and a.startswith(f + "="):
                out.append(a.split("=", 1)[1])
            elif not f.startswith("--") and a.startswith(f) and len(a) > len(f) and not a.startswith("--"):
                out.append(a[len(f):])
    return out


def writable_problem(path):
    """None when `path` (a file or folder to be created) can be written, else a short reason."""
    p = Path(path).expanduser()
    q = p
    while not q.exists():
        if q.parent == q:
            break
        q = q.parent
    if q.exists() and not q.is_dir():
        if q == p:
            q = q.parent
        else:
            return f"{q} is a file, not a folder"
    try:
        with tempfile.TemporaryFile(dir=q, prefix=".ecadlib-probe-"):
            pass
    except OSError as e:
        return f"cannot write in {q} ({e.strerror or e})"
    return None


def preflight(cmd, argv, environ=None):
    """(message, hint) for a command line that cannot work (a --lib folder that does not exist, an output folder that
    cannot be written), or None. Runs before the command, so a typo gives one line instead of a silent empty result."""
    if any(a in ("-h", "--help") for a in argv):
        return None
    if cmd in LIB_CHECKED:
        for l in _values(argv, ["--lib"]):
            if not Path(l).expanduser().exists():
                return (f"library folder not found: {l}",
                        "fix --lib (or ECADLIB_LIB, or [setup] libs in ecadlib.toml); `ecadlib doctor` checks every path")
            if not Path(l).expanduser().is_dir():     # a .kicad_sym itself would index nothing, silently
                return (f"--lib must be a library folder, not a file: {l}",
                        f"give the folder it is in: --lib {Path(l).expanduser().parent}")
    if cmd in OUT_CHECKED:
        flags = ["--to"] if cmd == "dblib-sync" else ["-o", "--out"]
        for o in _values(argv, flags + (["--out"] if cmd == "dblib-sync" else [])):
            why = writable_problem(o)
            if why:
                return (f"cannot write the output {o}: {why}", "pick another -o / --out folder you can write to")
    return None


# ------------------------------------------------------------------ after a librarian run
def bom_hint(err):
    """A fix line for a BOM the reader could not use ('' when there is nothing to add)."""
    t = str(err)
    if "no BOM header found" in t:
        return ("\n  hint: name a column MPN (or Manufacturer Part Number), Designator or Value; or map your headers in "
                "ecadlib.toml, e.g. [bom.columns] mpn = \"Part No\" (`ecadlib triage BOM` shows what was recognised)")
    if "the BOM file is empty" in t or "no part rows" in t:
        return "\n  hint: export the BOM again with its part rows (one row per part or per group of designators)"
    return ""


NET_RE = re.compile(r"(ProxyError|ConnectionError|ConnectTimeout|ReadTimeout|timed out|Name or service not known|"
                    r"Temporary failure in name resolution|proxy CONNECT refused|HTTP 403|HTTP 407|HTTP 429|"
                    r"Max retries exceeded|offline(?::| and) not (?:in cache|in the cache|cached|downloaded))", re.I)


def blockers(rows, rules=None, offline=False):
    """[(message, fix)] for causes outside the parts that held them back: part sources that could not be reached, and
    the datasheet reader without an API key, and one field missing on many parts (a BOM without that column).
    rows: librarian.review_rows(items) (each has `part`, `tried`, and `status` / `why`)."""
    net, nokey, off = {}, [], []
    for r in rows:
        for step in re.split(r";\s+(?=[a-z_]+[: ])", r.get("tried") or ""):
            src = step.split(":", 1)[0].split(" ", 1)[0].strip()
            m = NET_RE.search(step)
            if m and src:
                net.setdefault(src, [set(), m.group(0)])[0].add(r["part"])
            if "no API key" in step:
                nokey.append(r["part"])
            if "--offline: datasheet not read" in step:
                off.append(r["part"])
    out = []
    only = {}                                  # parts held for one missing field and nothing else (a BOM column short)
    for r in rows:
        m = re.fullmatch(r"fields: (no datasheet link|(.+) is empty)", str(r.get("why") or ""))
        if r.get("status") == "held" and m:
            only.setdefault(m.group(1), []).append(r["part"])
    for what, parts in sorted(only.items()):
        if len(parts) >= 3:
            col = "Datasheet" if what == "no datasheet link" else what[:-len(" is empty")]
            out.append((f"{len(parts)} parts are held only because of '{what}' ({', '.join(parts[:3])}"
                        f"{' ...' if len(parts) > 3 else ''})",
                        f"add a {col} column to the BOM, or take {col} out of [parameters] required in ecadlib.toml"))
    for src, (parts, why) in sorted(net.items()):
        if offline:
            out.append((f"{src}: not in the download cache for {len(parts)} part(s) (--offline)",
                        "run once without --offline to fill the cache"))
        else:
            out.append((f"network: {src} could not be reached (or refused) for {len(parts)} part(s) ({why})",
                        "check your internet connection or proxy, or skip online lookups: --offline --sources standard"))
    if off:
        out.append((f"{len(set(off))} part(s) need the datasheet read by AI: --offline sends nothing to the model",
                    "run without --offline to read them"))
    if nokey:
        from .doctor import _ai_key_env
        env = _ai_key_env(rules)
        try:
            from . import llm as LL
            ep = LL.endpoint(rules if isinstance(rules, dict) else None, "datasheet")
            env = ep["key_env"] if ep["provider"] != "anthropic" and ep["key_env"] else env
        except Exception as e:
            from . import fallbacks as FB
            FB.note("[ai] / [datasheet] model setup did not resolve", e, instead=env)
        out.append((f"{len(set(nokey))} part(s) need the datasheet reader, but {env} is not set",
                    f"add it on the setup page (`ecadlib setup`) or with `ecadlib keys set {env}`, or take datasheet out of "
                    "[sources] order in ecadlib.toml"))
    return out


# ------------------------------------------------------------------ ecadlib demo
def build_demo(ws, tool="both", bom=None, log=print):
    """Build the demo library in Workspace `ws`, offline, from the bundled BOM (or `bom`). Returns a summary dict."""
    from .app import Workspace
    t0 = time.time()
    root = Path(ws.root)
    if root.resolve() == Path(root.resolve().anchor):
        raise ValueError(f"refusing to build the demo at the filesystem root ({root}); give a folder, e.g. -o ecadlib-demo")
    rules_f = root / "ecadlib.toml"
    if rules_f.exists() and not rules_f.read_text(encoding="utf-8").startswith(DEMO_HEADER):
        raise ValueError(f"{root} has its own ecadlib.toml; the demo needs a folder of its own (e.g. -o ecadlib-demo)")
    if not rules_f.exists():                  # the demo's own rules: standard parts only, so a stray ecadlib.toml in a
        rules_f.write_text(                   # parent folder cannot change the result, and nothing goes on the network
            DEMO_HEADER + ": standard parts only, no network, no API keys.\n"
            "[sources]\norder = [\"standard\"]\n\n[setup]\n"
            f"tool = \"{tool}\"\nlibs = []\nworkspace = \".\"\n", encoding="utf-8")
    src = Path(bom) if bom else DEMO_BOM
    dest = root / "uploads" / "demo" / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or dest.read_bytes() != src.read_bytes():
        shutil.copyfile(src, dest)
    rel = ws.rel(dest)
    log(f"[1/3] BOM: {rel} ({sum(1 for _ in open(dest, encoding='utf-8')) - 1} lines)")
    log("[2/3] finding symbols and footprints (built-in standard parts; no network, no API key) ...")
    res = ws.librarian_run(rel, sources=["standard"])
    files = {"plan": res["reports"]}
    kicad = None
    if tool in ("kicad", "both"):
        log("[3/3] writing KiCad files (.kicad_sym, .pretty, lib tables, database library) ...")
        kicad = ws.kicad_dblib(rel, name=DEMO_NAME)
    else:
        log("[3/3] Altium files are written by step 2 (.SchLib / .PcbLib)")
    lib_dir = ws._librarian_dir(dest)
    par = ws.parity(rel, kicad=bool(kicad), name=DEMO_NAME) if tool in ("kicad", "both") else None   # both libraries agree
    if par:
        log(f"      KiCad / Altium parity: {par['counts']['match']} parts match, {par['counts']['mismatch']} differ ({par['report']})")
    altium = sorted(ws.rel(p) for p in (lib_dir / "models").glob("*/*") if p.suffix in (".SchLib", ".PcbLib"))
    return {"workspace": str(root), "bom": rel, "parts": res["parts"], "ready": res["ready"], "held": res["held"],
            "held_parts": [r["part"] for r in res["review"] if r["status"] != "ready"],
            "review": [r for r in res["reports"] if r.endswith("review.md")],
            "plan": ws.rel(lib_dir / "plan.json"), "altium": altium,
            "kicad": kicad and {"folder": kicad["folder"], "files": kicad["files"]},
            "parity": par and {"ok": par["ok"], "counts": par["counts"], "report": par["report"], "mismatches": par["mismatches"]},
            "seconds": round(time.time() - t0, 1)}


def demo_main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ecadlib demo", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="build a small library from a bundled sample BOM, fully offline (no network, no API keys), "
                    "so you can see a real result in under a minute",
        epilog="examples:\n  ecadlib demo                       # writes ./ecadlib-demo\n"
               "  ecadlib demo -o /tmp/try --tool kicad\n  ecadlib demo --bom my_bom.csv      # your BOM, standard parts only")
    ap.add_argument("-o", "--out", "--workspace", dest="out", default="ecadlib-demo",
                    help="workspace folder to build in (default %(default)s); re-running reuses it")
    ap.add_argument("--tool", choices=TOOLS, default=None, help="which files to write (default: [setup] tool, else both)")
    ap.add_argument("--bom", help="use this BOM instead of the bundled sample (still offline, standard parts only)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    a = ap.parse_args(argv)
    from .app import Workspace
    tool = a.tool
    if tool is None:
        try:
            tool = (RU.load().get("setup") or {}).get("tool") or "both"
        except RU.RulesError:
            tool = "both"
    from . import term as TM
    if a.bom:                                   # checked before the workspace folders are made
        p = Path(a.bom).expanduser()
        why = ("not found" if not p.exists() else "is a folder, not a BOM file" if not p.is_file()
               else "is empty" if not p.read_bytes().strip() else "")
        if why:
            print(f"ecadlib demo: {a.bom} {why}\n  hint: give a BOM file (CSV, TSV or XLSX), or leave out --bom for "
                  "the bundled sample", file=sys.stderr)
            return 2
    say = (lambda *x: None) if a.json or TM.VERBOSITY < 0 else print   # -q: only the outcome
    say(f"ecadlib demo: building a small library in {a.out}/ from "
        f"{'the bundled sample BOM' if not a.bom else a.bom}, offline")
    ws = Workspace(a.out, offline=True)
    try:
        res = build_demo(ws, tool, a.bom, log=say)
    except ValueError as e:                     # a folder with someone's rules, or a BOM the reader cannot use
        print(f"ecadlib demo: {e}" + bom_hint(e), file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    from . import term as TM
    t = TM.Term()
    written = [(f"{a.out}/{res['plan']}", "the library plan"), (", ".join(f"{a.out}/{r}" for r in res["review"]), "review: what to check, part by part")]
    if res["altium"] and tool != "kicad":      # the librarian always writes them; --tool kicad does not point at them
        written.append((f"{a.out}/{Path(res['altium'][0]).parent.parent}/", f"{len(res['altium'])} Altium files (.SchLib, .PcbLib)"))
    if res["kicad"]:
        kd = Path(res["workspace"]) / res["kicad"]["folder"]
        written.append((f"{a.out}/{res['kicad']['folder']}/", "KiCad: " + ", ".join(sorted(p.name + ("/" if p.is_dir() else "")
                                                                                   for p in kd.iterdir() if p.suffix in (".kicad_sym", ".pretty", ".kicad_dbl")))))
    t.summary(f"{res['parts']} parts: {res['ready']} ready, {res['held']} held in {res['seconds']} s"
              + (f" (held: {', '.join(res['held_parts'][:5])}{' ...' if len(res['held_parts']) > 5 else ''}"
                 + ("; that is expected, see the review)" if not a.bom else "; the review says why)") if res["held"] else ""),
              written=written,
              next=[(f"ecadlib status --workspace {a.out}", "one-screen summary"),
                    (f"ecadlib ui --workspace {a.out}", "look at it in the browser (held parts, sign-off, exports)"),
                    ("ecadlib init", "set up for your own libraries and BOMs")])
    return 0
